"""Conditional reading for the public Forecast and the masthead.

Pure function of already-published models.json + backtest.json. It does not
refit anything and it does not replace Idle, Redline, or the conformal
P10/P90. Those stay what they are. This module only names the bucket today's
z falls in, the walk-forward bias of the Cruise line, a floor calibrated on
the same 1-year errors (labeled as such), and a peak-amplitude stress case.

The stress case is the median step between closed-era peak sigmas, applied
once to the last closed era. A time-decaying probability band was tested and
rejected (~77% realised coverage). This price is a scenario label, not a
percentile.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSTANTS_PATH = REPO_ROOT / "pipeline" / "model_constants.json"
SUMMARY_PATH = REPO_ROOT / "data" / "room_summary.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "room_summary.schema.json"

# Same edges as pipeline.backtest.Z_BUCKETS. Duplicated here so a masthead
# rebuild does not import the walk-forward module.
Z_BUCKETS = ((-99.0, -1.0), (-1.0, -0.5), (-0.5, 0.0), (0.0, 0.5), (0.5, 1.0), (1.0, 99.0))
BIAS_HORIZONS = (365, 730, 1461)
FLOOR_QUANTILE = 0.025
MIN_FLOOR_ORIGINS = 20
MIN_CLOSED_ERAS = 3
FORBIDDEN = ("holdings", "wallet", "balance", "email", "gmail", "btc_stack", "icansquat")


def _r(v: float | None, n: int = 2) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    idx = (len(s) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return s[lo]
    w = idx - lo
    return s[lo] * (1.0 - w) + s[hi] * w


def _bucket_label(lo: float, hi: float) -> str:
    if lo <= -90:
        return f"z below {hi:g}"
    if hi >= 90:
        return f"z at or above {lo:g}"
    return f"z from {lo:g} up to {hi:g}"


def bucket_for_z(z: float, buckets: list[dict] | None) -> dict | None:
    """Half-open [lo, hi), matching backtest._realtime_signals."""
    rows = buckets or []
    for row in rows:
        lo = float(row["z_low"])
        hi = float(row["z_high"])
        if lo <= z < hi:
            out = dict(row)
            out["label"] = _bucket_label(lo, hi)
            return out
    for lo, hi in Z_BUCKETS:
        if lo <= z < hi:
            return {"z_low": lo, "z_high": hi, "label": _bucket_label(lo, hi), "n_months": None, "episodes": None}
    return None


def _parse_eval_date(raw: str) -> date:
    if len(raw) == 7:
        return date.fromisoformat(raw + "-15")
    return date.fromisoformat(raw)


def _trend(a: float, b: float, day: float) -> float:
    return 10 ** (a + b * math.log10(max(day, 2.0)))


def _peak_stress(pl: dict, constants: dict, genesis: date) -> dict | None:
    rows = list(pl.get("cycle_top_era_maxima_sigma") or [])
    if len(rows) < MIN_CLOSED_ERAS + 1:
        return None
    closed = rows[:-1]
    if len(closed) < MIN_CLOSED_ERAS:
        return None
    sigmas = [float(r["sigma_vs_trend"]) for r in closed]
    deltas = [sigmas[i + 1] - sigmas[i] for i in range(len(sigmas) - 1)]
    if not deltas:
        return None
    step = _median(deltas)
    stress_sigma = sigmas[-1] + step
    params = pl["params"]
    sigma = float(params["sigma"])
    a = float(params["a"])
    b = float(params["b"])
    raw = (constants.get("cycle_overlay") or {}).get("next_halving_est_date")
    if not raw:
        return None
    eval_on = _parse_eval_date(str(raw))
    day = (eval_on - genesis).days
    cruise = _trend(a, b, day)
    stress_price = cruise * (10 ** (stress_sigma * sigma))
    strictly_down = all(deltas[i] < 0 for i in range(len(deltas)))
    return {
        "kind": "median_step_from_closed_era_sigma",
        "closed_era_sigma": [_r(s, 4) for s in sigmas],
        "closed_era_dates": [r.get("date") for r in closed],
        "median_step": _r(step, 4),
        "stress_sigma": _r(stress_sigma, 4),
        "steps_strictly_down": strictly_down,
        "eval_date": eval_on.isoformat(),
        "cruise_price": _r(cruise, 2),
        "stress_price": _r(stress_price, 2),
        "note": (
            "Median step between successive closed-era peak sigmas, applied once "
            "to the last closed era and scored on today's fit. A scenario label, "
            "not a percentile. A decaying probability band was tested and rejected."
        ),
    }


def _walkforward_floor(pl: dict, backtest: dict, genesis: date, as_of: date) -> dict | None:
    rows = backtest.get("per_origin_1y") or []
    errs = []
    for row in rows:
        v = row.get("miss_log10")
        if v is None:
            continue
        errs.append(float(v))
    if len(errs) < MIN_FLOOR_ORIGINS:
        return None
    offset = _quantile(errs, FLOOR_QUANTILE)
    params = pl["params"]
    a = float(params["a"])
    b = float(params["b"])
    day = (as_of - genesis).days + 365
    cruise = _trend(a, b, day)
    outer = (pl.get("bands") or {}).get("outer_offsets_log10") or [None, None]
    in_sample = outer[0]
    return {
        "horizon_days": 365,
        "quantile": FLOOR_QUANTILE,
        "n_origins": len(errs),
        "offset_log10": _r(offset, 6),
        "floor_price": _r(cruise * (10 ** offset), 2),
        "cruise_price": _r(cruise, 2),
        "in_sample_outer_offset_log10": _r(in_sample, 6) if in_sample is not None else None,
        "note": (
            "2.5th percentile of past 1-year log misses versus the Cruise line, "
            "priced off today's Cruise one year out. Fitted on the same walk-forward "
            "errors it summarizes. Does not replace Idle or P10."
        ),
    }


def _signals(backtest: dict) -> dict:
    out = {}
    for row in backtest.get("signals") or []:
        name = row.get("signal")
        if not name:
            continue
        out[name] = {
            "spearman_vs_fwd_1y": row.get("spearman_vs_fwd_1y"),
            "n_months": row.get("n_months"),
            "first_origin": row.get("first_origin"),
            "approx_independent_windows": row.get("approx_independent_windows"),
        }
    return out


def _horizon_bias(backtest: dict) -> list[dict]:
    wanted = set(BIAS_HORIZONS)
    rows = []
    for h in backtest.get("horizons") or []:
        days = h.get("horizon_days")
        if days not in wanted:
            continue
        rows.append(
            {
                "horizon_days": days,
                "trend_bias_pct": h.get("trend_bias_pct"),
                "trend_mae_factor": h.get("trend_mae_factor"),
                "random_walk_mae_factor": h.get("random_walk_mae_factor"),
                "coverage_outer": h.get("coverage_outer"),
                "share_below_outer": h.get("share_below_outer"),
                "share_above_outer": h.get("share_above_outer"),
                "approx_independent_windows": h.get("approx_independent_windows"),
                "n_origins": h.get("n_origins"),
            }
        )
    rows.sort(key=lambda r: r["horizon_days"])
    return rows


def _short_term(backtest: dict, days: int = 90) -> dict | None:
    for row in backtest.get("short_term") or []:
        if row.get("horizon_days") == days:
            ar = row.get("trend_plus_ar1_mae_factor")
            rw = row.get("random_walk_mae_factor")
            ties = None
            if ar and rw:
                ties = abs(float(ar) - float(rw)) / float(rw) <= 0.05
            return {
                "horizon_days": days,
                "trend_plus_ar1_mae_factor": ar,
                "random_walk_mae_factor": rw,
                "ties_random_walk": ties,
            }
    return None


def build_reading(models: dict, backtest: dict, constants: dict | None = None) -> dict:
    if constants is None:
        constants = json.loads(CONSTANTS_PATH.read_text())
    pl = models["power_law"]
    current = pl["current"]
    params = pl["params"]
    genesis = date.fromisoformat(params["genesis_date"])
    as_of = date.fromisoformat(current["date"])
    z = float(current["z_score"])
    noise = float((constants.get("honesty") or {}).get("signal_noise_abs", 0.3))
    bucket = bucket_for_z(z, backtest.get("z_buckets"))
    reading = {
        "as_of": current["date"],
        "models_generated_at": models.get("generated_at"),
        "backtest_generated_at": backtest.get("generated_at"),
        "spot": _r(current.get("price"), 2),
        "cruise": _r(current.get("trend_price"), 2),
        "deviation_pct": _r(current.get("deviation_pct"), 2),
        "z_score": _r(z, 3),
        "residual_percentile": _r(current.get("residual_percentile"), 2),
        "noise_abs": noise,
        "bucket": bucket,
        "signals": _signals(backtest),
        "horizon_bias": _horizon_bias(backtest),
        "short_term_90": _short_term(backtest, 90),
        "walkforward_floor": _walkforward_floor(pl, backtest, genesis, as_of),
        "peak_stress": _peak_stress(pl, constants, genesis),
    }
    blob = json.dumps(reading).lower()
    for needle in FORBIDDEN:
        if needle in blob:
            raise ValueError(f"reading contains forbidden token {needle!r}")
    return reading


def write_room_summary(reading: dict, path: Path | None = None) -> Path:
    path = path or SUMMARY_PATH
    doc = {
        "schema_version": 1,
        "generated_at": reading.get("models_generated_at"),
        "reading": reading,
    }
    blob = json.dumps(doc).lower()
    for needle in FORBIDDEN:
        if needle in blob:
            raise ValueError(f"room summary contains forbidden token {needle!r}")
    if SCHEMA_PATH.exists():
        import jsonschema

        schema = json.loads(SCHEMA_PATH.read_text())
        jsonschema.validate(doc, schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(path)
    return path
