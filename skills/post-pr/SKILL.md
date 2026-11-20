---
name: post-pr
description: 'Drive a PR the rest of the way to review-ready — run Codex review until clean, watch CI, then queue the PR for the next #pr-review window (a launchd flusher posts it to Slack, badges the description, undrafts it and enables auto-merge) — or, for a sub-PR into a trunk branch, merge it into the trunk and reap its sswt instead of announcing it. Use when work on a branch is finished and its PR should be shared, e.g. "post this PR", "share it to Slack", "take this the rest of the way", "run the post-PR workflow", "it''s ready, ship the announcement". Runs automatically when a task''s work is complete (see the Post-PR Workflow section of the global CLAUDE.md); invoke by hand to re-run it for a later push, or to target a PR in a different worktree.'
---

# Post PR

## Overview

The last mile of a PR: review it until it's quiet, confirm CI is green, and queue it for `#pr-review`. The team's review capacity is limited, so announcements go out in batches: the `pr-review-batch` flusher (a launchd job, [`automations/pr-review-batch`](../../automations/pr-review-batch/flush.py)) releases the queue on window boundaries (`:00`/`:30` by default), one Slack message per PR, and only then flips the two signals that tell humans a PR is ready for eyes.

```
/loop /peer-review  →  resolve base conflicts  →  gh pr checks --watch  →  base is the default branch?
                                                ├─ yes → [hygiene] label → enqueue → offer screenshot to #product-updates
                                                │          … next window: flusher → Slack #pr-review → posted.jsonl → badge → gh pr ready → auto-merge
                                                └─ no  → gh pr merge --merge into the trunk → reap the sub-PR's sswt
```

Every step gates the next. A PR that never comes back clean is never queued and never merged. A queued PR **stays draft and unbadged** until the flusher actually posts it; a PR that is never posted is never badged and never leaves draft (the one exception is the undraft that `gh pr merge` requires immediately before a trunk merge, step 3.6). That chain is the whole point — the draft pill is load-bearing, not cosmetic.

**The invariant:** the badge, the undraft and auto-merge happen only after a real `#pr-review` post, and only the flusher makes that post. Never add `📣 **Posted to Slack**`, run `gh pr ready` or enable auto-merge on an announceable PR anywhere else, and never speculatively. To post *now*, run the flusher on that one URL (`--now`, step 4); don't post by hand.

