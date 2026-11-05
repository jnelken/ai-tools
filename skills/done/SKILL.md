---
name: done
description: Close the Superset terminal tab this Claude session is running in, after a quick check that this session isn't leaving its own uncommitted or unpushed work or live background tasks behind. Use only when the user types /done or explicitly says to close this terminal/tab/session ("close this terminal", "we're done, close it"). Superset terminals only; it does not delete the workspace or worktree (that is self-destruct).
disable-model-invocation: true
---

# done

Closes the one terminal this session lives in, via the local `superset` CLI, and nothing else.
It is the light sibling of [[self-destruct]] (which also tears down the worktree, branch and
workspace) and [[close-out]] (which sweeps for orphaned follow-ups). Use those when the work
needs wrapping up; use this when it already is. A session that merely signs off ⛏️ and is
left open gets the same treatment from the `reap-sswts` automation an hour later.

**This ends the session.** The close disposes the PTY the command runs in, so Claude cannot
say anything afterwards. The final message must be written *before* the close.

## Guardrails

Stop and say why, without closing, if any of these hold:

1. `$SUPERSET_WORKSPACE_ID` or `$SUPERSET_TERMINAL_ID` is unset or empty — this is not a
   Superset terminal. The CLI reports `disposed` even for an ID that doesn't exist, so an
   empty or wrong ID would silently do nothing (or hit the wrong terminal).
2. `superset` is not on `PATH`.
3. This session has its own uncommitted changes, unpushed commits, or running background
   tasks/monitors. List them and ask once. Dirty files that other sessions own (this repo
   is worked by several at once) are an FYI, not a blocker — mention them in one line.

Always close `$SUPERSET_TERMINAL_ID` from the environment. Never list terminals and close
another one, and never use the Superset MCP `terminals_close` tool: it goes through the cloud
relay and fails with `Host is not online` (503) when the host shows offline. The CLI with
`--local` talks to this machine directly.

## Steps

1. Run the guardrail checks above (`git status --short`, `git log @{u}..` if a branch has an
   upstream, and any tasks you started).
2. Write the final message: what was done, what is left, and the status line. Nothing after
   the close can be said, so put everything here. The status line is `⛏️ <label>` (emoji
   form, U+26CF U+FE0F), e.g. `⛏️ Terminal closing`: the session is ending with nothing
   left open. One exception: in an sswt whose branch is not merged yet, sign off
   `✅ <label>` instead, since ⛏️ also promises the sswt is merged.
3. As the **last tool call of the turn**, close detached so the message can render first:

   ```bash
   nohup sh ~/.claude/skills/done/scripts/close-tab.sh \
     "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" >/dev/null 2>&1 &
   ```

4. Write nothing after that call. If the harness asks for a reply anyway, answer with the
   status line alone.

## Known quirk: the dead tab

The close itself works: the host disposes the terminal and ends the Claude session in it
right away (`terminal_agent_bindings.end_reason = disposed` within a second of the request in
`~/.superset/host/*/host.db`, checked on three past `/done` runs). What lingers is the **tab** in a desktop app that views this host
from another machine (e.g. jake2's tabs in the MacBook's app): that app keeps a dead tab after
the terminal is disposed, and `superset panes` cannot reach it ("This host has no pane layout
to edit"). `close-tab.sh` uses `panes close` whenever a desktop is attached to this host, which
removes the tab outright, and falls back to `terminals close` otherwise. In the fallback case
the dead tab has to be closed by hand; don't retry the CLI from a new session. Each run is
logged to `~/.claude/state/done-close.log`.
