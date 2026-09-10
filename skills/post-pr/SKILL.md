---
name: post-pr
description: 'Drive a PR the rest of the way to review-ready — run Codex review until clean, watch CI, post the PR to the #pr-review Slack channel, badge the description, and undraft it — or, for a sub-PR into a trunk branch, merge it into the trunk instead of announcing it. Use when work on a branch is finished and its PR should be shared, e.g. "post this PR", "share it to Slack", "take this the rest of the way", "run the post-PR workflow", "it''s ready, ship the announcement". Runs automatically when a task''s work is complete (see the Post-PR Workflow section of the global CLAUDE.md); invoke by hand to re-run it for a later push, or to target a PR in a different worktree.'
---

# Post PR

## Overview

The last mile of a PR: review it until it's quiet, confirm CI is green, announce it, and flip the two signals that tell humans it's ready for eyes.

```
/loop /peer-review  →  gh pr checks --watch  →  base is the default branch?
                                                ├─ yes → Slack #pr-review → badge → gh pr ready
                                                └─ no  → gh pr merge --merge into the trunk
```

Every step gates the next. A PR that never comes back clean is never announced and never merged; a PR that is never announced is never badged and never leaves draft (the one exception is the undraft that `gh pr merge` requires immediately before a trunk merge, step 3.6). That chain is the whole point — the draft pill is load-bearing, not cosmetic.

This skill owns the *only* place the badge and the undraft happen. Never add `📣 **Posted to Slack**` or run `gh pr ready` anywhere else, and never speculatively.

**External announcement (steps 4-6: Slack, badge, undraft) is gated on the PR's base branch being the repo's default branch.** A PR based on anything else — a stacked PR onto another feature branch, a sub-PR into a shared `feature/*` integration branch — is internal-only: it still gets reviewed and CI-checked (steps 1-3 run exactly as normal), and once clean it is **merged into its trunk with a merge commit (step 3.6)** instead of being posted to `#pr-review` or badged. The point is to keep the number of PRs landing in front of a human reviewer proportional to the number of semantically distinct changes, not the number of branches used to build one: a stack of sub-PRs is one reviewable unit, and the *only* member of that stack a reviewer should ever see announced is the one whose base is the default branch — typically the last one, once the stack is flattened or the integration branch itself opens its PR into main. The global CLAUDE.md calls this the **trunk flow** and says when to use it (closely-knit features → one trunk; standalone fixes → straight to main). See steps 3.5 and 3.6.

## Invocation

```
/post-pr                      # the PR for the current branch
/post-pr 1271                 # a PR by number, in the current repo
/post-pr <worktree-path>      # a PR whose branch lives in another worktree
/post-pr 41 1271 1433         # several, run to completion one at a time
```

Runs automatically when a task's work is complete — see [When it fires on its own](#when-it-fires-on-its-own).

Targeting matters more than it looks: `/peer-review` resolves against the **current branch**, so a session coordinating several PRs from a root checkout will review the wrong thing unless each target is run from its own worktree. Resolve every target to a worktree path before starting, and run each one's steps with that as the working directory.

## When NOT to use

- No PR exists for the branch yet — that's [[create-pr]]. Come back here after.
- Work is mid-flight (dirty tree, task not finished). Announcing a WIP branch wastes reviewer attention and burns Codex rounds on an unfinished diff.
- The user only wants a review pass with no PR-side effects — that's `/peer-review local`.
- The user wants existing review threads addressed rather than a fresh pass — that's [[babysit-pr]].
- The PR's base is not the repo's default branch — this skill still runs (review + CI), then merges it into the trunk (step 3.6) instead of announcing it. Don't route around this by manually posting/badging a stacked PR yourself; the trunk's own PR into main is what gets announced, once it exists and the user declares the trunk done.

## Steps

### 1. Resolve targets

For each target, establish: worktree path, repo (`owner/name`), PR number, branch. Refuse to continue on a target whose branch is the repo's default branch.

```bash
gh pr view --json number,url,title,isDraft,headRefName -q '.'
```

If several targets were given, finish one completely before starting the next. Their reviews are independent, but interleaving makes the report unreadable and the dedup state hard to reason about.

### 2. Review until clean

```
/loop /peer-review
```

Automatic review is disabled at all PR stages in these repos, so start the loop directly rather than waiting for a bot. `/peer-review` runs Codex against the branch, applies mechanical findings, commits, pushes, and comments the round count on the PR; `/loop` re-runs it until the review is quiet. That skill owns its own convergence ceiling — don't second-guess it here.

