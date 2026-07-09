import json

import jsonschema
import pytest

from pipeline.validation import check_ascending_no_duplicate_dates

METRICS = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily", "fng_daily"]


def _load(data_history_dir, metric):
    path = data_history_dir / f"{metric}.json"
    assert path.exists(), f"{path} does not exist -- run `python -m pipeline.backfill` first"
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("metric", METRICS)
def test_history_file_validates_against_schema(metric, data_history_dir, load_schema):
    document = _load(data_history_dir, metric)
    jsonschema.validate(document, load_schema(metric))


@pytest.mark.parametrize("metric", METRICS)
def test_history_file_dates_ascending_no_duplicates(metric, data_history_dir):
    document = _load(data_history_dir, metric)
    assert check_ascending_no_duplicate_dates(document["series"]) == []


@pytest.mark.parametrize("metric", METRICS)
def test_history_file_has_required_metadata(metric, data_history_dir):
    document = _load(data_history_dir, metric)
    assert document["metric"] == metric
    assert document["schema_version"] == 1
    assert document["series"], "series should not be empty"
