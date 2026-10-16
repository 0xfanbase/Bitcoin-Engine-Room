"""Public scenario-forecast artifact for the Forecast section.

Built ONLY from Bitcoin-Engine-Room's own fitted power-law + AR(1) +
walk-forward conformal residual offsets + Idle/Redline corridor +
optional closed-loop ledger correction.
Does NOT ingest private btc-watcher JSON.

v3 (2026-10-15): replace √t width growth with per-horizon conformal
bands for ≤4y and Idle/Redline caps for 10y/30y. See
pipeline/notes/FORECAST_CALIBRATION_V3.md.

v3.1 (2026-10-16): apply shrunk/capped bias + optional band widen from
matured live-ledger published→outcome misses. See
pipeline/notes/FORECAST_CLOSED_LOOP_V1.md.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from pipeline.forecast import ar1_residual, month_starts, ols_fit, residual_quantiles

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "data" / "models.json"
PRICE_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
BACKTEST_PATH = REPO_ROOT / "data" / "backtest.json"
LEDGER_PATH = REPO_ROOT / "data" / "forecasts.json"
OUT_PATH = REPO_ROOT / "data" / "scenario_forecast.json"
CORRECTION_PATH = REPO_ROOT / "data" / "forecast_correction.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "scenario_forecast.schema.json"

LEDGER_HORIZON_DAYS = {30, 90, 365, 730, 1461}

HORIZONS = [
    {"id": "1m", "label": "1 month", "days": 30},
    {"id": "1y", "label": "1 year", "days": 365},
    {"id": "2y", "label": "2 years", "days": 730},
    {"id": "4y", "label": "4 years", "days": 1461},
    {"id": "10y", "label": "10 years", "days": 3652},
    {"id": "30y", "label": "30 years", "days": 10957},
]

# Knots used for conformal + fan interpolation (days).
CONFORMAL_KNOTS = (30, 90, 180, 365, 730, 1461)
ULTRA_LONG_DAYS = {3652, 10957}

P_QUANTS = [0.10, 0.50, 0.90]
AR1_SAMPLE_DAYS = 30
ORIGIN_START = date(2013, 1, 1)
MIN_FIT_POINTS = 365
CONFORMAL_LO = 0.10
CONFORMAL_HI = 0.90


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _r(v: float | None, n: int = 2) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


def _price_at(d: float, a: float, b: float, offset: float = 0.0) -> float:
    return 10 ** (a + b * math.log10(max(d, 2.0)) + offset)


def _build_residuals(series: list[dict], genesis: date, a: float, b: float, fit_start: date) -> np.ndarray:
    resid = []
    for row in series:
        d = date.fromisoformat(row["date"])
        if d < fit_start or row.get("carried_forward"):
            continue
        days = (d - genesis).days
        if days <= 1 or row.get("value") in (None, 0):
            continue
        y = math.log10(float(row["value"]))
        resid.append(y - (a + b * math.log10(days)))
    return np.asarray(resid, dtype=float)


def _residual_mean_ar1(r0: float, phi: float, innov_sigma: float, days: int) -> tuple[float, float]:
    exact = max(days, 0) / AR1_SAMPLE_DAYS
    if days <= 0:
        return r0, 0.0
    mean = r0 * (phi**exact) if phi > 0 else 0.0
    if phi <= 0:
        sd = innov_sigma * math.sqrt(exact)
    elif phi < 1.0:
        var = innov_sigma**2 * (1 - phi ** (2 * exact)) / (1 - phi**2)
        sd = math.sqrt(max(var, 0.0))
    else:
        sd = innov_sigma * math.sqrt(exact)
    return mean, sd


class _PriceSeries:
    """Minimal causal price view (same exclusions as backtest.Series)."""

    def __init__(self, rows: list[dict], genesis: date, fit_start: date):
        rows = [
            r
            for r in rows
            if not r.get("carried_forward") and date.fromisoformat(r["date"]) >= fit_start
        ]
        self.dates = [date.fromisoformat(r["date"]) for r in rows]
        self.d = np.array([(dt - genesis).days for dt in self.dates], dtype=float)
        self.x = np.log10(self.d)
        self.y = np.log10(np.array([r["value"] for r in rows], dtype=float))

    def n_before(self, origin: date) -> int:
        return bisect.bisect_left(self.dates, origin)

    def index_on_or_after(self, target: date) -> int | None:
        i = bisect.bisect_left(self.dates, target)
        if i >= len(self.dates) or (self.dates[i] - target).days > 7:
            return None
        return i


def compute_conformal_offsets(
    s: _PriceSeries,
    *,
    sample_days: int = AR1_SAMPLE_DAYS,
    knots: tuple[int, ...] = CONFORMAL_KNOTS,
) -> dict[int, dict]:
    """Walk-forward P10/P90 offsets of AR(1)-relative forecast errors.

    For each monthly origin: refit power law on data before origin, take
    AR(1) mean residual at horizon h, score
    err = y_actual - (trend_h + ar1_mean_h). Offsets are quantiles of err.
    """
    origins = month_starts(ORIGIN_START, s.dates[-1])
    fits: dict[date, dict] = {}
    for o in origins:
        n = s.n_before(o)
        if n < MIN_FIT_POINTS:
            continue
        a, b, _sigma = ols_fit(s.x[:n], s.y[:n])
        resid = s.y[:n] - (a + b * s.x[:n])
        fits[o] = {"n": n, "a": a, "b": b, "resid": resid}

    span_days = (s.dates[-1] - ORIGIN_START).days
    out: dict[int, dict] = {}
    for h in knots:
        errs: list[float] = []
        for o, f in fits.items():
            i1 = s.index_on_or_after(o + timedelta(days=h))
            if i1 is None:
                continue
            phi, _innov = ar1_residual(f["resid"], sample_days)
            exact = h / sample_days
            mean = float(f["resid"][-1]) * (phi**exact) if phi > 0 else 0.0
            trend1 = f["a"] + f["b"] * s.x[i1]
            errs.append(float(s.y[i1] - (trend1 + mean)))
        if len(errs) < 20:
            continue
        arr = np.asarray(errs, dtype=float)
        lo = float(np.quantile(arr, CONFORMAL_LO))
        hi = float(np.quantile(arr, CONFORMAL_HI))
        out[h] = {
            "horizon_days": h,
            "n_origins": len(errs),
            "approx_independent_windows": round(max(span_days - h, 0) / h, 1),
            "offset_lo": round(lo, 6),
            "offset_hi": round(hi, 6),
            "width": round(hi - lo, 6),
            "err_p50": round(float(np.quantile(arr, 0.5)), 6),
        }
    return out


def _interp_offsets(conformal: dict[int, dict], days: int) -> tuple[float, float, str]:
    """Interpolate conformal offsets in log-day space; clamp outside knots."""
    knots = sorted(conformal.keys())
    if not knots:
        return 0.0, 0.0, "none"
    if days in conformal:
        c = conformal[days]
        return c["offset_lo"], c["offset_hi"], f"conformal@{days}d"
    if days <= knots[0]:
        c = conformal[knots[0]]
        return c["offset_lo"], c["offset_hi"], f"conformal@{knots[0]}d"
    if days >= knots[-1]:
        c = conformal[knots[-1]]
        return c["offset_lo"], c["offset_hi"], f"conformal@{knots[-1]}d"
    for i in range(len(knots) - 1):
        d0, d1 = knots[i], knots[i + 1]
        if d0 <= days <= d1:
            c0, c1 = conformal[d0], conformal[d1]
            t = (math.log(days) - math.log(d0)) / (math.log(d1) - math.log(d0))
            lo = c0["offset_lo"] + t * (c1["offset_lo"] - c0["offset_lo"])
            hi = c0["offset_hi"] + t * (c1["offset_hi"] - c0["offset_hi"])
            return lo, hi, f"conformal_interp_{d0}_{d1}"
    c = conformal[knots[-1]]
    return c["offset_lo"], c["offset_hi"], f"conformal@{knots[-1]}d"


def _taper_to_corridor(
    lo: float,
    hi: float,
    days: int,
    outer_lo: float,
    outer_hi: float,
    *,
    start_days: int = 1461,
    end_days: int = 3652,
) -> tuple[float, float, str]:
    """Between 4y and 10y, blend conformal offsets toward Idle/Redline."""
    if days <= start_days:
        return lo, hi, "conformal"
    if days >= end_days:
        return outer_lo, outer_hi, "idle_redline"
    t = (math.log(days) - math.log(start_days)) / (math.log(end_days) - math.log(start_days))
    return (
        lo + t * (outer_lo - lo),
        hi + t * (outer_hi - hi),
        "taper_conformal_to_corridor",
    )


def build(*, now: datetime | None = None, correction: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    models = _load(MODELS_PATH)
    price_doc = _load(PRICE_PATH)
    backtest = _load(BACKTEST_PATH) or {}
    ledger = _load(LEDGER_PATH) or {}
    if correction is None:
        correction = _load(CORRECTION_PATH)
    if correction is None:
        from pipeline import closed_loop

        correction = closed_loop.build_correction(ledger, now=now)
    if not models or not price_doc:
        raise SystemExit("models.json and price_daily.json are required")

    pl = models["power_law"]
    params = pl["params"]
    a, b = float(params["a"]), float(params["b"])
    genesis = date.fromisoformat(params["genesis_date"])
    fit_start = date.fromisoformat(params["fit_start_date"])
    series = price_doc["series"]
    last = next(r for r in reversed(series) if not r.get("carried_forward") and r.get("value"))
    as_of = date.fromisoformat(last["date"])
    spot = float(last["value"])
    last_days = max(2, (as_of - genesis).days)

    resid = _build_residuals(series, genesis, a, b, fit_start)
    q10, q50, q90 = residual_quantiles(resid, P_QUANTS)
    r0 = float(resid[-1]) if len(resid) else 0.0
    phi, innov = ar1_residual(resid, AR1_SAMPLE_DAYS)

    outer = pl["bands"]["outer_offsets_log10"]
    outer_lo, outer_hi = float(outer[0]), float(outer[1])

    price_s = _PriceSeries(series, genesis, fit_start)
    conformal = compute_conformal_offsets(price_s)

    from pipeline.closed_loop import bias_widen_for_days

    horizons_out = []
    for h in HORIZONS:
        target = as_of + timedelta(days=h["days"])
        d = last_days + h["days"]
        mean_r, _sd = _residual_mean_ar1(r0, phi, innov, h["days"])
        in_ledger = h["days"] in LEDGER_HORIZON_DAYS
        bias_c, widen_c, corr_src = 0.0, 0.0, "identity"

        if h["days"] in ULTRA_LONG_DAYS:
            # Demote: Cruise ± Idle/Redline — no √t, no fake long-horizon conformal
            # Closed-loop does not touch ultra-long corridor (no ledger sample).
            mean_r = 0.0
            lo, hi = outer_lo, outer_hi
            band_source = "idle_redline_corridor"
            caveat = (
                "Scenario only — corridor is Models Idle/Redline (empirical residual envelope), "
                "not a walk-forward-calibrated CI. Independent sample at this horizon is effectively zero."
            )
        else:
            lo, hi, band_source = _interp_offsets(conformal, h["days"])
            if h["days"] > 1461:
                lo, hi, band_source = _taper_to_corridor(lo, hi, h["days"], outer_lo, outer_hi)
            bias_c, widen_c, corr_src = bias_widen_for_days(correction, h["days"])
            mean_r = mean_r + bias_c
            lo = lo - widen_c
            hi = hi + widen_c
            if abs(bias_c) > 1e-12 or widen_c > 1e-12:
                band_source = f"{band_source}+closed_loop"
            caveat = None if in_ledger else (
                "Not frozen in the live monthly ledger (ledger tracks 30d / 90d / 1y / 2y / 4y)."
            )

        p50 = _price_at(d, a, b, mean_r)
        p10 = _price_at(d, a, b, mean_r + lo)
        p90 = _price_at(d, a, b, mean_r + hi)
        p10, p90 = min(p10, p50), max(p90, p50)

        meta_c = conformal.get(h["days"]) or {}
        horizons_out.append(
            {
                "id": h["id"],
                "label": h["label"],
                "days": h["days"],
                "target_date": target.isoformat(),
                "p10": _r(p10, 2),
                "p50": _r(p50, 2),
                "p90": _r(p90, 2),
                "cruise": _r(_price_at(d, a, b, 0.0), 2),
                "multiple_vs_spot_p50": _r(p50 / spot, 3) if spot else None,
                "log_width": _r(math.log10(p90 / p10), 4) if p10 > 0 else None,
                "offset_lo": _r(lo, 6),
                "offset_hi": _r(hi, 6),
                "residual_mean": _r(mean_r, 6),
                "closed_loop_bias": _r(bias_c, 6),
                "closed_loop_widen": _r(widen_c, 6),
                "closed_loop_source": corr_src,
                "band_source": band_source,
                "conformal_n_origins": meta_c.get("n_origins"),
                "conformal_indep_windows": meta_c.get("approx_independent_windows"),
                "in_live_ledger": in_ledger,
                "caveat": caveat,
            }
        )

    # Fan: monthly to 10y + sparse long markers
    fan = []
    month_list = list(range(0, 121)) + [15 * 12, 20 * 12, 25 * 12, 30 * 12]
    for months in month_list:
        days = 0 if months == 0 else int(round(months * 365.25 / 12))
        if months >= 15 * 12:
            days = int(round((months // 12) * 365.25))
        d = last_days + days
        if days == 0:
            mean_r = r0
            lo, hi, src = _interp_offsets(conformal, 30)
            # at as-of, collapse toward a tight envelope around spot residual
            lo, hi = lo * 0.25, hi * 0.25
            src = "asof_tight"
        elif days in ULTRA_LONG_DAYS or days > 3652:
            mean_r = 0.0
            lo, hi, src = outer_lo, outer_hi, "idle_redline_corridor"
        else:
            mean_r, _ = _residual_mean_ar1(r0, phi, innov, days)
            lo, hi, src = _interp_offsets(conformal, days)
            if days > 1461:
                lo, hi, src = _taper_to_corridor(lo, hi, days, outer_lo, outer_hi)
            bias_c, widen_c, _ = bias_widen_for_days(correction, days)
            mean_r = mean_r + bias_c
            lo = lo - widen_c
            hi = hi + widen_c
        fan.append(
            {
                "date": (as_of + timedelta(days=days)).isoformat(),
                "p10": _r(_price_at(d, a, b, mean_r + lo), 2),
                "p50": _r(_price_at(d, a, b, mean_r), 2),
                "p90": _r(_price_at(d, a, b, mean_r + hi), 2),
                "cruise": _r(_price_at(d, a, b, 0.0), 2),
            }
        )
    seen: set[str] = set()
    fan_u = []
    for pt in fan:
        if pt["date"] in seen:
            continue
        seen.add(pt["date"])
        fan_u.append(pt)
    fan = fan_u

    cutoff = as_of - timedelta(days=8 * 365)
    weekly: dict[str, dict] = {}
    for row in series:
        if row.get("carried_forward") or not row.get("value"):
            continue
        d = date.fromisoformat(row["date"])
        if d < cutoff:
            continue
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        weekly[key] = {"date": row["date"], "price": _r(float(row["value"]), 2)}
    history = sorted(weekly.values(), key=lambda x: x["date"])

    hz = {h["horizon_days"]: h for h in backtest.get("horizons", [])}
    h1 = hz.get(365) or {}
    ensemble = [
        {
            "id": "power_law_ar1",
            "name": "Power law + AR(1) residual",
            "role": "P50 path (mean-reverting toward Cruise)",
            "trust_note": (
                f"φ={phi:.3f} per {AR1_SAMPLE_DAYS}d; "
                f"walk-forward 1y typical miss ×{h1.get('trend_mae_factor', '—')}"
            ),
        },
        {
            "id": "conformal_bands",
            "name": "Walk-forward conformal envelope",
            "role": "P10/P90 for ≤4y (AR1-relative error quantiles)",
            "trust_note": (
                f"1y indep windows ~{(conformal.get(365) or {}).get('approx_independent_windows', '—')}; "
                "asymmetric; not a CI"
            ),
        },
        {
            "id": "idle_redline_cap",
            "name": "Idle / Redline corridor",
            "role": "P10/P90 for 10y & 30y only",
            "trust_note": "Same empirical residual envelope as Models — scenario ribbon, not calibrated long-CI",
        },
        {
            "id": "live_ledger",
            "name": "Monthly live ledger",
            "role": "scores 30d / 90d / 1y / 2y / 4y",
            "trust_note": "Cards outside those horizons are labeled scenario-only",
        },
        {
            "id": "closed_loop",
            "name": "Ledger closed-loop nudge",
            "role": "shrunk bias + optional band widen from matured published misses",
            "trust_note": (
                f"status={correction.get('status')}; "
                f"matured={correction.get('n_matured_total', 0)}; "
                "caps ±0.10 log10; not a CI recalibration"
            ),
        },
    ]

    matured = []
    for entry in ledger.get("entries", []):
        for hz_row in entry.get("horizons", []):
            if hz_row.get("outcome") is None:
                continue
            matured.append(
                {
                    "issued": entry.get("issued"),
                    "horizon_days": hz_row.get("horizon_days"),
                    "target_date": hz_row.get("target_date"),
                    "trend": hz_row.get("trend"),
                    "outcome": hz_row.get("outcome"),
                    "floor": hz_row.get("floor"),
                    "ceiling": hz_row.get("ceiling"),
                }
            )

    half_life = None
    if 0 < phi < 1:
        half_life = round(AR1_SAMPLE_DAYS * math.log(0.5) / math.log(phi), 1)

    doc = {
        "schema_version": 3,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of.isoformat(),
        "spot": _r(spot, 2),
        "method": {
            "family": "power_law_ar1_conformal",
            "a": a,
            "b": b,
            "genesis_date": genesis.isoformat(),
            "fit_start_date": fit_start.isoformat(),
            "residual_quantiles": {"p10": _r(q10, 6), "p50": _r(q50, 6), "p90": _r(q90, 6)},
            "n_residuals": int(len(resid)),
            "ar1": {
                "sample_days": AR1_SAMPLE_DAYS,
                "phi": round(phi, 6),
                "innov_sigma": _r(innov, 6),
                "half_life_days": half_life,
                "r0": _r(r0, 6),
            },
            "width": {
                "rule": (
                    "≤4y: walk-forward conformal P10/P90 of AR1-relative log errors; "
                    "10y/30y: Idle/Redline corridor around Cruise; fan tapers 4y→10y"
                ),
                "conformal": {
                    str(k): v for k, v in sorted(conformal.items())
                },
                "ultra_long": {
                    "source": "models.power_law.bands.outer_offsets_log10",
                    "offset_lo": outer_lo,
                    "offset_hi": outer_hi,
                },
                "replaced": "sqrt_years_scale_from_v2",
            },
            "ledger_horizons_days": sorted(LEDGER_HORIZON_DAYS),
            "closed_loop": {
                "status": correction.get("status"),
                "n_matured_total": correction.get("n_matured_total"),
                "pooled": correction.get("pooled"),
                "note_path": "pipeline/notes/FORECAST_CLOSED_LOOP_V1.md",
            },
            "disclaimer": (
                "P50 mean-reverts toward Cruise via AR(1) (same family as Models short-term). "
                "P10/P90 for ≤4y are walk-forward conformal envelopes of AR1 forecast errors — "
                "empirical coverage targets, NOT confidence intervals (independent windows are small). "
                "When enough ledger calls have matured, a shrunk/capped closed-loop bias "
                "(and optional band widen) nudges ≤4y forecasts from published→outcome misses; "
                "this is still not a CI. "
                "10y/30y use the Models Idle/Redline corridor as a scenario ribbon only. "
                "Live ledger freezes 30d/90d/1y/2y/4y."
            ),
            "note_path": "pipeline/notes/FORECAST_CALIBRATION_V3.md",
        },
        "horizons": horizons_out,
        "history": history,
        "fan": fan,
        "ensemble": ensemble,
        "learning": {
            "matured": matured,
            "miss_by_year": backtest.get("miss_by_year") or [],
            "backtest_horizons": backtest.get("horizons") or [],
            "ledger_started": ledger.get("started"),
            "ledger_updated_at": ledger.get("updated_at"),
            "ledger_note": (
                "Live ledger scores 30d/90d/1y/2y/4y; other cards are scenario-labeled. "
                "Closed-loop applies shrunk ledger bias when matured sample clears min_n."
            ),
            "correction": {
                "status": correction.get("status"),
                "n_matured_total": correction.get("n_matured_total"),
                "pooled": correction.get("pooled"),
                "horizons": correction.get("horizons"),
                "note_path": "pipeline/notes/FORECAST_CLOSED_LOOP_V1.md",
            },
        },
    }
    forbidden = ("holdings", "wallet", "balance", "email", "gmail", "btc_stack", "icansquat")
    blob = json.dumps(doc).lower()
    for needle in forbidden:
        if needle in blob:
            raise SystemExit(f"refusing to write scenario_forecast.json: forbidden token {needle!r}")
    return doc


def write(doc: dict) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write("\n")
    tmp.replace(OUT_PATH)
    if SCHEMA_PATH.exists():
        import jsonschema

        with SCHEMA_PATH.open() as f:
            schema = json.load(f)
        jsonschema.validate(doc, schema)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    doc = build()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "as_of": doc["as_of"],
                    "method_family": doc["method"]["family"],
                    "ar1": doc["method"]["ar1"],
                    "conformal_knots": doc["method"]["width"]["conformal"],
                    "horizons": [
                        {
                            "id": h["id"],
                            "p10": h["p10"],
                            "p50": h["p50"],
                            "p90": h["p90"],
                            "log_width": h["log_width"],
                            "band_source": h["band_source"],
                            "in_live_ledger": h["in_live_ledger"],
                        }
                        for h in doc["horizons"]
                    ],
                },
                indent=2,
            )
        )
        return
    write(doc)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