**Gate:** if the loop exits with unresolved P1/P2 findings, or hits its ceiling without converging, **stop**. Report what's outstanding and do not proceed to step 3. Those need a human decision before the PR goes near reviewers.

Never post `@codex review` to trigger a round yourself — that's [[cycle-review-pr]]'s job, and only when asked.

### 3. Watch CI

Wait 5 seconds after the last push settles before reading checks, or you will pick up stale results from the previous push and declare a red PR green.

```bash
sleep 5
gh pr checks <PR_NUMBER> --repo <OWNER/REPO> --watch
```

Report every check: name, status, URL. Flag failures prominently with their URLs.

**Gate:** any failing check stops the workflow here. Announcing a red PR is worse than not announcing it.

Checks reporting `NEUTRAL` or `SKIPPED` are not failures — Netlify's informational checks land this way routinely.

A PR whose base is not the repo's default branch typically has *fewer* checks to watch, not zero — this repo's `unit-tests.yml` and `e2e-tests.yml` are both scoped to `pull_request: branches: [main]`, so a stacked PR only gets whatever workflows aren't main-restricted (e.g. a labeler). That's expected, not a red flag: don't wait for checks that structurally cannot run, and don't treat their absence as a gate failure. Rely on local verification (tests, typecheck) for the rest.

### 3.5. Check whether this PR is externally announceable

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json baseRefName -q .baseRefName
gh api <OWNER/REPO> --jq .default_branch   # or: git symbolic-ref refs/remotes/origin/HEAD --short
```

If `baseRefName` is **not** the repo's default branch, this PR is internal-only:

- **Branch here.** Skip steps 4-6 (Slack, badge, undraft) for this target and go to step 3.6 instead.
- Report it plainly (see step 7's format) — this is not a failure, it's the normal outcome for a stacked/sub-PR, but it must be visible so nobody assumes silence means the workflow didn't run.

If `baseRefName` **is** the default branch, skip step 3.6 and continue to step 4 as normal.

This check is per-target and re-evaluated every time the skill runs — a PR that starts stacked and later gets retargeted onto the default branch (e.g. once its integration branch merges) becomes announceable the next time `/post-pr` runs against it, with no special-casing needed.

### 3.6. Merge a sub-PR into its trunk

Only for a target that step 3.5 classified as internal-only. Steps 2 and 3 must both have passed — a sub-PR that didn't converge or has a failing check is not merged, for the same reason it would not be announced.

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json baseRefName,mergeable,mergeStateStatus,isDraft -q '.'
git ls-remote --heads origin <BASE_REF>    # the trunk must exist on origin
```

Merge when `mergeable` is `MERGEABLE` and `mergeStateStatus` is `CLEAN` — or `UNSTABLE` only because main-scoped workflows (unit-tests, e2e-tests, Netlify) structurally didn't run, per the note under step 3. Any other state stops here.

```bash
gh pr ready <PR_NUMBER> --repo <OWNER/REPO>        # only if isDraft — GitHub refuses to merge a draft
gh pr merge <PR_NUMBER> --repo <OWNER/REPO> --merge --delete-branch=false
```

- **Merge commit, never squash.** Squashing a sub-PR rewrites its commits and orphans the base of anything stacked on it (the restack pain [[rebase-after-squash]] exists to undo). The trunk is squashed into main at the end, so intermediate history never reaches main anyway.
- **Keep the branch.** Superset autoprunes a workspace whose branch disappears; deleting it mid-session pulls the worktree out from under whoever is working in it. Let [[clean-sswts]] / [[cleanup-local-branches]] reap it later.
- **The undraft here is a merge prerequisite, not a signal.** No human reviews a sub-PR, so its draft pill was never load-bearing. Do it immediately before the merge and never badge the PR.
- **Conflicts** (`CONFLICTING` / `DIRTY`): stop and report "merge blocked — conflicts with `<trunk>`". Don't resolve them automatically; that is a judgment call for the session that owns the branch (see [[resolve-conflict]]).
- After the merge the trunk PR's diff has grown — say so in the report, and pull the trunk in the worktree if the next piece of work starts from it. Nothing is written to `pr-review-posted.jsonl`; there was no post to dedup.

### 4. Post to Slack

Channel `#pr-review` (ID `C0APAAD3PP0`). The message body is *only* the link:

```
<PR_URL|[REPO_LABEL] PR_TITLE>
```

The repo label and title together form the hyperlink text. Nothing outside the link — no preamble, no emoji, no summary.

| Repo | Label |
|---|---|
| woodrow | `ui` |
| api | `api` |
| folio-platform | `platform` |

