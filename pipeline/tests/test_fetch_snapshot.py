import json
from datetime import datetime, timezone

import jsonschema
import responses

from pipeline import fetch_snapshot


def _seed_history(tmp_path, metric, unit, series):
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "metric": metric,
        "unit": unit,
        "schema_version": 1,
        "generated_at": "2026-07-08T06:30:00Z",
        "series": series,
    }
    with open(history_dir / f"{metric}.json", "w") as f:
        json.dump(doc, f)


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_snapshot, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(fetch_snapshot, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # Source clients use request_with_retry's default sleep_fn=time.sleep;
    # tests that exhaust retries would otherwise really sleep several seconds.
    monkeypatch.setattr("pipeline.sources.time.sleep", lambda seconds: None)


def _load_history(tmp_path, metric):
    with open(tmp_path / "history" / f"{metric}.json") as f:
        return json.load(f)


@responses.activate
def test_price_snapshot_appends_new_day_on_success(tmp_path, monkeypatch):
    _seed_history(tmp_path, "price_daily", "USD", [{"date": "2026-07-08", "value": 61000.0, "source": "mempool_space"}])
    _patch_paths(monkeypatch, tmp_path)

    # Called twice: once by the failover chain, once by the cross-source check.
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", json={"USD": 62000}, status=200)
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", json={"USD": 62000}, status=200)
    responses.add(
        responses.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json={"bitcoin": {"usd": 62050}},
        status=200,
    )

    health = fetch_snapshot.run_snapshot(["price_daily"])

    assert health["metrics"]["price_daily"]["status"] == "OK"
    assert health["metrics"]["price_daily"]["source"] == "mempool_space"
    assert health["metrics"]["price_daily"]["cross_source_variance_warn"] is False

    doc = _load_history(tmp_path, "price_daily")
    assert len(doc["series"]) == 2
    assert doc["series"][-1] == {"date": fetch_snapshot._today(), "value": 62000.0, "source": "mempool_space"}


@responses.activate
def test_already_recorded_today_is_a_no_op(tmp_path, monkeypatch):
    today = fetch_snapshot._today()
    _seed_history(tmp_path, "fng_daily", "index_0_100", [{"date": today, "value": 50, "classification": "Neutral", "source": "alternative_me"}])
    _patch_paths(monkeypatch, tmp_path)

    # No responses registered at all -- if the code tried to fetch, this would error.
    health = fetch_snapshot.run_snapshot(["fng_daily"])

    doc = _load_history(tmp_path, "fng_daily")
    assert len(doc["series"]) == 1  # unchanged
    # No prior health.json entry to reuse -- derived from the already-valid
    # committed row, so this reports OK (not a fabricated STALE).
    assert health["metrics"]["fng_daily"]["status"] == "OK"
    assert health["metrics"]["fng_daily"]["source"] == "alternative_me"
    assert health["metrics"]["fng_daily"]["last_success_date"] == today


@responses.activate
def test_price_falls_through_to_coingecko_when_mempool_fails(tmp_path, monkeypatch):
    _seed_history(tmp_path, "price_daily", "USD", [{"date": "2026-07-08", "value": 61000.0, "source": "coingecko"}])
    _patch_paths(monkeypatch, tmp_path)

    responses.add(responses.GET, "https://mempool.space/api/v1/prices", status=500)
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", status=500)
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", status=500)
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", status=500)
    responses.add(
        responses.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json={"bitcoin": {"usd": 61500}},
        status=200,
    )

    health = fetch_snapshot.run_snapshot(["price_daily"])

    assert health["metrics"]["price_daily"]["status"] == "OK"
    assert health["metrics"]["price_daily"]["source"] == "coingecko"
    doc = _load_history(tmp_path, "price_daily")
    assert doc["series"][-1]["source"] == "coingecko"


@responses.activate
def test_all_sources_failing_carries_forward_and_increments_failures(tmp_path, monkeypatch):
    _seed_history(tmp_path, "difficulty_daily", "raw_difficulty", [{"date": "2026-07-08", "value": 1.0e14, "source": "mempool_space"}])
    _patch_paths(monkeypatch, tmp_path)

    # health.json from a prior run shows 2 consecutive failures already.
    (tmp_path / "health.json").write_text(json.dumps({
        "schema_version": 1,
        "generated_at": "2026-07-08T06:30:00Z",
        "metrics": {
            "difficulty_daily": {
                "source": "mempool_space", "status": "STALE", "latency_ms": None,
                "consecutive_failures": 2, "last_success_date": "2026-07-06", "stale_since": "2026-07-07",
            }
        },
    }))

    for _ in range(4):
        responses.add(responses.GET, "https://mempool.space/api/v1/difficulty-adjustment", status=500)
    for _ in range(4):
        responses.add(responses.GET, "https://blockchain.info/q/getdifficulty", status=500)

    health = fetch_snapshot.run_snapshot(["difficulty_daily"])

    record = health["metrics"]["difficulty_daily"]
    assert record["status"] == "STALE"
    assert record["consecutive_failures"] == 3  # 2 -> 3, now issue-worthy
    assert record["stale_since"] == "2026-07-07"  # preserved from prior run, not reset

    doc = _load_history(tmp_path, "difficulty_daily")
    assert len(doc["series"]) == 2  # carried forward, not skipped
    assert doc["series"][-1]["value"] == 1.0e14  # same value as last known good
    assert doc["series"][-1]["date"] == fetch_snapshot._today()


@responses.activate
def test_supply_falls_through_to_computed_subsidy_schedule(tmp_path, monkeypatch):
    _seed_history(tmp_path, "supply_daily", "BTC", [{"date": "2026-07-08", "value": 20_053_800.0, "source": "blockchain_info"}])
    _patch_paths(monkeypatch, tmp_path)

    for _ in range(4):
        responses.add(responses.GET, "https://blockchain.info/q/totalbc", status=500)
    responses.add(responses.GET, "https://mempool.space/api/blocks/tip/height", body="957246", status=200)

    health = fetch_snapshot.run_snapshot(["supply_daily"])

    record = health["metrics"]["supply_daily"]
    assert record["status"] == "OK"
    assert record["source"] == "computed_subsidy_schedule"
    doc = _load_history(tmp_path, "supply_daily")
    assert doc["series"][-1]["source"] == "computed_subsidy_schedule"


@responses.activate
def test_tip_height_recorded_in_health_even_without_supply_daily(tmp_path, monkeypatch, load_schema):
    """tip_height should be persisted in health.json regardless of which
    metrics are being snapshotted this run -- not just as a side effect of
    supply_daily's own chain/sanity-check needing it -- so the frontend can
    always paint a real committed block height on boot (spec 'never blank
    gauges', extended to the masthead odometer).
    """
    _seed_history(tmp_path, "price_daily", "USD", [{"date": "2026-07-08", "value": 61000.0, "source": "mempool_space"}])
    _patch_paths(monkeypatch, tmp_path)

    responses.add(responses.GET, "https://mempool.space/api/v1/prices", json={"USD": 62000}, status=200)
    responses.add(responses.GET, "https://mempool.space/api/v1/prices", json={"USD": 62000}, status=200)
    responses.add(
        responses.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json={"bitcoin": {"usd": 62050}},
        status=200,
    )
    responses.add(responses.GET, "https://mempool.space/api/blocks/tip/height", body="912345", status=200)

    health = fetch_snapshot.run_snapshot(["price_daily"])

    assert health["tip_height"] == {"height": 912345, "date": fetch_snapshot._today()}
    jsonschema.validate(health, load_schema("health"))


@responses.activate
def test_tip_height_carries_forward_last_known_value_on_fetch_failure(tmp_path, monkeypatch, load_schema):
    _seed_history(tmp_path, "fng_daily", "index_0_100", [{"date": "2026-07-08", "value": 50, "classification": "Neutral", "source": "alternative_me"}])
    _patch_paths(monkeypatch, tmp_path)

    (tmp_path / "health.json").write_text(json.dumps({
        "schema_version": 1,
        "generated_at": "2026-07-08T06:30:00Z",
        "tip_height": {"height": 900000, "date": "2026-07-08"},
        "metrics": {},
    }))

    today_unix = int(datetime.now(timezone.utc).timestamp())
    responses.add(
        responses.GET,
        "https://api.alternative.me/fng/",
        json={"data": [{"timestamp": str(today_unix), "value": "55", "value_classification": "Greed"}]},
        status=200,
    )
    for _ in range(4):
        responses.add(responses.GET, "https://mempool.space/api/blocks/tip/height", status=500)

    health = fetch_snapshot.run_snapshot(["fng_daily"])

    # Today's fetch failed (4x 500s exhausts retries) -- last known height
    # carried forward under its own original date, not fabricated as today.
    assert health["tip_height"] == {"height": 900000, "date": "2026-07-08"}
    jsonschema.validate(health, load_schema("health"))
