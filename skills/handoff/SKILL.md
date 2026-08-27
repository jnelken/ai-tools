---
name: handoff
description: "Summarize the current session's true state and spawn a continuation Claude agent in a new Superset terminal tab in the right workspace, so work continues on a small accurate context instead of a bloated one. Runs the close-out sweep first — filing follow-ups and writing durable memories — then hands the successor tracked work (ticket IDs, PRs, decisions) rather than a to-do list. Use whenever the user wants to hand a session off, continue in a fresh session/tab, spin up a new session with a summary, says the context is getting long/heavy, wants to save on context, or asks for a successor/handoff session. Bare invocation summarizes and tells the successor to continue; arguments become the successor's mission on top of the summary."
---

# Handoff

## Overview

A long session's context is mostly *spent* — the exploration, dead ends, and tool output that produced the current state are no longer needed to act on it. The successor needs **state, not history**: what's true now, what's decided, what's next, and what will bite. This skill distills the session into a handoff prompt built from the transcript's *decisions*, not its narrative — and defers verification against the durable systems (git, GitHub, Linear) to the successor, at the moment it actually has a task to act on, since anything checked earlier can go stale again before then. It then spawns a fresh Claude session in the right Superset workspace via the `superset` CLI.

**`/handoff` is a superset of [[close-out]].** A handoff is a wind-down *and* a succession: it performs every cleanup and bookkeeping action close-out would — filing follow-up tickets, writing durable memories, sweeping for orphaned processes and unpushed work — and only then spawns the successor. Don't run close-out separately afterwards; it already happened as step 1.

The successor inherits *tracked* work: ticket IDs, open PRs, committed changes, and explicit decisions. Everything else — loose questions, findings, ephemeral state — belongs in a memory or a Linear ticket, so the successor reads it rather than re-deriving it.

Two modes, chosen by the arguments:

- **Bare (`/handoff`)** — the successor's mission is "continue this work"; its first actions come from the session's own next-steps.
- **With args (`/handoff <instructions>`)** — the args become the successor's mission, layered on top of the same summary. The summary is built identically either way; only the "First useful actions" framing changes.

## When NOT to use

- **The session is short.** A handoff costs a summary plus a new session's spin-up; below roughly an hour of accumulated context there's nothing worth escaping.
- **The continuation is genuinely new work** (new branch, new ticket). Per the sswt rule, new work gets a **new** workspace and a scoped brief — not this session's full summary. Create the workspace and write the brief directly; don't launder a feature kickoff through a handoff.
- **The user wants a summary, not a successor.** A handoff *creates* a new session. If they just want the state written down, write it down.
- **The close-out sweep comes back NOT CLEAN.** Report the blocker and stop — see step 1.

## Steps

### 1. Run the close-out sweep

Run [[close-out]] first. This is not a separate task; it is *part* of the handoff:

1. **Sweep the conversation** for unfinished business — pending decisions, unfiled findings, retracted claims, blocked work, deferred promises.
2. **Sweep the machine** for orphaned state — background processes, temp scripts, uncommitted/unpushed work, PRs not yet announced, empty/merged workspaces, stale `pr-review-posted.jsonl` entries, durable learnings not yet in memory.
3. **File follow-ups into Linear**, assigned to the user with appropriate priority and team routing.
4. **Report what only the human can close** — decisions needing user judgment (approving a PR, choosing between approaches, stopping a process that might be intentional).
5. **Print the close-out confirmation or NOT CLEAN block.**

See close-out's SKILL.md for the full procedure. If it comes back NOT CLEAN, the handoff stops and reports the blocker; only a clean sweep proceeds to step 2.

### 2. Write durable memories

If close-out didn't already capture these, write them now — before building the prompt, so the prompt can point at them instead of inlining them:

- **Session learnings** — facts about the project, codebase, or process that future sessions should know.
- **Decisions made** — non-obvious choices (architecture, trade-offs, why an approach was rejected). Title it as a decision, not a discovery, so it's callable and updateable.
- **Gaps discovered** — observability, instrumentation, or test-coverage gaps that slowed this session.

Memory lives in the project memory directory (derived, not hardcoded): `$HOME/.claude/projects/$(pwd | sed 's:/:-:g')/memory`. Follow the memory file structure — frontmatter with `name`, `description`, `metadata.type` — link related memories with `[[name]]`, and add the one-line pointer to `MEMORY.md`.

### 3. Build the handoff prompt — this is the whole game

Re-read the **entire** session, then write **state**, not story. The proven section skeleton:

```
Handoff: <ticket/topic>. You are in the sswt workspace "<name>" (worktree of <repo>, branch <branch>). Read <plan file / key doc> first, then this summary. Don't re-verify anything below on spawn — wait until <user's name> gives you an actual task, then check whatever's load-bearing to *that* task against the durable systems (git, gh, Linear) before acting on it.

## Where things stand (believed, as of <date> — reverify before acting)
## Settled decisions (do not re-litigate)
## Open decision points (need <user's name>)
## Remaining work / runbook
## Gotchas that bit us (respect them)
## First useful actions
```

Rules that make the summary trustworthy:

