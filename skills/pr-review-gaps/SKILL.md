---
name: pr-review-gaps
description: "Use when auditing which open sswt-tracked PRs across api/woodrow/folio-platform have not yet been posted to the #pr-review Slack channel, and optionally posting the gaps after confirmation. Trigger phrases include \"check pr-review gaps\", \"which PRs haven't been posted to pr-review\", \"find unshared PRs\", \"audit the pr-review channel\", \"sswt PRs not shared yet\"."---

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

0. **Load state.** Read `~/.claude/state/pr-review-holds.jsonl`,
   `~/.claude/state/pr-review-posted.jsonl` and `~/.claude/state/pr-review-queue.jsonl`.
   Create any with `touch` if missing — an absent file means zero holds / zero known-posted /
   nothing queued, not an error. All are flat JSONL, one JSON object per line. A URL in the
   queue is **pending**, not a gap: `/post-pr` queued it and the `pr-review-batch` flusher
   posts it at the next window boundary (`:00`/`:30` by default). The queue is per machine, so
   a PR queued on the other machine still looks like a gap here; the step 6 Slack search
   catches it once it posts.

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
   - Anything else → skip, it's not a PR-tracked repo (e.g. `internal-tools`, `ai-tools`,
     `gh-tab-mgmt`, `CC-statusline-node`).

3. **Find each workspace's open PR.** For each (repo, branch) pair:
   ```
   gh pr list --repo <repo> --head <branch> --state open --json number,title,url,baseRefName
   ```
   No result → skip, that workspace has no open PR right now.

   Drop any PR whose `baseRefName` is not the repo's default branch. A sub-PR into a
   `feature/*` trunk is internal-only by design — `/post-pr` merges it into the trunk and never
   announces it (see the Post-PR Workflow section of the global `CLAUDE.md`). Keep these in a
   separate "internal (stacked) — not announceable" list for step 7 so they can't be picked in
   step 8; the trunk's own PR into the default branch is the one that can be a gap.

4. **Filter out held PRs.** Drop any candidate whose `(repo, number)` matches a line in
   `pr-review-holds.jsonl`.

5. **Filter out PRs already known-posted or pending.** Drop any candidate whose `url` matches a
   line in `pr-review-posted.jsonl`. Set aside any whose `url` is in `pr-review-queue.jsonl`
   into a "pending — queued for the next window" list for step 7; don't offer those in step 8.

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
   survives steps 4–6. List the stacked PRs set aside in step 3 underneath, marked
   "internal — not announceable", and the queued PRs from step 5 marked "pending — queued", so
   the user sees them without being offered them.

8. **Confirm before posting anything.** Ask the user which of the reported gaps to post now.
   Support "post all," "post none," a per-item choice, and a per-item "hold instead, because:
   `<reason>`" option. Do not post without this step, regardless of how confident the gap looks.

9. **Enqueue approved items.** Don't post directly. For each PR the user approves, append one
   queue line exactly as `/post-pr` step 4 does (same `text` format with the repo label and
   `[hygiene]` rule, current `headSha`, `autoMerge` per that step's rules, `queuedAt`), built
   with `jq -nc` and written with a single `>>` to `pr-review-queue.jsonl`. The flusher posts
   it at the next window, appends `pr-review-posted.jsonl`, then badges, undrafts and enables
   auto-merge. If the user wants it out now, or `launchctl print gui/$(id -u)/com.jake.pr-review-batch`
   shows the flusher isn't loaded on this machine, follow with
   `~/.claude/automations/pr-review-batch/run.sh --only <PR_URL>` (exit 0 means posted; the
   reason for anything else is in `~/.claude/automations/pr-review-batch/logs/flush.log`).

10. **Record new holds.** For each item the user chose to hold instead of post, append
    `{"repo","pr","reason","heldAt"}` to `pr-review-holds.jsonl`.

11. **Summarize**: queued N (or posted, with `--only`), held M, left unresolved if the user deferred on any without
    posting or holding.

## Common mistakes

- Matching posted/held state by title instead of **repo + PR number** (or URL) — titles change
  between posts of the same PR and will cause both false negatives and false positives.
- Searching Slack for a bare `pull/<number>` without the repo name — PR numbers collide across
  repos (api and woodrow both have a PR #1107), so an unscoped search can match the wrong PR
  entirely. Always include the repo short name in the query.
- Relying on `slack_read_channel` bulk reads to decide "already posted" — no lookback
  guarantee. Always use `slack_search_public` per PR for the backfill check.
- Treating a stacked PR (base ≠ default branch) as a gap — it is never announced by design;
  its trunk's PR into the default branch is the announceable unit.
- Posting without the Step 8 confirmation — this skill's whole point is a human-gated
  second look at PRs that a fully-automatic flow wouldn't have announced on its own.
- Leaving a stale entry behind: when a workspace/branch backing a held or queued PR is deleted
  (e.g. via the `cleanup-local-branches` skill), remove its `pr-review-holds.jsonl` and
  `pr-review-queue.jsonl` entries too — same cleanup rule `/post-pr` specifies for
  `pr-review-posted.jsonl`. (The flusher also drops a queued PR on its own once it is no longer
  open.)
- Treating a queued PR as a gap — it is already on its way; posting it again would double-post.

## Why this exists

Not every open PR gets its `/peer-review` loop run immediately, and sswt workspaces can sit
for days before their PR is opened, reviewed, or picked back up. Nothing else surfaces "this
has quietly gone dark and #pr-review never heard about it" — this skill is the periodic check
for that gap, with the hold-list keeping deliberately-parked work from becoming permanent noise.
