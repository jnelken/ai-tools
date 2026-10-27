---
name: post-pr
description: 'Drive a PR the rest of the way to review-ready — run Codex review until clean, watch CI, post the PR to the #pr-review Slack channel, badge the description, and undraft it — or, for a sub-PR into a trunk branch, merge it into the trunk and reap its sswt instead of announcing it. Use when work on a branch is finished and its PR should be shared, e.g. "post this PR", "share it to Slack", "take this the rest of the way", "run the post-PR workflow", "it''s ready, ship the announcement". Runs automatically when a task''s work is complete (see the Post-PR Workflow section of the global CLAUDE.md); invoke by hand to re-run it for a later push, or to target a PR in a different worktree.'
---

# Post PR

## Overview

The last mile of a PR: review it until it's quiet, confirm CI is green, announce it, and flip the two signals that tell humans it's ready for eyes.

```
/loop /peer-review  →  resolve base conflicts  →  gh pr checks --watch  →  base is the default branch?
                                                ├─ yes → Slack #pr-review → badge → gh pr ready → offer screenshot to #product-updates
                                                └─ no  → gh pr merge --merge into the trunk → reap the sub-PR's sswt
```

Every step gates the next. A PR that never comes back clean is never announced and never merged; a PR that is never announced is never badged and never leaves draft (the one exception is the undraft that `gh pr merge` requires immediately before a trunk merge, step 3.6). That chain is the whole point — the draft pill is load-bearing, not cosmetic.

This skill owns the *only* place the badge and the undraft happen. Never add `📣 **Posted to Slack**` or run `gh pr ready` anywhere else, and never speculatively.

**External announcement (steps 4-6: Slack, badge, undraft) is gated on the PR's base branch being the repo's default branch.** A PR based on anything else — a stacked PR onto another feature branch, a sub-PR into a shared `feature/*` integration branch — is internal-only: it still gets reviewed and CI-checked (steps 1-3 run exactly as normal), and once clean it is **merged into its trunk with a merge commit (step 3.6)** instead of being posted to `#pr-review` or badged. The point is to keep the number of PRs landing in front of a human reviewer proportional to the number of semantically distinct changes, not the number of branches used to build one: a stack of sub-PRs is one reviewable unit, and the *only* member of that stack a reviewer should ever see announced is the one whose base is the default branch — typically the last one, once the stack is flattened or the integration branch itself opens its PR into main. The global CLAUDE.md calls this the **trunk flow** and says when to use it (closely-knit features → one trunk; standalone fixes → straight to main). See steps 3.5 and 3.6.

## Invocation

