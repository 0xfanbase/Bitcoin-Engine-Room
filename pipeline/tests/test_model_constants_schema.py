from datetime import date

import jsonschema


def test_model_constants_validates_against_schema(model_constants, load_schema):
    jsonschema.validate(model_constants, load_schema("model_constants"))


def test_fit_start_date_after_genesis(model_constants):
    power_law = model_constants["power_law"]
    assert date.fromisoformat(power_law["fit_start_date"]) > date.fromisoformat(power_law["genesis_date"])


def test_halving_dates_sorted_ascending(model_constants):
    parsed = [date.fromisoformat(d) for d in model_constants["cycle_overlay"]["halving_dates"]]
    assert parsed == sorted(parsed)


def test_expected_range_consistent_with_spec_reference_values(model_constants):
    expected = model_constants["power_law"]["expected_range"]
    b_low, b_high = expected["b"]
    a_low, a_high = expected["a"]
    assert 5.0 < b_low < b_high < 6.5
    assert -18.0 < a_low < a_high < -16.0
    assert 0.9 < expected["r_squared_min"] <= 1.0


def test_audit_drift_band_wider_than_expected_range(model_constants):
    # Director-reviewed correction: the audit WARN band must be wider than the
    # reference range, since a single bad point among ~5,800+ observations has
    # negligible leverage on `b` -- the audit should catch bulk corruption, not
    # enforce the reference range as a hard gate.
    power_law = model_constants["power_law"]
    warn_low, warn_high = power_law["audit_drift_thresholds"]["b_warn_outside_range"]
    expected_low, expected_high = power_law["expected_range"]["b"]
    assert warn_low < expected_low
    assert warn_high > expected_high
