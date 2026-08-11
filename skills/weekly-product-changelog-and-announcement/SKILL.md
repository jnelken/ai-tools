# Weekly Product Changelog & Announcement

This skill generates two Slack deliverables from the same weekly commit
history — a per-engineer **changelog** (internal, dev-facing) and a
**product announcement** (external, customer/PM/CEO-facing) — using a
fixed Friday cutoff. Include all product-related repos: `woodrow`, `api`,
and `folio-platform`.

## 1) Pick the date window

Always include all landed changes since the last successful run, and store the
exact `until` timestamp used by this run in automation memory for next time.

- `since`: the exact `until` timestamp recorded from the last successful run
- `until`: this run's actual invocation time — whatever hour the schedule fires
  at, as long as the date is a Friday

The exact hour doesn't need to be 1:00 PM — schedules may fire at a different
hour. What matters is that runs don't overlap by more than about a day, and
that a run can backfill multiple missed weeks in a single pass if needed.

If no previous run history is available, default `since` to the previous
Friday at the same hour as this run's invocation, unless the user specified a
different lookback.

Example:

- Previous successful run recorded: `2026-06-06 13:00`
- This run fires: `2026-06-13 13:00` (Friday)
- Window: `2026-06-06 13:00` through `2026-06-13 13:00`

## 2) Pull commits for the window

Use committer date (`%cd`) so the window reflects when code landed, not original author timestamp.

Consider all product-related repos:

```bash
WOODROW_REPO='/Users/jake/code/woodrow'
API_REPO='/Users/jake/code/api'
FOLIO_PLATFORM_REPO='/Users/jake/code/folio-platform'
```

Run in all repos:

```bash
(cd "$WOODROW_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'[woodrow]\t%cd\t%an\t%s' --date=iso-local)
(cd "$API_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'[api]\t%cd\t%an\t%s' --date=iso-local)
(cd "$FOLIO_PLATFORM_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'[folio-platform]\t%cd\t%an\t%s' --date=iso-local)
```

## 3) List authors (exclude bots)

```bash
{
  (cd "$WOODROW_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'%an')
  (cd "$API_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'%an')
  (cd "$FOLIO_PLATFORM_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'%an')
} \
  | sort -u \
  | rg -v 'github-actions\[bot\]'
```

## 4) Gather commits per person (exclude merges)

```bash
{
  (cd "$WOODROW_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --author='Full Name' --no-merges --pretty=format:'[woodrow]\t%cd\t%s' --date=short)
  (cd "$API_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --author='Full Name' --no-merges --pretty=format:'[api]\t%cd\t%s' --date=short)
  (cd "$FOLIO_PLATFORM_REPO" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --author='Full Name' --no-merges --pretty=format:'[folio-platform]\t%cd\t%s' --date=short)
}
```

Note:

- If someone uses different author names in git history, run multiple `--author` queries and combine them.

## 5) Write the Slack copy

Turn each author's commit list into two deliverables, generated from the same
underlying commit data but written for two different audiences:

- **Changelog** — internal, dev-facing (e.g. an eng manager skimming it). One
  standalone Slack message per author. Grouped per-engineer, not by
  date/version like a typical `CHANGELOG.md` — the "changelog" framing here
  means the format and tone (structured, technical, terse), not the grouping
  axis.
- **Announcement** — external, customer/PM/CEO-facing, non-technical. One
  standalone Slack message summarizing all authors, posted after every
  changelog message.

Do not use threads for either deliverable.

### Changelog (per author)

Use these sections in this exact order:

1. `Product`
2. `Bugs`
3. `Dev updates`

Section rules:

- Sections may be omitted if empty.
- `Bugs` is only for user-impact issues (broken behavior, regressions, or visible product issues).
- Engineering cleanups or non-user-facing fixes belong in `Dev updates`, not `Bugs`.
- Ticket IDs and low-level implementation details are still stripped here — the audience is an eng manager skimming, not a PR reviewer.

Template:

```text
:ledger: Changelog — NAME (Mon D to Fri D)

Product :rocket:
• Fixed/Added/Improved/Created ...

Bugs :beetle:
• User-impact fix ...

Dev updates :male-technologist:
• Internal tooling/process/infra cleanup ...
```

### Announcement (all authors, one message)

- Post one final standalone Slack message titled exactly `proposed public product announcement`, after all per-author changelog messages.
- Summarize the key product changes by author, with 2-3 bullets per person.
- Prioritize public-facing product changes, give lower priority to bug fixes, and give the lowest priority to dev updates.
- If an author has no meaningful user-facing or engineering updates after filtering bots/merges, omit that author.
- This is the stricter deliverable: no ticket IDs, no implementation detail, no internal jargon — a non-technical customer or CEO should be able to read it cold.

Template:

```text
*proposed public product announcement*

*NAME*
• Product-focused summary ...
• Product-focused summary ...
• Optional bug/dev update only if it materially affects users ...
```

### Delivery rules (both deliverables)

- Post one standalone Slack message per author for the changelog, at Friday 1:00 PM local time (or this run's actual invocation time — see Section 1).
- After the per-author changelog messages, post the single announcement message.
- Post to the incoming webhook URL stored in `SLACK_FOLIO_CHANGELOG_WEBHOOK_URL`.
- If `SLACK_FOLIO_CHANGELOG_WEBHOOK_URL` is unset, do not post; report that the webhook env var is missing.
- Use that incoming webhook directly; do not use Slack Web API, `chat.postMessage`, or threaded replies.
- Never print the webhook URL value in logs or final output.

### Emoji and style rules

- Keep section order fixed even when some sections are omitted.
- Use concise, direct bullet phrasing with strong verbs (`Fixed`, `Added`, `Improved`, `Created`, `Deleted`).
- Prefer concrete shipped surfaces over abstract summaries.
- Name the visible product area when possible (`Upload Center`, `File Explorer`, `variable comments`, `entity URLs`).
- Use one bullet per distinct user-facing capability or workflow change.
- Only group multiple changes into one bullet when they belong to the same surface or workflow.
- If grouping, name the shared surface first, then list 2-4 concrete examples.
- If screenshots are added, include the matching PR link for each screenshot so readers can open the implementation context directly.

### Bugs vs. Dev updates

```text
Avoid:
- Bugs: "Fixed a bug in the entity-comment cache invalidation logic."
  (This is an internal implementation detail — no visible user impact.)

Prefer:
- Dev updates: "Fixed entity-comment cache invalidation logic."
- Bugs (only if user-visible): "Fixed stale comments sometimes appearing after edits."
```

### Ticket IDs and implementation detail

```text
Avoid:
- Fixed CON-4821: refactor upload retry logic in `UploadQueue.ts`.

Prefer:
- Fixed uploads sometimes failing silently on flaky connections.
```

### Section omission

```text
Avoid:
- Including an empty "Bugs :beetle:" header with no bullets underneath.

Prefer:
- Omit the "Bugs" section entirely for that author if there are no
  user-impact fixes — don't print a header with nothing under it.
```

### Bullet grouping precision

```text
Avoid:
- Improved file workflows with several usability updates.

Prefer:
- Upload Center now has folder-drop preview, right-click menu, updated File Explorer grouping options, and faster re-classifying.
```

## Date display rules

- Data collection uses the exact cutoff from Section 1.
- Both the changelog and announcement headers should show day-level dates only (example: `Feb 9 to Feb 13`), not cutoff time.
