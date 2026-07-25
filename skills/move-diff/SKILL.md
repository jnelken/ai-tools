---
name: move-diff
description: Use when you want to relocate uncommitted git changes — staged, unstaged, or untracked — from the current workspace to a different branch or worktree without committing — e.g. "move these changes to branch X", "apply my diff to another worktree", "I started work on the wrong branch".
---

# move-diff

Moves all current uncommitted changes — staged, unstaged, and untracked (but not gitignored) — to another branch's worktree, then reverts the source.

## Usage

```bash
move-diff <target>   # a branch name, or a path to an existing worktree
```

## Behavior

1. Resolve `<target>` to a worktree path:
   - If it's an existing directory and `git -C <target> rev-parse --path-format=absolute --git-common-dir` matches the source repo's, use it directly — `<target>` was a worktree path.
   - Otherwise treat `<target>` as a branch name: search `git worktree list --porcelain` for a `branch refs/heads/<target>` entry and use its `worktree` path.
   - No match either way → create a temporary worktree for that branch (as before).
2. `git stash push -u -m "move-diff: <target>"` — captures staged + unstaged + untracked changes and atomically reverts the source to HEAD. If it prints `No local changes to save`, there's nothing to move — stop.
3. `git -C <resolved-worktree> stash apply stash@{0}` — a merge-based apply, so it tolerates target-branch divergence automatically (no separate 3-way flag needed).
4. Clean apply → `git stash drop stash@{0}` in the source, to discard the now-redundant entry. **Must use `stash@{0}` (or `stash@{N}`), not the raw commit SHA** — `git stash drop <sha>` fails with `not a stash reference` even though `apply`/`show` accept a bare SHA fine.
5. Conflict (`UU` status, conflict markers, exit 1) → leave the stash. Report which files conflicted; resolve them in the target worktree with normal git tooling (edit, `git add`). The stash stays as a safety net until dropped manually.
6. Harder failure (e.g. an untracked file already exists at that path in target) → git refuses to clobber and applies nothing; recover by running `git stash pop` in the *source* to restore the exact pre-move state.

## Notes

- `<target>` accepts either form because path-resolution is tried first and only falls back to branch-name matching when the argument isn't an existing worktree directory of this repo — a branch name containing `/` (e.g. `jake/dark-mode`) is never mistaken for a path unless a same-named directory actually exists.
- `--path-format=absolute` (git ≥2.31) is required for the common-dir comparison — plain `--git-common-dir` returns a relative `.git` from the main worktree but an absolute path from linked worktrees, so a naive string compare silently fails on the most common case.
- Moves **everything uncommitted**: staged, unstaged, and untracked changes — the same scope `git status` reports. Gitignored files are excluded (`-u`, not `-a`).
- A clean apply preserves the original staged/unstaged/untracked classification in the target — it isn't flattened to "everything unstaged."
- Binary files move correctly (stash stores real git objects, not text patches).
- Worktrees of the same repo share one `refs/stash` stack, which is what makes this work — `stash@{0}` immediately after push is always the entry just created, from either worktree.
- If no worktree is found, a temp one is created under `/tmp/`. The path is printed; clean up with `git worktree remove <path>`.
- On any failure, the stash is never silently lost — check `git stash list` to find and recover it.
