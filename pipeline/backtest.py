"""Walk-forward backtest + live forecast ledger (Phase A, 2026-10-01).

Two different kinds of evidence, kept deliberately separate:

1. data/backtest.json -- a WALK-FORWARD replay. On the first of every month
   since `origin_start`, the exact model the site publishes is refit using
   only data available before that date, and its forecast for 1/2/4 years
   later is scored against what price actually did. This is a simulation
   (the method existed then; nobody was publishing it), recomputed nightly.

2. data/forecasts.json -- a LIVE LEDGER. Once per calendar month the site's
   real, current forecast is frozen and appended; nothing in it is ever
   rewritten except an entry's `outcome` filling in once its target date
   arrives. This is the record that cannot be fit after the fact.

Run after fit_models: `python -m pipeline.backtest`.
"""

from __future__ import annotations

import bisect
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import numpy as np

from pipeline import forecast
from pipeline.fit_models import honesty_constants

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
HASHRATE_PATH = REPO_ROOT / "data" / "history" / "hashrate_daily.json"
FNG_PATH = REPO_ROOT / "data" / "history" / "fng_daily.json"
MODELS_PATH = REPO_ROOT / "data" / "models.json"
CONSTANTS_PATH = REPO_ROOT / "pipeline" / "model_constants.json"
BACKTEST_OUT_PATH = REPO_ROOT / "data" / "backtest.json"
LEDGER_PATH = REPO_ROOT / "data" / "forecasts.json"
SCHEMAS_DIR = REPO_ROOT / "pipeline" / "schemas"

HORIZONS = [365, 730, 1461]
SHORT_HORIZONS = [90, 180, 365]
ORIGIN_START = date(2013, 1, 1)
MIN_FIT_POINTS = 365
Z_BUCKETS = [(-99.0, -1.0), (-1.0, -0.5), (-0.5, 0.0), (0.0, 0.5), (0.5, 1.0), (1.0, 99.0)]
EPISODE_GAP_DAYS = 90


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write("\n")


def validate(document: dict, schema_name: str) -> None:
    with open(SCHEMAS_DIR / f"{schema_name}.schema.json") as f:
        jsonschema.validate(document, json.load(f))


def _r(v: float | None, n: int = 4) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


class Series:
    """Price history as aligned numpy arrays (carried-forward rows excluded,
    same rule as fit_models)."""

    def __init__(self, rows: list[dict], genesis: date, fit_start: date):
        rows = [r for r in rows if not r.get("carried_forward") and date.fromisoformat(r["date"]) >= fit_start]
        self.dates = [date.fromisoformat(r["date"]) for r in rows]
        self.d = np.array([(dt - genesis).days for dt in self.dates], dtype=float)
        self.x = np.log10(self.d)
        self.y = np.log10(np.array([r["value"] for r in rows], dtype=float))
        self.genesis = genesis

    def n_before(self, origin: date) -> int:
        return bisect.bisect_left(self.dates, origin)

    def index_on_or_after(self, target: date) -> int | None:
        i = bisect.bisect_left(self.dates, target)
        if i >= len(self.dates) or (self.dates[i] - target).days > 7:
            return None
        return i


def _mae_factor(errs: list[float]) -> float | None:
    return _r(10 ** float(np.mean(np.abs(errs))), 3) if errs else None


def _bias_pct(errs: list[float]) -> float | None:
    return _r((10 ** float(np.mean(errs)) - 1) * 100, 1) if errs else None