```
/post-pr                      # the PR for the current branch
/post-pr 1271                 # a PR by number, in the current repo
/post-pr <worktree-path>      # a PR whose branch lives in another worktree
/post-pr 41 1271 1433         # several: one per worktree, run in parallel
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

Stamp the run's start for the metrics ledger, from the target's worktree: `~/.ai-tools/bin/review-metrics mark started`. Also stamp `review_done` when step 2 passes its gate and `ci_done` when step 3 does, and finish every run (announced, merged, or stopped at a gate) with the ledger line in step 7. Ledger writes are best-effort: ignore a nonzero exit.

If several targets were given, run them **in parallel**. Targets in different worktrees (and especially different repos) are independent: dispatch each into its own sswt with its own agent (`superset workspaces create --local --project <id> --branch <branch> --agent claude --prompt "/post-pr <n>"`, or one Agent call per target in a single message) rather than finishing one before starting the next. Each run owns its worktree, so reviews, CI watches and merges don't interact. Append to `pr-review-posted.jsonl` with a single `>>` line write so concurrent runs can't clobber each other, and have each run report its own block (step 7) rather than interleaving output.

Serialize only for a real dependency: two targets sharing a worktree or branch, a sub-PR whose trunk another target is still merging into, or the user stating a hard ordering requirement. A soft ordering hint ("API first so the endpoint exists") is not a hard requirement: run in parallel and flag the risk in the report (e.g. "UI PR announced before the API PR merged").

### 2. Review until clean

```
/loop /peer-review
```

Automatic review is disabled at all PR stages in these repos, so start the loop directly rather than waiting for a bot. `/peer-review` runs Codex against the branch, applies mechanical findings, commits, pushes, and comments the round count on the PR; `/loop` re-runs it until the review is quiet. That skill owns its own convergence ceiling — don't second-guess it here.

**Rate limits are not a gate.** A Codex throttle (`rate_limited`: 429, tokens-per-minute) is waited out and retried inside `/peer-review`, then falls back to the GitHub review on the other credential pool (see below) if it persists; the run continues rather than stopping at step 2. Parallel `/post-pr` runs share one Codex quota, so when dispatching several targets at once, stagger the launches by ~60s or lower the lens count (`lenses=1`) instead of serializing them. Report any throttled retries in the step 7 block (`Review: clean after 3 rounds, 2 throttled retries`). Stop only if the fallback itself can't run (no PR, `local` mode) and say so.

**Gate:** if the loop exits with unresolved P1/P2 findings, or hits its ceiling without converging, **stop**. Report what's outstanding and do not proceed to step 3. Those need a human decision before the PR goes near reviewers.

The GitHub fallback is whichever trigger bills the *other* credential pool from local Codex: `/codex-action-review` (API key) when local runs on a ChatGPT login, `@codex review` (ChatGPT plan) when local runs on an API key. `/peer-review` owns the pick; either may be posted without asking.

**Watching the GitHub fallback.** The fallback is a `codex-review.yml` run, and a run that *fails* (runner or install error — not "requests changes") posts no review at all, so a watch keyed on "a review appears" never fires. Treat a failed run as a failed gate: find it with `gh run list --workflow codex-review.yml --branch <branch>` (the `issue_comment` event), read `gh run view <id> --log-failed`, and if the cause is infra — npm registry `error (23)` retries, `Missing optional dependency @openai/codex-linux-x64` — re-post the trigger comment **once**; a second failure is reported, not retried again. Re-arm the Monitor on every expiry until the run concludes, the same way `superset-workspaces.md` watches a dispatched agent — an expired watch is indistinguishable from a review still running. Evidence: api#1544 sat ~18h (2026-10-15→16) after run 38803271874 failed on npm flakiness and the watch quietly expired; woodrow#1724's first run failed the same way and passed on retry.

### 2.5. Resolve merge conflicts with the base

```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json mergeable,mergeStateStatus -q '.'
```

If `mergeable` is `CONFLICTING` (`mergeStateStatus` `DIRTY`), resolve it here, in the PR's worktree. Don't stop and report it. GitHub can report `UNKNOWN` for a few seconds after a push, so re-read until it settles.

1. `git fetch origin <base> && git merge origin/<base>`. Merge, don't rebase: the branch is already shared.
2. Resolve each conflict to keep **both** intents: the branch's change applied on top of what the base added. When the branch renamed or moved something that the base edited, carry the base's edit into the new name or location. Don't take one side wholesale. [[resolve-conflict]] has the procedure.
3. After an auto-merge, check for stale references to anything the branch renamed or deleted. Regenerate generated files, since they conflict or drift silently.
4. Verify before committing: typecheck every touched file (git auto-merge is not type-safe), and run the tests.
5. Commit the merge, push, and say in the report what the base brought in and how each conflict was resolved.

The re-push restarts CI, so step 3 watches the merge commit. Re-check mergeability whenever this skill runs again on the same PR, including for an already-approved PR. Main moves while a PR waits for review.

Stop and report only when a conflict needs a product or design decision: both sides changed the same behavior in incompatible ways. A conflict in imports, renames or neighboring edits is mechanical, so resolve it.

### 3. Watch CI

Wait 5 seconds after the last push settles before reading checks, or you will pick up stale results from the previous push and declare a red PR green.

```bash
sleep 5
gh pr checks <PR_NUMBER> --repo <OWNER/REPO> --watch
```

Report every check: name, status, URL. Flag failures prominently with their URLs.

**Gate:** any failing check stops the workflow here. Announcing a red PR is worse than not announcing it.

Checks reporting `NEUTRAL` or `SKIPPED` are not failures — Netlify's informational checks land this way routinely.

**A green check is not always a green step.** In woodrow, the Unit Tests and Lint & Typecheck steps run with `continue-on-error`, so the check reports pass even when tests or lint fail. Read the step logs for the head commit (`gh run list --commit <sha>`, then `gh run view <id> --log`, grepping for the vitest `Tests`/`Test Files` summary, `✖ N problems`, and `error TS`). Failures the branch introduced gate like a red check. Errors that already exist on the base count as not introduced only after you compare error counts per file against the base (`git show origin/<base>:<file> | eslint --stdin --stdin-filename <file>`). Report them separately rather than silently passing them.

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
- **Keep the remote branch at merge time** (`--delete-branch=false`). Branches stacked on it still resolve, and the PR keeps its head ref. The sub-PR's local workspace and branch are reaped separately, as the run's last action (step 3.7).
- **The undraft here is a merge prerequisite, not a signal.** No human reviews a sub-PR, so its draft pill was never load-bearing. Do it immediately before the merge and never badge the PR.
- **Conflicts** (`CONFLICTING` / `DIRTY`): resolve them as in step 2.5, with the trunk as the base. Stop and report "merge blocked — conflicts with `<trunk>`" only for a conflict that needs a product or design decision.
- After the merge the trunk PR's diff has grown — say so in the report, and pull the trunk in the worktree if the next piece of work starts from it. Nothing is written to `pr-review-posted.jsonl`; there was no post to dedup.

### 3.7. Reap the sub-PR's sswt

Only after step 3.6 merged the PR. A merged sub-PR's workspace has nothing left to do, so reaping it is part of the run, not a later cleanup. The trunk's own workspace is never reaped here; it lives until the trunk PR merges into main.

Deleting the workspace closes the terminal of whatever session runs inside it — usually this one. That makes ordering load-bearing:

1. **Put the record on the PR first.** Post the step 7 sub-PR report as a PR comment (`gh pr comment <PR_NUMBER> --body-file …`). The PR description plus that comment are the durable record; the terminal is not.
2. **Check, and skip the reap if any check fails:**
   ```bash
   git -C <WORKTREE> status --porcelain                          # must be empty
   git -C <WORKTREE> log --oneline origin/<BRANCH>..HEAD         # must be empty (nothing unpushed)
   git fetch origin <TRUNK> && git merge-base --is-ancestor "$(git -C <WORKTREE> rev-parse HEAD)" origin/<TRUNK>
   gh pr list --repo <OWNER/REPO> --base <BRANCH> --state open   # must be empty (nothing stacked on it)
   superset workspaces list --local --json                       # resolve the workspace id by branch
   ```
   A failed check is reported as `Reap: skipped — <which check>`, never worked around.
3. **Reap as the very last command,** detached so it finishes even though it kills the calling terminal:
   ```bash
   nohup sh -c 'superset workspaces delete <WORKSPACE_ID> --local; \
     git -C <ROOT_CHECKOUT> branch -D <BRANCH>; git -C <ROOT_CHECKOUT> worktree prune' >/dev/null 2>&1 &
   ```
   `<ROOT_CHECKOUT>` is the repo's main checkout (e.g. `$WOODROW_ROOT`), never the worktree being deleted. Nothing may run after this line: the session ends with it.

When `/post-pr` runs from outside the sub-PR's worktree (a coordinator targeting it), the same checks and commands apply, and the coordinator simply keeps running afterwards.

### 3.8. Point the Preview at the changed screen

Only for a `Concentro-Inc/woodrow` PR whose base is the default branch, before step 4. api and folio-platform have no deploy previews, and a sub-PR into a trunk never gets one (step 3.5).

woodrow's PR guide (`docs/guides/pr-descriptions.md`, `## Preview`) owns the host, flag-param and omit rules. This step only makes sure each link opens the screen a reviewer has to look at, not the app's front page:

