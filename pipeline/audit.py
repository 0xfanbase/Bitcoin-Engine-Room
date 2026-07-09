"""Self-audit (P5, spec Section 11). Runs after each snapshot (or standalone)
and checks: continuity, cross-source variance, model drift, staleness,
sanity replay of the last 30 days, and site integrity. Writes
data/audit/latest.json (+ a dated copy, pruning anything older than 90
days), appends WARN/FAIL findings to IMPROVEMENT_BACKLOG.md, and
opens/updates/closes a GitHub issue on FAIL/recovery.

Run as a module from the repo root: `python -m pipeline.audit`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pipeline import gh_issues
from pipeline.validation import check_ascending_no_duplicate_dates, check_backfill_sanity

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "data" / "history"
HEALTH_PATH = REPO_ROOT / "data" / "health.json"
MODELS_PATH = REPO_ROOT / "data" / "models.json"
SANITY_RULES_PATH = REPO_ROOT / "pipeline" / "sanity_rules.json"
KNOWN_GAPS_PATH = REPO_ROOT / "pipeline" / "known_gaps.json"
AUDIT_DIR = REPO_ROOT / "data" / "audit"
BACKLOG_PATH = REPO_ROOT / "IMPROVEMENT_BACKLOG.md"
INDEX_HTML_PATH = REPO_ROOT / "index.html"
ASSETS_DIR = REPO_ROOT / "assets"

# Dense-daily-since-start metrics: gaps are real findings. difficulty_daily
# is deliberately excluded -- its P1 backfill portion is a sparse step
# function by design (see its schema's description), so a literal
# no-missing-dates check would flag thousands of "gaps" that aren't gaps.
CONTINUITY_CHECKED_METRICS = ["price_daily", "hashrate_daily", "supply_daily", "fng_daily"]
ALL_HISTORY_METRICS = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily", "fng_daily"]

STALENESS_WARN_HOURS = 24
STALENESS_FAIL_HOURS = 72
DRIFT_B_PCT_WARN = 0.005  # 0.5% day-over-day, per spec Section 11.3
DRIFT_R_SQUARED_DROP_WARN = 0.01
JSON_PAYLOAD_BUDGET_BYTES = 2 * 1024 * 1024
AUDIT_RETENTION_DAYS = 90


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _finding(check: str, severity: str, detail: str, metric: str | None = None) -> dict:
    return {"check": check, "severity": severity, "metric": metric, "detail": detail}


# --------------------------------------------------------------------------
# 1. Continuity
# --------------------------------------------------------------------------


def _load_known_gaps() -> set[tuple[str, str, str]]:
    # known_gaps.json is a small, hand-curated allowlist of gaps that have
    # already been investigated and confirmed genuine (e.g. real historical
    # events, not a pipeline or data-source defect) -- see its own "reason"
    # field for each entry's citation. Excluding them from continuity's
    # findings isn't weakening the check: the underlying gap is still fully
    # visible in the committed history file itself, this just stops a fact
    # that can never change (it's immutable past history) from re-triggering
    # the same WARN every single day forever, which trains readers to stop
    # taking the audit seriously.
    doc = load_json(KNOWN_GAPS_PATH) or {"gaps": []}
    return {(g["metric"], g["gap_start"], g["gap_end"]) for g in doc.get("gaps", [])}


def check_continuity() -> list[dict]:
    known_gaps = _load_known_gaps()
    findings = []
    for metric in CONTINUITY_CHECKED_METRICS:
        doc = load_json(HISTORY_DIR / f"{metric}.json")
        if not doc:
            findings.append(_finding("continuity", "FAIL", "history file missing", metric))
            continue
        series = doc["series"]
        gap_count = 0
        first_gap = None
        for i in range(1, len(series)):
            prev = date.fromisoformat(series[i - 1]["date"])
            curr = date.fromisoformat(series[i]["date"])
            if curr != prev + timedelta(days=1):
                if (metric, prev.isoformat(), curr.isoformat()) in known_gaps:
                    continue
                gap_count += 1
                if first_gap is None:
                    first_gap = f"{prev.isoformat()} -> {curr.isoformat()}"
        if gap_count:
            findings.append(
                _finding("continuity", "WARN", f"{gap_count} gap(s) in daily series, first at {first_gap}", metric)
            )
    return findings


# --------------------------------------------------------------------------
# 2. Cross-source variance
# --------------------------------------------------------------------------


def check_cross_source_variance() -> list[dict]:
    health = load_json(HEALTH_PATH)
    if not health:
        return []
    findings = []
    price_health = health.get("metrics", {}).get("price_daily", {})
    if price_health.get("cross_source_variance_warn"):
        findings.append(
            _finding(
                "cross_source_variance",
                "WARN",
                "mempool.space and CoinGecko price disagree beyond threshold (see health.json)",
                "price_daily",
            )
        )
    # hashrate: no cross-source check is currently recorded anywhere to audit
    # against -- each committed row has exactly one source, not parallel
    # readings from multiple sources for the same day. Logged as a known
    # limitation in IMPROVEMENT_BACKLOG.md rather than silently skipped.
    return findings


# --------------------------------------------------------------------------
# 3. Model drift
# --------------------------------------------------------------------------


def check_model_drift() -> list[dict]:
    models = load_json(MODELS_PATH)
    if not models:
        return [_finding("model_drift", "WARN", "models.json missing -- run fit_models.py")]

    pl = models["power_law"]
    prev = pl.get("previous_params")
    if not prev:
        return []  # first run, nothing to diff against

    findings = []
    b_now, b_prev = pl["params"]["b"], prev["b"]
    if b_prev:
        b_pct_change = abs(b_now - b_prev) / abs(b_prev)
        if b_pct_change > DRIFT_B_PCT_WARN:
            findings.append(
                _finding(
                    "model_drift",
                    "WARN",
                    f"power-law b moved {b_pct_change:.2%} day-over-day ({b_prev} -> {b_now}) -- usually bad input data, not a broken market",
                )
            )

    r2_drop = prev["r_squared"] - pl["params"]["r_squared"]
    if r2_drop > DRIFT_R_SQUARED_DROP_WARN:
        findings.append(
            _finding(
                "model_drift",
                "WARN",
                f"power-law R² dropped {r2_drop:.4f} day-over-day ({prev['r_squared']} -> {pl['params']['r_squared']})",
            )
        )

    return findings


# --------------------------------------------------------------------------
# 4. Staleness
# --------------------------------------------------------------------------


def check_staleness(*, now: datetime | None = None) -> list[dict]:
    health = load_json(HEALTH_PATH)
    if not health:
        return []
    now = now or datetime.now(timezone.utc)

    findings = []
    for metric, record in health.get("metrics", {}).items():
        if record.get("status") != "STALE" or not record.get("stale_since"):
            continue
        stale_since = datetime.fromisoformat(record["stale_since"]).replace(tzinfo=timezone.utc)
        hours_stale = (now - stale_since).total_seconds() / 3600
        if hours_stale >= STALENESS_FAIL_HOURS:
            findings.append(_finding("staleness", "FAIL", f"STALE for {hours_stale:.0f}h (since {record['stale_since']})", metric))
        elif hours_stale >= STALENESS_WARN_HOURS:
            findings.append(_finding("staleness", "WARN", f"STALE for {hours_stale:.0f}h (since {record['stale_since']})", metric))
    return findings


# --------------------------------------------------------------------------
# 5. Sanity replay (last 30 days)
# --------------------------------------------------------------------------


def check_sanity_replay() -> list[dict]:
    sanity_rules = load_json(SANITY_RULES_PATH)
    if not sanity_rules:
        return [_finding("sanity_replay", "FAIL", "sanity_rules.json missing")]

    findings = []
    for metric in ALL_HISTORY_METRICS:
        doc = load_json(HISTORY_DIR / f"{metric}.json")
        if not doc:
            continue
        recent = doc["series"][-30:]
        violations = check_backfill_sanity(metric, recent, sanity_rules["backfill_absolute"])
        dupe_violations = check_ascending_no_duplicate_dates(recent)
        if violations:
            findings.append(
                _finding(
                    "sanity_replay",
                    "FAIL",
                    f"{len(violations)} sanity violation(s) in the last 30 days, e.g. {violations[0]}",
                    metric,
                )
            )
        if dupe_violations:
            findings.append(_finding("sanity_replay", "FAIL", f"date ordering violation(s): {dupe_violations[0]}", metric))
    return findings


# --------------------------------------------------------------------------
# 6. Site integrity
# --------------------------------------------------------------------------


def check_site_integrity() -> list[dict]:
    findings = []

    if not INDEX_HTML_PATH.exists():
        findings.append(_finding("site_integrity", "FAIL", "index.html missing"))
        return findings

    html = INDEX_HTML_PATH.read_text()
    if "<html" not in html.lower() or "</html>" not in html.lower():
        findings.append(_finding("site_integrity", "FAIL", "index.html does not look like a complete HTML document"))

    import re

    local_links = re.findall(r'(?:href|src)="(assets/[^"]+|data/[^"]+)"', html)
    for link in local_links:
        if not (REPO_ROOT / link).exists():
            findings.append(_finding("site_integrity", "FAIL", f"local asset link does not resolve: {link}"))

    total_bytes = 0
    if HISTORY_DIR.exists():
        total_bytes += sum(f.stat().st_size for f in HISTORY_DIR.glob("*.json"))
    if MODELS_PATH.exists():
        total_bytes += MODELS_PATH.stat().st_size
    if HEALTH_PATH.exists():
        total_bytes += HEALTH_PATH.stat().st_size
    if total_bytes > JSON_PAYLOAD_BUDGET_BYTES:
        findings.append(
            _finding(
                "site_integrity",
                "WARN",
                f"committed JSON payload is {total_bytes / 1024 / 1024:.2f} MB, over the {JSON_PAYLOAD_BUDGET_BYTES / 1024 / 1024:.0f} MB budget (spec Section 11.6) -- see IMPROVEMENT_BACKLOG.md's P4 entry",
            )
        )

    return findings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _result_from_findings(findings: list[dict]) -> str:
    if any(f["severity"] == "FAIL" for f in findings):
        return "FAIL"
    if any(f["severity"] == "WARN" for f in findings):
        return "WARN"
    return "PASS"


def run_audit(*, now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    findings = []
    findings.extend(check_continuity())
    findings.extend(check_cross_source_variance())
    findings.extend(check_model_drift())
    findings.extend(check_staleness(now=now))
    findings.extend(check_sanity_replay())
    findings.extend(check_site_integrity())

    document = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": _result_from_findings(findings),
        "findings": findings,
    }

    if not dry_run:
        write_json(AUDIT_DIR / "latest.json", document)
        write_json(AUDIT_DIR / f"{now.strftime('%Y-%m-%d')}.json", document)
        _prune_old_audits(now)
        _append_findings_to_backlog(document)
        _handle_github_issue(document)

    return document


def _prune_old_audits(now: datetime) -> None:
    if not AUDIT_DIR.exists():
        return
    cutoff = now.date() - timedelta(days=AUDIT_RETENTION_DAYS)
    for f in AUDIT_DIR.glob("*.json"):
        if f.stem in ("latest",):
            continue
        try:
            file_date = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            f.unlink()


def _append_findings_to_backlog(document: dict) -> None:
    actionable = [f for f in document["findings"] if f["severity"] in ("WARN", "FAIL")]
    if not actionable:
        return

    today = document["generated_at"][:10]
    entries = []
    for f in actionable:
        metric_part = f" ({f['metric']})" if f.get("metric") else ""
        entries.append(
            f"\n### [audit] {f['check']}{metric_part} -- {f['severity']} ({today})\n"
            f"Source: audit-auto\n"
            f"Description: {f['detail']}\n"
            f"Suggested fix: (fill in during the next /improve pass)\n"
        )

    with open(BACKLOG_PATH, "a") as fh:
        fh.write("".join(entries))


def _handle_github_issue(document: dict) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return

    fail_findings = [f for f in document["findings"] if f["severity"] == "FAIL"]
    try:
        if fail_findings:
            gh_issues.open_or_update_audit_issue(token, findings=fail_findings)
        elif document["result"] != "FAIL":
            gh_issues.close_audit_issue_if_open(token)
    except Exception as exc:
        print(f"WARNING: audit GitHub issue automation failed: {exc}", file=sys.stderr)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    document = run_audit(dry_run=args.dry_run)
    print(f"audit result: {document['result']}")
    for f in document["findings"]:
        print(f"  [{f['severity']}] {f['check']}" + (f" ({f['metric']})" if f.get("metric") else "") + f": {f['detail']}")


if __name__ == "__main__":
    main()