def walk_forward(s: Series, hc: dict) -> dict:
    outer_q = hc["band_quantiles"]["outer"]
    inner_q = hc["band_quantiles"]["inner"]
    origins = forecast.month_starts(ORIGIN_START, s.dates[-1])
    fits = {}
    for o in origins:
        n = s.n_before(o)
        if n < MIN_FIT_POINTS:
            continue
        a, b, sigma = forecast.ols_fit(s.x[:n], s.y[:n])
        resid = s.y[:n] - (a + b * s.x[:n])
        fits[o] = {
            "n": n,
            "a": a,
            "b": b,
            "sigma": sigma,
            "outer": forecast.residual_quantiles(resid, outer_q),
            "inner": forecast.residual_quantiles(resid, inner_q),
            "resid": resid,
        }

    horizons = []
    per_origin_1y = []
    for h in HORIZONS:
        e_trend, e_rw, n_in_outer, n_in_inner, n_in_g2, n_below, n_above = [], [], 0, 0, 0, 0, 0
        for o, f in fits.items():
            i1 = s.index_on_or_after(o + timedelta(days=h))
            if i1 is None:
                continue
            i0 = f["n"] - 1
            resid = s.y[i1] - (f["a"] + f["b"] * s.x[i1])
            e_trend.append(resid)
            e_rw.append(s.y[i1] - s.y[i0])
            n_in_outer += f["outer"][0] <= resid <= f["outer"][1]
            n_in_inner += f["inner"][0] <= resid <= f["inner"][1]
            n_in_g2 += abs(resid) <= 2 * f["sigma"]
            n_below += resid < f["outer"][0]
            n_above += resid > f["outer"][1]
            if h == 365:
                per_origin_1y.append(
                    {
                        "origin": o.isoformat(),
                        "target": s.dates[i1].isoformat(),
                        "trend": _r(10 ** (f["a"] + f["b"] * s.x[i1]), 2),
                        "actual": _r(10 ** s.y[i1], 2),
                        "miss_log10": _r(resid, 4),
                        "inside_outer": bool(f["outer"][0] <= resid <= f["outer"][1]),
                    }
                )
        n = len(e_trend)
        span_days = (s.dates[-1] - ORIGIN_START).days
        horizons.append(
            {
                "horizon_days": h,
                "n_origins": n,
                "approx_independent_windows": _r(max(span_days - h, 0) / h, 1),
                "trend_mae_factor": _mae_factor(e_trend),
                "trend_bias_pct": _bias_pct(e_trend),
                "random_walk_mae_factor": _mae_factor(e_rw),
                "coverage_outer": _r(n_in_outer / n) if n else None,
                "coverage_inner": _r(n_in_inner / n) if n else None,
                "coverage_gaussian_2sigma": _r(n_in_g2 / n) if n else None,
                "share_below_outer": _r(n_below / n) if n else None,
                "share_above_outer": _r(n_above / n) if n else None,
            }
        )

    miss_by_year: dict[int, list[float]] = {}
    for row in per_origin_1y:
        miss_by_year.setdefault(int(row["origin"][:4]), []).append(row["miss_log10"])
    by_year = [
        {"year": yr, "n": len(v), "mae_factor": _mae_factor(v), "bias_pct": _bias_pct(v)}
        for yr, v in sorted(miss_by_year.items())
    ]
    return {"fits": fits, "horizons": horizons, "per_origin_1y": per_origin_1y, "miss_by_year": by_year}


def short_term_eval(s: Series, fits: dict, sample_days: int) -> list[dict]:
    out = []
    for h in SHORT_HORIZONS:
        e_t, e_ar, e_rw, in68, in95 = [], [], [], 0, 0
        for o, f in fits.items():
            i1 = s.index_on_or_after(o + timedelta(days=h))
            if i1 is None:
                continue
            i0 = f["n"] - 1
            phi, innov = forecast.ar1_residual(f["resid"], sample_days)
            steps = max(1, round(h / sample_days))
            mean, sd = forecast.ar1_path(float(f["resid"][-1]), phi, innov, steps)[-1]
            trend1 = f["a"] + f["b"] * s.x[i1]
            err_ar = s.y[i1] - (trend1 + mean)
            e_t.append(s.y[i1] - trend1)
            e_ar.append(err_ar)
            e_rw.append(s.y[i1] - s.y[i0])
            in68 += abs(err_ar) <= sd
            in95 += abs(err_ar) <= 1.96 * sd
        n = len(e_ar)
        out.append(
            {
                "horizon_days": h,
                "n_origins": n,
                "trend_mae_factor": _mae_factor(e_t),
                "trend_plus_ar1_mae_factor": _mae_factor(e_ar),
                "random_walk_mae_factor": _mae_factor(e_rw),
                "ar1_coverage_68": _r(in68 / n) if n else None,
                "ar1_coverage_95": _r(in95 / n) if n else None,
            }
        )
    return out


