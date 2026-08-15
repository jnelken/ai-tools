---
name: close-sswt
description: Tear down the current Superset workspace (worktree, branch, and workspace registration) once its branch is confirmed merged to main — but only after checking IN_PROGRESS.md and other live terminals for anything that would be lost, and putting that loss in front of the user like a delete-confirmation dialog before acting. Trigger phrases include "close this workspace", "this PR merged, clean up this workspace", "delete this workspace", "tear down this superset workspace", "can I close this out", "am I safe to close this workspace".
disable-model-invocation: true
---

# close-sswt

The active, single-workspace sibling of three read-mostly skills:

- [[close-out]] step 2e already proposes deleting an sswt workspace once its git tree is
  clean and its PR is merged/closed — but never calls `workspaces delete` itself, and
  never looks at `IN_PROGRESS.md` content before proposing. This skill is what happens
  when the user actually wants that deletion to happen, right now, for the workspace
  they're sitting in.
- [[cleanup-local-branches]] has the worktree/branch teardown mechanics and the
  `~/.claude/local-branch-archive.md` recovery-log convention this skill reuses. It
  explicitly *skips* the current cwd's own worktree ("removing the worktree from inside
  it orphans the session") — this skill exists to do exactly that, deliberately, as its
  whole point.
- [[pick-up]] verifies `IN_PROGRESS.md` items against current repo state before turning
  them into a resumption plan. This skill borrows that verification method but for the
  opposite purpose: not "what should I do next" but "is it safe to throw this away."

**This skill destroys things: a git worktree, a local branch, a Superset workspace, and
every terminal (and Claude session) running inside it — including this one.** That's why
model-invocation is off: it only runs when the user explicitly asks for it, never as a
reflex after a PR merges.

## Guardrails — check before anything else

Refuse outright, no exceptions, if any of these hold:

1. **`$SUPERSET_WORKSPACE_ID` is unset.** This skill only runs inside a Superset
   workspace terminal — it has nothing to key off of otherwise.
2. **The workspace's `type` is not `worktree`.** Check with:
   ```bash
   superset workspaces get --json
   ```
   `type: "main"` means this *is* a project's root checkout (e.g. `/Users/jake/code/api`)
   — there is no separate worktree to remove, and deleting it would take the whole
   project registration with it. Never proceed on a `main`-type workspace.
3. **The current branch is `main`/`master`.** Belt-and-suspenders on top of (2) —
   `git branch --show-current`.

If any guardrail trips, say so plainly and stop. Nothing below runs.

## 1. Confirm the branch is actually merged

This is the second hard gate — everything after it is destructive, so detection has to
be real, not assumed.

```bash
BRANCH=$(git branch --show-current)
gh pr list --head "$BRANCH" --state all --json number,state,mergedAt,url,headRefOid --limit 5
```

- Any returned PR with `state: MERGED` → merged, proceed to step 2.
- A PR exists but its most recent state is `OPEN` or `CLOSED` (never merged) → **not
  merged**. Report the PR state and stop — closed-without-merge is not merged, even if
  some of its commits coincidentally landed elsewhere.
- **No PR found at all** → fall back to ancestry, since some merges never go through a
  PR:
  ```bash
  git fetch origin main --quiet
  git merge-base --is-ancestor "$BRANCH" origin/main && echo "ancestor of origin/main"
  ```
  Treat this as merged only on a clean "ancestor" result, and say explicitly in your
  report that detection was via ancestry, not a PR record — squash-merged branches will
  *not* show as an ancestor even though they did merge, so an ancestry miss is not by
  itself proof of "not merged." If ancestry is inconclusive and there's no PR, say
  detection failed and stop. Never guess.

## 2. Build the loss picture

Gather all of this before deciding whether to just proceed or to stop and ask —
it's the difference between an empty-trash confirmation and a real warning.

**a. Git state** — reuses close-out's exact safety check:
```bash
WT=$(git rev-parse --show-toplevel)
git -C "$WT" status --porcelain                    # must be empty
git -C "$WT" log --oneline origin/main..HEAD       # must be empty
```
Anything here is uncommitted or unpushed work that the PR never carried — the single
biggest thing that could be silently lost.

**b. `IN_PROGRESS.md` verification** — same file-lookup order as [[pick-up]]:
1. `.claude/IN_PROGRESS.md`, else repo-root `IN_PROGRESS.md`, else treat as absent (nothing
   to lose here — skip to 2c).
