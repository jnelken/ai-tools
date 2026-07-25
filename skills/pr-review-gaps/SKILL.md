---
name: pr-review-gaps
description: Use when auditing which open sswt-tracked PRs across api/woodrow/folio-platform have not yet been posted to the #pr-review Slack channel, and optionally posting the gaps after confirmation. Trigger phrases include "check pr-review gaps", "which PRs haven't been posted to pr-review", "find unshared PRs", "audit the pr-review channel", "sswt PRs not shared yet".
---

# PR Review Gaps

## Overview

Cross-references live Superset (sswt) workspaces against open GitHub PRs and the `#pr-review`
Slack channel to find PRs that should have been announced but weren't. A persistent hold-list
lets deliberately-parked PRs (drafts, on-hold work, exploratory branches) get excluded from
every future run instead of re-appearing as false positives.

This skill only **reports and asks** — it never posts to Slack without an explicit go-ahead,
even for a PR that isn't held. That's a deliberate difference from the always-on auto-post
after a clean `/peer-review` loop (see the global `CLAUDE.md` "Post-PR Workflow" section) —
that flow is already-authorized and tied to a specific trigger (a clean review); this skill is
a periodic/ad-hoc audit and needs a confirmation step every time.

## Steps

0. **Load state.** Read `~/.claude/state/pr-review-holds.jsonl` and
   `~/.claude/state/pr-review-posted.jsonl`. Create either with `touch` if missing — an absent
   file means zero holds / zero known-posted, not an error. Both are flat JSONL, one JSON
   object per line.

1. **List sswt workspaces.** Call the Superset `workspaces_list` tool. Drop any entry with
   `type: "main"` — that's a root checkout, not a feature worktree, and never carries a PR
   worth auditing.

2. **Map each workspace to a GitHub repo.** Static table (extend inline the day a new repo
   needs tracking — there's no dynamic discovery available, `projects_list` errors with a cert
   issue in this environment):
   - `api` → `Concentro-Inc/api`
   - `woodrow` → `Concentro-Inc/woodrow`
   - `woodrow (alt)` → `Concentro-Inc/woodrow`
   - `folio-platform` → `Concentro-Inc/folio-platform`
   - Anything else → skip, it's not a PR-tracked repo (e.g. `internal-tools`, `claude-skills`,
     `gh-tab-mgmt`, `CC-statusline-node`).

3. **Find each workspace's open PR.** For each (repo, branch) pair:
   ```
   gh pr list --repo <repo> --head <branch> --state open --json number,title,url
   ```
   No result → skip, that workspace has no open PR right now.

4. **Filter out held PRs.** Drop any candidate whose `(repo, number)` matches a line in
   `pr-review-holds.jsonl`.

5. **Filter out PRs already known-posted.** Drop any candidate whose `url` matches a line in
   `pr-review-posted.jsonl`.

6. **Backfill-check via Slack search.** For each remaining candidate, search rather than
   bulk-read — a plain `slack_read_channel` history pull has a lookback horizon and will miss
   an older post. Use:
   ```
   slack_search_public query: "<repo-short-name>/pull/<number>" in:#pr-review
   ```
   e.g. `"api/pull/1107" in:#pr-review` — **include the repo short name in the query, not just
   the bare `pull/<number>`.** PR numbers collide across repos (api#1107 and woodrow#1107 are
   different PRs); searching `"pull/1107"` alone matched the wrong repo's PR during this
   skill's own dry run. Match on the **PR number/URL fragment**, never the title — the same PR
   shows up in the channel under different message text across posts (e.g. api#1068 was
   announced twice with two different titles). A hit means it was already posted: append
   `{"url","repo","pr","postedAt"}` (postedAt = the message's timestamp) to
   `pr-review-posted.jsonl` and drop the candidate from the gap list.

7. **Report the gap list** to the user: repo, PR number, title, URL for everything that
   survives steps 4–6.

8. **Confirm before posting anything.** Ask the user which of the reported gaps to post now.
   Support "post all," "post none," a per-item choice, and a per-item "hold instead, because:
   `<reason>`" option. Do not post without this step, regardless of how confident the gap looks.

9. **Post approved items.** For each PR the user approves:
   - `security find-generic-password -s slack-pr-review-user-token -a "$USER" -w` to get the
     `xoxp` token.
   - Call `chat.postMessage` on channel `C0APAAD3PP0` with text `<PR_URL|PR_TITLE>`,
     `unfurl_links: false`, `unfurl_media: false`.
   - On success, append `{"url","repo","pr","postedAt"}` to `pr-review-posted.jsonl`.

10. **Record new holds.** For each item the user chose to hold instead of post, append
    `{"repo","pr","reason","heldAt"}` to `pr-review-holds.jsonl`.

11. **Summarize**: posted N, held M, left unresolved if the user deferred on any without
    posting or holding.

## Common mistakes

- Matching posted/held state by title instead of **repo + PR number** (or URL) — titles change
  between posts of the same PR and will cause both false negatives and false positives.
- Searching Slack for a bare `pull/<number>` without the repo name — PR numbers collide across
  repos (api and woodrow both have a PR #1107), so an unscoped search can match the wrong PR
  entirely. Always include the repo short name in the query.
- Relying on `slack_read_channel` bulk reads to decide "already posted" — no lookback
  guarantee. Always use `slack_search_public` per PR for the backfill check.
- Posting without the Step 8 confirmation — this skill's whole point is a human-gated
  second look at PRs that a fully-automatic flow wouldn't have announced on its own.
- Leaving a stale hold behind: when a workspace/branch backing a held PR is deleted (e.g. via
  the `cleanup-local-branches` skill), remove the matching `pr-review-holds.jsonl` entry too —
  same cleanup rule `CLAUDE.md` already specifies for `pr-review-posted.jsonl`.

## Why this exists

Not every open PR gets its `/peer-review` loop run immediately, and sswt workspaces can sit
for days before their PR is opened, reviewed, or picked back up. Nothing else surfaces "this
has quietly gone dark and #pr-review never heard about it" — this skill is the periodic check
for that gap, with the hold-list keeping deliberately-parked work from becoming permanent noise.
