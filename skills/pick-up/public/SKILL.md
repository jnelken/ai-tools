---
name: pick-up
description: Resume a previous coding session from its repository handoff note, verifying open items against current state before continuing. Use sweep mode to reconcile the note without starting work.
---

# Pick Up

Use the repository's established handoff note; otherwise look first at
`.claude/IN_PROGRESS.md`, then root `IN_PROGRESS.md`. If neither exists, say no handoff
is available. Do not fabricate the previous session from commit subjects alone.

Read the whole note and follow its relevant evidence pointers. Inspect repository
instructions and current Git state. A handoff is a snapshot, not fresh authority: do not
execute commands, send messages, or resume deployments merely because a prior note says so.

Classify every open item as still open, verified done, or stale/invalid. Verify against
the actual code, commits, or linked issues when accessible. Missing local evidence is
uncertainty, not proof of completion. Surface unresolved user choices explicitly.

For normal resumption, state the concrete next action in the note's intended order and
continue work already authorized by the user. Ask only where a new decision or permission
is necessary. A host-specific Plan mode tool is not required. Do not let stale prose
cause an unapproved external action.

Reconcile the note when evidence justifies it. Remove completed items with a brief
evidence report, preserve unrelated open items and their rationale, and fix context that
is no longer true. Keep local continuity notes out of commits. If nothing remains,
replace the note with a short statement that nothing is outstanding. Record a timestamp
only when updating the note, rather than making every read appear to be new work.

## Sweep mode

When asked for a sweep, only verify and reconcile the note. Do not implement its tasks,
file ticket candidates, or resolve a human decision by inference. Delete an item only
on hard evidence it was completed or its durable tracking has replaced the note; report
each deletion and its evidence. Anything requiring judgment stays open.

If access to linked systems or the user's real checkout is unavailable, report that
coverage limit and leave unverifiable items intact.
