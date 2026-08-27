---
name: post-pr
description: 'Drive a PR the rest of the way to review-ready — run Codex review until clean, watch CI, post the PR to the #pr-review Slack channel, badge the description, and undraft it. Use when work on a branch is finished and its PR should be shared, e.g. "post this PR", "share it to Slack", "take this the rest of the way", "run the post-PR workflow", "it''s ready, ship the announcement". Runs automatically when a task''s work is complete (see the Post-PR Workflow section of the global CLAUDE.md); invoke by hand to re-run it for a later push, or to target a PR in a different worktree.'
---

# Post PR

## Overview

The last mile of a PR: review it until it's quiet, confirm CI is green, announce it, and flip the two signals that tell humans it's ready for eyes.

```
/loop /peer-review  →  gh pr checks --watch  →  Slack #pr-review  →  badge  →  gh pr ready
```

Every step gates the next. A PR that never comes back clean is never announced; a PR that is never announced is never badged and never leaves draft. That chain is the whole point — the draft pill is load-bearing, not cosmetic.

This skill owns the *only* place the badge and the undraft happen. Never add `📣 **Posted to Slack**` or run `gh pr ready` anywhere else, and never speculatively.

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

If the run stopped at a gate, say which gate and what is outstanding — don't bury it.

## When it fires on its own

The global CLAUDE.md instructs this to run **automatically, without asking**, once a task's work is complete. Concretely, all of:

- the task the user asked for is finished — not a checkpoint, not a WIP push
- the working tree is clean and the branch is pushed
- a PR exists for the branch

Do not fire on intermediate pushes mid-task, and do not fire on a branch whose PR is deliberately parked. When several PRs finished together, run this for each of them.

Because it self-starts, it must also self-report: state plainly what it did to each PR, including anything a gate stopped.

## Why it doesn't ask first

Posting to Slack is outward-facing, and outward-facing actions normally warrant a confirmation. The standing instruction in the global CLAUDE.md **is** that authorization — it is durable and given in advance, which is exactly what removes the need to ask each time.

The design stays safe because the gates, not the user's attention, are what hold a PR back: an unconverged review, a failing check, or a missing PR all stop the run before anything is announced. A PR reads as "not ready" until this skill has actually run, which is why [[create-pr]] and [[refresh-pr]] open PRs as drafts in the first place.
