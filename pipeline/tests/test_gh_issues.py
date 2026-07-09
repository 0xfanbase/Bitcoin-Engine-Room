import responses

from pipeline import gh_issues

REPO = "fandamentals/bitcoin-engine-room"
ISSUES_URL = f"https://api.github.com/repos/{REPO}/issues"


@responses.activate
def test_opens_new_issue_when_none_exists(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    responses.add(responses.GET, ISSUES_URL, json=[], status=200)  # no open outage issue
    responses.add(responses.POST, ISSUES_URL, json={"number": 42, "state": "open"}, status=201)

    result = gh_issues.open_or_update_outage_issue(
        "fake-token",
        failing_metrics=[{"metric": "price_daily", "consecutive_failures": 3, "last_error": "all sources failed"}],
    )

    assert result["number"] == 42
    post_call = responses.calls[-1]
    assert post_call.request.method == "POST"
    assert "data-outage" in post_call.request.body.decode()


@responses.activate
def test_updates_existing_issue_instead_of_opening_a_duplicate(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    responses.add(responses.GET, ISSUES_URL, json=[{"number": 7, "state": "open"}], status=200)
    responses.add(responses.PATCH, f"{ISSUES_URL}/7", json={"number": 7, "state": "open"}, status=200)

    result = gh_issues.open_or_update_outage_issue(
        "fake-token",
        failing_metrics=[{"metric": "hashrate_daily", "consecutive_failures": 5, "last_error": "x"}],
    )

    assert result["number"] == 7
    assert responses.calls[-1].request.method == "PATCH"


@responses.activate
def test_close_outage_issue_if_open_closes_and_comments(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    responses.add(responses.GET, ISSUES_URL, json=[{"number": 7, "state": "open"}], status=200)
    responses.add(responses.POST, f"{ISSUES_URL}/7/comments", json={"id": 1}, status=201)
    responses.add(responses.PATCH, f"{ISSUES_URL}/7", json={"number": 7, "state": "closed"}, status=200)

    result = gh_issues.close_outage_issue_if_open("fake-token")

    assert result["state"] == "closed"


@responses.activate
def test_close_outage_issue_if_open_is_a_no_op_when_none_open(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    responses.add(responses.GET, ISSUES_URL, json=[], status=200)

    assert gh_issues.close_outage_issue_if_open("fake-token") is None
