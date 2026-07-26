"""Model fitting (P4): power law corridor, 4-year cycle overlay, Mayer
Multiple, and 200-week moving average.

Refits daily from data/history/price_daily.json; writes data/models.json.
Methodology (fit window, sampling, weighting) is pinned in
pipeline/model_constants.json / MODEL_METHODOLOGY.md -- see those before
changing any formula here, and update both together if you do (a
methodology change is not a routine data update).

Explicitly skips Stock-to-Flow per spec Section 8.6.

Run as a module from the repo root: `python -m pipeline.fit_models`.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_HISTORY_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
MODEL_CONSTANTS_PATH = REPO_ROOT / "pipeline" / "model_constants.json"
MODELS_OUT_PATH = REPO_ROOT / "data" / "models.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "models.schema.json"

PROJECTION_YEARS = [2027, 2028, 2030, 2035]
MAYER_WINDOW_DAYS = 200
WMA_WINDOW_WEEKS = 200


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _days_since_genesis(d: date, genesis: date) -> int:
    return (d - genesis).days


# --------------------------------------------------------------------------
# Cycle tops (owner request 2026-07-26: a grounded counterweight to the
# corridor's long-run upward curve, without fitting a second, fragile model
# through only a handful of points -- rejected explicitly, see
# MODEL_METHODOLOGY.md and IMPROVEMENT_BACKLOG.md. Every point here is scored
# against the SAME power-law fit already used for Idle/Cruise/Redline; no
# independent curve is fit through the tops. Marker admission is a fixed,
# mechanical rule -- see compute_cycle_tops -- specifically so no one top gets
# hand-picked over another one an equally valid rule would also admit.)
# --------------------------------------------------------------------------


def identify_cycle_tops(price_series: list[dict], drawdown_confirm_pct: float, recovery_confirm_pct: float) -> list[dict]:
    """ZigZag swing detection over the full chronological price series.

    A running high is confirmed a cycle top once price later falls
    `drawdown_confirm_pct` from it; a running low is confirmed a bottom once
    price later rises `recovery_confirm_pct` from it (resuming the search for
    the next top). If the series ends still tracking an unconfirmed running
    high, that high is returned too, flagged `confirmed: False` -- the
    current cycle may not be over yet, and silently omitting it would hide
    exactly the kind of in-progress data point this site's transparency
    stance requires showing. Deliberately returns only `date`/`price`/
    `confirmed` -- era membership and any "how far has it fallen" figure are
    the caller's job (`compute_cycle_tops`), since this function has no idea
    what a "halving era" is and shouldn't need to.
    """
    if not price_series:
        return []

    tops: list[dict] = []
    seeking_top = True
    extreme = price_series[0]

    for row in price_series[1:]:
        if seeking_top:
            if row["value"] >= extreme["value"]:
                extreme = row
            elif 1.0 - row["value"] / extreme["value"] >= drawdown_confirm_pct:
                tops.append({"date": extreme["date"], "price": extreme["value"], "confirmed": True})
                seeking_top = False
                extreme = row
        else:
            if row["value"] <= extreme["value"]:
                extreme = row
            elif row["value"] / extreme["value"] - 1.0 >= recovery_confirm_pct:
                seeking_top = True
                extreme = row

    if seeking_top:
        tops.append({"date": extreme["date"], "price": extreme["value"], "confirmed": False})

    return tops


def _sigma_vs_trend(d: int, price: float, a: float, b: float, sigma: float) -> float:
    trend_log10 = a + b * math.log10(d)
    return (math.log10(price) - trend_log10) / sigma if sigma else 0.0


def _era_slices(price_series: list[dict], halving_dates: list[date]) -> list[tuple[date | None, date | None, list[dict]]]:
    """[(era_start|None, era_end|None, rows), ...], one slice per halving era
    plus the pre-first-halving era (None start) and the current, still-open
    era (None end) -- symmetrical with compute_cycle_overlay's own
    halving-anchored epochs, just extended backward to cover 2011's cycle top,
    which predates every halving and would otherwise belong to no era."""
    bounds = [None, *halving_dates, None]
    slices = []
    for era_start, era_end in zip(bounds, bounds[1:]):
        rows = [
            row
            for row in price_series
            if (era_start is None or _parse_date(row["date"]) >= era_start)
            and (era_end is None or _parse_date(row["date"]) < era_end)
        ]
        if rows:
            slices.append((era_start, era_end, rows))
    return slices


def compute_cycle_tops(price_series: list[dict], constants: dict, genesis: date, a: float, b: float, sigma: float) -> list[dict]:
    """Admits a marker only through a stated, mechanical rule -- never
    hand-picked (see MODEL_METHODOLOGY.md):

    (i) every drawdown/recovery-confirmed cycle top (`identify_cycle_tops`),
        kind "confirmed_top"; its unconfirmed remainder (the ZigZag
        algorithm's own currently-tracked running high, wherever it's dated)
        is kind "unconfirmed_top", or "current_era_high" specifically when
        that date falls in the still-open era -- these are NOT the same
        claim: an old, not-yet-70%-crashed high sitting in a closed era is a
        different fact than "the current era's own high so far."
    (ii) each halving era's own highest sigma-vs-trend excursion, when that's
         a date not already admitted by (i) for the same era;
    (iii) the current, still-open era's highest CLOSE (raw price, not sigma)
          to date, kind "current_era_high", when that's a date not already
          admitted above.

    Every point is scored against THIS SAME power-law fit's a/b/sigma -- no
    independent curve is fit through the tops. Where two rules disagree about
    the same era (e.g. the drawdown-confirmed top isn't also that era's peak
    sigma reading), BOTH points are kept: resolving that by fiat would be
    exactly the cherry-picking this method exists to avoid.

    Every entry with `confirmed: False` carries `drawdown_so_far_pct` --
    computed once, uniformly, here, regardless of which rule admitted it, so
    the frontend never has to guess whether a given kind happens to carry the
    field (a prior version attached it only on the ZigZag path, which could
    render "-undefined% so far" for an era-loop-admitted point).
    """
    pl = constants["power_law"]
    drawdown_pct = pl.get("cycle_top_drawdown_confirm_pct", 0.70)
    recovery_pct = pl.get("cycle_top_recovery_confirm_pct", 0.50)
    halving_dates = [_parse_date(d) for d in constants["cycle_overlay"]["halving_dates"]]
    era_slices = _era_slices(price_series, halving_dates)
    # The open era is always the LAST slice (_era_slices' bounds always end in
    # None) -- None here means "no lower bound", i.e. treat everything as
    # open if there are no eras at all (degenerate, empty-input case).
    open_era_start = era_slices[-1][0] if era_slices else None
    last_price = price_series[-1]["value"] if price_series else None

    def sigma_of(row: dict) -> float:
        d = _days_since_genesis(_parse_date(row["date"]), genesis)
        return _sigma_vs_trend(d, row["value"], a, b, sigma)

    def make_entry(row: dict, kind: str, confirmed: bool) -> dict:
        entry = {
            "date": row["date"],
            "price": row["value"],
            "sigma_vs_trend": round(sigma_of(row), 4),
            "kind": kind,
            "confirmed": confirmed,
        }
        if not confirmed and last_price is not None:
            entry["drawdown_so_far_pct"] = round((1.0 - last_price / row["value"]) * 100.0, 2)
        return entry

    by_date: dict[str, dict] = {}

    for top in identify_cycle_tops(price_series, drawdown_pct, recovery_pct):
        row = {"date": top["date"], "value": top["price"]}
        if top["confirmed"]:
            by_date[top["date"]] = make_entry(row, "confirmed_top", True)
        else:
            in_open_era = open_era_start is None or _parse_date(top["date"]) >= open_era_start
            by_date[top["date"]] = make_entry(row, "current_era_high" if in_open_era else "unconfirmed_top", False)

    for era_start, era_end, rows in era_slices:
        era_max_sigma_row = max(rows, key=sigma_of)
        if era_max_sigma_row["date"] not in by_date:
            by_date[era_max_sigma_row["date"]] = make_entry(era_max_sigma_row, "era_max_sigma", era_end is not None)

        if era_end is None:  # the current, open era
            era_max_price_row = max(rows, key=lambda r: r["value"])
            if era_max_price_row["date"] not in by_date:
                by_date[era_max_price_row["date"]] = make_entry(era_max_price_row, "current_era_high", False)

    return sorted(by_date.values(), key=lambda e: e["date"])


def cycle_top_era_maxima(price_series: list[dict], constants: dict, genesis: date, a: float, b: float, sigma: float) -> list[dict]:
    """Each halving era's own single highest sigma-vs-trend excursion, oldest
    first, current (open) era last -- `{date, sigma_vs_trend}`, not a bare
    number: site copy needs the DATE this value actually occurred on (e.g. to
    say "since 2011"), and that date is not reliably the same as any other
    entry's date (the era's confirmed top and its peak-sigma reading can be,
    and in the real committed history are, different days). Site copy claims
    each era topping lower than the last only when this sequence's
    `sigma_vs_trend` values are actually non-increasing -- an observed
    pattern in past data, not a rule, and nothing here assumes it holds; the
    frontend checks it fresh against whatever this returns."""
    halving_dates = [_parse_date(d) for d in constants["cycle_overlay"]["halving_dates"]]

    def sigma_of(row: dict) -> float:
        d = _days_since_genesis(_parse_date(row["date"]), genesis)
        return _sigma_vs_trend(d, row["value"], a, b, sigma)

    maxima = []
    for _era_start, _era_end, rows in _era_slices(price_series, halving_dates):
        row = max(rows, key=sigma_of)
        maxima.append({"date": row["date"], "sigma_vs_trend": round(sigma_of(row), 4)})
    return maxima


# --------------------------------------------------------------------------
# Power law corridor (spec Section 8.1)
# --------------------------------------------------------------------------


def fit_power_law(price_series: list[dict], constants: dict, previous_models: dict | None) -> dict:
    pl = constants["power_law"]
    genesis = _parse_date(pl["genesis_date"])
    fit_start = _parse_date(pl["fit_start_date"])

    fit_rows = [row for row in price_series if _parse_date(row["date"]) >= fit_start]
    days = np.array([_days_since_genesis(_parse_date(r["date"]), genesis) for r in fit_rows], dtype=float)
    prices = np.array([r["value"] for r in fit_rows], dtype=float)

    x = np.log10(days)
    y = np.log10(prices)

    b, a = np.polyfit(x, y, 1)  # y = b*x + a, matching log10(price) = a + b*log10(d)

    predicted = a + b * x
    residuals = y - predicted
    sigma = float(np.std(residuals, ddof=1))
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot

    last_row = fit_rows[-1]
    last_date = _parse_date(last_row["date"])
    last_d = _days_since_genesis(last_date, genesis)
    last_predicted_log10 = a + b * math.log10(last_d)
    last_actual_log10 = math.log10(last_row["value"])
    last_residual = last_actual_log10 - last_predicted_log10
    z_score = last_residual / sigma if sigma else 0.0
    trend_price = 10 ** last_predicted_log10
    deviation_pct = (last_row["value"] / trend_price - 1.0) * 100.0

    residual_percentile = float(np.mean(residuals <= last_residual) * 100.0)

    projections = []
    for year in PROJECTION_YEARS:
        target = date(year, 1, 1)
        d = _days_since_genesis(target, genesis)
        trend_log10 = a + b * math.log10(d)
        projections.append(
            {
                "date": target.isoformat(),
                "floor": round(10 ** (trend_log10 - 2 * sigma), 2),
                "trend": round(10**trend_log10, 2),
                "ceiling": round(10 ** (trend_log10 + 2 * sigma), 2),
            }
        )

    previous_params = None
    if previous_models and previous_models.get("power_law", {}).get("params"):
        previous_params = previous_models["power_law"]["params"]

    cycle_tops = compute_cycle_tops(price_series, constants, genesis, a, b, sigma)
    era_maxima = cycle_top_era_maxima(price_series, constants, genesis, a, b, sigma)

    return {
        "params": {
            "a": round(float(a), 6),
            "b": round(float(b), 6),
            "r_squared": round(r_squared, 6),
            "sigma": round(sigma, 6),
            "fit_start_date": fit_start.isoformat(),
            "genesis_date": genesis.isoformat(),
            "n_points": len(fit_rows),
        },
        "previous_params": previous_params,
        "current": {
            "date": last_date.isoformat(),
            "price": last_row["value"],
            "trend_price": round(trend_price, 2),
            "deviation_pct": round(deviation_pct, 2),
            "z_score": round(z_score, 4),
            "residual_percentile": round(residual_percentile, 2),
        },
        "bands": {
            "floor_label": "Idle",
            "trend_label": "Cruise",
            "ceiling_label": "Redline",
            "note": "floor/ceiling are trend * 10^(-/+ 2*sigma) in log10 space -- descriptive envelopes from historical fit residuals, not statistical confidence intervals (residuals are autocorrelated across multi-year cycles).",
        },
        "projections": projections,
        "cycle_tops": cycle_tops,
        "cycle_top_era_maxima_sigma": era_maxima,
    }


# --------------------------------------------------------------------------
# 4-year cycle overlay (spec Section 8.2)
# --------------------------------------------------------------------------


def _nearest_price_on_or_after(price_series: list[dict], target: date) -> dict | None:
    for row in price_series:
        if _parse_date(row["date"]) >= target:
            return row
    return None


def compute_cycle_overlay(price_series: list[dict], constants: dict) -> dict:
    halving_dates = [_parse_date(d) for d in constants["cycle_overlay"]["halving_dates"]]
    by_date = {row["date"]: row["value"] for row in price_series}
    last_date = _parse_date(price_series[-1]["date"])

    avg_epoch_days = 4 * 365.25  # calendar approximation; fit_models.py has no live block height

    epochs = []
    for i, halving_date in enumerate(halving_dates):
        epoch_end = halving_dates[i + 1] if i + 1 < len(halving_dates) else None
        anchor_row = _nearest_price_on_or_after(price_series, halving_date)
        if anchor_row is None:
            continue
        anchor_price = anchor_row["value"]

        days_list, pct_list = [], []
        for row in price_series:
            d = _parse_date(row["date"])
            if d < halving_date:
                continue
            if epoch_end is not None and d >= epoch_end:
                break
            days_since = (d - halving_date).days
            pct = (row["value"] / anchor_price - 1.0) * 100.0
            days_list.append(days_since)
            pct_list.append(round(pct, 2))

        epochs.append(
            {
                "halving_date": halving_date.isoformat(),
                "epoch_end_date": epoch_end.isoformat() if epoch_end else None,
                "anchor_price": anchor_price,
                "is_current": epoch_end is None,
                "days_since_halving": days_list,
                "pct_performance": pct_list,
            }
        )

    current_epoch_meta = None
    if epochs and epochs[-1]["is_current"]:
        current = epochs[-1]
        days_into_epoch = (last_date - _parse_date(current["halving_date"])).days
        pct_complete = round(min(days_into_epoch / avg_epoch_days, 1.0) * 100.0, 1)
        current_pct_performance = current["pct_performance"][-1] if current["pct_performance"] else None

        # Percentile-rank the current epoch's performance against the prior
        # completed epochs' performance at the SAME days-since-halving offset
        # (nearest available). Only 3 historical epochs exist -- crude by
        # design, matching spec's "toy composite, methodology published
        # inline" framing for anything derived from this.
        comparison_values = []
        for epoch in epochs[:-1]:
            offsets = epoch["days_since_halving"]
            if not offsets:
                continue
            nearest_idx = min(range(len(offsets)), key=lambda i: abs(offsets[i] - days_into_epoch))
            comparison_values.append(epoch["pct_performance"][nearest_idx])

        cycle_percentile = None
        if comparison_values and current_pct_performance is not None:
            cycle_percentile = round(
                sum(1 for v in comparison_values if v <= current_pct_performance) / len(comparison_values) * 100.0,
                1,
            )

        current_epoch_meta = {
            "halving_date": current["halving_date"],
            "days_into_epoch": days_into_epoch,
            "pct_complete_of_avg_epoch": pct_complete,
            "pct_performance": current_pct_performance,
            "cycle_percentile_vs_prior_epochs": cycle_percentile,
        }

    return {"epochs": epochs, "current_epoch": current_epoch_meta}


# --------------------------------------------------------------------------
# Mayer Multiple (spec Section 8.3)
# --------------------------------------------------------------------------


def compute_mayer_multiple(price_series: list[dict]) -> dict:
    values = [row["value"] for row in price_series]
    dates = [row["date"] for row in price_series]

    series = []
    for i in range(MAYER_WINDOW_DAYS - 1, len(values)):
        window = values[i - MAYER_WINDOW_DAYS + 1 : i + 1]
        sma = sum(window) / MAYER_WINDOW_DAYS
        multiple = values[i] / sma if sma else None
        if multiple is not None:
            series.append({"date": dates[i], "value": round(multiple, 4)})

    if not series:
        return {"current": None, "series": []}

    multiples = [row["value"] for row in series]
    current_multiple = multiples[-1]
    percentile = round(sum(1 for m in multiples if m <= current_multiple) / len(multiples) * 100.0, 1)

    return {
        "current": {
            "date": series[-1]["date"],
            "price": values[-1],
            "sma_200d": round(values[-1] / current_multiple, 2) if current_multiple else None,
            "multiple": current_multiple,
            "percentile": percentile,
        },
        "series": series,
    }


# --------------------------------------------------------------------------
# 200-Week Moving Average (spec Section 8.4)
# --------------------------------------------------------------------------


def _weekly_closes(price_series: list[dict]) -> list[dict]:
    """One row per ISO week: the last available daily close that week."""
    by_week: dict[tuple, dict] = {}
    for row in price_series:
        d = _parse_date(row["date"])
        iso_year, iso_week, _ = d.isocalendar()
        key = (iso_year, iso_week)
        by_week[key] = row  # later rows in the same week overwrite earlier ones
    return [by_week[k] for k in sorted(by_week)]


def compute_200wma(price_series: list[dict]) -> dict:
    weekly = _weekly_closes(price_series)
    values = [row["value"] for row in weekly]
    dates = [row["date"] for row in weekly]

    series = []
    for i in range(WMA_WINDOW_WEEKS - 1, len(values)):
        window = values[i - WMA_WINDOW_WEEKS + 1 : i + 1]
        wma = sum(window) / WMA_WINDOW_WEEKS
        distance_pct = (values[i] / wma - 1.0) * 100.0 if wma else None
        series.append({"date": dates[i], "price": values[i], "wma_200w": round(wma, 2), "distance_pct": round(distance_pct, 2)})

    if not series:
        return {"current": None, "series": []}

    return {"current": series[-1], "series": series}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_fit(*, dry_run: bool = False) -> dict:
    price_doc = load_json(PRICE_HISTORY_PATH)
    # carried_forward rows are a repeat of the last real observation, written
    # under today's date during an outage (fetch_snapshot.py) -- real for
    # display (never blank a gauge), but not a genuine second data point.
    # Filtered once here, at the source, so every model derived from
    # price_series (power law, cycle overlay, Mayer, 200WMA) counts only
    # real observations instead of quietly inflating n_points with repeats.
    price_series = [row for row in price_doc["series"] if not row.get("carried_forward")]
    constants = load_json(MODEL_CONSTANTS_PATH)
    previous_models = load_json(MODELS_OUT_PATH) if MODELS_OUT_PATH.exists() else None

    power_law = fit_power_law(price_series, constants, previous_models)
    cycle_overlay = compute_cycle_overlay(price_series, constants)
    mayer_multiple = compute_mayer_multiple(price_series)
    wma_200 = compute_200wma(price_series)

    document = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "power_law": power_law,
        "cycle_overlay": cycle_overlay,
        "mayer_multiple": mayer_multiple,
        "wma_200": wma_200,
    }

    if not dry_run:
        write_json(MODELS_OUT_PATH, document)

    return document


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    document = run_fit(dry_run=args.dry_run)
    pl = document["power_law"]["params"]
    print(f"power_law: a={pl['a']} b={pl['b']} r2={pl['r_squared']} sigma={pl['sigma']} n={pl['n_points']}")
    print(f"current deviation: {document['power_law']['current']['deviation_pct']}% (z={document['power_law']['current']['z_score']})")


if __name__ == "__main__":
    main()
