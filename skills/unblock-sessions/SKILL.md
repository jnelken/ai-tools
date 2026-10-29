---
name: unblock-sessions
description: Walk every Claude session running in a Superset terminal tab on this machine, one at a time, and ask Jake here (AskUserQuestion) about each one that is waiting on him — open questions, Decisions blocks, live AskUserQuestion/permission dialogs, and finished (✅/⛏️) tabs that could be reaped — with enough transcript context that he never has to open the tab. Relays each answer into the session, then rescans for follow-ups until nothing is left. Every prompt offers Skip for now and Skip forever. Use when Jake says "unblock my sessions", "what's waiting on me", "check my tabs for questions", "answer my sessions", "survey open sessions", or "/unblock-sessions".
---

# unblock-sessions

An interactive inbox over Jake's parallel Claude sessions. Questions pile up in tabs he isn't
looking at; this skill brings them to him one at a time, relays his answer, and keeps going.

**Helper:** `~/.claude/skills/unblock-sessions/unblock.py` (stdlib Python, run with `python3 -I`).
It reads `superset snapshot`, maps each terminal to its Claude session via Superset's
`host.db` (read-only), and parses that session's transcript JSONL. The transcript holds
the full turn, not just the visible screen. Always go through the helper rather than dumping
snapshots or transcripts into context.

State lives in `~/.claude/state/unblock-sessions/`:
- `skip-forever.json`: question keys Jake muted.
- `no-question.json`: turns already judged to ask nothing, so later runs don't re-read them.

A **key** is `terminal + session + last assistant turn`. When a session produces a new turn,
it gets a new key and shows up again. "Skip forever" therefore mutes that exact question,
not the session.

## Procedure

### 1. Set up the run

```bash
H=~/.claude/skills/unblock-sessions/unblock.py
SEEN="<scratchpad>/unblock-seen.txt"; : > "$SEEN"
```

Keep a `relayed` list (`WORKSPACE_ID:TERMINAL_ID`) in your head.

### 2. Get the next candidate

```bash
python3 -I "$H" next --seen "$SEEN"
```

This returns one idle or blocked agent tab from **any** workspace on this host (your own tab
is excluded), or `{"done": true}`. The fields:
- `project`, `workspace_name`, `branch`, `title`, `glyph`, `state`
- `first_prompt`: the session's mission or handoff
- `last_user_message`
- `earlier_turn_text` and `final_message`: the last turn
- `pending_tool_use`: an unanswered tool call, meaning a live dialog
- `screen_tail`: blocked or dialog tabs only
- `key`

Handle exactly this one candidate (step 3), then call `next` again. Order doesn't matter.

### 3. Classify it

Work through these in order.

**a. Live dialog.** `pending_tool_use` is set, or `state` is `blocked`.
- **AskUserQuestion:** the questions and options are in `pending_tool_use.input`. Ask Jake the
  same questions (step 4) and answer with key presses (step 5).
- **Permission prompt or another tool:** describe the tool and its input, and ask
  Approve / Deny / Skip for now / Skip forever.
- If you can't map the dialog to keys with confidence, show Jake the context and say to open
  the tab. Then add the key to `$SEEN`.

**b. Finished tab.** `glyph` is ✅ or ⛏️, or the final status line uses one of them.
Ask: "Reply `reap` to <project/workspace> (<one-line what it did>)?" with options
**Reap** / **Skip for now** / **Skip forever**. On Reap, relay the text `reap`.

**c. Everything else.** Read `final_message` (plus `earlier_turn_text` if needed) and judge
whether it asks Jake for anything. Signals:
- a `**Decisions**` block
- a 🛑 status line
- a direct question
- a hand-back like "needs your push", "your call", or "pending your OK"
- "waiting on you to X" where X is Jake's action

Ignore ⏳ lines that wait on someone else (CI, a teammate, a form) and pure reports.
- **Nothing asked:** run `python3 -I "$H" no-question <key>` and go back to step 2. No prompt.
- **Something asked:** go to step 4.

