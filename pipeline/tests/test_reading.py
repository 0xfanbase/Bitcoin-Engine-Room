"""Conditional reading: bucket edges, bias rows, floor, peak stress."""

from __future__ import annotations

import json

from pipeline.reading import FORBIDDEN, bucket_for_z, build_reading, write_room_summary
from pipeline.scenario_forecast import MODELS_PATH, BACKTEST_PATH


def _models(**current):
    base = {
        "date": "2026-11-20",
        "price": 60000,
        "trend_price": 140000,
        "deviation_pct": -57.1,
        "z_score": -1.25,
        "residual_percentile": 3.7,
    }
    base.update(current)
    return {
        "generated_at": "2026-11-20T00:00:00Z",
        "power_law": {
            "params": {
                "a": -16.0,
                "b": 5.6,
                "sigma": 0.30,
                "genesis_date": "2009-01-03",
            },
            "current": base,
            "bands": {"outer_offsets_log10": [-0.40, 0.72]},
            "cycle_top_era_maxima_sigma": [
                {"date": "2011-06-11", "sigma_vs_trend": 4.0},
                {"date": "2013-11-30", "sigma_vs_trend": 3.4},
                {"date": "2017-12-17", "sigma_vs_trend": 2.6},
                {"date": "2021-03-14", "sigma_vs_trend": 1.8},
                {"date": "2024-12-17", "sigma_vs_trend": 0.5},
            ],
        },
    }


def _backtest():
    return {
        "generated_at": "2026-11-20T00:00:00Z",
        "z_buckets": [
            {"z_low": -99.0, "z_high": -1.0, "n_months": 15, "episodes": 3, "median_fwd_1y_pct": 130.3, "share_negative": 0.0},
            {"z_low": -1.0, "z_high": -0.5, "n_months": 36, "episodes": 7, "median_fwd_1y_pct": 127.2, "share_negative": 0.028},
        ],
        "horizons": [
            {
                "horizon_days": 365,
                "trend_bias_pct": -16.1,
                "trend_mae_factor": 1.994,
                "random_walk_mae_factor": 2.384,
                "coverage_outer": 0.845,
                "share_below_outer": 0.142,
                "share_above_outer": 0.013,
                "approx_independent_windows": 12.9,
                "n_origins": 155,
            }
        ],
        "short_term": [
            {"horizon_days": 90, "trend_plus_ar1_mae_factor": 1.433, "random_walk_mae_factor": 1.418}
        ],
        "signals": [
            {"signal": "fear_greed", "spearman_vs_fwd_1y": 0.077, "n_months": 93, "first_origin": "2018-03-01", "approx_independent_windows": 7.7},
            {"signal": "mayer_multiple", "spearman_vs_fwd_1y": 0.17, "n_months": 155, "first_origin": "2013-01-01", "approx_independent_windows": 12.8},
        ],
        "per_origin_1y": [{"miss_log10": -0.8 + i * 0.01} for i in range(100)],
    }


CONSTANTS = {
    "honesty": {"signal_noise_abs": 0.3},
    "cycle_overlay": {"next_halving_est_date": "2028-04"},
}


def test_bucket_edges_are_half_open():
    buckets = _backtest()["z_buckets"]
    low = bucket_for_z(-1.25, buckets)
    assert low["episodes"] == 3
    assert low["label"] == "z below -1"
    edge = bucket_for_z(-1.0, buckets)
    assert edge["z_low"] == -1.0
    assert edge["episodes"] == 7


def test_reading_keeps_stress_off_the_percentile_names():
    reading = build_reading(_models(), _backtest(), CONSTANTS)
    stress = reading["peak_stress"]
    assert stress["steps_strictly_down"] is True
    assert stress["stress_sigma"] < 1.8
    assert stress["stress_price"] > 0
    assert "P90" not in stress["note"]
    assert "not a percentile" in stress["note"]
    floor = reading["walkforward_floor"]
    assert floor["floor_price"] < floor["cruise_price"]
    assert "Does not replace Idle or P10." in floor["note"]
    assert reading["short_term_90"]["ties_random_walk"] is True
    assert reading["horizon_bias"][0]["trend_bias_pct"] == -16.1
    blob = json.dumps(reading).lower()
    for needle in FORBIDDEN:
        assert needle not in blob


def test_committed_reading_matches_published_bucket():
    models = json.loads(MODELS_PATH.read_text())
    backtest = json.loads(BACKTEST_PATH.read_text())
    reading = build_reading(models, backtest)
    z = models["power_law"]["current"]["z_score"]
    assert reading["bucket"]["z_low"] <= z < reading["bucket"]["z_high"]
    assert reading["bucket"]["episodes"] >= 1
    assert reading["peak_stress"]["stress_price"] > 0
    assert reading["walkforward_floor"]["n_origins"] >= 20
    # The published 1-year outer band has been short on the downside.
    one = next(h for h in reading["horizon_bias"] if h["horizon_days"] == 365)
    assert one["share_below_outer"] > one["share_above_outer"]


def test_write_room_summary_validates(tmp_path):
    reading = build_reading(_models(), _backtest(), CONSTANTS)
    path = tmp_path / "room_summary.json"
    write_room_summary(reading, path)
    doc = json.loads(path.read_text())
    assert doc["schema_version"] == 1
    assert doc["reading"]["z_score"] == -1.25
