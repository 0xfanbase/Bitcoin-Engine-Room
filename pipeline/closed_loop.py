"""Closed-loop forecast correction from the live ledger (v1, 2026-10-16).

Matured published forecasts in data/forecasts.json produce a shrunk, capped
log10 bias and optional band widen applied by scenario_forecast.build().
Does not rewrite frozen ledger forecast numbers — outcomes only.

See pipeline/notes/FORECAST_CLOSED_LOOP_V1.md.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = REPO_ROOT / "data" / "forecasts.json"
OUT_PATH = REPO_ROOT / "data" / "forecast_correction.json"
SCHEMA_PATH = REPO_ROOT / "pipeline" / "schemas" / "forecast_correction.schema.json"
NOTE_PATH = "pipeline/notes/FORECAST_CLOSED_LOOP_V1.md"

# Shrinkage / safety (documented in the note)
PRIOR_STRENGTH = 12.0  # κ in n/(n+κ)
MIN_N_HORIZON = 3
MIN_N_POOLED = 4
MAX_BIAS_ABS = 0.10  # log10 ≈ ×1.26
MAX_WIDEN = 0.08  # log10 half-width add
OUTER_MISS_TARGET = 0.20  # widen only if miss rate exceeds this
# Horizons that receive a direct correction (≤4y family)
CORRECTABLE_DAYS = (30, 90, 180, 365, 730, 1461)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _r(v: float | None, n: int = 6) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


def collect_matured_errors(ledger: dict | None) -> list[dict]:
    """Published trend vs outcome → log10 error rows (ledger only)."""
    out: list[dict] = []
    if not ledger:
        return out
    for entry in ledger.get("entries") or []:
        issued = entry.get("issued")
        for hz in entry.get("horizons") or []:
            outcome = hz.get("outcome")
            if outcome is None:
                continue
            trend = hz.get("trend")
            actual = outcome.get("actual") if isinstance(outcome, dict) else None
            if trend in (None, 0) or actual in (None, 0):
                continue
            if float(trend) <= 0 or float(actual) <= 0:
                continue
            log_err = math.log10(float(actual)) - math.log10(float(trend))
            out.append(
                {
                    "issued": issued,
                    "horizon_days": int(hz["horizon_days"]),
                    "target_date": hz.get("target_date"),
                    "trend": float(trend),
                    "actual": float(actual),
                    "log_err": log_err,
                    "miss_factor": float(outcome.get("miss_factor") or (actual / trend)),
                    "inside_outer": bool(outcome.get("inside_outer")),
                    "inside_inner": bool(outcome.get("inside_inner")),
                }
            )
    return out


def _shrink_mean(errs: list[float], *, kappa: float, max_abs: float) -> float:
    if not errs:
        return 0.0
    n = len(errs)
    raw = sum(errs) / n
    shrunk = (n / (n + kappa)) * raw
    return float(max(-max_abs, min(max_abs, shrunk)))


def _widen_from_misses(inside_flags: list[bool], *, min_n: int) -> float:
    if len(inside_flags) < min_n:
        return 0.0
    miss_rate = 1.0 - (sum(1 for x in inside_flags if x) / len(inside_flags))
    if miss_rate <= OUTER_MISS_TARGET:
        return 0.0
    return float(min(MAX_WIDEN, max(0.0, miss_rate - OUTER_MISS_TARGET)))


def estimate_correction(
    matured: list[dict],
    *,
    kappa: float = PRIOR_STRENGTH,
    min_n_horizon: int = MIN_N_HORIZON,
    min_n_pooled: int = MIN_N_POOLED,
    max_bias_abs: float = MAX_BIAS_ABS,
) -> dict:
    """Build per-horizon bias/widen with shrinkage, caps, and pooled fallback."""
    by_h: dict[int, list[dict]] = {}
    for row in matured:
        by_h.setdefault(int(row["horizon_days"]), []).append(row)

    pooled_errs = [r["log_err"] for r in matured if r["horizon_days"] in CORRECTABLE_DAYS]
    pooled_inside = [
        r["inside_outer"] for r in matured if r["horizon_days"] in CORRECTABLE_DAYS
    ]
    pooled_n = len(pooled_errs)
    pooled_bias = (
        _shrink_mean(pooled_errs, kappa=kappa, max_abs=max_bias_abs)
        if pooled_n >= min_n_pooled
        else 0.0
    )
    pooled_widen = _widen_from_misses(pooled_inside, min_n=min_n_pooled)

    horizons: dict[str, dict] = {}
    active_any = False
    for days in CORRECTABLE_DAYS:
        rows = by_h.get(days) or []
        n = len(rows)
        errs = [r["log_err"] for r in rows]
        insides = [r["inside_outer"] for r in rows]
        if n >= min_n_horizon:
            bias = _shrink_mean(errs, kappa=kappa, max_abs=max_bias_abs)
            widen = _widen_from_misses(insides, min_n=min_n_horizon)
            source = "ledger_horizon"
            active = True
            active_any = True
        elif pooled_n >= min_n_pooled and days in CORRECTABLE_DAYS:
            # Weak prior: half-strength pooled bias; full pooled widen is too aggressive
            bias = 0.5 * pooled_bias
            widen = 0.5 * pooled_widen
            source = "ledger_pooled_prior"
            active = abs(bias) > 1e-12 or widen > 1e-12
            if active:
                active_any = True
        else:
            bias, widen, source, active = 0.0, 0.0, "identity", False

        raw_mean = (sum(errs) / n) if n else None
        miss_rate = (
            1.0 - (sum(1 for x in insides if x) / n) if n else None
        )
        horizons[str(days)] = {
            "horizon_days": days,
            "n": n,
            "bias_log10": _r(bias, 6),
            "widen_log10": _r(widen, 6),
            "raw_mean_log_err": _r(raw_mean, 6) if raw_mean is not None else None,
            "outer_miss_rate": _r(miss_rate, 4) if miss_rate is not None else None,
            "source": source,
            "active": active,
        }

    if active_any:
        status = "active"
    elif matured:
        status = "inactive_below_min_n"
    else:
        status = "inactive_waiting_for_matured"

    return {
        "status": status,
        "kappa": kappa,
        "min_n_horizon": min_n_horizon,
        "min_n_pooled": min_n_pooled,
        "max_bias_abs": max_bias_abs,
        "max_widen": MAX_WIDEN,
        "outer_miss_target": OUTER_MISS_TARGET,
        "pooled": {
            "n": pooled_n,
            "bias_log10": _r(pooled_bias, 6),
            "widen_log10": _r(pooled_widen, 6),
        },
        "horizons": horizons,
        "n_matured_total": len(matured),
    }


def bias_widen_for_days(correction: dict, days: int) -> tuple[float, float, str]:
    """Interpolate bias/widen across correctable knots (log-day), else 0."""
    hz = correction.get("horizons") or {}
    knots = sorted(int(k) for k in hz.keys())
    if not knots or correction.get("status") == "inactive_waiting_for_matured":
        return 0.0, 0.0, "identity"

    def _bw(d: int) -> tuple[float, float, str]:
        row = hz.get(str(d)) or {}
        return (
            float(row.get("bias_log10") or 0.0),
            float(row.get("widen_log10") or 0.0),
            str(row.get("source") or "identity"),
        )

    if days in hz or str(days) in hz:
        b, w, src = _bw(days)
        return b, w, src
    if days <= knots[0]:
        return _bw(knots[0])
    if days >= knots[-1]:
        return _bw(knots[-1])
    for i in range(len(knots) - 1):
        d0, d1 = knots[i], knots[i + 1]
        if d0 <= days <= d1:
            b0, w0, _ = _bw(d0)
            b1, w1, _ = _bw(d1)
            t = (math.log(days) - math.log(d0)) / (math.log(d1) - math.log(d0))
            return b0 + t * (b1 - b0), w0 + t * (w1 - w0), f"interp_{d0}_{d1}"
    return _bw(knots[-1])


def build_correction(
    ledger: dict | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    ledger = ledger if ledger is not None else _load(LEDGER_PATH)
    matured = collect_matured_errors(ledger)
    est = estimate_correction(matured)
    matured_digest = [
        {
            "issued": m["issued"],
            "horizon_days": m["horizon_days"],
            "target_date": m["target_date"],
            "log_err": _r(m["log_err"], 6),
            "inside_outer": m["inside_outer"],
        }
        for m in matured
    ]
    doc = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note_path": NOTE_PATH,
        "ledger_started": (ledger or {}).get("started"),
        "ledger_updated_at": (ledger or {}).get("updated_at"),
        "ledger_entries": len((ledger or {}).get("entries") or []),
        **est,
        "matured": matured_digest,
        "disclaimer": (
            "Shrunk ledger bias / optional band widen from matured published "
            "forecasts only. Not a confidence-interval recalibration. Identity "
            "when the matured sample is below min_n."
        ),
    }
    forbidden = ("holdings", "wallet", "balance", "email", "gmail", "btc_stack", "icansquat")
    blob = json.dumps(doc).lower()
    for needle in forbidden:
        if needle in blob:
            raise SystemExit(f"refusing to write forecast_correction.json: forbidden token {needle!r}")
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
    doc = build_correction()
    if args.dry_run:
        print(json.dumps({k: doc[k] for k in ("status", "pooled", "horizons", "n_matured_total")}, indent=2))
        return
    write(doc)
    print(f"wrote {OUT_PATH} status={doc['status']} matured={doc['n_matured_total']}")


if __name__ == "__main__":
    main()
