## Superset Workspaces (sswt)

Read `~/.claude/instructions/superset-workspaces.md` before creating, renaming or reasoning about a workspace. Hard rules:

- **Never build a feature in a root checkout** (`/Users/jake/code/woodrow`, `/Users/jake/code/api`) — create a sswt first. Exception: a repo whose own `CLAUDE.md` says to work in its root checkout (folio-platform's batch-branch flow).
- **Never switch branches in a worktree.** In a sswt, new work stays on its branch; a new branch means a new sswt.
- **Use the local `superset` CLI**, not host-scoped `mcp__superset__*` tools — the relay never connects on this machine.
- **Default to jake2 for non-UI sswts.** jake1 freezes under memory pressure during parallel agent work, so create sswts with `--host` jake2 when it is online and the work doesn't impact UI; UI work stays on jake1. Mechanics in the doc above.
- **Title** is `#<pr> <CON-ID> <short-slug>`; add the PR number as soon as the PR opens.
