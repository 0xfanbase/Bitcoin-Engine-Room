"""Public scenario forecast builder — privacy + shape guards."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.scenario_forecast import build

FORBIDDEN = (
    "holdings",
    "wallet",
    "balance",
    "email",
    "gmail",
    "icansquat",
    "burnston",
    "10.03",
    "drive folder",
)


def test_build_shape_and_privacy():
    doc = build()
    assert doc["schema_version"] == 1
    assert len(doc["horizons"]) == 5
    assert [h["id"] for h in doc["horizons"]] == ["1m", "1y", "4y", "10y", "30y"]
    for h in doc["horizons"]:
        assert h["p10"] <= h["p50"] <= h["p90"]
        # Anchored: 1m median should be near spot, not raw Cruise alone
        if h["id"] == "1m":
            assert abs(h["p50"] / doc["spot"] - 1.0) < 0.15
    assert len(doc["fan"]) >= 10
    assert doc["ensemble"]
    blob = json.dumps(doc).lower()
    for needle in FORBIDDEN:
        assert needle not in blob, needle
    assert "holdings" not in doc


def test_committed_file_scrubbed():
    path = Path("data/scenario_forecast.json")
    if not path.exists():
        return
    blob = path.read_text().lower()
    for needle in FORBIDDEN:
        assert needle not in blob, needle