- Find the route in `app/lib/routes/definitions.ts`. For a screen inside an entity (an entity's tab, such as Document Studio), use `buildEntityViewPath.ts`. Link the deepest path that shows the change, with any flag params after it.
- A screen that needs an entity uses the stage entity `/e/portfolios/legends-solar-bc65ea81da/<view>`: swap `dashboard` for the view under review. Never invent an entity id. Deploy previews talk to the stage API, so this entity exists there. Claudio's drafter uses the same entity (`WOODROW_PREVIEW_ENTITY_PATH` in internal-tools' `services/claudio-slack-bot/src/prDrafter.ts`); change both together.
- When the change shows on several distinct screens, give one link per screen, each on its own line as `<label>: <url>`.
- Link the bare host only when the change is genuinely app-wide (theme tokens, global layout, the app shell) and no single screen represents it.

If the body's `## Preview` already follows these rules, leave it. Otherwise rewrite only that section, reading and writing the body through a file the same way step 5 does. Add the section, placed after `## Summary` as the guide's template has it, when it's missing and the change is visible in a preview.

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

**Hygiene tag.** When the PR's net diff (`git diff <base>...HEAD`) is *only* code hygiene — codemods, behavior-preserving refactors or moves/renames, test fixes, lint or format fixes, with no user-visible or runtime behavior change — insert `[hygiene]` right after the repo label: `<PR_URL|[api][hygiene] PR_TITLE>`. It tells reviewers the PR is a skim-for-safety read, not a behavior review. One tag covers all of those; don't split it into `[lintfix]` / `[testfix]`. A PR that mixes hygiene with any feature, fix or config change gets no tag. The tag goes in the Slack link text only, never in the PR title itself. Whenever the tag applies, also add the GitHub label before posting: `gh pr edit <PR> --add-label hygiene` (idempotent). If the repo has no `hygiene` label, don't create it — skip and say so in the report.

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