Post as the real user via `chat.postMessage` with the `xoxp` token from the macOS Keychain — not the claude.ai Slack connector, which posts with an app tag:

```bash
TOKEN=$(security find-generic-password -s slack-pr-review-user-token -a "$USER" -w)
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-type: application/json; charset=utf-8' \
  --data @payload.json
```

with `unfurl_links: false` and `unfurl_media: false` in the payload. Build the payload as a file rather than an inline shell string — PR titles carry backticks, quotes and `$` that command substitution will mangle.

The response showing `bot_id` and "Jake (bot)" is expected and does **not** mean the token is wrong — it still renders as plain "Jake" in the Slack UI.

**Dedup, before posting:** check `~/.claude/state/pr-review-posted.jsonl` for the PR's URL and skip if it's already there. After a successful post, append one line:

```json
{"url": "...", "repo": "...", "pr": 0, "postedAt": "2026-01-01T00:00:00Z"}
```

This file is local dedup state, not durable memory. When a PR's sswt workspace/branch is deleted (e.g. by [[cleanup-local-branches]]), drop its entry too — a gone workspace means the work is done and there is no double-post left to guard against.

### 5. Badge the description

Only after a *real* post in step 4 — never after one the dedup check skipped, and never after a loop that failed to converge.

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json body -q .body > /tmp/pr-body.txt
```

Read the body through a file. Do not round-trip it through a shell string; bodies contain backticks and `$` that command substitution will mangle.

If it already ends with `📣 **Posted to Slack**`, it is already badged — skip the append. (This happens when the dedup jsonl lost its entry while the PR was still open.) Otherwise append a trailing `---` and a `📣 **Posted to Slack**` line, then:

```bash
gh pr edit <PR_NUMBER> --repo <OWNER/REPO> --body-file /tmp/pr-body.txt
```

Append only. Never regenerate the rest of the body here.

### 6. Undraft

```bash
gh pr ready <PR_NUMBER> --repo <OWNER/REPO>
```

Only if it is currently a draft. Together with the badge this gives reviewers two independent signals that the PR has been shared and wants eyes.

### 7. Report

Per target, one block:

```
Concentro-Inc/api#1271
- Review: clean after 3 rounds
- CI: 4 checks, all pass
- Slack: posted (or: skipped, already in dedup state)
- Badge: appended    Draft: cleared
```

For a sub-PR (step 3.5 classified it internal-only), report the trunk merge in place of the announcement — never omit the announcement steps silently:

```
Concentro-Inc/woodrow#1555
- Review: clean after 2 rounds
- CI: 1 check (label), pass — unit-tests/e2e-tests don't run against a non-main base
- Base: feature/vde-airtable-parity (not the default branch) — sub-PR, not announced
- Merged into feature/vde-airtable-parity (merge commit abc1234); trunk PR #1500 now carries it
- Slack / Badge: skipped (step 3.5 gate)
```

If the merge was blocked, replace the `Merged` line with `Merge: blocked — conflicts with <trunk>` and say what is outstanding.

If the run stopped at a different gate (unconverged review, failing check), say which gate and what is outstanding — don't bury it.

## When it fires on its own

The global CLAUDE.md instructs this to run **automatically, without asking**, once a task's work is complete. Concretely, all of:

- the task the user asked for is finished — not a checkpoint, not a WIP push
- the working tree is clean and the branch is pushed
- a PR exists for the branch

Do not fire on intermediate pushes mid-task, and do not fire on a branch whose PR is deliberately parked. When several PRs finished together, run this for each of them.

Firing automatically still runs the full skill, base-branch gate included: a finished task on a branch stacked onto something other than the default branch gets reviewed and CI-checked same as any other, then merged into its trunk (step 3.6) rather than announced. The trunk's own PR into the default branch is announced when the user declares the trunk complete — not automatically on each sub-merge.

Because it self-starts, it must also self-report: state plainly what it did to each PR, including anything a gate stopped.

## Why it doesn't ask first

Posting to Slack is outward-facing, and outward-facing actions normally warrant a confirmation. The standing instruction in the global CLAUDE.md **is** that authorization — it is durable and given in advance, which is exactly what removes the need to ask each time.

The design stays safe because the gates, not the user's attention, are what hold a PR back: an unconverged review, a failing check, a missing PR, or a non-default base branch all stop the run before anything is announced. A PR reads as "not ready" until this skill has actually run, which is why [[create-pr]] and [[refresh-pr]] open PRs as drafts in the first place.