**External announcement (step 4: enqueue, then the flusher's post, badge, undraft and auto-merge) is gated on the PR's base branch being the repo's default branch.** A PR based on anything else — a stacked PR onto another feature branch, a sub-PR into a shared `feature/*` integration branch — is internal-only: it still gets reviewed and CI-checked (steps 1-3 run exactly as normal), and once clean it is **merged into its trunk with a merge commit (step 3.6)** instead of being queued for `#pr-review` or badged. The point is to keep the number of PRs landing in front of a human reviewer proportional to the number of semantically distinct changes, not the number of branches used to build one: a stack of sub-PRs is one reviewable unit, and the *only* member of that stack a reviewer should ever see announced is the one whose base is the default branch — typically the last one, once the stack is flattened or the integration branch itself opens its PR into main. The global CLAUDE.md calls this the **trunk flow** and says when to use it (closely-knit features → one trunk; standalone fixes → straight to main). See steps 3.5 and 3.6.

## Invocation

```
/post-pr                      # the PR for the current branch
/post-pr 1271                 # a PR by number, in the current repo
/post-pr <worktree-path>      # a PR whose branch lives in another worktree
/post-pr 41 1271 1433         # several: one per worktree, run in parallel
/post-pr --now                # hotfix: enqueue, then flush just this PR immediately
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

If several targets were given, run them **in parallel**. Targets in different worktrees (and especially different repos) are independent: dispatch each into its own sswt with its own agent (`superset workspaces create --local --project <id> --branch <branch> --agent claude --prompt "/post-pr <n>"`, or one Agent call per target in a single message) rather than finishing one before starting the next. Each run owns its worktree, so reviews, CI watches and merges don't interact. Append to `pr-review-queue.jsonl` with a single `>>` line write so concurrent runs can't clobber each other, and have each run report its own block (step 7) rather than interleaving output.

Serialize only for a real dependency: two targets sharing a worktree or branch, a sub-PR whose trunk another target is still merging into, or the user stating a hard ordering requirement. A soft ordering hint ("API first so the endpoint exists") is not a hard requirement: run in parallel and flag the risk in the report (e.g. "UI PR queued before the API PR merged").

### 1.5. Confirm the Linear ticket is in the PR title

The ticket id as a `(CON-N)` / `(DEV-N)` suffix on the PR title is the link: Linear picks it up from the title, the same way it does from commit titles.
```bash
gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json title -q .title | grep -oiE '\((dev|con)-[0-9]+\)'
```

- Title carries a ticket: report `Linear: <ID> (title suffix)` and move on.
- No ticket in the title but the branch name or the branch's commit subjects carry one: append it to the title as ` (<ID>)` (`gh pr edit --title`), the same normalization [[create-pr]] does.
- No ticket anywhere: report `Linear: no ticket found`. Do not create a ticket here; filing tickets is a separate decision.

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

- **Branch here.** Skip steps 4 and 6.5 (enqueue, product update) for this target and go to step 3.6 instead.
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

**Re-check the parent first.** The trunk's own PR may have been approved or merged since this sub-PR opened. [[create-pr]]'s same-work stacking (same ticket or same Linear project) makes this common, because it stacks onto ordinary feature PRs that reviewers are actively approving.
```bash
gh pr list --repo <OWNER/REPO> --head <BASE_REF> --state all --json number,state,reviewDecision -q '.[0]'
```
- If the parent is `OPEN` and not `APPROVED`, merge as below.
- If the parent is `APPROVED`, `MERGED` or `CLOSED`, **don't merge into it**. Merging would put unreviewed code under an approval, or into a branch that is already done. Retarget instead with `gh pr edit <PR_NUMBER> --repo <OWNER/REPO> --base <DEFAULT_BRANCH>`, re-run step 2.5 (conflicts with main) and step 3 (main-scoped checks now run), then continue at step 4 as an announceable PR. Report it as `Base: retargeted to <default> (parent #N <approved|merged>)`.

```bash
gh pr ready <PR_NUMBER> --repo <OWNER/REPO>        # only if isDraft — GitHub refuses to merge a draft
gh pr merge <PR_NUMBER> --repo <OWNER/REPO> --merge --delete-branch=false
```

- **Merge commit, never squash.** Squashing a sub-PR rewrites its commits and orphans the base of anything stacked on it (the restack pain [[rebase-after-squash]] exists to undo). The trunk is squashed into main at the end, so intermediate history never reaches main anyway.
- **Keep the remote branch at merge time** (`--delete-branch=false`). Branches stacked on it still resolve, and the PR keeps its head ref. The sub-PR's local workspace and branch are reaped separately, as the run's last action (step 3.7).
- **The undraft here is a merge prerequisite, not a signal.** No human reviews a sub-PR, so its draft pill was never load-bearing. Do it immediately before the merge and never badge the PR.
- **Conflicts** (`CONFLICTING` / `DIRTY`): resolve them as in step 2.5, with the trunk as the base. Stop and report "merge blocked — conflicts with `<trunk>`" only for a conflict that needs a product or design decision.
- After the merge the trunk PR's diff has grown — say so in the report, and pull the trunk in the worktree if the next piece of work starts from it. Nothing is written to `pr-review-queue.jsonl` or `pr-review-posted.jsonl`; there is no post to queue or dedup.

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
- A PR that changes the management portal (`apps/management/`) also links each changed portal screen on the management preview host, `https://deploy-preview-<N>.management.folio.concentro.io` + the route from `apps/management/src/routes.ts`, alongside any Folio app links. A missing management link counts as a Preview that doesn't follow these rules.
- A screen that needs an entity uses the stage entity `/e/portfolios/legends-solar-bc65ea81da/<view>`: swap `dashboard` for the view under review. Never invent an entity id. Deploy previews talk to the stage API, so this entity exists there. Claudio's drafter uses the same entity (`WOODROW_PREVIEW_ENTITY_PATH` in internal-tools' `services/claudio-slack-bot/src/prDrafter.ts`); change both together.
- When the change shows on several distinct screens, give one link per screen, each on its own line as `<label>: <url>`.
- Link the bare host only when the change is genuinely app-wide (theme tokens, global layout, the app shell) and no single screen represents it.

If the body's `## Preview` already follows these rules, leave it. Otherwise rewrite only that section, reading the body into a file (`gh pr view --json body -q .body > file`) and writing it back with `gh pr edit --body-file`, never through a shell string. Add the section, placed after `## Summary` as the guide's template has it, when it's missing and the change is visible in a preview.

### 4. Enqueue for #pr-review

Don't post here. Queue the PR; the `pr-review-batch` flusher posts it at the next window boundary and then badges, undrafts and enables auto-merge on it. The PR stays draft and unbadged until then.

**The Slack text.** The flusher posts each queued PR as its own message, whose body is *only* the link:

```
<PR_URL|[REPO_LABEL] PR_TITLE>
```

The repo label and title together form the hyperlink text. Nothing outside the link — no preamble, no emoji, no summary.

| Repo | Label |
|---|---|
| woodrow | `ui` |
| api | `api` |
| folio-platform | `platform` |

**Hygiene tag.** When the PR's net diff (`git diff <base>...HEAD`) is *only* code hygiene — codemods, behavior-preserving refactors or moves/renames, test fixes, lint or format fixes, with no user-visible or runtime behavior change — insert `[hygiene]` right after the repo label: `<PR_URL|[api][hygiene] PR_TITLE>`. It tells reviewers the PR is a skim-for-safety read, not a behavior review. One tag covers all of those; don't split it into `[lintfix]` / `[testfix]`. A PR that mixes hygiene with any feature, fix or config change gets no tag. The tag goes in the Slack link text only, never in the PR title itself. Whenever the tag applies, also add the GitHub label now, at enqueue time: `gh pr edit <PR> --add-label hygiene` (idempotent). If the repo has no `hygiene` label, don't create it — skip and say so in the report.

**Auto-merge method.** The global work instructions make auto-merge the default without asking (`instructions/development/work.md`); the flusher applies it after the undraft, since GitHub refuses auto-merge on a draft. Pick it here:

- `null` when `gh api repos/<OWNER/REPO> --jq .allow_auto_merge` is `false` (report `n/a (repo disallows)`; don't change the repo setting), when `gh pr view <PR_NUMBER> --repo <OWNER/REPO> --json autoMergeRequest -q .autoMergeRequest` is already non-null (report `already on`), or when either of work.md's two conditions fails: the PR depends on another PR merging first, or there is an open question about the implementation or merge order (report which condition failed). The flusher can't judge those conditions, so this is the only place they are applied.
- Otherwise `"squash"`, unless the repo's `allow_squash_merge` is false, then `"merge"`. Never `rebase` unless it is the only method the repo allows.

**Dedup.** Skip the enqueue (report `Slack: skipped, already in dedup state`) when the PR's URL is already in `~/.claude/state/pr-review-posted.jsonl`. If it is already *queued*, enqueue again anyway: the new line supersedes the old one (the flusher keeps the newest line per URL), which is how a later push refreshes `headSha` and the title.

**Enqueue.** Append one line to `~/.claude/state/pr-review-queue.jsonl`, built with `jq` or Python and written with a single `>>` (titles carry backticks, quotes and `$`; never interpolate them into a shell string):

```bash
jq -nc --arg url "$URL" --arg repo "$REPO" --argjson pr "$N" --arg text "$TEXT" \
  --arg sha "$(gh pr view "$URL" --json headRefOid -q .headRefOid)" --argjson am "$AUTOMERGE_JSON" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{url:$url, repo:$repo, pr:$pr, text:$text, headSha:$sha, autoMerge:$am, queuedAt:$at}' \
  >> ~/.claude/state/pr-review-queue.jsonl
```

```json
{"url": "...", "repo": "Concentro-Inc/api", "pr": 0, "text": "<URL|[api] title>", "headSha": "...", "autoMerge": "squash", "queuedAt": "2026-01-01T00:00:00Z"}
```

`text` is the finished Slack link text; `autoMerge` is `"squash"`, `"merge"` or `null` (`AUTOMERGE_JSON='"squash"'` or `null`). `headSha` must be the PR's current head: the flusher drops an entry whose PR has moved on (a later push runs `/post-pr` again and re-enqueues), whose PR is no longer open or no longer targets the default branch, or whose URL is already in `pr-review-posted.jsonl`.

**Mark it queued on GitHub.** Right after the enqueue, add an invisible `<!-- pr-review-queued -->` line to the end of the PR body, read and written through a file, and only if it isn't there yet. Queues are per machine, so this marker is how the other machine's flusher sees that this PR is queued; the flusher removes it when it adds the badge. A failed marker write is reported, not fatal: the PR still posts, though a sibling on the other machine may wait out its hold first.

```bash
f=$(mktemp)
# Edit only after a successful read: an empty file would replace the whole body.
gh pr view "$URL" --json body -q .body > "$f" && {
  grep -qxF '<!-- pr-review-queued -->' "$f" ||
    { printf '\n\n<!-- pr-review-queued -->\n' >> "$f" && gh pr edit "$URL" --body-file "$f"; }
}
rm -f "$f"
```

**Cross-repo siblings.** When one CON ticket has a PR in more than one of api, woodrow and folio-platform, the flusher pairs them; nothing extra goes in `text`. A sibling is Jake's open PR into the default branch of another of those repos whose title carries the exact same `(CON-N)`. A queued PR with a sibling that is neither queued nor posted is **held** in the queue until the sibling is queued or posted, or until `siblingHoldMin` minutes (default 60) after `queuedAt`; then it posts alone. Each sibling's message gets a second line: api says `UI follow-up: <woodrow url|woodrow #N>, merge after this one`, woodrow says `Merges after <api url|api #N>`, and any pair involving folio-platform, or a ticket in three repos, gets `Sibling: <url|repo #N>` for each with no merge-order claim. Check for siblings **before picking the auto-merge method** (it is read-only and works before the enqueue), and use the result in step 7:

```bash
python3 -I ~/.claude/automations/pr-review-batch/flush.py --siblings "$URL"   # read-only
```

It prints one `sibling <repo>#<n> <posted|queued|in progress> <url>` line per sibling, then the message the flusher would post. A woodrow PR whose api sibling hasn't merged depends on it, so its `autoMerge` is `null` (work.md's first condition). Report `queued (holds for sibling woodrow#N)` when any sibling is in progress, and `queued (paired with sibling api#N)` when every sibling is already queued or posted.

**Safety net: is the flusher loaded on this machine?** Queues are per machine (`/post-pr` runs on jake1 and jake2), so check before relying on one:

```bash
launchctl print "gui/$(id -u)/com.jake.pr-review-batch" >/dev/null 2>&1
```

If that fails, the queue would never drain. Enqueue as above, then post immediately with `~/.claude/automations/pr-review-batch/run.sh --only <PR_URL>` and report `Slack: posted (flusher not loaded — immediate)`. Say in the report that this machine needs the plist loaded (see `automations/README.md`).

**`/post-pr --now`** (hotfixes): enqueue, then run `~/.claude/automations/pr-review-batch/run.sh --only <PR_URL>` the same way. `--only` flushes just that URL, leaves the rest of the queue for the window, and ignores both the window and the sibling hold (the message still gets its sibling line).

`run.sh` logs to `~/.claude/automations/pr-review-batch/logs/flush.log` when its output isn't a terminal, which includes the Bash tool, so judge an immediate post by its **exit code** (0 means this URL was posted, nonzero means it wasn't and the entry is still queued or was dropped) and quote the `tail` of the log for the reason.

