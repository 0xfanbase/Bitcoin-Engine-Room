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
    assert by_id["1m"]["in_live_ledger"]
    assert by_id["1y"]["in_live_ledger"] and by_id["2y"]["in_live_ledger"] and by_id["4y"]["in_live_ledger"]
    assert not by_id["10y"]["in_live_ledger"] and not by_id["30y"]["in_live_ledger"]
    assert doc["learning"]["correction"]["status"] in (
        "active",
        "inactive_waiting_for_matured",
        "inactive_below_min_n",
    )
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


def test_idle_not_labeled_p10_past_conformal():
    """Corridor / ≥10y display Idle/Cruise/Redline. p10/p90 keys stay as aliases."""
    doc = sf.build()
    by_id = {h["id"]: h for h in doc["horizons"]}
    for hid in ("1m", "1y", "2y", "4y"):
        labels = by_id[hid]["band_labels"]
        assert labels["low"] == "P10" and labels["high"] == "P90" and labels["kind"] == "conformal"
        assert "idle" not in by_id[hid] and "redline" not in by_id[hid]
        blob = labels["low"] + labels["mid"] + labels["high"]
        assert "Idle" not in blob and "Redline" not in blob
    assert by_id["1y"]["coverage_earned"] is True
    assert by_id["2y"]["coverage_earned"] is True
    assert by_id["4y"]["coverage_earned"] is False
    assert by_id["4y"]["conformal_indep_windows"] < sf.UNEARNED_INDEP_BELOW
    for hid in ("10y", "30y"):
        h = by_id[hid]
        assert h["band_source"] == "idle_redline_corridor"
        assert h["band_labels"] == {"low": "Idle", "mid": "Cruise", "high": "Redline", "kind": "corridor"}
        assert h["idle"] == h["p10"] and h["redline"] == h["p90"]
        assert h["conformal_indep_windows"] is None
        assert h["coverage_earned"] is False
        assert "P10" not in json.dumps(h["band_labels"])
        assert h["cagr_to_idle"] is not None and h["cagr_to_cruise"] is not None
    assert "cagr_to_idle" not in by_id["1y"]
    long_fan = [p for p in doc["fan"] if (p.get("days") or 0) >= 3652]
    assert long_fan, "20y/30y fan points missing"
    assert all(p["band_labels"]["low"] == "Idle" for p in long_fan)
    assert all(p["band_labels"]["kind"] == "corridor" for p in long_fan)
    assert all("P10" not in p["band_labels"]["low"] for p in long_fan)
    assert all(p.get("idle") == p["p10"] and p.get("redline") == p["p90"] for p in long_fan)
    past = [p for p in doc["fan"] if (p.get("days") or 0) > sf.CONFORMAL_LABEL_MAX_DAYS]
    assert past and all(p["band_labels"]["low"] != "P10" for p in past)
    near = [p for p in doc["fan"] if (p.get("days") or 0) <= sf.CONFORMAL_LABEL_MAX_DAYS]
    assert near and all(p["band_labels"]["low"] == "P10" for p in near)
    assert sf.RIBBON_DISCLAIMER in doc["method"]["disclaimer"]
    assert "no live recalibration" in doc["learning"]["ledger_note"].lower()
    assert "applies shrunk" not in doc["learning"]["ledger_note"].lower()
    cap = next(e for e in doc["ensemble"] if e["id"] == "idle_redline_cap")
    assert "Idle" in cap["role"] and "P10" not in cap["role"]
    loop = next(e for e in doc["ensemble"] if e["id"] == "closed_loop")
    assert "no live recalibration" in loop["role"].lower()
    assert doc["learning"]["correction"]["n_matured_total"] == 0


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
