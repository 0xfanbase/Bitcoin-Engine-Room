"""Daily snapshot pipeline (P2).

For each of the five history metrics, tries each source in its live-failover
chain (CLAUDE.md Section 3) in priority order: fetch -> schema-validate ->
sanity-check (sanity_rules.json's live_snapshot profile) -> on success,
append today's row and record health; on total failure, carry the last known
value forward under today's date and mark the metric STALE. Writes
data/health.json every run. Opens or updates a GitHub issue after 3+
consecutive failures on any metric; auto-closes it once everything recovers.

Idempotent: if today's date is already the last row in a metric's history,
that metric is skipped entirely for this run (spec Section 6: "idempotent:
skip if date exists"), so re-running the same day (e.g. via workflow_dispatch)
never duplicates or corrupts history.

Run as a module from the repo root: `python -m pipeline.fetch_snapshot`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from pipeline import gh_issues
from pipeline.sources import (
    AlternativeMeFngClient,
    BlockchainInfoChartsClient,
    BlockchainInfoSimpleClient,
    CoinbaseClient,
    CoinGeckoClient,
    MempoolSpaceClient,
)
from pipeline.subsidy import estimate_supply_at_height
from pipeline.validation import (
    check_cross_source_variance,
    check_difficulty_live,
    check_hashrate_live,
    check_price_live,
    check_supply_live,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "data" / "history"
SCHEMAS_DIR = REPO_ROOT / "pipeline" / "schemas"
SANITY_RULES_PATH = REPO_ROOT / "pipeline" / "sanity_rules.json"
HEALTH_PATH = REPO_ROOT / "data" / "health.json"

FAILURE_ISSUE_THRESHOLD = 3
ALL_METRICS = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily", "fng_daily"]
DEFAULT_UNIT = {
    "price_daily": "USD",
    "hashrate_daily": "EH/s",
    "difficulty_daily": "raw_difficulty",
    "supply_daily": "BTC",
    "fng_daily": "index_0_100",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_history(metric: str) -> dict | None:
    path = HISTORY_DIR / f"{metric}.json"
    return load_json(path) if path.exists() else None


def _tip_height(height_cache: dict) -> int | None:
    if "height" not in height_cache:
        try:
            height_cache["height"] = MempoolSpaceClient().fetch_tip_height()
        except Exception:
            height_cache["height"] = None
    return height_cache["height"]


def _price_chain() -> list[tuple[str, callable]]:
    mempool, coingecko, coinbase = MempoolSpaceClient(), CoinGeckoClient(), CoinbaseClient()
    return [
        ("mempool_space", mempool.fetch_price),
        ("coingecko", coingecko.fetch_price),
        ("coinbase", coinbase.fetch_price),
    ]


def _hashrate_chain() -> list[tuple[str, callable]]:
    mempool, bi_charts = MempoolSpaceClient(), BlockchainInfoChartsClient()

    def bi_latest():
        return bi_charts.fetch_chart("hash-rate")[-1]

    return [("mempool_space", mempool.fetch_hashrate), ("blockchain_info", bi_latest)]


def _difficulty_chain() -> list[tuple[str, callable]]:
    mempool, bi_simple = MempoolSpaceClient(), BlockchainInfoSimpleClient()
    return [("mempool_space", mempool.fetch_difficulty), ("blockchain_info", bi_simple.fetch_difficulty)]


def _supply_chain(height_cache: dict) -> list[tuple[str, callable]]:
    bi_simple = BlockchainInfoSimpleClient()

    def computed():
        height = _tip_height(height_cache)
        if height is None:
            raise RuntimeError("tip height unavailable -- cannot compute subsidy-schedule supply")
        return {"date": _today(), "value": estimate_supply_at_height(height), "source": "computed_subsidy_schedule"}

    return [("blockchain_info", bi_simple.fetch_total_supply), ("computed_subsidy_schedule", computed)]


def _fng_chain() -> list[tuple[str, callable]]:
    return [("alternative_me", AlternativeMeFngClient().fetch_latest)]


def _source_chain(metric: str, height_cache: dict) -> list[tuple[str, callable]]:
    return {
        "price_daily": _price_chain,
        "hashrate_daily": _hashrate_chain,
        "difficulty_daily": _difficulty_chain,
        "supply_daily": lambda: _supply_chain(height_cache),
        "fng_daily": _fng_chain,
    }[metric]()


def _validate_schema(metric: str, document: dict) -> None:
    schema = load_json(SCHEMAS_DIR / f"{metric}.schema.json")
    jsonschema.validate(document, schema)


def _sanity_check(metric: str, row: dict, prior_series: list[dict], live_rules: dict, height_cache: dict) -> list[str]:
    prev_value = prior_series[-1]["value"] if prior_series else None

    if metric == "price_daily":
        return check_price_live(row["value"], prev_value, live_rules["price_usd"])
    if metric == "hashrate_daily":
        return check_hashrate_live(row["value"], prior_series, live_rules["hash_rate_eh_s"])
    if metric == "difficulty_daily":
        return check_difficulty_live(row["value"], prev_value, live_rules["difficulty"])
    if metric == "supply_daily":
        height = _tip_height(height_cache)
        estimated = estimate_supply_at_height(height) if height is not None else None
        return check_supply_live(row["value"], prev_value, live_rules["supply_btc"], estimated_from_height=estimated)
    return []  # fng_daily: bounded by schema alone (0-100 int)


def _check_price_cross_source(row: dict, live_rules: dict) -> bool | None:
    """Best-effort mempool.space vs CoinGecko cross-check (spec Section 6).

    Reuses `row`'s own value for whichever of the two sources it actually
    came from, instead of re-fetching a source already hit (or already
    known-failed) moments earlier in the same run by the primary chain --
    saves up to 2 redundant live HTTP calls/day, including a full
    retry/backoff cycle against a source that just failed.

    Returns True if variance exceeds the threshold (i.e. a WARN should be
    raised), False if within tolerance, None if the check couldn't run.
    Never blocks recording the primary value either way.
    """
    try:
        mempool_value = (
            row["value"] if row["source"] == "mempool_space" else MempoolSpaceClient().fetch_price()["value"]
        )
        coingecko_value = (
            row["value"] if row["source"] == "coingecko" else CoinGeckoClient().fetch_price()["value"]
        )
    except Exception:
        return None
    within_tolerance = check_cross_source_variance(
        mempool_value, coingecko_value, live_rules["cross_source_variance"]["price_pct"]
    )
    return not within_tolerance


def _latest_fields(metric: str, row: dict) -> dict:
    """Extract the small last-known-value digest health.json carries per
    metric (last_date/last_value, plus last_classification for fng_daily) so
    the frontend can paint every gauge straight from health.json -- already
    the first thing app.js fetches on boot -- instead of separately
    downloading each metric's full, multi-hundred-KB history file just to
    read its final row (spec 'never blank gauges', same mechanics as
    `_record_tip_height` below, extended from block height to these five).
    """
    fields = {"last_date": row["date"], "last_value": row["value"]}
    if metric == "fng_daily":
        fields["last_classification"] = row["classification"]
    return fields


def _is_stale_or_duplicate_date(row: dict, prior_series: list[dict]) -> bool:
    """True if `row`'s date doesn't advance past the last committed date.

    Most chain entries stamp `_today()` themselves (always fresh), but a
    fallback that reads its date from upstream data -- blockchain.info's
    hash-rate chart (`bi_latest`, which can lag a day behind) or
    alternative.me's "latest" Fear & Greed reading -- can hand back a date
    that's already the last row in history, or older. Without this guard
    that candidate gets appended as a second, out-of-order/duplicate-dated
    row instead of being treated as a failed source, silently corrupting
    the committed series and re-corrupting it on every subsequent run while
    the primary stays down (since the idempotent same-day skip never
    matches a date that isn't actually today).
    """
    return bool(prior_series) and row["date"] <= prior_series[-1]["date"]


def snapshot_metric(
    metric: str, *, history: dict | None, live_rules: dict, prior_health: dict, height_cache: dict
) -> tuple[dict, dict | None]:
    """Try each source in `metric`'s chain. Returns (health_record, new_row_or_None)."""
    prior_series = history["series"] if history else []

    for source_name, fetch_fn in _source_chain(metric, height_cache):
        start = time.monotonic()
        try:
            row = fetch_fn()
            latency_ms = (time.monotonic() - start) * 1000

            if _is_stale_or_duplicate_date(row, prior_series):
                print(
                    f"WARNING: {metric} rejected {source_name} candidate: stale/duplicate date "
                    f"{row['date']} (last recorded {prior_series[-1]['date']})",
                    file=sys.stderr,
                )
                continue

            candidate_doc = {
                "metric": metric,
                "unit": history["unit"] if history else DEFAULT_UNIT[metric],
                "schema_version": 1,
                "generated_at": _now_iso(),
                "series": prior_series + [row],
            }
            _validate_schema(metric, candidate_doc)

            violations = _sanity_check(metric, row, prior_series, live_rules, height_cache)
            if violations:
                print(f"WARNING: {metric} rejected {source_name} candidate: {violations}", file=sys.stderr)
                continue

            record = {
                "source": source_name,
                "status": "OK",
                "latency_ms": round(latency_ms, 1),
                "consecutive_failures": 0,
                "last_success_date": row["date"],
                "stale_since": None,
            }
            return record, row
        except Exception as exc:
            print(f"WARNING: {metric} source {source_name} failed: {exc}", file=sys.stderr)
            continue

    # every source in the chain failed or was rejected -- carry forward
    consecutive_failures = prior_health.get("consecutive_failures", 0) + 1
    stale_since = prior_health.get("stale_since") or _today()
    record = {
        "source": prior_health.get("source"),
        "status": "STALE",
        "latency_ms": None,
        "consecutive_failures": consecutive_failures,
        "last_success_date": prior_health.get("last_success_date"),
        "stale_since": stale_since,
    }

    carried_row = None
    if prior_series:
        last = prior_series[-1]
        if last["date"] != _today():
            # `source` stays whatever it was on `last` -- it names where the
            # underlying VALUE originally came from, not today's (failed)
            # fetch -- so `carried_forward` is the only signal in the history
            # file itself that this row is a repeat, not a fresh observation
            # (health.json's STALE status is a same-day flag that clears on
            # recovery; this stays permanently attached to the row).
            carried_row = {**last, "date": _today(), "carried_forward": True}

    return record, carried_row


def run_snapshot(metrics: list[str], *, dry_run: bool = False) -> dict:
    sanity_rules = load_json(SANITY_RULES_PATH)
    live_rules = sanity_rules["live_snapshot"]

    prior_health_doc = load_json(HEALTH_PATH) if HEALTH_PATH.exists() else {"metrics": {}}
    prior_health = prior_health_doc.get("metrics", {})
    height_cache: dict = {}

    # Seed from prior_health so a `--metrics` subset run (e.g. workflow_dispatch
    # with a custom --metrics flag) only ever updates the metrics it actually
    # processed -- previously this replaced the whole metrics dict, wiping
    # every other metric's health record and, via _handle_github_issue's
    # "all OK" check below, wrongly auto-closing an open outage issue for a
    # metric this run never even looked at.
    new_health = {"schema_version": 1, "generated_at": _now_iso(), "metrics": dict(prior_health)}
    issue_worthy = []

    for metric in metrics:
        history = load_history(metric)

        if history and history["series"] and history["series"][-1]["date"] == _today():
            # Idempotent: already recorded today, nothing to do this run.
            # Reuse prior health.json's record if we have one; otherwise derive
            # a proper OK record from the already-valid last row rather than
            # fabricating a misleading STALE placeholder.
            record = dict(prior_health.get(metric) or _health_record_from_existing(history))
            record.update(_latest_fields(metric, history["series"][-1]))
            new_health["metrics"][metric] = record
            continue

        record, row = snapshot_metric(
            metric, history=history, live_rules=live_rules, prior_health=prior_health.get(metric, {}), height_cache=height_cache
        )

        if metric == "price_daily" and record["status"] == "OK":
            warn = _check_price_cross_source(row, live_rules)
            if warn is not None:
                record["cross_source_variance_warn"] = warn

        if row is not None:
            record.update(_latest_fields(metric, row))

        new_health["metrics"][metric] = record

        if row is not None and not dry_run:
            document = {
                "metric": metric,
                "unit": history["unit"] if history else DEFAULT_UNIT[metric],
                "schema_version": 1,
                "generated_at": _now_iso(),
                "series": (history["series"] if history else []) + [row],
            }
            write_json(HISTORY_DIR / f"{metric}.json", document)

        if record["status"] == "STALE" and record["consecutive_failures"] >= FAILURE_ISSUE_THRESHOLD:
            issue_worthy.append(
                {
                    "metric": metric,
                    "consecutive_failures": record["consecutive_failures"],
                    "last_error": "all sources in the failover chain failed or were rejected by sanity checks",
                }
            )

    _record_tip_height(new_health, prior_health_doc, height_cache)

    if not dry_run:
        write_json(HEALTH_PATH, new_health)
        _handle_github_issue(issue_worthy, new_health)

    return new_health


def _record_tip_height(new_health: dict, prior_health_doc: dict, height_cache: dict) -> None:
    """Persist the chain tip height (already fetched via `_tip_height` as an
    input to the supply subsidy-schedule fallback/sanity check above) into
    health.json under a top-level `tip_height: {height, date}` key, so the
    frontend's masthead odometer and halving-countdown stats can paint a
    real committed value on boot instead of blank dashes/"--" when the
    browser can't reach mempool.space directly (extends the "never blank
    gauges" rule -- spec Section 10.3 -- from the 5 history metrics to the
    live-only block-height display).

    Reuses `height_cache` so this never triggers a second network fetch when
    `supply_daily`'s chain already populated it this run. Carries the last
    committed value forward under its own original date (not today's) if
    today's fetch fails and a prior value exists -- never fabricates a fresh
    reading, mirroring the per-metric carry-forward pattern above.
    """
    height = _tip_height(height_cache)
    if height is not None:
        new_health["tip_height"] = {"height": height, "date": _today()}
    elif "tip_height" in prior_health_doc:
        new_health["tip_height"] = prior_health_doc["tip_height"]


def _blank_health_record() -> dict:
    return {
        "source": None,
        "status": "STALE",
        "latency_ms": None,
        "consecutive_failures": 0,
        "last_success_date": None,
        "stale_since": None,
    }


def _health_record_from_existing(history: dict) -> dict:
    """Derive a health record from an already-valid committed row.

    Used on the idempotent-skip path when there's no prior health.json entry
    to reuse (e.g. its very first run after a metric's data already covered
    today, such as fng_daily's backfill reaching alternative.me's latest
    day) -- reports OK with the row's real source, not a fabricated STALE.
    """
    if not history or not history["series"]:
        return _blank_health_record()
    last_row = history["series"][-1]
    return {
        "source": last_row["source"],
        "status": "OK",
        "latency_ms": None,
        "consecutive_failures": 0,
        "last_success_date": last_row["date"],
        "stale_since": None,
    }


def _handle_github_issue(issue_worthy: list[dict], health: dict) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return  # no token (e.g. local run) -- skip issue automation entirely, don't fail the snapshot

    try:
        if issue_worthy:
            gh_issues.open_or_update_outage_issue(token, failing_metrics=issue_worthy)
        elif all(entry.get("status") == "OK" for entry in health["metrics"].values()):
            gh_issues.close_outage_issue_if_open(token)
    except Exception as exc:
        print(f"WARNING: GitHub issue automation failed: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", default=",".join(ALL_METRICS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    unknown = set(metrics) - set(ALL_METRICS)
    if unknown:
        parser.error(f"unknown metrics: {sorted(unknown)}")

    health = run_snapshot(metrics, dry_run=args.dry_run)
    for metric, record in health["metrics"].items():
        print(f"{metric}: {record['status']} via {record['source']} (consecutive_failures={record['consecutive_failures']})")


if __name__ == "__main__":
    main()
