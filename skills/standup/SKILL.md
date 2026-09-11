---
name: standup
description: "Scan Superset workspaces, open PRs across woodrow/api/folio-platform, unpushed local branches, and active Linear tickets into one standup-ready report — bucketed by what needs a decision today, with the remediation skill named per finding. Read-only: it never merges, uploads, posts, or files anything. Trigger phrases include \"standup\", \"what's my open work\", \"scan my work for standup\", \"daily work scan\", \"what am I working on\", \"what's blocked\", \"where did I leave things\"."
---

# Standup

## Overview

Open work lives on four surfaces that nothing joins up: Superset workspaces (sswts), open GitHub
PRs across three repos, local branches, and the Linear assigned list. This skill collects all
four, reconciles them against each other, and cuts the result down to what actually needs a
decision today.

**Read-only aggregator.** Remediation already exists — `/fix-pr-conflicts`, `/clean-sswts`,
`/cleanup-local-branches`, `/pr-review-gaps`, `/post-pr`, `/create-pr`, `/pick-up`. This skill
does the cross-reference and the cut, then *names* the remediation skill per finding. It never
merges, publishes a branch, announces to Slack, or mutates Linear.

The check that justifies the skill is **step 5** — committed work with no remote branch and no
PR. Nothing else surfaces it, and it is how a finished cross-repo pair can sit invisible to the
whole team for days.

## Steps

0. **Load state.** Read `~/.claude/state/standup-last-run.json` (`{"lastRunAt": "<ISO8601>"}`).
   Absent → default the "shipped since" window to 24h, or 72h if today is Monday. Never hardcode
   a date. Write the new timestamp at the very end, only on a successful run.

1. **List workspaces.** `superset workspaces list` — the **local CLI**, not the host-scoped MCP
   tools, which cannot reach this host (see "Superset MCP Tools vs Local CLI" in the global
   `CLAUDE.md`). Entries with `type != "main"` are feature worktrees. Entries with
   `type == "main"` are root checkouts — carry them only to report dirt or divergence, never as
   feature work.

2. **Per-worktree git state.** One loop over each `worktreePath`:
   ```bash
   git -C "$p" status --porcelain | wc -l                        # dirty count
   git -C "$p" rev-parse --abbrev-ref HEAD                       # branch
   git -C "$p" rev-list --left-right --count origin/main...HEAD  # behind<TAB>ahead
   git -C "$p" log -1 --format='%ad %s' --date=short             # last commit
   ```
   `--left-right --count` prints **behind first, ahead second**. Getting that backwards inverts
   every staleness claim in the report.

3. **Open PRs per repo.**
   ```bash
   gh pr list --repo <repo> --author @me --state open \
     --json number,title,isDraft,baseRefName,headRefName,updatedAt,url
   ```
   Repo map (same static table `pr-review-gaps` uses): `woodrow` and `woodrow (alt)` →
   `Concentro-Inc/woodrow`; `api` → `Concentro-Inc/api`; `folio-platform` →
   `Concentro-Inc/folio-platform`. Everything else (`ai-tools`, `dotfiles`, `internal-tools`,
   `interview`, `gh-tab-mgmt`) is not PR-tracked — skip.

4. **Per-PR detail**, for active PRs only (skip anything already destined for the parked
   roll-up):
   ```bash
   gh pr view <n> -R <repo> --json body,isDraft,mergeable,reviewDecision,updatedAt,statusCheckRollup
   ```
   Derive:
   - `slackPosted` = body contains the literal string `Posted to Slack`
   - `conflicting` = `mergeable == "CONFLICTING"`
   - `reviewDecision` = `APPROVED` / `REVIEW_REQUIRED` / `CHANGES_REQUESTED`
   - open review threads:
     `gh api repos/<repo>/pulls/<n>/comments --jq '[.[]|select(.in_reply_to_id==null)]|length'`
   - A PR whose `baseRefName` is not `main` is a **sub-PR into a trunk** — internal by design,
     never a Slack gap. Bucket it under the trunk, not on its own.

5. **Unpushed-branch sweep — the first-class check.** A branch is **invisible work** when all
   three hold:
   ```bash
   git -C "$p" ls-remote --heads origin "$b" | wc -l      # → 0: no remote branch
   git -C "$p" cherry origin/main "$b" | grep -c '^+'     # → >0: real unmerged content
   ```
   ...and no open PR from step 3 has that `headRefName`.

   Use `git cherry`, never `git log origin/main..<branch>`. woodrow and api **squash-merge every
   PR**, so a merged commit's hash never appears in main and plain `log` reports shipped work as
   unmerged. `git cherry` compares patch-ids: a `-` line means "already upstream under a
   different hash." **Zero `+` lines = residue from a squash-merged PR** → parked roll-up, never
   invisible work.

