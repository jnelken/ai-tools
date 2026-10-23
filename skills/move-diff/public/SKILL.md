---
name: move-diff
description: Move staged, unstaged, and untracked Git changes to another branch or worktree when work started in the wrong place, preserving a recoverable copy and the index state.
---

# Move Diff

Move all uncommitted work to the user's named branch/worktree in the same repository.
This includes non-ignored untracked files and binary changes. Ask about scope if the user
intends only selected paths; the procedure below moves everything reported by Git status.
Requires local Git and filesystem access; a hosted scratch checkout is not the user's tree.

## Preflight

- Record source path, HEAD, branch, `git status --porcelain=v1 -uall`, staged and unstaged
  diffs, and untracked paths. Never print credential-bearing file contents unnecessarily.
- Resolve a path using `git rev-parse --path-format=absolute --git-common-dir`; source and
  target must identify the same common directory. Otherwise resolve the target branch
  through `git worktree list --porcelain`. Create a separate worktree for an existing
  branch if needed; ask for a starting point if the branch does not exist.
- Reject a target identical to the source. Require a clean target and no in-progress
  merge/rebase or unmerged index in either tree. Check submodules explicitly: stash does
  not capture their working trees. Stop if their changes would be left behind.
- Record the stash stack and coordinate with any active sessions: worktrees share it.
  Do not assume `stash@{0}` continues to refer to the same object after another operation.

## Transfer with recovery

1. Create a uniquely labeled stash with `git stash push -u -m <label>`. Record its object
   ID immediately. If no new stash was created, stop. Confirm the source is clean before
   proceeding. Keep the object reachable with a unique temporary backup ref, such as
   `refs/move-diff/<unique-id>`, until verification is complete.
2. Run `git -C <target> stash apply --index <recorded-object-id>`. `--index` is necessary
   to restore staging; plain apply does not preserve it. Check the command's exit status
   and inspect the target afterward.
3. On any failure, retain the stash and backup ref and report both trees' exact state.
   Apply can partially change the target, including on an untracked-file failure. Never
   assume it was atomic, automatically retry, reset/clean either tree, or pop the stash.
   Resolve the target or restore the source only after inspecting the actual changes.
4. On success, compare the target's staged/unstaged diffs and untracked contents to the
   recorded source. A changed base can require semantic comparison, not byte-identical
   patches. Confirm the source is clean and all intended work is accounted for.
5. Find the current `stash@{N}` whose object ID matches the recorded one. Drop only that
   entry after verification; `git stash drop` needs a stash selector, not a raw SHA.
   If the stack changed unexpectedly, leave it and report the recovery handle. Remove
   the temporary backup ref only after the transfer and stash cleanup are verified.

Report the target path, preserved staging, and any retained recovery object/ref. Keep a
new worktree containing the moved work; do not delete it as temporary cleanup.
