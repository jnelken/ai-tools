---
name: lunchmoney-hygiene
description: Run the Lunch Money data hygiene audit and report actionable changes, including reimbursement, refund and transfer grouping suggestions. Use for scheduled audits or an on-demand finance data integrity check; does not apply live financial edits.
---

# Lunch Money hygiene

Run the existing checks in Jake's `lunchmoney` repository, verify the data they
actually used, and surface findings that warrant review. Use the repository's
checks as the source of truth rather than recreating their matching heuristics.

## Repository and state

Use the caller's Lunch Money project directory. Otherwise locate
`~/Dropbox/code/lunchmoney` or `~/code/lunchmoney`; if neither exists, report the
missing checkout and stop. Resolve its instruction profile and read its local
instructions, `docs/finance-glossary.md`, and the data-integrity/grouping sections
of `docs/lunch-money-runbook.md` before interpreting findings.

Keep machine-local run history in `~/.local/state/lunchmoney-hygiene/` (create
with mode 700). Keep reports and comparison state out of Git and the skill
directory. Never store API tokens, `.env` contents or full transaction history.

Before running, read `last-success.json` if present. It records `checked_at`,
snapshot dates, and findings with their check, code, refs, message, review decision
and last surfaced material signature. Use check/code/sorted refs as finding
identity; counts, amounts, categories and resolution are material changes, while
fetch timestamps and output ordering alone are not. A first run establishes a
baseline and still reports actionable findings; an absent baseline does not mean
the audit has no issues.

## Run and establish coverage

Run `npm run check` in the repository and capture its complete stdout/stderr in
the local run history. Its `precheck` refreshes API-backed categories, recurring
items and transactions, then the runner executes the integrity checks. Allow one
attempt; if it hangs, fails or cannot authenticate, report the limitation rather
than repeatedly retrying or changing the environment.

Verify the refresh's success from the output, not just the command exit code:
`precheck` can deliberately fall back to old cache files and still exit zero.
Check the fetched dates and transaction fields in the resulting cache. Grouping
needs currency, status, account IDs, notes and group/split membership; missing
metadata is incomplete coverage, not evidence of no grouping opportunities.

Rules and Plaid auto-category mappings are browser-sourced and are **not**
refreshed by this command. Report their actual snapshot dates separately from API
freshness. Do not silently describe the whole audit as live. Do not launch a
browser scrape or sign-in merely to clear a stale-snapshot warning.

The current runner covers auto-category mappings, archived-category references,
overly broad category rules, recurring-category drift, Zero Day business
separation, transfer exclusions, uncategorized card payments, and transaction
group suggestions. Follow the runner if the repository adds or changes checks.

## Review findings

- Group related findings; distinguish confirmed integrity errors, possible
  matches and intentional/allowlisted behavior. A warning is not proof of bad
  data. Read the glossary before proposing category changes, especially Zero Day
  and mixed Concentro work reimbursements.
- For actionable grouping proposals, include transaction IDs, matching evidence,
  currency and proposed net. Review the **group's category and date**: traceable
  refunds/reimbursements should offset the original expense category; account
  transfers need exclusion from both totals and budgets. Cross-month group dates
  can shift budget-period attribution.
- Honor the check's explicit `shared:<reference>` note links for shared expenses.
  An incoming Venmo/Zelle payment, similar amount or nearby date is insufficient
  to invent a purchase link. Ambiguous alternatives remain unresolved; never
  select the nearest purchase or imply permission to group them.
- Check known user decisions and the previous successful report before surfacing
  a finding again. Do not create allowlists or treat an unresolved finding as
  approved merely because it appeared in an earlier run.

## Report and retain the comparison

On a scheduled run, surface new/materially changed actionable findings, meaningful
resolutions, refresh failures or required user decisions. Stay quiet when the
result is unchanged or non-actionable. On an explicitly requested manual run,
return a concise outcome even if unchanged. Summarize counts by check, include
the important evidence/IDs, and link the local report for a large backlog rather
than dumping hundreds of warnings. Never call a warnings-only exit zero a clean
bill of health.

After a completed audit with a verified successful API refresh, atomically write
the new `last-success.json`, retaining known decisions and surfaced signatures.
Include browser-source coverage limitations in that record. A failed or stale-API
run gets a separate failure report and must not advance the successful baseline.
When prior history is unreadable, disclose that comparison is unavailable and
preserve the old file instead of overwriting it silently.

This is a read-only financial audit. Local cache refresh and local report/state
writes are expected. Do not mutate live transactions, notes, categories, rules,
recurring items or budgets; do not commit refreshed caches, create tickets, send
messages to others, or push repository changes as a side effect of the audit.
