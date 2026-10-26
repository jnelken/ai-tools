---
name: done
description: Close the Superset terminal tab this Claude session is running in, after a quick check that this session isn't leaving its own uncommitted or unpushed work or live background tasks behind. Use only when the user types /done or explicitly says to close this terminal/tab/session ("close this terminal", "we're done, close it"). Superset terminals only; it does not delete the workspace or worktree (that is close-sswt).
disable-model-invocation: true
---

# done

Closes the one terminal this session lives in, via the local `superset` CLI, and nothing else.
It is the light sibling of [[close-sswt]] (which also tears down the worktree, branch and
workspace) and [[close-out]] (which sweeps for orphaned follow-ups). Use those when the work
needs wrapping up; use this when it already is.

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
   the close can be said, so put everything here.
3. As the **last tool call of the turn**, close detached so the message can render first:

   ```bash
   nohup sh -c 'sleep 2; superset terminals close --workspace "$0" --terminal "$1" --local' \
     "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" >/dev/null 2>&1 &
   ```

4. Write nothing after that call.

## Known quirk

Closing from inside the terminal being closed can leave the tab open but frozen in the
Superset UI even though the session is gone (seen 2026-10-26, with the close run
synchronously). The detached delay in step 3 is meant to let the exit reach the UI cleanly;
if the tab still freezes, close it by hand and don't retry the CLI from a new session.
