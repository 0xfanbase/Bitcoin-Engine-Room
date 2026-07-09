"""Minimal GitHub Issues client for the two auto-issue workflows:
- data-outage (spec Section 6/10): 3+ consecutive source failures on any
  metric during a daily snapshot (used by fetch_snapshot.py).
- audit-fail (spec Section 11): the daily audit reports FAIL (used by
  audit.py).

Both share the same find/open-or-update/close-with-comment mechanics,
distinguished only by label set -- see the generic `*_issue` functions.

Requires a GitHub token (GITHUB_TOKEN in Actions). Callers check for its
presence and skip issue automation entirely when absent (e.g. local runs)
rather than failing the whole run over a missing token.
"""

from __future__ import annotations

import os

from pipeline.sources import request_with_retry

API_BASE = "https://api.github.com"
SOURCE_NAME = "github_issues"

OUTAGE_LABELS = ["auto", "data-outage"]
AUDIT_LABELS = ["auto", "audit-fail"]


def repo_slug() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "0xfanbase/Bitcoin-Engine-Room")


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def find_open_issue(token: str, *, labels: list[str], request_fn=request_with_retry) -> dict | None:
    response = request_fn(
        "GET",
        f"{API_BASE}/repos/{repo_slug()}/issues",
        source_name=SOURCE_NAME,
        params={"state": "open", "labels": ",".join(labels)},
        extra_headers=_auth_headers(token),
    )
    issues = response.json()
    return issues[0] if issues else None


def open_or_update_issue(
    token: str, *, labels: list[str], title: str, body: str, request_fn=request_with_retry
) -> dict:
    existing = find_open_issue(token, labels=labels, request_fn=request_fn)
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
        json_body={"title": title, "body": body, "labels": labels},
    )
    return response.json()


def close_issue_if_open(
    token: str, *, labels: list[str], closing_comment: str, request_fn=request_with_retry
) -> dict | None:
    existing = find_open_issue(token, labels=labels, request_fn=request_fn)
    if not existing:
        return None

    request_fn(
        "POST",
        f"{API_BASE}/repos/{repo_slug()}/issues/{existing['number']}/comments",
        source_name=SOURCE_NAME,
        extra_headers=_auth_headers(token),
        json_body={"body": closing_comment},
    )
    response = request_fn(
        "PATCH",
        f"{API_BASE}/repos/{repo_slug()}/issues/{existing['number']}",
        source_name=SOURCE_NAME,
        extra_headers=_auth_headers(token),
        json_body={"state": "closed"},
    )
    return response.json()


# --- data-outage issues (fetch_snapshot.py) --------------------------------


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

    return open_or_update_issue(
        token,
        labels=OUTAGE_LABELS,
        title="Data outage: one or more sources failing",
        body="\n".join(body_lines),
        request_fn=request_fn,
    )


def close_outage_issue_if_open(token: str, *, request_fn=request_with_retry) -> dict | None:
    return close_issue_if_open(
        token,
        labels=OUTAGE_LABELS,
        closing_comment="All metrics recovered on the latest snapshot -- closing.",
        request_fn=request_fn,
    )


# --- audit-fail issues (audit.py) -------------------------------------------


def open_or_update_audit_issue(token: str, *, findings: list[dict], request_fn=request_with_retry) -> dict:
    """`findings`: list of {"check", "severity", "detail"} (WARN/FAIL only)."""
    body_lines = [
        "Automated: the daily audit reported FAIL. Full report in `data/audit/latest.json`.",
        "",
        "| Check | Severity | Detail |",
        "|---|---|---|",
    ]
    for f in findings:
        body_lines.append(f"| {f['check']} | {f['severity']} | {f['detail']} |")
    body_lines.append("")
    body_lines.append("This issue auto-closes once the audit passes again.")

    return open_or_update_issue(
        token,
        labels=AUDIT_LABELS,
        title="Audit FAIL: data integrity checks failing",
        body="\n".join(body_lines),
        request_fn=request_fn,
    )


def close_audit_issue_if_open(token: str, *, request_fn=request_with_retry) -> dict | None:
    return close_issue_if_open(
        token,
        labels=AUDIT_LABELS,
        closing_comment="Audit passed on the latest run -- closing.",
        request_fn=request_fn,
    )
