---
description: Read the audit history and write a 5-line status digest.
---

Read the last 7 days of `data/audit/*.json` (or however many exist) and the current `data/health.json`. Write a status digest — **5 lines, no more** — covering:

1. Current audit result (PASS/WARN/FAIL) and how many days it's held that result.
2. Any FAIL or recurring WARN finding that needs attention, named specifically (check + metric).
3. Per-source health: how many of the 5 metrics are OK vs. STALE right now, and the longest `consecutive_failures` streak if any.
4. Last snapshot age — flag it if it's approaching or past 48h (the site's own cron-caveat red-flag threshold, per `CLAUDE.md` Section 3).
5. One-line verdict: is anything actually actionable right now, or is the engine quietly running fine?

Keep it to plain text, no headers, no code blocks — this is meant to be read on a phone in ten seconds. Do not propose or make any changes; this command is read-only.
