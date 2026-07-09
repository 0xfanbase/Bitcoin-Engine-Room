---
description: Read the audit backlog and health report, pick the single highest-value item, implement it, verify, and commit on approval.
---

Read `CLAUDE.md` first if you haven't already this session — it has the hard rules and creative-direction bullets that apply to everything below.

This is the weekly self-improvement ritual (spec Section 12). Its whole design rests on one constraint: **you stay editor-in-chief.** Do exactly one item per run, show your diff, and let the user approve before committing.

## 1. Gather context

- Read `IMPROVEMENT_BACKLOG.md` in full.
- Read `data/audit/` for the last 7 dated reports (or however many exist if fewer than 7 days of history), to see which findings are recurring vs. one-off.
- Read `data/health.json` for current per-source status.

## 2. Pick ONE item

Pick the single highest-value item from the backlog. "Highest value" generally means, in rough priority order:

1. Anything marked FAIL in a recent audit (data integrity beats everything else).
2. A recurring WARN (same finding across multiple days) over a one-off.
3. A `manual`-sourced entry the project owner flagged as important, if any are unaddressed.
4. Otherwise, whatever's cheapest to fix well.

If the backlog is genuinely empty (nothing actionable), say so and stop — **do not invent work.** An empty backlog is a good outcome, not a problem to solve.

## 3. Implement it

- One logical change. Resist the urge to also clean up unrelated things you notice along the way — log those as new backlog entries instead (per the existing entry format at the top of `IMPROVEMENT_BACKLOG.md`) rather than doing them now.
- If the fix touches a data format, update the corresponding schema in `pipeline/schemas/` **and** its tests in the same commit — never ship a format change without both.

## 4. Guardrails (non-negotiable, per spec Section 12.4)

- **Never** weaken a sanity bound (`pipeline/sanity_rules.json`) or a model constant (`pipeline/model_constants.json`) just to make a check or an audit pass. If a bound is genuinely wrong, that's a deliberate, well-justified change with its own explanation — not a side effect of "fixing" a WARN.
- **Never** add a paid dependency, API key requirement, or anything that breaks the $0 constraint.
- **Never** increase polling frequency (live.js's intervals, fetch_snapshot.py's cadence) — politeness to upstream APIs is the compliance strategy, not a knob to tune for freshness.
- **Never** edit `.github/workflows/*.yml`, the attribution footer in `index.html`, or `pipeline/sanity_rules.json`'s existing bounds as a side effect of an unrelated fix.
- **Never** touch bitbo or any competing dashboard's servers, ever, for any reason.

## 5. Verify

- `pytest pipeline/tests -v` must be fully green.
- If you touched any `data/*.json` file's shape, confirm it validates against its schema.
- If you touched the frontend, actually load the page (a local server + browser check, or Playwright) and look at the result — don't just eyeball the diff.

## 6. Summarize and commit

- Show a concise summary of the diff and why it's the right item to have picked.
- Update `PROGRESS.md` with a dated log entry.
- Only commit after the user approves. Follow the commit-message conventions in `CLAUDE.md` Section 7.
