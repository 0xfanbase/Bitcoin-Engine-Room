"""Public scenario-forecast artifact for the Forecast section.

Built ONLY from Bitcoin-Engine-Room's own fitted power-law + residual
quantiles + AR(1) short-term dynamics + walk-forward coverage. Does NOT
ingest private btc-watcher JSON (holdings / out-of-allow-list sources).

v2 (2026-10-15 calibration):
  - P50 uses AR(1) mean-reversion of today's residual toward Cruise
    (same family as Models short_term path) — fixes 1y desync.
  - P10/P90 half-widths grow with sqrt(horizon years) and are inflated
    so walk-forward outer coverage at 1y meets a nominal 80% target when
    the raw empirical band undershoots (coverage calibration).
  - Instrument stack has NO decorative share weights.
  - Each horizon is labeled in_live_ledger for honesty vs forecasts.json.

Bands are empirical / calibrated envelopes, NOT confidence intervals.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from pipeline.forecast import ar1_path, ar1_residual, residual_quantiles

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "data" / "models.json"
PRICE_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
BACKTEST_PATH = REPO_ROOT / "data" / "backtest.json"
LEDGER_PATH = REPO_ROOT / "data" / "forecasts.json"
OUT_PATH = REPO_ROOT / "data" / "scenario_forecast.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "scenario_forecast.schema.json"

# Must match pipeline.backtest.HORIZONS for in_live_ledger honesty.
LEDGER_HORIZON_DAYS = {365, 730, 1461}

HORIZONS = [
    {"id": "1m", "label": "1 month", "days": 30},
    {"id": "1y", "label": "1 year", "days": 365},
    {"id": "2y", "label": "2 years", "days": 730},
    {"id": "4y", "label": "4 years", "days": 1461},
    {"id": "10y", "label": "10 years", "days": 3652},
    {"id": "30y", "label": "30 years", "days": 10957},
]

P_QUANTS = [0.10, 0.50, 0.90]
AR1_SAMPLE_DAYS = 30  # match fit_models honesty short_term.sample_days
# Nominal coverage for a P10–P90 envelope. Walk-forward outer (2.5–97.5)
# coverage is used as a proxy signal to inflate when bands run narrow.
TARGET_P10_P90_COVERAGE = 0.80
COVERAGE_INFLATE_CAP = 1.75


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
    """Match fit_models residual universe: all positive-price rows ≥ fit_start.
    (carried_forward rows are still prices; excluding them only on CF days
    would desync scenario quantiles from published Idle/Redline offsets.)"""
    resid = []
    for row in series:
        d = date.fromisoformat(row["date"])
        if d < fit_start:
            continue
        days = (d - genesis).days
        if days <= 1 or row.get("value") in (None, 0):
            continue
        y = math.log10(float(row["value"]))
        yhat = a + b * math.log10(days)
        resid.append(y - yhat)
    return np.asarray(resid, dtype=float)


def _coverage_inflate(backtest: dict) -> tuple[float, dict]:
    """Inflate log half-widths when 1y walk-forward outer coverage < target.

    Uses backtest outer-band coverage (Idle–Redline family) as a conservative
    proxy: if even the wider outer band undershoots in walk-forward, grow
    the published P10/P90 envelope. Never shrink below 1.0.
    """
    meta = {
        "target_coverage": TARGET_P10_P90_COVERAGE,
        "source_horizon_days": 365,
        "raw_coverage_outer": None,
        "inflate": 1.0,
    }
    hz = {h["horizon_days"]: h for h in backtest.get("horizons", [])}
    h1 = hz.get(365) or {}
    cov = h1.get("coverage_outer")
    meta["raw_coverage_outer"] = cov
    if cov is None or cov <= 0:
        return 1.0, meta
    if cov >= TARGET_P10_P90_COVERAGE:
        return 1.0, meta
    inflate = min(COVERAGE_INFLATE_CAP, TARGET_P10_P90_COVERAGE / cov)
    meta["inflate"] = round(inflate, 4)
    return inflate, meta


def _width_scale(days: int) -> float:
    """Horizon growth: 1.0 at 1y, sqrt scaling, floored so sub-year is not tiny."""
    years = max(days, 1) / 365.25
    return max(0.40, math.sqrt(years))


def _residual_mean_ar1(r0: float, phi: float, innov_sigma: float, days: int) -> tuple[float, float]:
    """AR(1) expected residual and sd at `days` ahead (sample step = 30d)."""
    steps = max(1, int(round(days / AR1_SAMPLE_DAYS)))
    path = ar1_path(r0, phi, innov_sigma, steps)
    mean, sd = path[-1]
    # Interpolate fractional step toward next if needed (mild)
    exact = days / AR1_SAMPLE_DAYS
    if steps >= 1 and abs(exact - steps) > 0.05 and phi > 0:
        mean = r0 * (phi**exact)
        if phi < 1.0:
            var = innov_sigma**2 * (1 - phi ** (2 * exact)) / (1 - phi**2)
        else:
            var = innov_sigma**2 * exact
        sd = math.sqrt(max(var, 0.0))
    return mean, sd


def build(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    models = _load(MODELS_PATH)
    price_doc = _load(PRICE_PATH)
    backtest = _load(BACKTEST_PATH) or {}
    ledger = _load(LEDGER_PATH) or {}
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
    base_lo = abs(q10 - q50)
    base_hi = abs(q90 - q50)

    trend_now = _price_at(last_days, a, b, 0.0)
    residual_now = math.log10(spot / trend_now) if spot > 0 and trend_now > 0 else 0.0
    # Prefer last residual from the same series the AR(1) is fit on
    r0 = float(resid[-1]) if len(resid) else residual_now
    phi, innov = ar1_residual(resid, AR1_SAMPLE_DAYS)
    inflate, cov_meta = _coverage_inflate(backtest)

    horizons_out = []
    for h in HORIZONS:
        target = as_of + timedelta(days=h["days"])
        d = last_days + h["days"]
        mean_r, sd_ar1 = _residual_mean_ar1(r0, phi, innov, h["days"])
        scale = _width_scale(h["days"]) * inflate
        half_lo = max(base_lo * scale, 1.2816 * sd_ar1 * 0.5)  # floor with mild AR noise
        half_hi = max(base_hi * scale, 1.2816 * sd_ar1 * 0.5)
        p50 = _price_at(d, a, b, mean_r)
        p10 = _price_at(d, a, b, mean_r - half_lo)
        p90 = _price_at(d, a, b, mean_r + half_hi)
        p10 = min(p10, p50)
        p90 = max(p90, p50)
        in_ledger = h["days"] in LEDGER_HORIZON_DAYS
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
                "width_scale": _r(scale, 4),
                "residual_mean": _r(mean_r, 6),
                "in_live_ledger": in_ledger,
                "caveat": None
                if in_ledger
                else (
                    "Scenario only — not frozen in the live monthly ledger "
                    "(ledger tracks 1y / 2y / 4y). Independent sample at this horizon is tiny."
                    if h["days"] >= 3652
                    else "Not frozen in the live monthly ledger (ledger tracks 1y / 2y / 4y)."
                ),
            }
        )

    # Fan: monthly to 10y + yearly markers; same AR1 + scaled width math
    fan = []
    for months in list(range(0, 121)) + [15 * 12, 20 * 12, 25 * 12, 30 * 12]:
        if months > 120 and months % 12:
            continue
        days = int(round(months * 365.25 / 12)) if months else 0
        if months in (15 * 12, 20 * 12, 25 * 12, 30 * 12):
            days = int(round((months // 12) * 365.25))
        d = last_days + max(days, 0)
        if days == 0:
            mean_r, sd_ar1 = r0, 0.0
            scale = 0.40 * inflate
        else:
            mean_r, sd_ar1 = _residual_mean_ar1(r0, phi, innov, days)
            scale = _width_scale(days) * inflate
        half_lo = max(base_lo * scale, 1.2816 * sd_ar1 * 0.5)
        half_hi = max(base_hi * scale, 1.2816 * sd_ar1 * 0.5)
        fan.append(
            {
                "date": (as_of + timedelta(days=days)).isoformat(),
                "p10": _r(_price_at(d, a, b, mean_r - half_lo), 2),
                "p50": _r(_price_at(d, a, b, mean_r), 2),
                "p90": _r(_price_at(d, a, b, mean_r + half_hi), 2),
                "cruise": _r(_price_at(d, a, b, 0.0), 2),
            }
        )
    # de-dupe fan dates (monthly loop + yearly markers)
    seen = set()
    fan_u = []
    for pt in fan:
        if pt["date"] in seen:
            continue
        seen.add(pt["date"])
        fan_u.append(pt)
    fan = fan_u

    cutoff = as_of - timedelta(days=8 * 365)
    weekly = {}
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
    # Instrument stack — roles + honesty only (no fake share weights)
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
            "id": "scaled_residual_bands",
            "name": "Scaled residual envelope",
            "role": "P10/P90 (√horizon × coverage inflate)",
            "trust_note": (
                f"1y outer coverage {h1.get('coverage_outer', '—')}; "
                f"inflate ×{cov_meta['inflate']} — not a CI"
            ),
        },
        {
            "id": "cycle_overlay",
            "name": "Halving-cycle overlay",
            "role": "phase context on Models only",
            "trust_note": "Not blended into Forecast cards (tiny cycle sample)",
        },
        {
            "id": "live_ledger",
            "name": "Monthly live ledger",
            "role": "scores 1y / 2y / 4y only",
            "trust_note": "Cards outside those horizons are labeled scenario-only",
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
        "schema_version": 2,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of.isoformat(),
        "spot": _r(spot, 2),
        "method": {
            "family": "power_law_ar1_scaled_quantiles",
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
                "rule": "half_width = empirical_|q-q50| * max(0.40, sqrt(years)) * coverage_inflate",
                "coverage_calibration": cov_meta,
            },
            "ledger_horizons_days": sorted(LEDGER_HORIZON_DAYS),
            "disclaimer": (
                "P50 mean-reverts today's residual toward Cruise via AR(1) (same family as Models "
                "short-term path). P10/P90 grow with √horizon and may widen when walk-forward "
                "coverage undershoots an 80% target. Scenario fans, not confidence intervals — "
                "see Track Record / backtest.json. Live ledger only freezes 1y/2y/4y."
            ),
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
            "ledger_note": "Live ledger scores 1y/2y/4y only; other cards are scenario-labeled.",
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
                    "coverage": doc["method"]["width"]["coverage_calibration"],
                    "horizons": [
                        {
                            "id": h["id"],
                            "p50": h["p50"],
                            "log_width": h["log_width"],
                            "in_live_ledger": h["in_live_ledger"],
                        }
                        for h in doc["horizons"]
                    ],
                    "bytes": len(json.dumps(doc)),
                },
                indent=2,
            )
        )
        return
    write(doc)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
