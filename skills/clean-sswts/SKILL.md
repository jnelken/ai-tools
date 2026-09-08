---
name: clean-sswts
description: Survey and clean up Superset workspaces (sswts) across all projects on this machine — classify each by PR state, dirtiness and liveness, then delete the finished ones via the superset CLI and remove the local branches it leaves behind. Use when the user says "clean up my worktrees", "clean sswts", "tidy superset workspaces", "which workspaces can I delete", "my worktrees are piling up", "remove merged worktrees", or after a batch of PRs merge. The worktree-scoped counterpart to cleanup-local-branches.
---

# Clean sswts

Superset workspaces (sswts) accumulate one per PR and never leave on their own. This is the phased, conservative sweep: survey everything, classify by evidence, delete only what is provably finished, and surface the rest for a human decision.

Related: [[cleanup-local-branches]] cleans *branches* repo-by-repo. This skill cleans *workspaces* across all projects and then cleans up after the CLI. Run this one first — it removes worktrees, which are the thing that blocks branch deletion in that skill.

## The one thing that makes this skill necessary

`superset workspaces delete <id>` removes the worktree **directory** and its **git worktree registration** — but **leaves the local branch behind.** Verified empirically; the CLI does not document it.

So the CLI alone does not tidy anything. It converts worktree sprawl into branch sprawl. **Every workspace delete must be paired with a `git branch -D`** in the owning repo, or you have simply moved the mess.

## Safety model

Because the branch survives the delete, **committed work is recoverable** — the commits stay reachable on the branch, and in reflog for ~90 days. The only thing a delete destroys irrecoverably is an **uncommitted working tree**.

That gives one hard line:

| Signal | Meaning |
|---|---|
| `dirty > 0` | **Never auto-delete.** This is the only irreversible case. |
| clean, committed | Recoverable — the branch outlives the workspace. |

Everything else is judgment, not risk.

## Phase 1 — Survey

Fetch once per repo first. Worktrees share the root checkout's object store (`git rev-parse --git-common-dir` points back at it), so **one fetch per repo covers all of its worktrees** — do not fetch per worktree.

```bash
# Derive the repo list from the CLI; never hardcode project names.
superset projects list --json \
  | jq -r '.[].path' \
  | while read -r r; do
      [ -d "$r/.git" ] && git -C "$r" fetch origin --prune -q
    done
```

Then gather, per workspace:

```bash
superset workspaces list          # id, name, branch, projectName, worktreePath, worktreeExists, archivedAt
superset projects list            # projectName -> repo URL AND -> root checkout path
```

For each workspace resolve:

- **root?** — see Phase 2
- **PR state** — bulk per repo, never per branch:
  `gh pr list --repo <owner/repo> --state all --limit 500 --json number,state,headRefName,isDraft`
  then join **on `headRefName == workspace.branch`**
- **dirty** — `git -C <path> status --short | grep -vc '^##'`
- **held** — is a live process cwd'd inside it (see Phase 2)

## Phase 2 — Three traps that produce wrong answers

### Trap 1: the workspace name lies about the PR

The naming convention embeds the PR number (`#412 PROJ-99 some-slug`), but a branch can be re-PR'd — the original PR is closed and a new one opened from the same branch. Seen in practice: a workspace whose name said `#412` had a branch that resolved to a **different, still-open PR**. Deleting on the name's stale number would have destroyed live work.

**Always join on `branch`, never parse the PR number out of the name.** The name is a human label; the branch is the identity.

### Trap 2: squash-merge makes ancestry and commit counts useless

These repos squash-merge, so a merged branch's tip is **not** an ancestor of `origin/main`. Consequences:

- `git merge-base --is-ancestor <branch> origin/main` → false for merged work.
- `git rev-list --count origin/main..HEAD` → large for merged work.

Seen in practice: a worktree whose HEAD commit subject ended in `(#NNN)` — the unmistakable shape of a merged PR — reported **121 commits ahead** and was not an ancestor of `origin/main`. That commit was the pre-squash branch tip, not the squashed commit that actually landed.

**PR state is the only reliable "is this finished" signal.** Never gate a delete on ancestry or `ahead` count. For branches with no PR at all, do not guess — surface them (Phase 4).

### Trap 3: root checkouts look deletable

Every project has a permanent root checkout registered as a workspace (typically named `main`, `local`, or `main2`). Deleting one destroys the primary clone.

Do **not** identify them by name. Identify structurally:

```
root  ⇔  workspace.worktreePath == the project's path from `superset projects list`
```

Why the name heuristic fails: one root checkout was sitting on a merged feature branch, so it presented as `MERGED + clean` — a textbook delete candidate by every other signal. Only the path check saved it.

Also note a project name is not a repo. A setup can register two projects (e.g. `myrepo` and `myrepo (alt)`) that are separate clones of the *same* GitHub repo at different paths. Map project → repo *and* project → path via `superset projects list`, and key branch deletion off the **owning clone's path**, not the repo name.

### Also: liveness and the cwd trap

- **Held** — a running agent or terminal cwd'd inside the worktree. Check before removing:
  `lsof -a -d cwd -c node -c claude -Fn | grep '^n/' | sed 's/^n//'`
  Skip any workspace whose path is (or is a parent of) a live cwd.
- **cwd trap** — never delete the workspace the current session is running in. Check `pwd` against each candidate and hand the user a manual command for afterwards instead.

## Phase 3 — Classify and delete

| Bucket | Condition | Action |
|---|---|---|
| **Root** | path == project path | **Never touch** |
| **Held** | live process cwd inside | **Skip**, report |
| **Dirty** | `dirty > 0` | **Skip**, report — the only irreversible case |
| **Finished** | PR `MERGED`, clean, not held | **Delete** |
| **Empty** | no PR, clean, no content vs main | **Delete** — nothing to lose |
| **Abandoned** | PR `CLOSED`, clean | Delete after confirming |
| **Active** | PR `OPEN` (incl. draft) | **Keep** |
| **No PR, has work** | no PR, clean, real commits | **Surface** (Phase 4) |

Delete in one batched call, then clean up the branches the CLI left:

```bash
superset workspaces delete <id1> <id2> <id3> ...   # variadic; one call
# then, per deleted workspace, in the OWNING clone's path:
git -C <project-path> branch -D <branch>
# finally, once per clone
git -C <project-path> worktree prune -v
```

Confirm the delete result rather than assuming: the JSON response carries `deleted[]` and `warnings[]` — report both.

## Phase 4 — Surface what you didn't delete

For each skipped workspace give the user one line of evidence, not just a name — what is in it and why it survived:

- **Dirty** → which files are modified (`git status --short`), so they can judge whether it matters.
- **No PR but real commits** → the branch's own contribution, diffed **from the merge-base**, never against `origin/main` directly:
  ```bash
  mb=$(git -C <path> merge-base HEAD origin/main)
  git -C <path> log --oneline "$mb..HEAD"
  git -C <path> diff --shortstat "$mb..HEAD"
  ```
  Diffing against `origin/main` directly reports main's own advances as if they were the branch's changes — wildly misleading on an old branch.
- **Held** → which process holds it, so they can decide whether to stop it.

Then ask what to do with them. Do not loop back and delete on your own initiative.

## Report

```
Workspaces: 27 surveyed -> 6 deleted, 9 kept (open PRs), 9 root, 3 surfaced

Deleted (workspace + branch):
  <project>   #401 MERGED   some-finished-branch
  <project>   #405 MERGED   another-finished-branch
  ...

Surfaced - needs your call:
  <project>   saved-groupings    no PR, 2 commits, 547 insertions
  <project>   input-padding-fix  3 files uncommitted
```