def scenario_eval(s: Series, scenario_start: date) -> list[dict]:
    """Recent-window fit vs full-history fit, scored on the same origins
    (only origins with >= 2 years of post-window data)."""
    first = date(scenario_start.year + 2, scenario_start.month, 1)
    start_i = s.n_before(scenario_start)
    out = []
    for h in HORIZONS:
        e_full, e_recent = [], []
        for o in forecast.month_starts(first, s.dates[-1]):
            n = s.n_before(o)
            i1 = s.index_on_or_after(o + timedelta(days=h))
            if i1 is None or n - start_i < 365:
                continue
            a, b, _ = forecast.ols_fit(s.x[:n], s.y[:n])
            ar, br, _ = forecast.ols_fit(s.x[start_i:n], s.y[start_i:n])
            e_full.append(s.y[i1] - (a + b * s.x[i1]))
            e_recent.append(s.y[i1] - (ar + br * s.x[i1]))
        out.append(
            {
                "horizon_days": h,
                "n_origins": len(e_full),
                "full_history_mae_factor": _mae_factor(e_full),
                "full_history_bias_pct": _bias_pct(e_full),
                "recent_window_mae_factor": _mae_factor(e_recent),
                "recent_window_bias_pct": _bias_pct(e_recent),
            }
        )
    return out


def _episodes(dates: list[date]) -> int:
    if not dates:
        return 0
    count = 1
    for prev, cur in zip(dates, dates[1:]):
        if (cur - prev).days > EPISODE_GAP_DAYS:
            count += 1
    return count


