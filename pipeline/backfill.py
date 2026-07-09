"""One-time full-history backfill for BTC Engine Room.

Populates data/history/*.json from genesis (or as far back as each source
goes) using the corrected source priorities in CLAUDE.md Section 3 /
docs/PHASE1_DIRECTOR_CORRECTIONS.md: Coin Metrics primary for price/hashrate/
supply (blockchain.com Charts fallback, gap-filled per missing date, not a
metric-wide switch), blockchain.com Charts primary (sole source) for
difficulty, alternative.me for Fear & Greed.

Run as a module from the repo root: `python -m pipeline.backfill`.
Idempotent: re-running recomputes each series from source and produces the
same `series` content (only `generated_at` differs) since this is a full
historical reload, not an append -- distinct from P2's fetch_snapshot.py,
which will do a true append-only daily upsert.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from pipeline.sources import (
    AlternativeMeFngClient,
    BlockchainInfoChartsClient,
    CoinMetricsClient,
)
from pipeline.validation import check_ascending_no_duplicate_dates, check_backfill_sanity

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "history"
SCHEMAS_DIR = REPO_ROOT / "pipeline" / "schemas"
SANITY_RULES_PATH = REPO_ROOT / "pipeline" / "sanity_rules.json"

CM_START_TIME = "2010-01-01"

# name -> (output file metric name, unit, Coin Metrics metric name or None,
#          blockchain.info chart name or None)
METRIC_CONFIG = {
    "price": {"file": "price_daily", "unit": "USD", "cm_metric": "PriceUSD", "bi_chart": "market-price"},
    "hashrate": {"file": "hashrate_daily", "unit": "EH/s", "cm_metric": "HashRate", "bi_chart": "hash-rate"},
    "supply": {"file": "supply_daily", "unit": "BTC", "cm_metric": "SplyCur", "bi_chart": "total-bc"},
    "difficulty": {"file": "difficulty_daily", "unit": "raw_difficulty", "cm_metric": None, "bi_chart": "difficulty"},
    "fng": {"file": "fng_daily", "unit": "index_0_100", "cm_metric": None, "bi_chart": None},
}
ALL_METRICS = list(METRIC_CONFIG)
COIN_METRICS_BACKED = ("price", "hashrate", "supply")


def merge_series(primary: list[dict], fallback: list[dict]) -> list[dict]:
    """Fill only the specific dates missing from `primary` using `fallback`.

    Not a metric-wide switch: primary wins wherever it has a value; fallback
    fills exactly the gap dates, keeping its own source tag on those rows.
    """
    by_date = {row["date"]: row for row in primary}
    for row in fallback:
        by_date.setdefault(row["date"], row)
    return sorted(by_date.values(), key=lambda r: r["date"])


def build_series_for_metric(name: str, *, cm_rows_cache: dict) -> list[dict]:
    config = METRIC_CONFIG[name]

    if name == "fng":
        return AlternativeMeFngClient().fetch_full_history()

    if name == "difficulty":
        return BlockchainInfoChartsClient().fetch_chart(config["bi_chart"])

    # price / hashrate / supply share one paginated Coin Metrics call.
    if "rows" not in cm_rows_cache:
        cm_rows_cache["rows"] = CoinMetricsClient().fetch_asset_metrics(
            metrics=[METRIC_CONFIG[m]["cm_metric"] for m in COIN_METRICS_BACKED],
            start_time=CM_START_TIME,
        )
    primary = CoinMetricsClient.split_metric_series(cm_rows_cache["rows"], config["cm_metric"])
    fallback = BlockchainInfoChartsClient().fetch_chart(config["bi_chart"])
    return merge_series(primary, fallback)


def load_schema(file_metric: str) -> dict:
    with open(SCHEMAS_DIR / f"{file_metric}.schema.json") as f:
        return json.load(f)


def load_sanity_rules() -> dict:
    with open(SANITY_RULES_PATH) as f:
        return json.load(f)


def assemble_and_validate(name: str, series: list[dict], sanity_rules: dict) -> dict:
    config = METRIC_CONFIG[name]
    file_metric = config["file"]

    series = sorted(series, key=lambda r: r["date"])
    dupe_violations = check_ascending_no_duplicate_dates(series)
    if dupe_violations:
        raise ValueError(f"{file_metric}: duplicate/out-of-order dates: {dupe_violations[:5]}")

    document = {
        "metric": file_metric,
        "unit": config["unit"],
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": series,
    }

    jsonschema.validate(document, load_schema(file_metric))

    for violation in check_backfill_sanity(file_metric, series, sanity_rules["backfill_absolute"]):
        print(
            f"WARNING: {file_metric} sanity violation on {violation['date']}: "
            f"value={violation['value']} rule={violation['rule']} {violation['detail']}",
            file=sys.stderr,
        )

    return document


def summarize(file_metric: str, series: list[dict]) -> None:
    if not series:
        print(f"{file_metric}: no rows")
        return
    counts: dict[str, int] = {}
    for row in series:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    breakdown = ", ".join(f"{n} from {s}" for s, n in counts.items())
    print(f"{file_metric}: {series[0]['date']} -> {series[-1]['date']}, {len(series)} rows ({breakdown})")


def run_backfill(metrics: list[str], out_dir: Path, dry_run: bool) -> list[dict]:
    sanity_rules = load_sanity_rules()
    cm_rows_cache: dict = {}
    documents = []

    for name in metrics:
        series = build_series_for_metric(name, cm_rows_cache=cm_rows_cache)
        document = assemble_and_validate(name, series, sanity_rules)
        summarize(document["metric"], document["series"])
        documents.append(document)

        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{document['metric']}.json"
            with open(out_path, "w") as f:
                json.dump(document, f, indent=2)
                f.write("\n")

    return documents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metrics",
        default=",".join(ALL_METRICS),
        help="comma-separated subset of: " + ",".join(ALL_METRICS),
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate but do not write files")
    args = parser.parse_args()

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    unknown = set(metrics) - set(ALL_METRICS)
    if unknown:
        parser.error(f"unknown metrics: {sorted(unknown)}")

    run_backfill(metrics, Path(args.out_dir), args.dry_run)


if __name__ == "__main__":
    main()
