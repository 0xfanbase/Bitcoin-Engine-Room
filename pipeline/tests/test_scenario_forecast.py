"""scenario_forecast v3: AR1 + conformal ≤4y + Idle/Redline ultra-long."""

from __future__ import annotations

import json
import math

from pipeline import scenario_forecast as sf


def test_build_conformal_family_and_ar1():
    doc = sf.build()
    assert doc["schema_version"] >= 3
    assert doc["method"]["family"] == "power_law_ar1_conformal"
    assert "ar1" in doc["method"]
    assert "conformal" in doc["method"]["width"]
    by_id = {h["id"]: h for h in doc["horizons"]}
    # ledger labels
    assert by_id["1y"]["in_live_ledger"] and by_id["2y"]["in_live_ledger"] and by_id["4y"]["in_live_ledger"]
    assert not by_id["10y"]["in_live_ledger"] and not by_id["30y"]["in_live_ledger"]
    # ≤4y use conformal; ultra-long use corridor
    assert "conformal" in by_id["1y"]["band_source"]
    assert by_id["10y"]["band_source"] == "idle_redline_corridor"
    assert by_id["30y"]["band_source"] == "idle_redline_corridor"
    # AR1 pulls |r| down by 1y
    r0 = doc["method"]["ar1"]["r0"]
    if r0 and abs(r0) > 1e-6 and 0 < doc["method"]["ar1"]["phi"] < 1:
        assert abs(by_id["1y"]["residual_mean"]) < abs(r0)
    # Ultra-long must not explode past Idle/Redline-scale width (~1.12 log10)
    assert by_id["30y"]["log_width"] < 1.5
    assert by_id["10y"]["log_width"] < 1.5
    # 30y P90 should be far below the old √t absurdity (~1e10)
    assert by_id["30y"]["p90"] < 1e9


def test_ensemble_has_no_share_weights():
    doc = sf.build()
    for row in doc["ensemble"]:
        assert "share" not in row


def test_privacy_scrub():
    doc = sf.build()
    blob = json.dumps(doc).lower()
    for needle in ("holdings", "wallet", "gmail", "icansquat", "btc_stack"):
        assert needle not in blob


def test_write_validates_schema(tmp_path, monkeypatch):
    out = tmp_path / "scenario_forecast.json"
    monkeypatch.setattr(sf, "OUT_PATH", out)
    doc = sf.build()
    sf.write(doc)
    assert json.loads(out.read_text())["schema_version"] >= 3


def test_conformal_offsets_computed():
    price = sf._load(sf.PRICE_PATH)
    models = sf._load(sf.MODELS_PATH)
    pl = models["power_law"]
    genesis = __import__("datetime").date.fromisoformat(pl["params"]["genesis_date"])
    fit_start = __import__("datetime").date.fromisoformat(pl["params"]["fit_start_date"])
    s = sf._PriceSeries(price["series"], genesis, fit_start)
    c = sf.compute_conformal_offsets(s)
    assert 365 in c and 730 in c and 1461 in c
    # WF showed 1y width > 4y width historically — allow either but require positive width
    assert c[365]["width"] > 0.3
    assert c[1461]["n_origins"] >= 20
