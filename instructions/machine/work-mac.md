## Work Mac

Concentro repositories are checked out under `~/code`; the Superset/sswt rules above apply there. Reserved for work-Mac-only facts; permissions still come from the target repository's classification, never from this machine.

### Offload agent work to jake2

jake1 (this laptop) is for human-led work; jake2 is the agent host. **Whenever jake2 is online and available, spawn subagents there** instead of running performance-heavy agent work (implementation, reviews, typechecks, test runs, investigations) on jake1.

- **Check first, every time:** `superset hosts list` shows jake2 `online: yes` and `superset projects list --host 5f5aff3dec6113da62545abf567f8d10` includes the repo. If either fails, fall back to jake1 and say so in one line.
- **Dispatch through Superset, not in-process.** The Agent and Workflow tools run on jake1, so they don't offload anything. Use `superset workspaces create --host 5f5aff3dec6113da62545abf567f8d10 --project <id> --branch <name> --agent claude --prompt "..."`. Mechanics, the pushed-branch and self-contained-prompt requirements, and watching: `~/.claude/instructions/superset-workspaces.md` ("Remote Host (jake2)").
- **UI work goes to jake2 too** (dev server, screenshots, Playwright, Chrome) unless Jake explicitly says in the session to keep it on jake1. This overrides the "UI stays on jake1" rule in `superset.md` and `superset-workspaces.md`.
- **Keep on jake1:** light read-only lookups where a dispatch costs more than it saves.
- **Parallel agents** still get one workspace each, all on jake2 when it's available.