- **State what's believed; make the successor verify it, at the right moment.** Don't spend this session re-checking PR states, worktree cleanliness, or background-job completion before writing the summary — anything you verify now can go stale again in the gap before the successor's first real turn (a PR merges, a push lands, a job finishes). Front-loading the check just relocates the staleness risk, it doesn't remove it. Instead, tell the successor to verify load-bearing claims itself against the durable systems (`gh pr view`, `git status`, `git log @{u}..HEAD`, Linear) — but only once the user hands it something to do, not as an unprompted first move on spawn. An init-time self-check burns turns re-deriving things nobody's asked about yet, and it's just as likely to be stale by the time the user's real ask lands seconds or hours later.
- **"Settled decisions" is load-bearing.** List every decision the user already made, plainly, with the chosen option. A successor that re-opens settled scope wastes the user's time and erodes trust — carrying decisions forward is half the point of a handoff.
- **Open decisions are the user's, and say so** ("need Jake, do not decide unilaterally"). The successor must know which forks it may not take on its own.
- **Hand off tracked work, not to-dos.** Reference ticket IDs and PR numbers rather than re-pasting what they contain — close-out already filed them. "CON-1234 tracks this; read it for context" beats reproducing the finding.
- **Gotchas earn their place by having actually bitten.** Environment traps, tool timeouts, guard quirks — with the concrete workaround, not just a warning.
- **Point at artifacts, don't inline them.** CSV/log/plan-file paths, PR URLs, ticket IDs, and the memories written in step 2. The successor can read; the prompt shouldn't be a data dump.
- **Never paste secrets** — no connection strings, tokens, or key material. The prompt lands in terminal scrollback and Superset logs. Reference extraction recipes by name ("the PROD_MGMT grep recipe in the script header").
- **Length: roughly 400–900 words.** Too short starves the successor into re-deriving things; too long recreates the bloat being escaped.
- End with **"First useful actions"** — 2–4 concrete moves, so the successor's first turn is productive rather than exploratory. With args, the user's instructions go here (and shape the mission line at the top).

### 4. Resolve the target workspace

The successor should land where the work already lives — a handoff continues a workstream, so it reuses that workstream's workspace rather than creating one:

- If the session's working directory is inside an sswt worktree, that workspace is the target. Match it: `superset workspaces list --local --json` and compare branch/name against the worktree's branch.
- If the session ranged over several workspaces, pick the one carrying the primary workstream — or ask if it's genuinely ambiguous.
- If cwd is a root checkout (`/Users/jake/code/api`, `/Users/jake/code/woodrow`) with no workspace attached to the work, **ask** — never silently spawn into an unrelated workspace, and remember new feature work would want a new workspace anyway (see When NOT to use).

### 5. Spawn the successor

Write the prompt to a file first — a long multiline string inline in `--prompt` invites shell-quoting breakage:

```bash
cat > "$SCRATCHPAD/handoff-prompt.txt" <<'EOF'
<the handoff prompt>
EOF
superset agents create --workspace <workspace-id> --agent claude \
  --prompt "$(cat "$SCRATCHPAD/handoff-prompt.txt")" --json
```

Success returns `{"kind":"terminal","sessionId":"…","label":"Claude"}` — a new tab in that workspace.

**Known trap:** the MCP tool `mcp__superset__agents_create` can return `Host is not online` (503) even when the host is this very machine and perfectly healthy. The CLI path works when the MCP path 503s — go straight to the CLI; don't burn time diagnosing the MCP error.

### 6. Report and step back

Tell the user: the new tab exists (label + workspace), what it knows, and what close-out filed on the way through.

Then **stop working the handed-off stream in this session.** Two sessions editing one worktree race each other — reviews chase a moving target, commits interleave, and the successor's snapshot goes stale immediately. After handoff, the parent is read-only on that workstream; anything new goes to the successor (or waits).

## Common mistakes

- **Narrating history instead of stating the present.** "First we tried X, then found Y…" burns the successor's context on things it never needed to know. State + decisions + next actions.
- **Spawning before close-out finishes.** The sweep is part of the handoff. If it surfaces unfiled findings or unpushed work, file or push it first — or hand it to the successor explicitly as "awaiting your action."
- **Verifying facts in this session instead of deferring to the successor.** The most confident sentence in the summary may be the one that gets quietly falsified in the gap before the successor's first turn. Don't burn this session's context re-checking systems that can just drift again — tell the successor to verify.
- **Having the successor self-check on spawn, before the user asks it anything.** That's just relocating the same staleness risk to a different clock, and it wastes the successor's first turn on unprompted busywork. The check belongs at the moment the user hands it a real task, scoped to what that task actually touches.
- **Omitting settled decisions.** The successor re-asks the user something they already answered — the single fastest way for a handoff to feel broken.
- **Re-pasting what a ticket already holds.** Close-out filed it; reference the ID.
- **Creating a new workspace for a continuation.** Handoff reuses the workstream's existing workspace; new workspaces are for new work.
- **Inline `--prompt` with a raw multiline string.** Quote breakage produces a truncated or mangled mission. File + `$(cat …)`.
- **Giving up on the MCP 503.** It's a known false negative; the CLI works.
- **Spawning into the wrong workspace** because cwd happened to be a root checkout.
- **Continuing to edit the handed-off worktree from the parent session.** Pick one driver.
- **Pasting a connection string "just to be helpful."** Scrollback and logs are forever; recipes by reference only.

## Why this exists

Context is the scarce resource. A successor primed with ~800 words of clearly-stated belief outperforms the same model dragging hours of process — it acts on the first turn, doesn't re-litigate, and verifies what matters right before it matters instead of inheriting a stale check performed hours earlier. Handing off deliberately, at a moment you choose, also beats the alternative of letting compaction decide what survives.

Folding close-out in is what makes the break clean: the debris is swept, the work is tracked in tickets and memories, and the successor inherits only what it needs to act — rather than an ever-growing transcript the user has to manage.