Never point the flusher at a real channel to "test" it. `--channel <id>` (or env `PR_REVIEW_BATCH_CHANNEL`) aims it at Jake's own DM instead.

`pr-review-posted.jsonl` is local dedup state that only the flusher appends (`{"url","repo","pr","postedAt"}`, the real post time), not durable memory. When a PR's sswt workspace/branch is deleted (e.g. by [[cleanup-local-branches]]), drop its entries in the posted file and the queue too — a gone workspace means the work is done and there is no double-post left to guard against.

### 5–6. Badge, undraft, auto-merge (the flusher)

Not done by this skill any more. After a real post the flusher, in order: appends to `pr-review-posted.jsonl`; appends `\n\n---\n📣 **Posted to Slack**` to the body unless it already ends with the badge, removing the `<!-- pr-review-queued -->` marker in the same edit (read and written through a file); runs `gh pr ready` if the PR is a draft; then `gh pr merge --auto --<method>` if the entry's `autoMerge` is set. A failed post is requeued for the next window; a failed badge, undraft or auto-merge is logged and never undoes the post. Its log (`flush.log`) has one summary line per PR.

### 6.5 Offer a product update

Only for a PR whose base is the default branch, right after step 4's enqueue — a sub-PR merged into a trunk (step 3.6) never reaches this step. It runs at enqueue time, not when the flusher posts, because it asks a question and the flusher is unattended. Skip it when the PR URL is already in `~/.claude/state/product-updates-posted.jsonl`, which records `posted` **and** `declined` lines so a re-run never re-asks.

