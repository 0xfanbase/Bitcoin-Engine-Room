import jsonschema


def test_known_gaps_validates_against_schema(known_gaps, load_schema):
    jsonschema.validate(known_gaps, load_schema("known_gaps"))


def test_known_gaps_entries_are_real_dates_in_order(known_gaps):
    from datetime import date

    for gap in known_gaps["gaps"]:
        start = date.fromisoformat(gap["gap_start"])
        end = date.fromisoformat(gap["gap_end"])
        assert end > start, gap