def _realtime_signals(s: Series, fits: dict, hashrate_rows: list[dict], fng_rows: list[dict]) -> dict:
    """Point-in-time signal values on each monthly origin, paired with the
    realised forward 1-year log return. Monthly sampling (not daily) so
    overlapping windows don't inflate n quite as badly -- still overlapping
    at 12:1, which is why `approx_independent_windows` travels with it."""
    hr_by_date = {r["date"]: r["value"] for r in hashrate_rows if r.get("value", 0) > 0}
    hr_dates = sorted(hr_by_date)
    fng_by_date = {r["date"]: r["value"] for r in fng_rows}
    price_by_date = {dt: 10 ** y for dt, y in zip(s.dates, s.y)}
    values: dict[str, list[tuple[date, float, float]]] = {
        "power_law_z_realtime": [],
        "mayer_multiple": [],
        "wma200_distance": [],
        "price_vs_hashrate_fit": [],
        "fear_greed": [],
    }
    for o, f in fits.items():
        # Origin-day reading (first row on/after the origin) scored against a
        # fit made strictly before it -- out-of-sample, the same convention
        # as fit_models' published realtime_z.
        i0 = s.index_on_or_after(o)
        i1 = s.index_on_or_after(o + timedelta(days=365))
        if i0 is None or i1 is None:
            continue
        fwd = float(s.y[i1] - s.y[i0])
        resid0 = s.y[i0] - (f["a"] + f["b"] * s.x[i0])
        z = float(resid0 / f["sigma"]) if f["sigma"] else 0.0
        values["power_law_z_realtime"].append((o, z, fwd))
        if i0 >= 199:
            sma = float(np.mean(10 ** s.y[i0 - 199 : i0 + 1]))
            values["mayer_multiple"].append((o, float(10 ** s.y[i0] / sma), fwd))
        if i0 >= 1399:
            wma = float(np.mean(10 ** s.y[i0 - 1399 : i0 + 1 : 7]))
            values["wma200_distance"].append((o, float(10 ** s.y[i0] / wma - 1), fwd))
        # price vs hashrate: log price regressed on log hashrate, fit only
        # on dates before the origin (no look-ahead), residual at the origin.
        hi = bisect.bisect_left(hr_dates, o.isoformat())
        if hi > 365:
            pairs = [(math.log10(hr_by_date[dd]), math.log10(price_by_date[date.fromisoformat(dd)])) for dd in hr_dates[:hi] if date.fromisoformat(dd) in price_by_date]
            if len(pairs) > 365:
                hx, py = np.array(pairs).T
                ha, hb, hs = forecast.ols_fit(hx, py)
                last_hr = hr_by_date[hr_dates[hi - 1]]
                resid = s.y[i0] - (ha + hb * math.log10(last_hr))
                values["price_vs_hashrate_fit"].append((o, float(resid / hs) if hs else 0.0, fwd))
        prev_day = (o - timedelta(days=1)).isoformat()
        if prev_day in fng_by_date:
            values["fear_greed"].append((o, float(fng_by_date[prev_day]), fwd))

    signals = []
    for name, rows in values.items():
        if len(rows) < 3:
            signals.append({"signal": name, "n_months": len(rows), "spearman_vs_fwd_1y": None, "approx_independent_windows": None})
            continue
        sig = np.array([r[1] for r in rows])
        fwd = np.array([r[2] for r in rows])
        span = (rows[-1][0] - rows[0][0]).days
        signals.append(
            {
                "signal": name,
                "n_months": len(rows),
                "first_origin": rows[0][0].isoformat(),
                "spearman_vs_fwd_1y": _r(forecast.spearman(sig, fwd), 3),
                "approx_independent_windows": _r(span / 365, 1),
            }
        )

    buckets = []
    zrows = values["power_law_z_realtime"]
    for lo, hi in Z_BUCKETS:
        sel = [r for r in zrows if lo <= r[1] < hi]
        if not sel:
            buckets.append({"z_low": lo, "z_high": hi, "n_months": 0, "episodes": 0, "median_fwd_1y_pct": None, "share_negative": None})
            continue
        fwd = np.array([r[2] for r in sel])
        buckets.append(
            {
                "z_low": lo,
                "z_high": hi,
                "n_months": len(sel),
                "episodes": _episodes([r[0] for r in sel]),
                "median_fwd_1y_pct": _r((10 ** float(np.median(fwd)) - 1) * 100, 1),
                "share_negative": _r(float(np.mean(fwd < 0)), 3),
            }
        )
    return {"signals": signals, "z_buckets": buckets}


def run_backtest(*, now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    constants = load_json(CONSTANTS_PATH)
    hc = honesty_constants(constants)
    pl = constants["power_law"]
    genesis = date.fromisoformat(pl["genesis_date"])
    s = Series(load_json(PRICE_PATH)["series"], genesis, date.fromisoformat(pl["fit_start_date"]))

    wf = walk_forward(s, hc)
    hashrate = (load_json(HASHRATE_PATH) or {}).get("series", [])
    fng = (load_json(FNG_PATH) or {}).get("series", [])
    sig = _realtime_signals(s, wf["fits"], hashrate, fng)

    document = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": s.dates[-1].isoformat(),
        "method": {
            "origins": f"first of every month from {ORIGIN_START.isoformat()}, refit on data strictly before each origin",
            "error_unit": "log10(actual / forecast); *_mae_factor = 10^mean|error| (x2.0 = typically off by a factor of 2 either way)",
            "caveat": "monthly origins overlap heavily -- approx_independent_windows is the honest sample size, and it is small.",
        },
        "horizons": wf["horizons"],
        "short_term": short_term_eval(s, wf["fits"], hc["short_term"]["sample_days"]),
        "scenario": scenario_eval(s, date.fromisoformat(hc["scenario_fit_start_date"])),
        "signals": sig["signals"],
        "z_buckets": sig["z_buckets"],
        "miss_by_year": wf["miss_by_year"],
        "per_origin_1y": wf["per_origin_1y"],
    }
    validate(document, "backtest")

    ledger = update_ledger(load_json(LEDGER_PATH), load_json(MODELS_PATH), s)
    validate(ledger, "forecasts")

    if not dry_run:
        write_json(BACKTEST_OUT_PATH, document)
        write_json(LEDGER_PATH, ledger)
    return {"backtest": document, "ledger": ledger}


