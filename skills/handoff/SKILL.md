---
name: handoff
description: Summarize the current session's true state and spawn a continuation Claude agent in a new Superset terminal tab in the right workspace — run the complete close-out sweep first, file follow-ups and write memories, then hand off tracked work to the successor (ticket IDs, PRs, decisions) rather than a to-do list. Use when the user wants to continue work in a fresh session with smaller context, or when context is heavy mid-task.
---

# Handoff

## Overview

A handoff is a complete session wind-down and succession event. It differs from `/close-out` in scope: **`/handoff` is a superset of `/close-out`**. A handoff must perform every cleanup and bookkeeping action itself — file follow-up tickets, write durable memories, sweep for orphaned processes and unpushed work, check PR announcement gaps — and *then* spawn a fresh successor agent in a new Superset workspace, handing off all tracked work (ticket IDs, PRs, decisions) rather than an unstructured to-do list.

When to use this: the user wants to continue work in a fresh session with clean context mid-task, or the conversation is heavy and they'd benefit from starting afresh with a handoff summary instead of bloated transcript. The handoff also works as a scheduled routine (e.g., end-of-day) to cleanly wrap up and spawn an unattended successor.

The successor inherits *only* tracked work: resolved tickets, open PRs, committed changes, and explicit decisions. Everything else — questions, findings, and ephemeral state — belongs in memories or Linear, so the successor doesn't re-discover or re-decide.

## Steps

### 1. Complete the close-out sweep

Run `/close-out` first. This is not a separate task; it is *part* of the handoff. Use its five-step procedure:

1. **Sweep the conversation** for unfinished business (pending decisions, unfiled findings, retracted claims, blocked work, deferred promises).
2. **Sweep the machine** for orphaned state (background processes, temp scripts, uncommitted/unpushed work, PRs not yet announced, empty/merged workspaces, stale `pr-review-posted.jsonl` entries, durable learnings not yet in memory).
3. **File follow-ups into Linear**, assigning them to the user with appropriate priority and team routing.
4. **Report what only the human can close** — decisions that require user judgment (approving a PR, choosing between approaches, stopping a process that might be intentional).
5. **Print the close-out confirmation or NOT CLEAN block.**

Refer to the `close-out` SKILL.md for the full procedure. Treat the entire five-step output as evidence for the handoff decision: if close-out came back NOT CLEAN, the handoff stops and reports the blocker. Only if close-out printed the success banner should you proceed to step 2.

### 2. Synthesize session findings into handoff state

Build a **handoff summary** that captures:

- **Session goal:** one sentence on what the user or the task set out to do (e.g., "implement the cache-coherency fix", "audit error handling in the PDF pipeline").
- **What was completed:** list tickets closed, PRs merged, significant features shipped, bugs fixed, or refactors landed. Include ticket IDs and merged PR numbers.
- **What remains open:** list tickets filed (with IDs), PRs awaiting review (with numbers), or work blocked on external dependencies (e.g., "waiting for design review on CON-1234").
- **Decisions made:** explicit choices about architecture, approach, or scope that inform the successor's direction.
- **Observability or instrumentation gaps:** short list of data gaps that slowed this session (link to the telemetry ticket if one was filed per CLAUDE.md's Datadog debugging follow-up flow).
- **Memories written:** list the memory files created/updated this session that the successor should read (e.g., "updated [[project_cache_strategy]] with the new hash scheme").

This summary is *not* a transcript replay — it's a data structure the successor can act on immediately.

### 3. Write durable memories

If step 1 (close-out) didn't already capture these, write them now:

- **Session learnings:** facts about the project, the codebase, or a process that future sessions should know.
- **Decisions made:** non-obvious choices (architecture, trade-offs, why an approach was rejected) — title it as a decision, not a discovery, so it's clearly callable and updateable.
- **Gaps discovered:** observability, instrumentation, or test coverage gaps that slowed this session.

Use the project memory directory (derived, not hardcoded): `$HOME/.claude/projects/$(pwd | sed 's:/:-:g')/memory`. Follow the memory file structure: frontmatter with `name`, `description`, `metadata.type` (user/feedback/project/reference), and body content. Link related memories with `[[name]]`. Update `MEMORY.md` index after writing.

### 4. Determine successor scope

The successor must have enough context to act, but not so much that they re-read the session's full transcript. Prepare a **handoff prompt** that:

- **States the immediate next step:** one sentence on what the successor should do first.
- **Lists outstanding tickets and PRs by ID/number:** these are owned, tracked, and queryable.
- **Names the session memories** the successor should read before acting.
- **Flags any manual decisions still needed:** "this PR awaits your approval", "the choice between approach A and B lives in CON-1234's comments", etc.

Keep it under ~500 words. The successor will read memories and ticket bodies for detail; the prompt is the glue that routes them.

### 5. Create a new Superset workspace and spawn the successor

Create a new Superset workspace scoped to the next phase of work:

```bash
superset workspaces create \
  --name "<CON-ID> <short-slug>" \
  --host <host-id> \
  --project <project-id>
```

(If a Linear ticket doesn't exist yet, use just the slug and backfill once the ticket is cut.)

Then open a terminal in that workspace and spawn the successor agent:

```bash
superset terminals create --workspace <workspace-id> --shell zsh
# Inside the terminal:
cat <<'EOF' | claude-code --stdin --model opus
<handoff-prompt-from-step-4>
EOF
```

The successor runs in isolation: fresh context window, new transcript, no access to this session's history except via the prompt and memories you handed off. They inherit the session's discoveries as *decisions* and *tracked work*, not as ephemera to re-process.

## When NOT to use

- The user only wants to summarize a session without spawning a successor — use `/summary` or just write a memo. A handoff *creates* a successor, so it's heavier than a summary.
- The session is short and the user is still present and active — handoff is for mid-task transitions and session boundaries, not for checking in.
- The close-out sweep comes back NOT CLEAN — the handoff cannot proceed until all blockers are resolved. Report the blocker and stop.
- The user hasn't confirmed they want a successor. Ask first: "Create a new session with [topic] and hand off [state]?"

## Common mistakes

- **Spawning a successor before close-out finishes.** The entire close-out sweep is part of the handoff. If close-out identifies unfiled findings or unpushed work, file/push them first or explicitly hand them off in the successor prompt as "awaiting your action."
- **Re-listing close-out findings in the successor prompt.** Close-out filed the tickets. The successor prompt says "CON-1234 tracks this; read it for context." It does not re-paste the finding or the decision.
- **Forgetting to update the workspace title with the PR number.** When the successor opens a PR, the workspace title should be updated to include the PR number (`#<n>`). This is part of opening the PR, not a separate task.
- **Handing off ephemeral to-dos as decisions.** If the close-out sweep didn't file a ticket and didn't write a memory, it's not tracked. The successor shouldn't inherit a list of "things to consider" — they inherit tracked work.
- **Not writing memories for decisions made.** If this session made a non-obvious architectural choice or rejected an approach with specific reasoning, write it to memory before handing off. The successor will read it before acting.

## Why this exists

Long sessions accumulate complexity and context bloat. A handoff creates a clean break: all debris is cleaned (via close-out), all work is tracked (via tickets and memories), and a fresh agent inherits only what they need to act. This avoids the pathology of ever-longer session transcripts that the user has to manage, and ensures that work continues without loss of fidelity.

A handoff differs from simply asking "should I wrap up?" in that it's *deterministic*: if close-out comes back clean, the handoff proceeds automatically. The user doesn't have to decide whether it's time; the sweep's verdict is the decision.
