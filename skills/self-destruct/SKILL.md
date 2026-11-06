---
name: self-destruct
description: The session deletes its own Superset workspace (worktree, local branch, workspace registration, and this terminal tab) once its branch is confirmed merged, then signs off ⛏️. In a project's root checkout it keeps the workspace and only closes the terminal (this is the old /done skill, now folded in). Checks IN_PROGRESS.md, unpushed commits and sibling terminals first and puts any loss in front of the user like a delete-confirmation dialog, which can run /close-out or /pick-up first. This is the one command for ending a session. Use when the user types /self-destruct or explicitly asks this session to clean up after itself or close its terminal. Trigger phrases include "self-destruct", "close this terminal", "we're done, close it", "delete your own worktree", "reap this session", "reap this workspace", "close this workspace", "this PR merged, clean up this workspace", "tear down this superset workspace", "am I safe to close this workspace". Formerly named close-sswt.
disable-model-invocation: true
---

# self-destruct

A session removes the workspace it is running in, then signs off. Everything is shaped by
one fact: **the worktree, the branch and this terminal all disappear under the running
session**, so the teardown cannot be a normal series of tool calls. The checks run now; the
destructive part runs from a detached script (`scripts/teardown.sh`) that outlives the
session.

This skill is `/reap` for the session itself. After a ⛏️ sign-off, a bare "reap" reply
already authorizes the same teardown; `/self-destruct` is the explicit, guarded form of it.

This is the single command for ending a session; the former `/done` (close only the tab) is
the root-checkout path below. It shares one ecosystem with [[close-out]] and [[pick-up]]:
`IN_PROGRESS.md` is written by close-out, read and verified here (never edited here), and
resumed by pick-up. The confirmation dialog in step 3 can run either one first.

Siblings: [[clean-sswts]] sweeps many workspaces
from the root checkout (it refuses to run from inside one, which is the gap this fills).
[[cleanup-local-branches]] owns the `~/.claude/local-branch-archive.md` recovery-log
convention. [[pick-up]] is the method for verifying `IN_PROGRESS.md` items, borrowed here for
the opposite question: not "what next" but "is it safe to throw this away".

**Destructive: a git worktree, a local branch, a Superset workspace, and every terminal in
it, including this one.** That's why model invocation is off. A branch *becomes* merged at
exactly the moment an agent loop finishes, which is exactly when this must not fire on its
own. Unattended reaping exists separately: the `reap-sswts` automation runs this skill's
`teardown.sh` on sswts whose sessions sat at ⛏️ for an hour, gated by the same merged/clean
checks in code (`automations/reap-sswts/reap.py`).

## Guardrails: check before anything else

Refuse outright, no exceptions, if any hold:

1. `$SUPERSET_WORKSPACE_ID` or `$SUPERSET_TERMINAL_ID` is unset or empty. The CLI reports
   success for IDs that don't exist, so empty or wrong IDs would silently do nothing.
2. `superset`, `jq` or `gh` is not on `PATH`.

Say plainly which guardrail tripped and stop.

### Root checkout: close the terminal only

If either of these holds, this is a project's root checkout, not a disposable worktree:

- The workspace `type` is not `worktree` (root checkouts report `local` or `main`):
  ```bash
  superset workspaces get --local --json | jq -r '.type'
  ```
- The current branch is `main`/`master` (`git branch --show-current`). Belt and braces over
  the type check.

Deleting it would remove the primary clone, so **never archive the workspace, delete a
branch or run `teardown.sh`.** The user still wants the session gone, so close only this
terminal:

1. Run the loss picture's 2a and 2c, plus 2b's `IN_PROGRESS.md` check, but scoped to *this
   session's own* work: its uncommitted changes, unpushed commits, background tasks and
   Monitors. Dirty files that other sessions own (a root checkout is worked by several at
   once) are one FYI line, not a blocker. Anything of this session's own goes through the
   step 3 dialog (the "merged" check in step 1 does not apply here).
2. Write the final message: that the workspace was kept because it is the root checkout and
   only this terminal is closing. Nothing after the close can be said. Sign-off is `⛏️`
   unless this is an sswt whose branch is unmerged, then `✅`.
