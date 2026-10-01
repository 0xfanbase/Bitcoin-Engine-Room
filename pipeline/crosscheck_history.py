"""Independent cross-check of committed price history (Phase F, 2026-10-01).

`price_daily.json` is ~99% blockchain.info-sourced (Coin Metrics is 401'd,
see CLAUDE.md Section 3). This compares every committed day against
Bitstamp's daily candle for the same UTC date and writes
data/history_crosscheck.json.

The test is RANGE containment, not close-to-close equality: blockchain.info's
daily "market-price" is not a UTC close, so on a crash day it can sit far
from Bitstamp's close while still being a price that genuinely traded that
day. A day counts as a disagreement only if our value lies outside
Bitstamp's [low, high] widened by RANGE_TOLERANCE.

The early, illiquid era carries the most leverage on the power-law slope,
so the report also refits `b` with Bitstamp closes substituted for every
disagreeing day -- how much the headline model depends on the contested
data.

Runs at most every REFRESH_DAYS (polite: ~6 requests per run). Network
failure is not fatal: the previous report stays, and audit.py flags it once
it is older than the audit's own staleness window.

Run: `python -m pipeline.crosscheck_history [--force]`.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import jsonschema
import numpy as np
import requests

from pipeline import forecast
from pipeline.sources import BitstampClient, SourceFetchError

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_PATH = REPO_ROOT / "data" / "history" / "price_daily.json"
CONSTANTS_PATH = REPO_ROOT / "pipeline" / "model_constants.json"
OUT_PATH = REPO_ROOT / "data" / "history_crosscheck.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "history_crosscheck.schema.json"

BITSTAMP_START = date(2011, 8, 1)
RANGE_TOLERANCE = 0.03
REFRESH_DAYS = 28


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def compare(price_rows: list[dict], ref_rows: list[dict]) -> dict:
    ours = {r["date"]: r["value"] for r in price_rows if not r.get("carried_forward")}
    by_year: dict[str, dict] = {}
    disagreements = []
    for ref in ref_rows:
        value = ours.get(ref["date"])
        if value is None:
            continue
        year = by_year.setdefault(ref["date"][:4], {"days": 0, "outside": 0, "devs": []})
        year["days"] += 1
        if ref["close"] > 0:
            year["devs"].append(abs(value / ref["close"] - 1))
        lo, hi = ref["low"] * (1 - RANGE_TOLERANCE), ref["high"] * (1 + RANGE_TOLERANCE)
        if not lo <= value <= hi:
            year["outside"] += 1
            disagreements.append({"date": ref["date"], "ours": value, "bitstamp_low": ref["low"], "bitstamp_high": ref["high"], "bitstamp_close": ref["close"]})
    years = [
        {
            "year": int(y),
            "days_compared": v["days"],
            "outside_range": v["outside"],
            "outside_share": round(v["outside"] / v["days"], 4) if v["days"] else 0.0,
            "median_abs_close_dev_pct": round(float(np.median(v["devs"])) * 100, 2) if v["devs"] else None,
        }
        for y, v in sorted(by_year.items())
    ]
    return {"years": years, "disagreements": disagreements}


def fit_sensitivity(price_rows: list[dict], disagreements: list[dict], constants: dict) -> dict:
    pl = constants["power_law"]
    genesis = date.fromisoformat(pl["genesis_date"])
    fit_start = date.fromisoformat(pl["fit_start_date"])
    swap = {d["date"]: d["bitstamp_close"] for d in disagreements if d["bitstamp_close"] > 0}

    def b_of(rows):
        rows = [r for r in rows if not r.get("carried_forward") and date.fromisoformat(r["date"]) >= fit_start]
        x = np.log10([(date.fromisoformat(r["date"]) - genesis).days for r in rows])
        y = np.log10([r["value"] for r in rows])
        return forecast.ols_fit(x, y)

    a0, b0, _ = b_of(price_rows)
    a1, b1, _ = b_of([{**r, "value": swap.get(r["date"], r["value"])} for r in price_rows])
    d2030 = (date(2030, 1, 1) - genesis).days
    t0, t1 = 10 ** (a0 + b0 * math.log10(d2030)), 10 ** (a1 + b1 * math.log10(d2030))
    return {
        "days_substituted": len(swap),
        "b_committed": round(b0, 6),
        "b_with_bitstamp_substituted": round(b1, 6),
        "trend_2030_committed": round(t0, 2),
        "trend_2030_with_bitstamp_substituted": round(t1, 2),
        "trend_2030_change_pct": round((t1 / t0 - 1) * 100, 2),
    }


def build_report(price_rows: list[dict], ref_rows: list[dict], constants: dict, now: datetime) -> dict:
    result = compare(price_rows, ref_rows)
    return {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_source": BitstampClient.SOURCE_NAME,
        "method": f"committed daily price inside the reference day's [low, high] widened by {RANGE_TOLERANCE:.0%}",
        "range_tolerance": RANGE_TOLERANCE,
        "years": result["years"],
        "fit_sensitivity": fit_sensitivity(price_rows, result["disagreements"], constants),
        "largest_disagreements": sorted(
            result["disagreements"], key=lambda d: abs(math.log10(d["ours"] / d["bitstamp_close"])) if d["bitstamp_close"] > 0 else 0, reverse=True
        )[:10],
    }


def run(*, force: bool = False, now: datetime | None = None, client: BitstampClient | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    existing = load_json(OUT_PATH)
    if existing and not force:
        age = (now - datetime.strptime(existing["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
        if age < REFRESH_DAYS:
            print(f"history cross-check is {age}d old (< {REFRESH_DAYS}d) -- skipping")
            return None
    client = client or BitstampClient()
    start_ts = int(datetime(BITSTAMP_START.year, BITSTAMP_START.month, BITSTAMP_START.day, tzinfo=timezone.utc).timestamp())
    try:
        ref_rows = client.fetch_daily_ohlc(start_ts, int(now.timestamp()))
    except (SourceFetchError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
        # A 4xx (raise_for_status -> HTTPError), an HTML error page (JSON
        # decode -> ValueError) or a changed payload shape all mean "the
        # reference is unavailable today" -- never a reason to fail the job.
        print(f"history cross-check skipped: {exc!r}", file=sys.stderr)
        return None
    report = build_report(load_json(PRICE_PATH)["series"], ref_rows, load_json(CONSTANTS_PATH), now)
    jsonschema.validate(report, load_json(SCHEMA_PATH))
    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = run(force=args.force)
    if report:
        fs = report["fit_sensitivity"]
        bad = [y for y in report["years"] if y["outside_range"]]
        print(f"years with disagreements: {[(y['year'], y['outside_range']) for y in bad]}")
        print(f"b {fs['b_committed']} -> {fs['b_with_bitstamp_substituted']} with {fs['days_substituted']} days substituted (2030 trend {fs['trend_2030_change_pct']:+}%)")


if __name__ == "__main__":
    main()