This is the one step that asks. The `#pr-review` post, badge, undraft and auto-merge are covered by the standing authorization in the global CLAUDE.md; a post to `#product-updates` (ID `C0C9DKM77HA`) is a user-facing announcement and is not, so Jake decides per PR whether the screenshot is worth announcing.

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

Record the run in the metrics ledger before reporting (and before step 3.7's reap, which kills the terminal): `~/.ai-tools/bin/review-metrics run --outcome queued|posted|merged|stopped|skipped [--gate review|ci|conflicts|merge]`. Use `queued` for an announceable PR handed to the flusher, `posted` only when this run posted it immediately (`--now` or the not-loaded safety net), `stopped` with the gate that fired when the run halted early, `skipped` when the dedup check found it already posted. It reads the phase stamps and round count from the worktree, so run it from there. `~/.ai-tools/bin/review-metrics summary` aggregates rounds, durations and gate stops.

Per target, one block:

```
Concentro-Inc/api#1271
- Review: clean after 3 rounds
- CI: 4 checks, all pass
- Preview: woodrow only — set (or: already set, n/a (not visible in a preview))
- Slack: queued for next :00/:30 window (or: queued (holds for sibling woodrow#N), queued (paired with sibling api#N), posted (flusher not loaded — immediate), posted (--now), skipped, already in dedup state)
- Badge / Draft: by the flusher after the post (or, when posted now: appended / cleared, from flush.log)
- Linear: CON-4112 (title suffix) (or: added to title, or: no ticket found)
- Product update: posted (or: declined, n/a (no screenshot), not offered (unattended), failed (<why>))
- Auto-merge: squash, applied by the flusher after the post (or: already on, n/a (repo disallows), off — <which work.md condition failed>)
```

For a sub-PR (step 3.5 classified it internal-only), report the trunk merge in place of the announcement — never omit the announcement steps silently:

```
Concentro-Inc/woodrow#1555
- Review: clean after 2 rounds
- CI: 1 check (label), pass — unit-tests/e2e-tests don't run against a non-main base
- Base: feature/vde-airtable-parity (not the default branch) — sub-PR, not announced
- Merged into feature/vde-airtable-parity (merge commit abc1234); trunk PR #1500 now carries it
- Queue / Product update: skipped (step 3.5 gate)
- Reap: sswt + local branch deleted (or: skipped — <which check>)
```

Post this report on the PR before the reap (step 3.7), since the reap closes the terminal it was printed in.

If the merge was blocked, replace the `Merged` line with `Merge: blocked — conflicts with <trunk>` and say what is outstanding.

If the run stopped at a different gate (unconverged review, failing check), say which gate and what is outstanding — don't bury it.

### 8. Pick up in a new tab

After a run that merged a PR (a sub-PR into its trunk, step 3.6) or otherwise finished the branch's work, if the repo's gitignored `.claude/IN_PROGRESS.md` has open items, **start `/pick-up` yourself in a new terminal tab; never offer it or leave it to Jake.** A new tab in an existing workspace, not a new sswt, and not the terminal this run is in:

```bash
superset agents create --workspace "$WS" --agent claude --prompt "/pick-up" --json
```

`$WS` is a workspace that survives the run: the current one, unless step 3.7 reaps it, in which case the trunk's workspace or the project's root checkout. The file is gitignored and lives in the root checkout, so a worktree tab sees no `IN_PROGRESS.md`; name the root checkout's path in the prompt when `$WS` is a worktree. Spawn it before step 3.7's reap, report it as `- Pick-up: tab spawned (N open items)`, and skip silently when the file is absent or empty.

## When it fires on its own

The global CLAUDE.md instructs this to run **automatically, without asking**, once a task's work is complete. Concretely, all of:

- the task the user asked for is finished — not a checkpoint, not a WIP push
- the working tree is clean and the branch is pushed
- a PR exists for the branch

Do not fire on intermediate pushes mid-task, and do not fire on a branch whose PR is deliberately parked. When several PRs finished together, run this for each of them.

Firing automatically still runs the full skill, base-branch gate included: a finished task on a branch stacked onto something other than the default branch gets reviewed and CI-checked same as any other, then merged into its trunk (step 3.6) and its sswt reaped (step 3.7) rather than queued. The trunk's own PR into the default branch is queued when the user declares the trunk complete — not automatically on each sub-merge.

Because it self-starts, it must also self-report: state plainly what it did to each PR, including anything a gate stopped.

## Why it doesn't ask first

Posting to Slack is outward-facing, and outward-facing actions normally warrant a confirmation. The standing instruction in the global CLAUDE.md **is** that authorization — it is durable and given in advance, which is exactly what removes the need to ask each time. Queueing doesn't change that: the flusher posts later, under the same authorization, with no model in the loop.

The design stays safe because the gates, not the user's attention, are what hold a PR back: an unconverged review, a failing check, a missing PR, or a non-default base branch all stop the run before anything is queued, and the flusher re-checks at post time that the PR is still open, still targets the default branch, hasn't been posted, and still has the head that was reviewed. A PR reads as "not ready" until the flusher has actually posted it, which is why [[create-pr]] and [[refresh-pr]] open PRs as drafts in the first place, and why a queued PR stays draft.

The authorization covers the `#pr-review` post, the badge, the undraft, auto-merge (work.md makes it the default, subject to its two conditions) and the trunk merge — nothing else. The `#product-updates` screenshot post (step 6.5) is a separate, user-facing announcement, so it is asked about once, at enqueue time.
