import json
from datetime import date, datetime, timedelta, timezone

import responses

from pipeline import crosscheck_history
from pipeline.sources import BitstampClient

CONSTANTS = {"power_law": {"genesis_date": "2009-01-03", "fit_start_date": "2010-07-17"}}


def _rows(n=800, start=date(2012, 1, 1)):
    out = []
    for i in range(n):
        d = start + timedelta(days=i)
        out.append({"date": d.isoformat(), "value": 10.0 * (1.002**i), "source": "test"})
    return out


def test_range_containment_not_close_equality():
    ours = [{"date": "2013-04-11", "value": 160.0}, {"date": "2013-04-12", "value": 300.0}]
    ref = [
        {"date": "2013-04-11", "low": 50.0, "high": 179.0, "close": 83.4},  # far from close, inside range: agreement
        {"date": "2013-04-12", "low": 90.0, "high": 130.0, "close": 100.0},  # outside range: disagreement
    ]
    result = crosscheck_history.compare(ours, ref)
    assert result["years"][0]["outside_range"] == 1
    assert [d["date"] for d in result["disagreements"]] == ["2013-04-12"]


def test_carried_forward_rows_are_not_compared():
    ours = [{"date": "2020-01-01", "value": 1.0, "carried_forward": True}]
    assert crosscheck_history.compare(ours, [{"date": "2020-01-01", "low": 5, "high": 6, "close": 5}])["years"] == []


def test_fit_sensitivity_zero_when_nothing_substituted():
    rows = _rows()
    fs = crosscheck_history.fit_sensitivity(rows, [], CONSTANTS)
    assert fs["days_substituted"] == 0
    assert fs["b_committed"] == fs["b_with_bitstamp_substituted"]
    assert fs["trend_2030_change_pct"] == 0.0


@responses.activate
def test_bitstamp_client_paginates_and_drops_zero_rows():
    base = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    page1 = [{"timestamp": str(base + i * 86400), "open": "1", "high": "2", "low": "1", "close": "1.5", "volume": "1"} for i in range(1000)]
    page1[0]["low"] = "0"
    page2 = [{"timestamp": str(base + (1000 + i) * 86400), "open": "1", "high": "2", "low": "1", "close": "1.5", "volume": "1"} for i in range(5)]
    responses.add(responses.GET, BitstampClient.URL, json={"data": {"ohlc": page1}})
    responses.add(responses.GET, BitstampClient.URL, json={"data": {"ohlc": page2}})
    responses.add(responses.GET, BitstampClient.URL, json={"data": {"ohlc": []}})
    rows = BitstampClient().fetch_daily_ohlc(base, base + 2000 * 86400)
    assert len(rows) == 1004
    assert rows[0]["date"] == "2020-01-02"
    assert "User-Agent" in responses.calls[0].request.headers
    assert "btc-engine-room" in responses.calls[0].request.headers["User-Agent"]


def test_run_skips_when_recent_and_survives_network_failure(tmp_path, monkeypatch):
    out = tmp_path / "x.json"
    monkeypatch.setattr(crosscheck_history, "OUT_PATH", out)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    out.write_text(json.dumps({"generated_at": "2026-09-20T00:00:00Z"}))
    assert crosscheck_history.run(now=now) is None  # 11 days old: polite skip

    class Failing:
        def fetch_daily_ohlc(self, *a, **k):
            from pipeline.sources import SourceFetchError
            raise SourceFetchError("bitstamp", "url", "boom")

    assert crosscheck_history.run(now=now, force=True, client=Failing()) is None
    assert json.loads(out.read_text())["generated_at"] == "2026-09-20T00:00:00Z"  # previous report kept


def test_run_survives_http_error_and_html_body(tmp_path, monkeypatch):
    import requests

    out = tmp_path / "x.json"
    monkeypatch.setattr(crosscheck_history, "OUT_PATH", out)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for exc in (requests.HTTPError("403 Forbidden"), ValueError("Expecting value: line 1 column 1")):
        class Broken:
            def fetch_daily_ohlc(self, *a, _exc=exc, **k):
                raise _exc

        assert crosscheck_history.run(now=now, force=True, client=Broken()) is None
    assert not out.exists()
