"""scenario_forecast: AR1 blend, growing bands, no fake shares, privacy scrub."""

from __future__ import annotations

import json
import math

import pytest

from pipeline import scenario_forecast as sf


def test_build_has_ar1_and_growing_widths():
    doc = sf.build()
    assert doc["schema_version"] >= 2
    assert doc["method"]["family"] == "power_law_ar1_scaled_quantiles"
    assert "ar1" in doc["method"]
    assert doc["method"]["ar1"]["phi"] is not None
    widths = [h["log_width"] for h in doc["horizons"] if h["log_width"]]
    # widths should generally grow with horizon (1m < 30y)
    by_id = {h["id"]: h for h in doc["horizons"]}
    assert by_id["30y"]["log_width"] > by_id["1m"]["log_width"]
    assert by_id["4y"]["log_width"] >= by_id["1y"]["log_width"] * 0.99
    # ledger labels
    assert by_id["1y"]["in_live_ledger"] is True
    assert by_id["2y"]["in_live_ledger"] is True
    assert by_id["4y"]["in_live_ledger"] is True
    assert by_id["1m"]["in_live_ledger"] is False
    assert by_id["10y"]["in_live_ledger"] is False
    assert by_id["30y"]["in_live_ledger"] is False
    # AR1 pulls residual toward 0: |r| at 1y < |r0| when phi in (0,1)
    r0 = doc["method"]["ar1"]["r0"]
    r1y = by_id["1y"]["residual_mean"]
    if r0 is not None and abs(r0) > 1e-6 and 0 < doc["method"]["ar1"]["phi"] < 1:
        assert abs(r1y) < abs(r0)


def test_ensemble_has_no_share_weights():
    doc = sf.build()
    for row in doc["ensemble"]:
        assert "share" not in row
        assert row["role"]
        assert row["trust_note"]


def test_privacy_scrub_forbids_holdings_token(tmp_path, monkeypatch):
    # build() already raises if forbidden tokens appear; assert clean dump
    doc = sf.build()
    blob = json.dumps(doc).lower()
    for needle in ("holdings", "wallet", "gmail", "icansquat", "btc_stack"):
        assert needle not in blob


def test_write_validates_schema(tmp_path, monkeypatch):
    out = tmp_path / "scenario_forecast.json"
    monkeypatch.setattr(sf, "OUT_PATH", out)
    doc = sf.build()
    sf.write(doc)
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["schema_version"] >= 2
    assert len(loaded["horizons"]) >= 5