# --------------------------------------------------------------------------
# Live ledger
# --------------------------------------------------------------------------


def update_ledger(ledger: dict | None, models: dict | None, s: Series) -> dict:
    """Append this month's frozen forecast (once per calendar month, keyed on
    the fit's own data date) and score any entry whose target has arrived.
    Existing entries' forecast numbers are never modified."""
    ledger = ledger or {"schema_version": 1, "entries": []}
    entries = ledger["entries"]

    if models and models.get("power_law"):
        pl = models["power_law"]
        issued = pl["current"]["date"]
        month = issued[:7]
        if not any(e["issued"][:7] == month for e in entries):
            a, b = pl["params"]["a"], pl["params"]["b"]
            outer = pl["bands"].get("outer_offsets_log10")
            inner = pl["bands"].get("inner_offsets_log10")
            if outer and inner:
                genesis = date.fromisoformat(pl["params"]["genesis_date"])
                issued_d = date.fromisoformat(issued)
                horizons = []
                for h in HORIZONS:
                    target = issued_d + timedelta(days=h)
                    t = a + b * math.log10((target - genesis).days)
                    horizons.append(
                        {
                            "horizon_days": h,
                            "target_date": target.isoformat(),
                            "trend": round(10**t, 2),
                            "inner_low": round(10 ** (t + inner[0]), 2),
                            "inner_high": round(10 ** (t + inner[1]), 2),
                            "floor": round(10 ** (t + outer[0]), 2),
                            "ceiling": round(10 ** (t + outer[1]), 2),
                            "outcome": None,
                        }
                    )
                entries.append(
                    {
                        "issued": issued,
                        "price_at_issue": pl["current"]["price"],
                        "a": a,
                        "b": b,
                        "horizons": horizons,
                    }
                )

    for e in entries:
        for hz in e["horizons"]:
            if hz["outcome"] is not None:
                continue
            target = date.fromisoformat(hz["target_date"])
            if target > s.dates[-1]:
                continue
            # First REAL row on/after the target, with no proximity cap: a
            # target that lands in an outage is scored on the first genuine
            # price after it (actual_date records which day), instead of
            # staying "due" forever.
            i = bisect.bisect_left(s.dates, target)
            if i >= len(s.dates):
                continue
            actual = float(10 ** s.y[i])
            hz["outcome"] = {
                "actual_date": s.dates[i].isoformat(),
                "actual": round(actual, 2),
                "miss_factor": round(actual / hz["trend"], 4),
                "inside_inner": hz["inner_low"] <= actual <= hz["inner_high"],
                "inside_outer": hz["floor"] <= actual <= hz["ceiling"],
            }

    entries.sort(key=lambda e: e["issued"])
    ledger["started"] = entries[0]["issued"] if entries else None
    ledger["updated_at"] = s.dates[-1].isoformat()
    return ledger


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    out = run_backtest(dry_run=args.dry_run)
    for h in out["backtest"]["horizons"]:
        print(
            f"{h['horizon_days']:>5}d: trend x{h['trend_mae_factor']} vs random walk x{h['random_walk_mae_factor']} "
            f"| outer coverage {h['coverage_outer']} inner {h['coverage_inner']} (n={h['n_origins']})"
        )
    print(f"ledger entries: {len(out['ledger']['entries'])}")


if __name__ == "__main__":
    main()
