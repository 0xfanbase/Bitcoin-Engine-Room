"""Public scenario-forecast artifact for the Forecast section (2026-10-15).

Built ONLY from Bitcoin-Engine-Room's own fitted power-law + residual
quantiles + walk-forward ledger/backtest. Does NOT ingest btc-watcher
JSON/CSV (those carry private holdings and out-of-allow-list sources).

Outputs data/scenario_forecast.json: horizon P10/P50/P90 cards, a slim
forward fan, an instrument "ensemble" table (roles + honesty metrics —
not opaque ML weights), and matured-call / miss summaries.

Bands are empirical residual envelopes, NOT confidence intervals.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from pipeline.forecast import residual_quantiles

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "data" / "models.json"
PRICE_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
BACKTEST_PATH = REPO_ROOT / "data" / "backtest.json"
LEDGER_PATH = REPO_ROOT / "data" / "forecasts.json"
OUT_PATH = REPO_ROOT / "data" / "scenario_forecast.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "scenario_forecast.schema.json"

# Public card set — watcher-inspired horizons, BER math.
HORIZONS = [
    {"id": "1m", "label": "1 month", "days": 30},
    {"id": "1y", "label": "1 year", "days": 365},
    {"id": "4y", "label": "4 years", "days": 1461},
    {"id": "10y", "label": "10 years", "days": 3652},
    {"id": "30y", "label": "30 years", "days": 10957},
]

# P10/P50/P90 residual quantiles (log10). P50 uses the fitted trend
# (quantile of residuals near 0 is reported separately as honesty).
P_QUANTS = [0.10, 0.50, 0.90]


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
    return 10 ** (a + b * math.log10(d) + offset)


def _build_residuals(series: list[dict], genesis: date, a: float, b: float, fit_start: date) -> np.ndarray:
    resid = []
    for row in series:
        if row.get("carried_forward"):
            continue
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

    # Anchored median: keep today's residual vs Cruise. Models chart still
    # shows fair-value Cruise; Forecast answers "paths from here".
    trend_now = _price_at(last_days, a, b, 0.0)
    residual_now = math.log10(spot / trend_now) if spot > 0 and trend_now > 0 else 0.0
    half_lo = abs(q10 - q50)
    half_hi = abs(q90 - q50)

    horizons_out = []
    for h in HORIZONS:
        target = as_of + timedelta(days=h["days"])
        d = last_days + h["days"]
        p50 = _price_at(d, a, b, residual_now)
        p10 = _price_at(d, a, b, residual_now - half_lo)
        p90 = _price_at(d, a, b, residual_now + half_hi)
        p10 = min(p10, p50)
        p90 = max(p90, p50)
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
            }
        )

    fan = []
    for months in range(0, 121):
        days = int(round(months * 365.25 / 12))
        d = last_days + days
        mid = residual_now
        fan.append(
            {
                "date": (as_of + timedelta(days=days)).isoformat(),
                "p10": _r(_price_at(d, a, b, mid - half_lo), 2),
                "p50": _r(_price_at(d, a, b, mid), 2),
                "p90": _r(_price_at(d, a, b, mid + half_hi), 2),
                "cruise": _r(_price_at(d, a, b, 0.0), 2),
            }
        )
    for years in (15, 20, 25, 30):
        days = int(round(years * 365.25))
        d = last_days + days
        mid = residual_now
        fan.append(
            {
                "date": (as_of + timedelta(days=days)).isoformat(),
                "p10": _r(_price_at(d, a, b, mid - half_lo), 2),
                "p50": _r(_price_at(d, a, b, mid), 2),
                "p90": _r(_price_at(d, a, b, mid + half_hi), 2),
                "cruise": _r(_price_at(d, a, b, 0.0), 2),
            }
        )

    # History spine for the chart (weekly closes, last ~8y) — public price only
    hist = []
    cutoff = as_of - timedelta(days=8 * 365)
    by_week: dict[str, float] = {}
    for row in series:
        if row.get("carried_forward") or not row.get("value"):
            continue
        d = date.fromisoformat(row["date"])
        if d < cutoff:
            continue
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        by_week[key] = float(row["value"])
        # keep last date per week via overwrite; also store date
        hist.append({"date": row["date"], "price": _r(float(row["value"]), 2), "_w": key})
    # collapse to one point per week (last)
    weekly = {}
    for pt in hist:
        weekly[pt["_w"]] = {"date": pt["date"], "price": pt["price"]}
    history = sorted(weekly.values(), key=lambda x: x["date"])

    # Ensemble = instrument stack with honesty metrics (not opaque weights)
    hz = {h["horizon_days"]: h for h in backtest.get("horizons", [])}
    h1 = hz.get(365) or {}
    ensemble = [
        {
            "id": "power_law",
            "name": "Power law Cruise",
            "role": "backbone trend",
            "trust_note": f"R²={params.get('r_squared')}; walk-forward 1y typical miss ×{h1.get('trend_mae_factor', '—')}",
            "share": 0.45,
        },
        {
            "id": "residual_bands",
            "name": "Empirical residual bands",
            "role": "P10/P90 envelope",
            "trust_note": f"1y outer coverage {h1.get('coverage_outer', '—')} (not a CI)",
            "share": 0.25,
        },
        {
            "id": "cycle_overlay",
            "name": "Halving-cycle overlay",
            "role": "phase context",
            "trust_note": "shape rhyme across cycles — tiny sample",
            "share": 0.15,
        },
        {
            "id": "short_term",
            "name": "Short-term AR(1) path",
            "role": "near-term mean reversion",
            "trust_note": "weeks-to-months only; see Models short-term path",
            "share": 0.15,
        },
    ]

    # Matured ledger entries (outcome filled) + miss-by-year from backtest
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
    miss_by_year = backtest.get("miss_by_year") or []

    doc = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of.isoformat(),
        "spot": _r(spot, 2),
        "method": {
            "family": "power_law_empirical_quantiles",
            "a": a,
            "b": b,
            "genesis_date": genesis.isoformat(),
            "fit_start_date": fit_start.isoformat(),
            "residual_quantiles": {"p10": _r(q10, 6), "p50": _r(q50, 6), "p90": _r(q90, 6)},
            "n_residuals": int(len(resid)),
            "disclaimer": (
                "P50 is Cruise anchored at today's spot (today's log residual held). "
                "P10/P90 widen that path by historical 10th/90th residual spreads. "
                "Scenario fans, not confidence intervals — see Track Record / backtest.json."
            ),
            "anchored_residual_log10": round(residual_now, 6),
        },
        "horizons": horizons_out,
        "history": history,
        "fan": fan,
        "ensemble": ensemble,
        "learning": {
            "matured": matured,
            "miss_by_year": miss_by_year,
            "backtest_horizons": backtest.get("horizons") or [],
            "ledger_started": ledger.get("started"),
            "ledger_updated_at": ledger.get("updated_at"),
        },
    }
    # Hard privacy scrub — never allow these keys even if a future edit adds them
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
        print(json.dumps({"as_of": doc["as_of"], "horizons": doc["horizons"], "bytes": len(json.dumps(doc))}, indent=2))
        return
    write(doc)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
