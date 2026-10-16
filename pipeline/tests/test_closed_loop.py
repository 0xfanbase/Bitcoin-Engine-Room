"""Closed-loop correction: shrinkage, caps, identity, application."""

from __future__ import annotations

import json
import math

import pytest

from pipeline import closed_loop as cl
from pipeline import scenario_forecast as sf


def _matured(n: int, horizon: int, log_err: float, inside_outer: bool = True) -> list[dict]:
    rows = []
    for i in range(n):
        trend = 100_000.0
        actual = trend * (10**log_err)
        rows.append(
            {
                "issued": f"2024-{i+1:02d}-01",
                "horizon_days": horizon,
                "target_date": f"2025-{i+1:02d}-01",
                "trend": trend,
                "actual": actual,
                "log_err": log_err,
                "miss_factor": actual / trend,
                "inside_outer": inside_outer,
                "inside_inner": inside_outer,
            }
        )
    return rows


def test_identity_when_no_matured():
    est = cl.estimate_correction([])
    assert est["status"] == "inactive_waiting_for_matured"
    assert est["horizons"]["365"]["bias_log10"] == 0.0
    assert est["horizons"]["365"]["active"] is False


def test_identity_below_min_n():
    est = cl.estimate_correction(_matured(2, 365, 0.2))
    assert est["status"] == "inactive_below_min_n"
    assert est["horizons"]["365"]["bias_log10"] == 0.0


def test_shrinkage_and_cap():
    # raw mean 0.5, n=3, kappa=12 → 3/15*0.5 = 0.1 exactly at cap boundary
    est = cl.estimate_correction(_matured(3, 365, 0.5), kappa=12.0, max_bias_abs=0.10)
    assert est["status"] == "active"
    assert est["horizons"]["365"]["bias_log10"] == pytest.approx(0.1, abs=1e-6)

    # larger raw mean still capped
    est2 = cl.estimate_correction(_matured(3, 365, 1.0), kappa=12.0, max_bias_abs=0.10)
    assert abs(est2["horizons"]["365"]["bias_log10"]) <= 0.10 + 1e-12


def test_widen_when_outer_misses():
    rows = _matured(5, 30, 0.0, inside_outer=False)
    est = cl.estimate_correction(rows)
    assert est["horizons"]["30"]["widen_log10"] > 0
    assert est["horizons"]["30"]["widen_log10"] <= cl.MAX_WIDEN


def test_pooled_prior_for_thin_horizon():
    # 4 matured at 30d only → pooled active; 365 gets half-strength prior
    rows = _matured(4, 30, 0.3)
    est = cl.estimate_correction(rows)
    assert est["status"] == "active"
    assert est["horizons"]["30"]["source"] == "ledger_horizon"
    assert est["horizons"]["365"]["source"] == "ledger_pooled_prior"
    assert abs(est["horizons"]["365"]["bias_log10"]) == pytest.approx(
        0.5 * abs(est["pooled"]["bias_log10"]), abs=1e-6
    )


def test_bias_widen_interp():
    corr = cl.estimate_correction(_matured(3, 30, 0.2) + _matured(3, 90, -0.1))
    b, w, src = cl.bias_widen_for_days(corr, 60)
    assert "interp" in src or src in ("ledger_horizon", "ledger_pooled_prior")
    assert isinstance(b, float)


def test_build_correction_privacy_and_schema(tmp_path, monkeypatch):
    out = tmp_path / "forecast_correction.json"
    monkeypatch.setattr(cl, "OUT_PATH", out)
    # empty ledger path
    monkeypatch.setattr(cl, "LEDGER_PATH", tmp_path / "missing.json")
    doc = cl.build_correction({"schema_version": 1, "entries": [], "started": None, "updated_at": "2026-10-16"})
    cl.write(doc)
    assert doc["status"] == "inactive_waiting_for_matured"
    blob = json.dumps(doc).lower()
    for needle in ("holdings", "wallet", "gmail", "icansquat", "btc_stack"):
        assert needle not in blob


def test_scenario_applies_active_correction(monkeypatch):
    # Force an active correction and ensure 1y residual shifts
    base = sf.build()
    r_before = next(h for h in base["horizons"] if h["id"] == "1y")["residual_mean"]

    fake = cl.estimate_correction(_matured(6, 365, 0.25))
    fake["status"] = "active"
    # rebuild estimate_correction already active; pass full build_correction shape
    corr = {
        "schema_version": 1,
        "generated_at": "2026-10-16T00:00:00Z",
        "note_path": cl.NOTE_PATH,
        "disclaimer": "test",
        **fake,
        "matured": [],
    }
    doc = sf.build(correction=corr)
    r_after = next(h for h in doc["horizons"] if h["id"] == "1y")["residual_mean"]
    bias = next(h for h in doc["horizons"] if h["id"] == "1y")["closed_loop_bias"]
    assert abs(bias) > 0
    assert r_after == pytest.approx((r_before or 0) + bias, abs=1e-5)
    assert doc["learning"]["correction"]["status"] == "active"
    assert any(e["id"] == "closed_loop" for e in doc["ensemble"])


def test_ultra_long_ignores_closed_loop():
    corr = {
        "schema_version": 1,
        "generated_at": "2026-10-16T00:00:00Z",
        "note_path": cl.NOTE_PATH,
        "disclaimer": "test",
        **cl.estimate_correction(_matured(6, 365, 0.25)),
        "matured": [],
    }
    doc = sf.build(correction=corr)
    h30 = next(h for h in doc["horizons"] if h["id"] == "30y")
    assert h30["closed_loop_bias"] == 0.0
    assert h30["band_source"] == "idle_redline_corridor"
