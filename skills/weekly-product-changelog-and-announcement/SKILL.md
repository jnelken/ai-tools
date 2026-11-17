---
name: weekly-product-changelog-and-announcement
description: Produce two Slack deliverables from one week of woodrow, api, and folio-platform commits — a per-engineer internal changelog and a customer/PM-facing product announcement — and add the same week to the team's demo checklist artifact, using a Friday cutoff window that resumes from the last run. Use for the weekly product update, weekly changelog, Friday shipping summary, the scheduled weekly Slack post, or adding a week to the demo checklist.
---

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
CODE_ROOT="${CODE_ROOT:-$HOME/code}"
WOODROW_REPO="$CODE_ROOT/woodrow"
API_REPO="$CODE_ROOT/api"
FOLIO_PLATFORM_REPO="$CODE_ROOT/folio-platform"
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

## 6) Add the week to the demo checklist

The dev team walks through a reverse-timeline checklist in the weekly product demo.
It is a Claude artifact, `https://claude.ai/artifact/3xVUT6X32ktXpvk3Kb4UZZ`, whose page reads
its content from the artifact database, so adding a week is a database write with
`ArtifactData`, not a republish. (If the artifact is gone, ask Jake before creating a new one.)

Data model (the page renders exactly this):

- `weeks/<YYYY-MM-DD>`: one document per week, id = the Friday the window ends. Fields:
  `week`, `label` (`Nov 6 to Nov 13`), `since`, `until` (`YYYY-MM-DD HH:MM`), optional `note`,
  `items: [{k, text, kind: "product"|"bug"|"dev", urls: [..]}]`.
- `checks/<week>_<k>`: check state, written only by the page. Never write or delete these;
  keep each item's `k` stable once published so check state survives edits.

Steps:

1. Window: `since` = the `until` of the newest `weeks` doc (read it with `ArtifactData`
   `list` on `weeks`, newest `week`), `until` = now. This resumes the same chain as Section 1.
   If that newest doc has a `note` (an in-progress week), remove it with `update` and
   `{"note": {"__delete__": true}}`.
2. Cluster: `python3 scripts/cluster_week.py --since '...' --until '...' --out <scratch>/clusters.json`.
   It reads `origin/main` along `--first-parent`, so one merged PR is one unit, then groups PRs by
   `CON-`/`DEV-` ticket across the three repos.
3. Write items from the clusters. Rules:
   - One item per demoable capability. Group related clusters, even across tickets. Aim for
     5 to 15 product items; never one bullet per PR.
   - `kind`: `product` = new or improved user-visible capability; `bug` = fix of a user-visible
     problem (this renders with a "Fix" tag); `dev` = internal tooling, CI, tests, refactors,
     infra, with no visible change (collapsed on the page; fold into at most about 6 lines).
   - `text`: one line, at most 170 characters, strong verb, names the visible area, no ticket IDs,
     PR numbers, file names or jargon, nothing the PR titles do not support. Use `gh pr view`
     only for clusters too vague to describe.
   - `urls`: PR URLs from that item's clusters only, woodrow (UI) PR first, at most 4.
   - `k`: the primary ticket key, else the first cluster id with characters outside
     `A-Za-z0-9_.~:@+-` replaced by `-` (for example `woodrow-1308`); suffix `-b` on a clash.
   - Order: product (most demo-worthy first), then bug, then dev.
   - Every cluster lands in exactly one item (fold trivia into one dev line instead of dropping).
4. Check in code before writing: no `CON-`/`DEV-` keys or `#123` in any `text`, every URL appears in
   the clusters file, every cluster id is covered once, ids unique. Fix failures; do not skip the check.
5. Write with `ArtifactData` `set` on `weeks/<Friday date>`. For a partial week (run before the
   Friday cutoff) add `note: "In progress: includes changes merged through <day, time>. Later merges will be added."`.
6. Mention the new week and item counts in the run report. Never put webhook values in anything written.

Scale: for a multi-week backfill, run Steps 2 to 4 once per week (one agent per week works well)
and write each week as its own document.