6. **Linear, filtered.** Query directly over GraphQL with `LINEAR_API_KEY` (see the
   `reference_linear_api_key` memory) rather than loading MCP schemas. Filter assignee = me,
   state type not in `completed`/`canceled`. Then **keep only**:
   - state `In Progress` or `In Review`
   - any ticket ID referenced by an active PR title or branch name
   - anything updated since `lastRunAt`

   Cross-check for **stale-open**: a ticket sitting in `In Review` whose PR has already merged.
   Do not dump the full assigned list — it runs to ~60 issues and buries everything.

7. **Pending decisions.** Grep the `## Decisions needed` section of
   `<repo>/.claude/IN_PROGRESS.md` for woodrow, api, and folio-platform. Report headlines and
   counts only — `/pick-up` owns expanding and acting on them.

8. **Shipped since last run.** `gh pr list --author @me --state merged` per repo, filtered to
   `mergedAt > lastRunAt`. A count per repo is usually enough; list titles only if asked.

## The cut

Report **bucketed by action, not by repo**. Omit any empty bucket entirely — never print an
empty heading.

| # | Bucket | Contents | Points at |
|---|--------|----------|-----------|
| 1 | **Ready to ship** | approved, mergeable, not draft, no blocking checks | merge it |
| 2 | **Invisible work** | step 5 branches: commit count, last-commit date, ticket state | `/create-pr` |
| 3 | **Merge-blocked** | `CONFLICTING` PRs, with ahead/behind | `/fix-pr-conflicts` |
| 4 | **Needs review attention** | open threads, `REVIEW_REQUIRED`, or non-draft and not Slack-posted | `/post-pr`, `/babysit-pr` |
| 5 | **Ticket hygiene** | stale-open Linear, QA-follow-up tickets on open PRs | — |
| 6 | **Decisions pending** | step 7 headlines | `/pick-up` |
| 7 | **Parked** | ONE roll-up line: drafts stale >30d, squash residue | `/clean-sswts` |

Invisible work sits near the top even though it is discovered last — it is the only bucket the
rest of the team cannot see at all.

Close with the shipped-since counts and any evidence caveat (see below).

## Common mistakes

- **Reading `rev-list --left-right --count A...HEAD` as ahead/behind.** It is `behind<TAB>ahead`.
  This turns "48 commits behind main" into "48 ahead" — and the behind-count is what makes a
  PR's Netlify deploy preview untrustworthy (previews build the merge commit).
- **Using commit presence instead of `git cherry` to decide merged-ness.** Both repos squash, so
  a hash-based check reports shipped branches as open work. Seen live: a branch that looked like
  one commit of unmerged work was entirely residue of an already-merged PR.
- **Guessing the Slack badge string.** It is literally `📣 **Posted to Slack**` in the PR body.
  A plausible first guess (`pr-review|Shared`) returned false for every PR including three that
  *were* announced, which silently turns the whole "needs review attention" bucket into noise.
- **zsh does not word-split unquoted parameters.** `set -- $spec` leaves the whole string in
  `$1`. Use `${spec%%:*}` / `${spec##*:}` parameter expansion, or `${=spec}`.
- **Claiming "CI green" from an empty `statusCheckRollup` failure list.** woodrow's tests, lint,
  and typecheck are all `continue-on-error`, so they cannot fail the rollup. Say "no blocking
  checks."
- **Dumping the full Linear assigned list or every open draft PR.** The filter is the feature; an
  unfiltered scan is worse than no scan because it reads as complete.
- **Treating a sub-PR into a `feature/*` trunk as an unannounced PR.** Sub-PRs never reach Slack
  by design — only the trunk's PR into `main` is announceable.
- **Taking any write action.** No merge, no branch upload, no Slack message, no Linear mutation.
  Name the remediation skill and stop.

## Why this exists

Every other skill in this set fixes one thing after you already know it needs fixing. Nothing
answers "what is open right now, and which of it is a decision rather than a chore." The
expensive part is not collecting the data — it is the cut, and the cut needs all four surfaces
at once to be correct: a PR is only "needs review" if Linear does not already say it shipped; a
branch is only "invisible" if no PR covers it and its commits are genuinely not in main.
