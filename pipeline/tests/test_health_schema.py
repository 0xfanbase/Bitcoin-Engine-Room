import json

import jsonschema

ALL_METRICS = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily", "fng_daily"]


def test_committed_health_json_validates_against_schema(repo_root, load_schema):
    path = repo_root / "data" / "health.json"
    assert path.exists(), f"{path} does not exist -- run `python -m pipeline.fetch_snapshot` first"
    with open(path) as f:
        health = json.load(f)
    jsonschema.validate(health, load_schema("health"))


def test_committed_health_json_covers_every_metric(repo_root):
    with open(repo_root / "data" / "health.json") as f:
        health = json.load(f)
    for metric in ALL_METRICS:
        assert metric in health["metrics"], metric
