---
name: close-out
description: Close a coding session without losing unfinished work or leaving session-created processes and temporary artifacts behind. Preserve decisions and next steps in a repository handoff note.
---

# Close Out

Review the session for unanswered questions, promises, unfiled findings, retracted claims,
and work whose only record is the conversation. Check the actual repository and artifacts
before claiming completion. This is a bounded sweep of the session's scope, not a license
to clean every repository, process, or worktree on the machine.

## Reconcile the work

- Inspect relevant Git status, staged changes, branch/upstream, unpushed commits, and
  worktrees touched by the session. Distinguish this session's changes from existing work.
- Check session-created servers, jobs, and temporary scripts. Verify process identity
  and ownership before stopping anything; a matching port or process name is insufficient.
  Preserve background work the user asked to keep running.
- Check any PRs, issues, or external artifacts changed during the session when access is
  available. Correct a false claim at its source within authorized scope, not only in chat.
- Complete authorized remaining steps where feasible. Never delete an unmerged branch,
  dirty worktree, or unknown file merely to make the close-out look clean. Preserve a
  recovery handle and report any action requiring user input.

## Preserve the handoff

Use the repository's established handoff file. If none exists, create a local
`.claude/IN_PROGRESS.md` note for compatibility with the pick-up workflow. Read before
editing and merge with existing open items; never replace another session's work wholesale.
Keep it out of commits: verify ignore rules and, if needed, use the repository's local
Git exclude file instead of changing shared configuration just for this note.

The note should contain only live, untracked work and user decisions:

- A few context sentences and the precise next action.
- Open items with evidence, file/commit references, and reproduction details needed to
  avoid repeating the investigation.
- Human decisions with the actual options and the consequence of deferring them.
- The date and session provenance when known.

Do not mirror every existing issue or PR. Link a tracked item only where it is needed
to explain the next step. Remove completed items only with evidence. File external
follow-up issues only when authorized and after checking for duplicates; otherwise keep
the finding in the handoff note. Never put credentials or raw production data in it.

If repository writes are unavailable, return the full handoff text in the conversation
and clearly state that it has not been saved to disk.

## Report the actual state

Distinguish "safe to close; remaining decisions recorded" from "cleanup incomplete."
Name any remaining process, unpushed work, unsaved finding, or verification failure that
prevents the former. Link the handoff and existing work artifacts. Do not claim a clean
machine from a clean Git status alone, and do not commit, push, merge, or send messages
without the authorization the session normally requires.
