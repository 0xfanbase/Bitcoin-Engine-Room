import json
import math
from datetime import date, timedelta

import jsonschema
import numpy as np
import pytest

from pipeline import backtest
from pipeline.fit_models import HONESTY_DEFAULTS

GENESIS = date(2009, 1, 3)
REPO_ROOT = backtest.REPO_ROOT


def _series(a, b, start, end, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    d = start
    while d <= end:
        day = (d - GENESIS).days
        rows.append({"date": d.isoformat(), "value": 10 ** (a + b * math.log10(day) + rng.normal(0, noise)), "source": "test"})
        d += timedelta(days=1)
    return backtest.Series(rows, GENESIS, start)


def test_walk_forward_on_a_perfect_power_law_has_no_error():
    s = _series(-17.0, 5.8, date(2010, 7, 17), date(2018, 1, 1))
    wf = backtest.walk_forward(s, HONESTY_DEFAULTS)
    one_year = next(h for h in wf["horizons"] if h["horizon_days"] == 365)
    assert one_year["n_origins"] > 30
    assert one_year["trend_mae_factor"] == pytest.approx(1.0, abs=1e-3)
    assert one_year["random_walk_mae_factor"] > 1.5  # a trending series beats "no change"


def test_walk_forward_never_uses_data_on_or_after_the_origin():
    s = _series(-17.0, 5.8, date(2010, 7, 17), date(2016, 1, 1), noise=0.1)
    wf = backtest.walk_forward(s, HONESTY_DEFAULTS)
    for origin, fit in wf["fits"].items():
        assert s.dates[fit["n"] - 1] < origin
        assert fit["n"] == len(fit["resid"])


def test_ledger_appends_once_per_month_and_never_rewrites_forecasts():
    s = _series(-17.0, 5.8, date(2010, 7, 17), date(2016, 1, 1))
    models = {
        "power_law": {
            "params": {"a": -17.0, "b": 5.8, "genesis_date": GENESIS.isoformat()},
            "current": {"date": "2014-06-30", "price": 500.0},
            "bands": {"outer_offsets_log10": [-0.4, 0.7], "inner_offsets_log10": [-0.3, 0.3]},
        }
    }
    ledger = backtest.update_ledger(None, models, s)
    assert len(ledger["entries"]) == 1
    frozen = json.dumps(ledger["entries"][0]["horizons"][0]["trend"])

    models["power_law"]["current"]["date"] = "2014-06-15"  # same month: no second entry
    models["power_law"]["params"]["b"] = 9.9
    ledger = backtest.update_ledger(ledger, models, s)
    assert len(ledger["entries"]) == 1
    assert json.dumps(ledger["entries"][0]["horizons"][0]["trend"]) == frozen

    # 1y target (2015-06-30) has arrived; 2y/4y have not.
    outcomes = [hz["outcome"] for hz in ledger["entries"][0]["horizons"]]
    assert outcomes[0] is not None and outcomes[0]["inside_outer"] is True
    assert outcomes[0]["miss_factor"] == pytest.approx(1.0, abs=1e-3)
    assert outcomes[1] is None and outcomes[2] is None


def test_ledger_target_inside_a_data_gap_is_scored_on_the_next_real_row():
    s = _series(-17.0, 5.8, date(2010, 7, 17), date(2016, 1, 1))
    # Knock out 20 days around the 1y target (2015-06-30) -- an outage.
    keep = [i for i, d in enumerate(s.dates) if not (date(2015, 6, 25) <= d <= date(2015, 7, 14))]
    s.dates = [s.dates[i] for i in keep]
    s.d, s.x, s.y = s.d[keep], s.x[keep], s.y[keep]
    models = {
        "power_law": {
            "params": {"a": -17.0, "b": 5.8, "genesis_date": GENESIS.isoformat()},
            "current": {"date": "2014-06-30", "price": 500.0},
            "bands": {"outer_offsets_log10": [-0.4, 0.7], "inner_offsets_log10": [-0.3, 0.3]},
        }
    }
    outcome = backtest.update_ledger(None, models, s)["entries"][0]["horizons"][0]["outcome"]
    assert outcome is not None and outcome["actual_date"] == "2015-07-15"


@pytest.mark.parametrize("name", ["backtest", "forecasts"])
def test_committed_files_validate(name):
    path = REPO_ROOT / "data" / f"{name}.json"
    with open(REPO_ROOT / "pipeline" / "schemas" / f"{name}.schema.json") as f:
        schema = json.load(f)
    with open(path) as f:
        jsonschema.validate(json.load(f), schema)


def test_committed_models_json_validates():
    with open(REPO_ROOT / "pipeline" / "schemas" / "models.schema.json") as f:
        schema = json.load(f)
    with open(REPO_ROOT / "data" / "models.json") as f:
        jsonschema.validate(json.load(f), schema)
