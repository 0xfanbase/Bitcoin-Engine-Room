import responses

from pipeline.sources import SourceFetchError, request_with_retry


@responses.activate
def test_retries_on_500_then_succeeds():
    url = "https://example.test/data"
    responses.add(responses.GET, url, status=500)
    responses.add(responses.GET, url, json={"ok": True}, status=200)

    sleeps = []
    result = request_with_retry("GET", url, source_name="test", max_retries=3, sleep_fn=sleeps.append)

    assert result.status_code == 200
    assert result.json() == {"ok": True}
    assert len(sleeps) == 1


@responses.activate
def test_honors_retry_after_header_on_429():
    url = "https://example.test/data"
    responses.add(responses.GET, url, status=429, headers={"Retry-After": "5"})
    responses.add(responses.GET, url, json={"ok": True}, status=200)

    sleeps = []
    result = request_with_retry("GET", url, source_name="test", max_retries=3, sleep_fn=sleeps.append)

    assert result.status_code == 200
    assert sleeps == [5.0]


@responses.activate
def test_retry_after_is_capped_at_max_retry_after_seconds():
    url = "https://example.test/data"
    responses.add(responses.GET, url, status=429, headers={"Retry-After": "600"})
    responses.add(responses.GET, url, json={"ok": True}, status=200)

    sleeps = []
    request_with_retry("GET", url, source_name="test", max_retries=3, sleep_fn=sleeps.append)

    assert sleeps == [60]


@responses.activate
def test_raises_source_fetch_error_after_exhausting_retries():
    url = "https://example.test/data"
    for _ in range(4):  # max_retries=3 -> 4 total attempts
        responses.add(responses.GET, url, status=500)

    try:
        request_with_retry("GET", url, source_name="test", max_retries=3, sleep_fn=lambda s: None)
        assert False, "expected SourceFetchError"
    except SourceFetchError as exc:
        assert exc.source_name == "test"
        assert url in str(exc)
