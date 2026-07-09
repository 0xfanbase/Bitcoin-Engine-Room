import json

import responses

from pipeline.backfill import run_backfill
from pipeline.sources import AlternativeMeFngClient


@responses.activate
def test_backfill_fng_is_idempotent(tmp_path, load_fixture):
    payload = load_fixture("alternative_me_fng.json")
    responses.add(responses.GET, AlternativeMeFngClient.URL, json=payload, status=200)
    responses.add(responses.GET, AlternativeMeFngClient.URL, json=payload, status=200)

    run_backfill(["fng"], tmp_path, dry_run=False)
    with open(tmp_path / "fng_daily.json") as f:
        first = json.load(f)

    run_backfill(["fng"], tmp_path, dry_run=False)
    with open(tmp_path / "fng_daily.json") as f:
        second = json.load(f)

    assert first["series"] == second["series"]
    assert first["metric"] == second["metric"] == "fng_daily"
    assert first["unit"] == second["unit"] == "index_0_100"
