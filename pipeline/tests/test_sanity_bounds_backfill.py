import json

from pipeline.validation import check_backfill_sanity

METRICS = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily"]


def test_committed_history_has_no_backfill_sanity_violations(data_history_dir, sanity_rules):
    for metric in METRICS:
        path = data_history_dir / f"{metric}.json"
        assert path.exists(), f"{path} does not exist -- run `python -m pipeline.backfill` first"
        with open(path) as f:
            document = json.load(f)

        violations = check_backfill_sanity(metric, document["series"], sanity_rules["backfill_absolute"])
        assert violations == [], f"{metric} has sanity violations: {violations[:5]}"