2. Read the whole file. Its sections (per [[close-out]]'s write convention) are
   `## Decisions needed`, `## Blocked`, `## Awaiting external action`, `## Deferred`.
3. For every item in every section, verify it against current state exactly as
   [[pick-up]] step 3 does: does the file/commit/PR it names still exist, does
   `git log` since the file's `_Last updated:_` date already cover it, is the stated
   blocker still a blocker. Classify each as **still open**, **already done**, or
   **stale/invalid**, with the evidence.
4. Leave the file untouched — this skill never edits or checks off `IN_PROGRESS.md`;
   that stays [[close-out]]'s job.

**c. Sibling terminals in this workspace** — the workspace's other tabs die too, not
just this one:
```bash
superset terminals list --workspace "$SUPERSET_WORKSPACE_ID" --json
```
For any terminal besides this one, note whether it's idle or busy and, if bound to a
Claude session (via the same `terminal_agent_bindings` mechanism [[resume-sswts]]
reads), what that session is doing. A busy sibling terminal is exactly the kind of thing
someone would be furious to lose silently.

## 3. Decide: clean close vs. confirm dialog

- **All-clear** — 2a is empty, 2b has no items classified "still open" (absent file or
  fully done/stale both count), and 2c shows no other busy/live terminals: proceed
  straight to step 4, but still show a one-line "nothing at stake, closing" summary
  first. Silent destruction is never appropriate even when there's nothing to lose.
- **Anything outstanding** — stop and ask, styled like a delete-confirmation dialog:
  succinct, states exactly what's at risk, no padding. Use `AskUserQuestion` with options
  along the lines of:
  - **Close anyway (discard)** — proceeds to step 4 regardless.
  - **Run `/pick-up` first** — since [[pick-up]] is agent-invokable, offer to hand off to
    it right now for anything still open in `IN_PROGRESS.md`, then return to this
    decision after.
  - **Cancel** — do nothing.

  Example shape for the prompt itself (keep it this short in practice):
  ```
  Branch "jake/con-3487-..." is merged. Closing deletes the worktree, branch, and
  Superset workspace — including 1 other open terminal (idle, session 2b60c8d2).

  Still open in IN_PROGRESS.md:
  - [Decisions needed] Confirm which of the two migration paths to take
  - [Awaiting] PR #1240 review from @grayson

  Close anyway, run /pick-up first, or cancel?
  ```

## 4. Tear down, in this exact order

Order matters: the last call in this list ends the terminal this skill is running in, so
nothing after it can run and nothing after it will be visible to the user. Do every
step that produces user-facing confirmation *before* it.

1. **Archive log** — append to `~/.claude/local-branch-archive.md`, same format
   [[cleanup-local-branches]] uses: repo, branch name, last-known SHA
   (`git -C "$WT" rev-parse HEAD`), and why deleted (merged PR URL). This is the
   recovery trail if anything here turns out to have mattered — reflog recovery works
   for ~90 days.
2. **`pr-review-posted.jsonl` cleanup** — per the user's `CLAUDE.md`, remove this PR's
   entry from `~/.claude/state/pr-review-posted.jsonl` now that its workspace is going
   away. Same rule [[close-out]] step 2f applies passively; this is the active version.
3. **Git-level teardown, run from the project's root checkout, never from inside `$WT`**
   (the exact trap [[cleanup-local-branches]] flags — you can't remove the directory
   you're standing in):
   ```bash
   ROOT=$(superset projects list --json | jq -r --arg id "$(superset workspaces get -f projectId)" '.[] | select(.id==$id) | .path')
   git -C "$ROOT" worktree remove "$WT"
   # -d if the branch is a clean ancestor of origin/main, -D if it was squash-merged
   git -C "$ROOT" branch -d "$BRANCH" 2>/dev/null || git -C "$ROOT" branch -D "$BRANCH"
   ```
4. **Deregister the Superset workspace — last call, always:**
   ```bash
   superset workspaces delete "$SUPERSET_WORKSPACE_ID"
   ```
   This is expected to kill every terminal in the workspace, including the one running
   this skill. Send your final summary to the user as a normal response *before* this
   call, not after — there's no guarantee anything sent after it is ever seen.

## Notes

- **Why `disable-model-invocation`:** the blast radius (git worktree + branch + Superset
  workspace + every live session inside it) is large enough that this should never fire
  from an agent's own judgment call — e.g. immediately after a `/loop /peer-review`
  comes back clean is exactly the moment a branch *becomes* merged, and exactly the
  moment this skill must *not* auto-trigger. Only a direct, explicit ask should invoke
  it. If that trade-off stops making sense later, this line can be removed the same way
  it was removed from [[pick-up]] — but do that deliberately, not as a side effect of
  something else.
- **This is single-workspace, not a sweep.** For "clean up all my merged branches across
  repos," that's [[cleanup-local-branches]]. This skill only ever touches
  `$SUPERSET_WORKSPACE_ID` — the one it's invoked from.
- **Never touches `IN_PROGRESS.md` itself.** Reads and classifies, never writes or
  checks off — matches [[pick-up]]'s same rule. If the user picks "close anyway," the
  file (and its unresolved items) is destroyed along with the worktree; that's the
  loss the confirmation dialog exists to make explicit before it happens, not something
  this skill tries to preserve on its own.