3. As the **last tool call of the turn**, close detached so the message renders first:
   ```bash
   nohup sh ~/.claude/skills/self-destruct/scripts/close-tab.sh \
     "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" "$PPID" >/dev/null 2>&1 &
   ```
   Write nothing after it. Always close `$SUPERSET_TERMINAL_ID` from the environment; never
   list terminals and close another, and never use the Superset MCP `terminals_close` (it
   goes through the cloud relay and 503s with `Host is not online`).

Skip steps 1, 2 (beyond the above) and 4 below entirely.

**Known quirk: the dead tab.** The close itself works (the host disposes the terminal and
ends the session within a second). What lingers is the *tab* in a desktop app that views
this host from another machine: it keeps a dead tab and `superset panes` can't reach it.
`close-tab.sh` uses `panes close` whenever a desktop is attached to this host, which removes
the tab outright. Otherwise it hangs up the tab's tty (found through the Claude process,
`$PPID`), so Claude and the login shell exit as if the tab were closed by hand, and uses
`terminals close` only if the terminal is still listed after that. Whether a remote app drops
the tab after a hangup is still being checked; if the dead tab remains, close it by hand and
don't retry the CLI from a new session. Each run logs to `~/.claude/state/self-destruct.log`.

## 1. Confirm the branch is merged

Everything after this is destructive, so detection has to be real.

```bash
BRANCH=$(git branch --show-current)
gh pr list --head "$BRANCH" --state all --json number,state,mergedAt,url --limit 5
```

- A `MERGED` PR → proceed.
- A PR exists but its latest state is `OPEN` or `CLOSED` (never merged) → not merged. Report
  the state and stop.
- No PR at all → fall back to ancestry (`git fetch origin main -q`, then
  `git merge-base --is-ancestor "$BRANCH" origin/main`). Merged only on a clean "ancestor".
  Say the detection was ancestry, not a PR record. A miss proves nothing (squash merges
  never show as ancestors), so with no PR and no ancestry, say detection failed and stop.

Never gate on `git rev-list origin/main..HEAD`: these repos squash-merge, so a merged
branch always looks many commits ahead.

## 2. Build the loss picture

**a. Git state.**
```bash
git status --porcelain | cut -c1-2 | sort | uniq -c    # summarize by status code, don't list
git fetch origin "$BRANCH" -q 2>/dev/null
git rev-list --count "origin/$BRANCH..HEAD"              # unpushed: vs the branch's own remote, never main
```
A merged PR usually deletes the remote branch, so the fetch fails and `origin/$BRANCH` is a
stale tracking ref that proves nothing. When the fetch fails, instead confirm `HEAD` is one of
the merged PR's commits:
```bash
gh api repos/<owner>/<repo>/pulls/<N>/commits --paginate --jq '.[].sha' | grep -c "$(git rev-parse HEAD)"   # 1 = carried by the PR
```
A `0` there means local commits the PR never carried: treat them as outstanding.
Uncommitted changes or unpushed commits are work the PR never carried. A tree that is
nothing but ` D` entries (a half-removed worktree, thousands of lines) is not real work, but
still say so before treating it as clean: it is uncommitted state that archiving will
discard.

**b. `IN_PROGRESS.md`.** Look in `.claude/IN_PROGRESS.md`, else the repo root; absent means
nothing to lose. Verify each item the way [[pick-up]] step 3 does and classify it **still
open**, **already done** or **stale**, with the evidence. Never edit the file; that stays
[[close-out]]'s job.

**c. Sibling terminals and this session's own loose ends.**
```bash
superset terminals list --workspace "$SUPERSET_WORKSPACE_ID" --local --json | jq -c '.sessions[] | {terminalId, exited, title}'
```
Note any terminal other than `$SUPERSET_TERMINAL_ID` that is busy or bound to a Claude
session (the output is `{"sessions":[…]}`, not a bare array). Also count this session's own background tasks, Monitors, scheduled wakeups and
open questions to Jake; each dies with the session.

## 3. Decide: clean close vs. confirm

