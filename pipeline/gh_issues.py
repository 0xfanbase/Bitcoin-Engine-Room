"""Minimal GitHub Issues client for the data-outage auto-issue workflow
(spec Section 6/10): open or update one issue after 3+ consecutive source
failures on any metric, auto-close it on recovery.

Requires a GitHub token (GITHUB_TOKEN in Actions). fetch_snapshot.py checks
for its presence and skips issue automation entirely when absent (e.g. local
runs) rather than failing the whole snapshot over a missing token.
"""

from __future__ import annotations

import os

from pipeline.sources import request_with_retry

API_BASE = "https://api.github.com"
OUTAGE_LABELS = ["auto", "data-outage"]
SOURCE_NAME = "github_issues"


def repo_slug() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "0xfanbase/Bitcoin-Engine-Room")


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def find_open_outage_issue(token: str, *, request_fn=request_with_retry) -> dict | None:
    response = request_fn(
        "GET",
        f"{API_BASE}/repos/{repo_slug()}/issues",
        source_name=SOURCE_NAME,
        params={"state": "open", "labels": ",".join(OUTAGE_LABELS)},
        extra_headers=_auth_headers(token),
    )
    issues = response.json()
    return issues[0] if issues else None


def open_or_update_outage_issue(
    token: str, *, failing_metrics: list[dict], request_fn=request_with_retry
) -> dict:
    """`failing_metrics`: list of {"metric", "consecutive_failures", "last_error"}."""
    body_lines = [
        "Automated: 3+ consecutive source-chain failures detected during the daily snapshot.",
        "",
        "| Metric | Consecutive failures | Last error |",
        "|---|---|---|",
    ]
    for m in failing_metrics:
        body_lines.append(f"| {m['metric']} | {m['consecutive_failures']} | {m['last_error']} |")
    body_lines.append("")
    body_lines.append("This issue auto-closes once every listed metric recovers.")
    body = "\n".join(body_lines)

    existing = find_open_outage_issue(token, request_fn=request_fn)
    if existing:
        response = request_fn(
            "PATCH",
            f"{API_BASE}/repos/{repo_slug()}/issues/{existing['number']}",
            source_name=SOURCE_NAME,
            extra_headers=_auth_headers(token),
            json_body={"body": body},
        )
        return response.json()

    response = request_fn(
        "POST",
        f"{API_BASE}/repos/{repo_slug()}/issues",
        source_name=SOURCE_NAME,
        extra_headers=_auth_headers(token),
        json_body={
            "title": "Data outage: one or more sources failing",
            "body": body,
            "labels": OUTAGE_LABELS,
        },
    )
    return response.json()


def close_outage_issue_if_open(token: str, *, request_fn=request_with_retry) -> dict | None:
    existing = find_open_outage_issue(token, request_fn=request_fn)
    if not existing:
        return None

    request_fn(
        "POST",
        f"{API_BASE}/repos/{repo_slug()}/issues/{existing['number']}/comments",
        source_name=SOURCE_NAME,
        extra_headers=_auth_headers(token),
        json_body={"body": "All metrics recovered on the latest snapshot -- closing."},
    )
    response = request_fn(
        "PATCH",
        f"{API_BASE}/repos/{repo_slug()}/issues/{existing['number']}",
        source_name=SOURCE_NAME,
        extra_headers=_auth_headers(token),
        json_body={"state": "closed"},
    )
    return response.json()