### 6.5 Offer a product update

Only for a PR whose base is the default branch, after step 6 — a sub-PR merged into a trunk (step 3.6) never reaches this step. Skip it when the PR URL is already in `~/.claude/state/product-updates-posted.jsonl`, which records `posted` **and** `declined` lines so a re-run never re-asks.

This is the one step that asks. Steps 4-6 are covered by the standing authorization in the global CLAUDE.md; a post to `#product-updates` (ID `C0C9DKM77HA`) is a user-facing announcement and is not, so Jake decides per PR whether the screenshot is worth announcing.

1. **Find and download the screenshots.** `scripts/fetch-pr-screenshots.sh <owner/repo> <pr> "$(mktemp -d)"` prints one local image path per line (at most 4, de-duplicated, verified as real images under 10 MB). No output means the PR has no screenshot: report `n/a (no screenshot)` and stop without asking.
2. **Show them, then ask once.** `Read` each image so Jake sees it, then `AskUserQuestion`: *Post this to #product-updates?* with **Post**, **Skip**, and Other for a caption of their own. If no human can answer (unattended run), treat it as no answer: report `not offered (unattended)`, record nothing, so a later interactive run still asks.
3. **Caption.** Match the channel's existing posts: one plain sentence a customer would understand, naming the visible surface, then the PR as a link, e.g. `You can now continue someone else's folio thread that was shared with you (<PR_URL|#1618>)`. Draft it from the PR's `## Summary`; no CON-IDs, no file or function names, no `[ui]`/`[api]` label. Write it to a file (titles carry backticks and `$`).
4. **Post.** `scripts/slack-upload-images.sh C0C9DKM77HA <caption-file> <image>...` — all images in one message, as the real user through the Keychain `xoxp` token, which needs the `files:write` user scope (`missing_scope` means it was lost). It exits nonzero with Slack's error; report it as `failed (<error>)` and do not record the PR, and never fail the run over it.
5. **Record**, one `>>` line each, `posted` on success and `declined` on Skip:

```json
{"url": "...", "repo": "...", "pr": 0, "action": "posted", "postedAt": "2026-01-01T00:00:00Z"}
```

Never run `slack-upload-images.sh` to "test" it against a real channel — it posts for real. Point it at Jake's own DM channel instead.

### 7. Report

Record the run in the metrics ledger before reporting (and before step 3.7's reap, which kills the terminal): `~/.ai-tools/bin/review-metrics run --outcome posted|merged|stopped|skipped [--gate review|ci|conflicts|merge]`. Use `stopped` with the gate that fired when the run halted early, `skipped` when the dedup check skipped the Slack post. It reads the phase stamps and round count from the worktree, so run it from there. `~/.ai-tools/bin/review-metrics summary` aggregates rounds, durations and gate stops.

Per target, one block:

```
Concentro-Inc/api#1271
- Review: clean after 3 rounds
- CI: 4 checks, all pass
- Preview: woodrow only — set (or: already set, n/a (not visible in a preview))
- Slack: posted (or: skipped, already in dedup state)
- Badge: appended    Draft: cleared
- Product update: posted (or: declined, n/a (no screenshot), not offered (unattended), failed (<why>))
```

For a sub-PR (step 3.5 classified it internal-only), report the trunk merge in place of the announcement — never omit the announcement steps silently:

```
Concentro-Inc/woodrow#1555
- Review: clean after 2 rounds
- CI: 1 check (label), pass — unit-tests/e2e-tests don't run against a non-main base
- Base: feature/vde-airtable-parity (not the default branch) — sub-PR, not announced
- Merged into feature/vde-airtable-parity (merge commit abc1234); trunk PR #1500 now carries it
- Slack / Badge / Product update: skipped (step 3.5 gate)
- Reap: sswt + local branch deleted (or: skipped — <which check>)
```

Post this report on the PR before the reap (step 3.7), since the reap closes the terminal it was printed in.

If the merge was blocked, replace the `Merged` line with `Merge: blocked — conflicts with <trunk>` and say what is outstanding.

If the run stopped at a different gate (unconverged review, failing check), say which gate and what is outstanding — don't bury it.

## When it fires on its own

The global CLAUDE.md instructs this to run **automatically, without asking**, once a task's work is complete. Concretely, all of:

- the task the user asked for is finished — not a checkpoint, not a WIP push
- the working tree is clean and the branch is pushed
- a PR exists for the branch

Do not fire on intermediate pushes mid-task, and do not fire on a branch whose PR is deliberately parked. When several PRs finished together, run this for each of them.

Firing automatically still runs the full skill, base-branch gate included: a finished task on a branch stacked onto something other than the default branch gets reviewed and CI-checked same as any other, then merged into its trunk (step 3.6) and its sswt reaped (step 3.7) rather than announced. The trunk's own PR into the default branch is announced when the user declares the trunk complete — not automatically on each sub-merge.

Because it self-starts, it must also self-report: state plainly what it did to each PR, including anything a gate stopped.

## Why it doesn't ask first

Posting to Slack is outward-facing, and outward-facing actions normally warrant a confirmation. The standing instruction in the global CLAUDE.md **is** that authorization — it is durable and given in advance, which is exactly what removes the need to ask each time.

The design stays safe because the gates, not the user's attention, are what hold a PR back: an unconverged review, a failing check, a missing PR, or a non-default base branch all stop the run before anything is announced. A PR reads as "not ready" until this skill has actually run, which is why [[create-pr]] and [[refresh-pr]] open PRs as drafts in the first place.

The authorization covers the `#pr-review` post, the badge, the undraft and the trunk merge — nothing else. The `#product-updates` screenshot post (step 6.5) is a separate, user-facing announcement, so it is asked about once, after everything above is done.
