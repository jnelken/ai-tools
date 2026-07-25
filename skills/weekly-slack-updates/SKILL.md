---
name: weekly-slack-updates
description: Use when generating and posting per-author "Dev Weekly" Slack updates from git history across your product repos, using a fixed Friday 1:00 PM cutoff, plus a final proposed product-announcement draft. Trigger phrases include "weekly slack update", "dev weekly", "post the changelog", "weekly dev update".
---

# Weekly Slack Updates

This skill documents how to generate and post Dev Weekly per-author Slack updates using a fixed Friday 1:00 PM cutoff. Include all of your product-related repos.

## 1) Pick the date window

Always include all landed changes since the last successful run.

- `since`: the last successful run time from the automation memory, inclusive
- `until`: the current Friday at 1:00 PM local time, inclusive intent

If no previous run history is available, use the previous Friday at 1:00 PM local time as `since` unless the user specified a different lookback.

Use `--until` exactly at `13:00` for that Friday.

Example:

- Previous successful run: `2026-06-06 13:00`
- New cutoff: `2026-06-13 13:00`
- Window: `2026-06-06 13:00` through `2026-06-13 13:00`

## 2) Pull commits for the window

Use committer date (`%cd`) so the window reflects when code landed, not original author timestamp.

List your product repos (the tag shown in output is each repo's directory name):

```bash
REPOS=(
  "$HOME/code/<repo-a>"
  "$HOME/code/<repo-b>"
  "$HOME/code/<repo-c>"
)
```

Run across all repos:

```bash
for repo in "${REPOS[@]}"; do
  tag=$(basename "$repo")
  (cd "$repo" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:"[$tag]\t%cd\t%an\t%s" --date=iso-local)
done
```

Example:

```bash
for repo in "${REPOS[@]}"; do
  tag=$(basename "$repo")
  (cd "$repo" && git log --since='2026-06-06 13:00' --until='2026-06-13 13:00' --pretty=format:"[$tag]\t%cd\t%an\t%s" --date=iso-local)
done
```

## 3) List authors (exclude bots)

```bash
for repo in "${REPOS[@]}"; do
  (cd "$repo" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --pretty=format:'%an')
done \
  | sort -u \
  | rg -v 'github-actions\[bot\]'
```

## 4) Gather commits per person (exclude merges)

```bash
for repo in "${REPOS[@]}"; do
  tag=$(basename "$repo")
  (cd "$repo" && git log --since='YYYY-MM-DD HH:MM' --until='YYYY-MM-DD HH:MM' --author='Full Name' --no-merges --pretty=format:"[$tag]\t%cd\t%s" --date=short)
done
```

Note:

- If someone uses different author names in git history, run multiple `--author` queries and combine them.

## 5) Write the Slack copy

Turn each author's commit list into a product-facing Slack message. Post one standalone message per author. Do not use threads. After the per-author messages, post one final product-announcement draft message.

Use these sections in this exact order:

1. `Product`
2. `Bugs`
3. `Dev updates`

Section rules:

- Sections may be omitted if empty.
- `Bugs` is only for user-impact issues (broken behavior, regressions, or visible product issues).
- Engineering cleanups or non-user-facing fixes belong in `Dev updates`, not `Bugs`.

Template:

```text
:star2: Dev Weekly - Updates (Mon D to Fri D)

*NAME*

Product :rocket:
• Fixed/Added/Improved/Created ...

Bugs :beetle:
• User-impact fix ...

Dev updates :male-technologist:
• Internal tooling/process/infra cleanup ...
```

Delivery rules:

- Post one standalone Slack message per author at Friday 1:00 PM local time.
- After the per-author messages, post one final standalone Slack message titled exactly `proposed public product announcement`.
- The final announcement message should summarize the key product changes by author, with 2-3 bullets per person.
- In the final announcement message, prioritize public-facing product changes, give lower priority to bug fixes, and give the lowest priority to dev updates.
- If an author has no meaningful user-facing or engineering updates after filtering bots/merges, omit that author.
- Post to the incoming webhook URL stored in `SLACK_CHANGELOG_WEBHOOK_URL`.
- If `SLACK_CHANGELOG_WEBHOOK_URL` is unset, do not post; report that the webhook env var is missing.
- Use that incoming webhook directly; do not use Slack Web API, `chat.postMessage`, or threaded replies.
- Never print the webhook URL value in logs or final output.

Final announcement template:

```text
*proposed public product announcement*

*NAME*
• Product-focused summary ...
• Product-focused summary ...
• Optional bug/dev update only if it materially affects users ...
```

Emoji and style rules:

- Keep section order fixed even when some sections are omitted.
- Use concise, direct bullet phrasing with strong verbs (`Fixed`, `Added`, `Improved`, `Created`, `Deleted`).
- Avoid ticket IDs and low-level implementation details in final Slack copy.
- Prefer concrete shipped surfaces over abstract summaries.
- Name the visible product area when possible (e.g. a specific page, panel, or workflow).
- Use one bullet per distinct user-facing capability or workflow change.
- Only group multiple changes into one bullet when they belong to the same surface or workflow.
- If grouping, name the shared surface first, then list 2-4 concrete examples.
- Avoid broad phrasing like `improved file workflows` when the shipped changes can be named directly.
- If screenshots are added, include the matching PR link for each screenshot so readers can open the implementation context directly.

Precision examples:

```text
Avoid:
- Improved file workflows with several usability updates.

Prefer:
- The uploads page now has folder-drop preview, a right-click menu, updated grouping options, and faster re-classifying.
```

Date display rules:

- Data collection uses the exact Friday `13:00` cutoff.
- The Slack header should show day-level dates only (example: `Feb 9 to Feb 13`), not cutoff time.
