---
name: rebase-after-squash
description: Rebase a stacked branch after its parent PR was squash-merged, identifying the exact old parent boundary so only the branch's new commits are replayed.
---

# Rebase After Squash

A parent branch's multiple commits are usually combined into a different patch by squash
merge. A normal rebase can replay those old commits and create misleading conflicts.
`git cherry` compares individual patch IDs: it cannot reliably prove that several commits
were included in a single squash. Do not derive an automatic reset or rebase boundary
from its minus/plus pattern alone.

## Establish the boundary

1. Inspect branch, upstream, worktree status, and commit graph. Require a clean tree with
   no operation already in progress. Refuse to rewrite the default/protected branch or
   a detached HEAD. Fetch relevant refs when access allows.
2. Find the merged parent PR through the available GitHub tools or `gh`, or use an exact
   parent-tip commit supplied by the user. Confirm the PR was merged into the intended
   upstream. Obtain the parent's final head SHA from the merged PR's recorded history;
   do not assume today's branch tip or squash merge commit is the old boundary.
3. Verify that old parent tip is an ancestor of the current branch:
   `git merge-base --is-ancestor <old-parent-tip> HEAD`. Inspect
   `git log --oneline <old-parent-tip>..HEAD` and
   `git diff <old-parent-tip>...HEAD` to establish which changes must survive. Check for
   later parent updates, interleaved commits, and merge commits that make the stack
   ambiguous. If the boundary cannot be verified, explain the ambiguity and stop.

## Replay only the child work

Record the original HEAD and create a uniquely named backup branch. Capture the expected
remote branch tip before rewriting a published branch. Then run:

```sh
git rebase --onto <upstream> <old-parent-tip> <current-branch>
```

Resolve conflicts from both sides and the intended child behavior; do not blindly choose
ours/theirs. For a branch with merge commits, decide whether preserving merges is needed
before running; the command above otherwise flattens history. Use `git rebase --abort`
to return to the recorded starting state if resolution becomes uncertain.

Compare the replayed commits and resulting diff to the recorded child work, using
`git range-diff` where helpful. Run the project's relevant checks and inspect any conflict
resolutions. Do not reset to upstream just because a heuristic reports zero new commits.

Rewriting local history does not itself authorize rewriting a shared remote. Follow the
user's existing authorization; when pushing a rewritten feature branch, use an explicit
`--force-with-lease=<ref>:<observed-remote-sha>` rather than raw force. A lease failure
requires investigating the remote changes, not overriding them.

Report the old parent boundary, backup branch, verification, and whether the remote was
updated. Retain the backup until the user no longer needs recovery.