### 4. Ask Jake (AskUserQuestion)

Use one call per session, with one question per decision (max 4). If a session has more than
4 decisions, ask the first 4, relay, and the rest come back on the next turn.

- **Question text:** start with where it lives and what the session is for, in one line, then
  the decision itself. Example: `[api · import-feature] Inventory done; scope for slice 1:
  the whole table at once, or one stage first?`
- **Header** (≤12 chars): the decision's topic, for example `Scope` or `Base branch`.
- **Options:** up to 2 real choices (the session's recommendation first, marked
  `(Recommended)`), then **Skip for now** and **Skip forever**. Additional lettered choices and
  free text go through the automatic "Other". If a decision has more than 2 choices, list them
  all in the preview so Jake can type the letter.
- **preview** (on each real option): this is what saves Jake opening the tab. Include:
  - the session's goal (from `first_prompt`, condensed)
  - what it just did
  - the relevant excerpt of `final_message`
  - what picking this option will make it do

  Keep it under ~25 lines. Repeat the shared context on each option; previews are per option.

Don't paste raw transcript or tool output.

### 5. Act on the answer

- **Real answer:**
  - **Text questions:** relay in the session's own format. Zen Decisions take the letters
    (`B D`). Otherwise send a short message: `Jake (via /unblock-sessions): 1) stage first
    2) base on main …`. If Jake skipped some of several questions, say so in the same message
    ("#3 skipped for now").
    ```bash
    python3 -I "$H" relay <workspace_id> <terminal_id> --text "<answer>"
    ```
  - **Live AskUserQuestion dialog:** navigate with keys, for example
    `--keys "down,down,enter"` to pick the third option. Multi-question dialogs advance per
    question, then usually need one more `enter` to submit. Free text: pick the "Type
    something" row, then `--text`. After **each** send, check the screen with
    `python3 -I "$H" screen <ws> <term> --lines 30` before the next key. If the screen isn't
    what you expect, stop and tell Jake to finish in the tab.

  Then add `<ws>:<term>` to `relayed` and append the key to `$SEEN`.
- **Skip for now:** append the key to `$SEEN`. It won't come back this run.
- **Skip forever:** `python3 -I "$H" skip-forever <key> "<project · short label>"`.

If every question was skipped, nothing is relayed. If all were Skip forever, use skip-forever;
otherwise treat it as Skip for now.

Then go back to step 2.

### 6. Rescan for follow-ups

When `next` returns `done`:
- **`relayed` is empty:** you're finished. Go to step 7.
- **Otherwise:** wait for those sessions to settle, then clear `relayed` and return to step 2.
  Answered sessions now have a new turn and a new key, so follow-up questions surface
  naturally.

```bash
python3 -I "$H" wait <ws:term> [<ws:term> ...]    # polls ≤540s; run with a 600000ms Bash timeout
```

If some are still working after one wait, wait once more. If they're still working after that,
stop and list them as still working.

### 7. Report

Write a Zen-style final message. Under **Verdict**, list what was relayed (per session: project
· workspace, and what was sent), what was reaped, and what was skipped. List forever-skips
with their labels so Jake can undo them by deleting the entry from `skip-forever.json`. End
with the status line.

## Gotchas

- **`terminals read` shows only the screen.** That's why the helper reads the transcript
  JSONL (via `host.db` → `~/.claude/projects/*/<session>.jsonl`). Non-Claude agents fall
  back to `superset agents read`.
- **A tab can close between `next` and `relay`.** A send error like "has ended" means the
  session is gone. Tell Jake, mark the key seen, and move on.
- **Never relay into a `working` tab.** `next` only returns idle or blocked tabs. If Jake took
  a long time to answer, re-check with `screen` before sending.
- **`reap` is just text.** The receiving session decides what reaping means: a sswt runs its
  close-out or reap flow, and a root-checkout `local` tab typically closes itself (`/done`).
- **The superset MCP tools are host-scoped and dead here.** Use the `superset` CLI, which the
  helper wraps.
