import json
from datetime import datetime, timedelta, timezone

import responses

from pipeline import audit


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(audit, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.setattr(audit, "MODELS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(audit, "BACKLOG_PATH", tmp_path / "IMPROVEMENT_BACKLOG.md")
    monkeypatch.setattr(audit, "INDEX_HTML_PATH", tmp_path / "index.html")
    monkeypatch.setattr(audit, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(audit, "KNOWN_GAPS_PATH", tmp_path / "known_gaps.json")  # absent by default -- no allowlist
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "history").mkdir(parents=True, exist_ok=True)
    (tmp_path / "IMPROVEMENT_BACKLOG.md").write_text("# Improvement Backlog\n")


def _write_known_gaps(tmp_path, gaps):
    doc = {"schema_version": 1, "gaps": gaps}
    with open(tmp_path / "known_gaps.json", "w") as f:
        json.dump(doc, f)


def _write_series(tmp_path, metric, dates_values, source="test"):
    doc = {
        "metric": metric,
        "unit": "USD",
        "schema_version": 1,
        "generated_at": "2026-07-09T00:00:00Z",
        "series": [{"date": d, "value": v, "source": source} for d, v in dates_values],
    }
    with open(tmp_path / "history" / f"{metric}.json", "w") as f:
        json.dump(doc, f)


# --------------------------------------------------------------------------
# Continuity
# --------------------------------------------------------------------------


def test_continuity_detects_a_gap(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    _write_series(tmp_path, "price_daily", [("2026-07-01", 1), ("2026-07-02", 2), ("2026-07-05", 3)])
    for metric in ("hashrate_daily", "supply_daily", "fng_daily"):
        _write_series(tmp_path, metric, [("2026-07-01", 1), ("2026-07-02", 2)])  # clean, no gap

    findings = audit.check_continuity()

    assert len(findings) == 1
    assert findings[0]["metric"] == "price_daily"
    assert findings[0]["severity"] == "WARN"
    assert "2026-07-02" in findings[0]["detail"]


def test_continuity_no_gap_is_clean(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    for metric in audit.CONTINUITY_CHECKED_METRICS:
        _write_series(tmp_path, metric, [("2026-07-01", 1), ("2026-07-02", 2), ("2026-07-03", 3)])

    assert audit.check_continuity() == []


def test_continuity_skips_difficulty_entirely(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    # A wildly sparse difficulty series (by design) must not be flagged --
    # difficulty_daily isn't in CONTINUITY_CHECKED_METRICS at all.
    _write_series(tmp_path, "difficulty_daily", [("2009-01-03", 1), ("2020-01-01", 2)])
    for metric in audit.CONTINUITY_CHECKED_METRICS:
        _write_series(tmp_path, metric, [("2026-07-01", 1)])

    findings = audit.check_continuity()

    assert all(f["metric"] != "difficulty_daily" for f in findings)


def test_continuity_allowlisted_gap_produces_no_finding(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    _write_known_gaps(
        tmp_path,
        [{"metric": "supply_daily", "gap_start": "2009-01-03", "gap_end": "2009-01-09", "reason": "test fixture"}],
    )
    _write_series(tmp_path, "supply_daily", [("2009-01-03", 1), ("2009-01-09", 2), ("2009-01-10", 3)])
    for metric in ("price_daily", "hashrate_daily", "fng_daily"):
        _write_series(tmp_path, metric, [("2026-07-01", 1), ("2026-07-02", 2)])

    assert audit.check_continuity() == []


def test_continuity_unlisted_gap_still_warns_even_with_known_gaps_present(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    _write_known_gaps(
        tmp_path,
        [{"metric": "supply_daily", "gap_start": "2009-01-03", "gap_end": "2009-01-09", "reason": "test fixture"}],
    )
    # A different, unexplained gap on the SAME metric must still be caught --
    # the allowlist matches on exact metric+date-range, not "any gap on a
    # metric that has ever had a known gap".
    _write_series(tmp_path, "supply_daily", [("2030-01-01", 1), ("2030-01-05", 2)])
    for metric in ("price_daily", "hashrate_daily", "fng_daily"):
        _write_series(tmp_path, metric, [("2026-07-01", 1), ("2026-07-02", 2)])

    findings = audit.check_continuity()

    assert len(findings) == 1
    assert findings[0]["metric"] == "supply_daily"
    assert "2030-01-01" in findings[0]["detail"]


# --------------------------------------------------------------------------
# Cross-source variance
# --------------------------------------------------------------------------


def test_cross_source_variance_surfaces_health_flag(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    health = {"metrics": {"price_daily": {"cross_source_variance_warn": True}}}
    (tmp_path / "health.json").write_text(json.dumps(health))

    findings = audit.check_cross_source_variance()

    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert findings[0]["metric"] == "price_daily"


def test_cross_source_variance_clean_when_flag_false(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    health = {"metrics": {"price_daily": {"cross_source_variance_warn": False}}}
    (tmp_path / "health.json").write_text(json.dumps(health))

    assert audit.check_cross_source_variance() == []


# --------------------------------------------------------------------------
# Model drift
# --------------------------------------------------------------------------


def _models_doc(b_now, b_prev, r2_now, r2_prev):
    return {
        "power_law": {
            "params": {"b": b_now, "a": -16.0, "r_squared": r2_now, "sigma": 0.3},
            "previous_params": {"b": b_prev, "a": -16.0, "r_squared": r2_prev} if b_prev is not None else None,
        }
    }


def test_model_drift_flags_large_b_change(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "models.json").write_text(json.dumps(_models_doc(5.9, 5.7, 0.96, 0.96)))

    findings = audit.check_model_drift()

    assert len(findings) == 1
    assert "b moved" in findings[0]["detail"]


def test_model_drift_flags_r_squared_drop(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "models.json").write_text(json.dumps(_models_doc(5.7, 5.7, 0.90, 0.96)))

    findings = audit.check_model_drift()

    assert any("R² dropped" in f["detail"] for f in findings)


def test_model_drift_no_previous_params_is_clean(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "models.json").write_text(json.dumps(_models_doc(5.7, None, 0.96, None)))

    assert audit.check_model_drift() == []


def test_model_drift_small_change_is_clean(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "models.json").write_text(json.dumps(_models_doc(5.700, 5.701, 0.960, 0.961)))

    assert audit.check_model_drift() == []


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


def test_staleness_warn_at_24h_fail_at_72h(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    health = {
        "metrics": {
            "price_daily": {"status": "STALE", "stale_since": (now - timedelta(hours=30)).strftime("%Y-%m-%d")},
            "hashrate_daily": {"status": "STALE", "stale_since": (now - timedelta(hours=80)).strftime("%Y-%m-%d")},
            "supply_daily": {"status": "OK", "stale_since": None},
        }
    }
    (tmp_path / "health.json").write_text(json.dumps(health))

    findings = audit.check_staleness(now=now)

    by_metric = {f["metric"]: f for f in findings}
    assert by_metric["price_daily"]["severity"] == "WARN"
    assert by_metric["hashrate_daily"]["severity"] == "FAIL"
    assert "supply_daily" not in by_metric


# --------------------------------------------------------------------------
# Sanity replay
# --------------------------------------------------------------------------


def test_sanity_replay_detects_violation_in_last_30_days(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    # price_usd backfill_absolute min is 0.01 -- a negative-adjacent tiny/invalid
    # value like 0 violates it. Use plenty of clean history plus one bad row.
    series = [(f"2026-06-{d:02d}", 100.0) for d in range(1, 10)] + [("2026-06-10", 0.0)]
    _write_series(tmp_path, "price_daily", series)

    findings = audit.check_sanity_replay()

    assert any(f["metric"] == "price_daily" and f["severity"] == "FAIL" for f in findings)


def test_sanity_replay_clean_history_passes(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    for metric in audit.ALL_HISTORY_METRICS:
        _write_series(tmp_path, metric, [(f"2026-06-{d:02d}", 100.0) for d in range(1, 10)])

    findings = audit.check_sanity_replay()

    assert findings == []


# --------------------------------------------------------------------------
# Site integrity
# --------------------------------------------------------------------------


def test_site_integrity_detects_missing_index_html(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)

    findings = audit.check_site_integrity()

    assert len(findings) == 1
    assert findings[0]["severity"] == "FAIL"


def test_site_integrity_detects_broken_asset_link(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "index.html").write_text('<html><script src="assets/missing.js"></script></html>')

    findings = audit.check_site_integrity()

    assert any("assets/missing.js" in f["detail"] for f in findings)


def test_site_integrity_flags_payload_over_budget(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "index.html").write_text("<html></html>")
    big = "x" * (audit.JSON_PAYLOAD_BUDGET_BYTES + 1000)
    (tmp_path / "history" / "price_daily.json").write_text(json.dumps({"padding": big}))

    findings = audit.check_site_integrity()

    assert any(f["check"] == "site_integrity" and "MB" in f["detail"] for f in findings)


# --------------------------------------------------------------------------
# Orchestration / acceptance criterion: injected bad datum -> FAIL + issue
# --------------------------------------------------------------------------


def test_result_aggregation():
    assert audit._result_from_findings([]) == "PASS"
    assert audit._result_from_findings([{"severity": "WARN"}]) == "WARN"
    assert audit._result_from_findings([{"severity": "WARN"}, {"severity": "FAIL"}]) == "FAIL"


def test_run_audit_writes_latest_and_dated_copy(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "index.html").write_text("<html></html>")
    for metric in audit.ALL_HISTORY_METRICS:
        _write_series(tmp_path, metric, [("2026-07-01", 1), ("2026-07-02", 2)])
    (tmp_path / "models.json").write_text(json.dumps(_models_doc(5.7, None, 0.96, None)))
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)

    document = audit.run_audit(now=now)

    assert (tmp_path / "audit" / "latest.json").exists()
    assert (tmp_path / "audit" / "2026-07-09.json").exists()
    assert document["result"] == "PASS"


def test_run_audit_prunes_dated_copies_older_than_90_days(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "index.html").write_text("<html></html>")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    old_file = audit_dir / "2026-01-01.json"
    old_file.write_text("{}")
    recent_file = audit_dir / "2026-06-20.json"
    recent_file.write_text("{}")

    audit.run_audit(now=datetime(2026, 7, 9, tzinfo=timezone.utc))

    assert not old_file.exists()
    assert recent_file.exists()


def test_run_audit_appends_warn_findings_to_backlog(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    (tmp_path / "index.html").write_text("<html></html>")
    _write_series(tmp_path, "price_daily", [("2026-07-01", 1), ("2026-07-03", 2)])  # gap -> WARN

    audit.run_audit(now=datetime(2026, 7, 9, tzinfo=timezone.utc))

    backlog = (tmp_path / "IMPROVEMENT_BACKLOG.md").read_text()
    assert "audit-auto" in backlog
    assert "continuity" in backlog


@responses.activate
def test_run_audit_injected_bad_datum_causes_fail_and_opens_issue(tmp_path, monkeypatch):
    """Acceptance criterion (spec Section 13, P5): injected bad datum -> audit FAIL + issue opened."""
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    (tmp_path / "index.html").write_text("<html></html>")

    # Inject a bad datum: a negative/invalid price violates backfill_absolute's min.
    series = [(f"2026-06-{d:02d}", 100.0) for d in range(1, 10)] + [("2026-06-10", -5.0)]
    _write_series(tmp_path, "price_daily", series)

    repo = audit.gh_issues.repo_slug()
    issues_url = f"https://api.github.com/repos/{repo}/issues"
    responses.add(responses.GET, issues_url, json=[], status=200)
    responses.add(responses.POST, issues_url, json={"number": 99, "state": "open"}, status=201)

    document = audit.run_audit(now=datetime(2026, 7, 9, tzinfo=timezone.utc))

    assert document["result"] == "FAIL"
    post_call = [c for c in responses.calls if c.request.method == "POST"]
    assert len(post_call) == 1
    assert "audit-fail" in post_call[0].request.body.decode()