- **All clear** (2a empty, 2b nothing still open, 2c no busy siblings, no loose ends of
  this session's own): print a one-line "nothing at stake, self-destructing" summary and go
  to step 4. Silent destruction is never appropriate, even with nothing to lose.
- **Anything outstanding**: `AskUserQuestion`, styled like a delete-confirmation dialog,
  succinct, naming exactly what is at risk. Options: **Run /close-out first** (the first
  option, and the recommended one when the loose ends are conversation-level: unfiled
  findings, unanswered questions, deferred promises), **Run /pick-up first** (when
  `IN_PROGRESS.md` items may already be done), **Close anyway (discard)**, **Cancel**.
  After either "first" option finishes, redo step 2 and come back to this decision; never
  proceed straight to step 4 on the stale picture. close-out writes `IN_PROGRESS.md`, so a
  decision it captures there no longer counts as at stake on the re-check.

## 4. Self-destruct

Resolve the inputs while the worktree still exists:

```bash
WT=$(git rev-parse --show-toplevel)
ROOT=$SUPERSET_ROOT_PATH                               # the project's root checkout
[ -d "$ROOT/.git" ] || ROOT=$(git rev-parse --path-format=absolute --git-common-dir | sed 's#/\.git$##')
SHA=$(git rev-parse HEAD)
```

Bookkeeping, still before the point of no return:

1. Remove this PR's entry from `~/.claude/state/pr-review-posted.jsonl` (match on `pr` and
   `repo`), per the global CLAUDE.md.
2. Nothing else needs archiving: the script logs the recovery command to
   `~/.claude/state/self-destruct.log`.

Then, in this order:

1. **Write the final message now**, because nothing after the teardown call can be seen:
   what was removed, what was at stake (or "nothing"), the recovery command
   (`git -C "$ROOT" branch <branch> <sha>`), and the status line `⛏️ <label>` (emoji form,
   U+26CF U+FE0F). Name the branch and PR in the message; never print the workspace UUID.
2. **As the last tool call of the turn**, launch the detached teardown and write nothing
   after it:
   ```bash
   nohup sh ~/.claude/skills/self-destruct/scripts/teardown.sh \
     "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" "$ROOT" "$WT" "$BRANCH" "$SHA" \
     >/dev/null 2>&1 &
   ```
   Reading or executing the script *through* the symlink is fine; never `rm`, `ln` or write
   to a path inside `~/.claude/skills/self-destruct/` (it resolves into the ai-tools repo).

The script waits a few seconds, then hands off to `scripts/reap-workspace.sh`, the removal
step it shares with [[clean-sswts]]. That script archives the workspace with `--local` (removes the worktree
folder, keeps the branch), removes the folder itself if it somehow survived, prunes worktree
registrations, deletes the local branch with `git branch -D` from the root checkout, and
closes this terminal's pane (archiving alone can leave a dead, unresponsive tab). Every
step is logged to `~/.claude/state/self-destruct.log`; if the tab is still there afterwards,
read that file.

## Notes

- **Why detached.** Run inline, the teardown kills the very shell executing it midway
  (worktree gone → `cd` fails; terminal gone → no further steps; archive alone leaves a
  frozen tab). A `nohup`'d script started from `/` has no worktree to lose and keeps going.
- **`archive`, not `delete`.** Superset CLI 2.x renamed `workspaces delete` to `archive`
  (`delete` still works as a hidden alias). Always pass `--local`; without it the CLI
  targets cloud workspaces.
- **Single workspace only.** For sweeping many merged workspaces, [[clean-sswts]], which calls
  the same `reap-workspace.sh` once per workspace (inline, with no terminal ID, so it closes
  every tab in each). Fix removal bugs there once and both skills get them.
- **History.** This skill replaces `close-sswt` (same single-workspace teardown), updated
  for CLI 2.x, squash-merge-safe unpushed detection and the detached teardown.
- **Testing the script.** `SELF_DESTRUCT_DRY_RUN=1 SELF_DESTRUCT_DELAY=0
  SELF_DESTRUCT_LOG=/tmp/x.log sh scripts/teardown.sh …` prints the commands instead of
  running them.
