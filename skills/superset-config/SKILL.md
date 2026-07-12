---
name: superset-config
description: Configure superset.sh (the agentic IDE) project scripts — the .superset/config.json setup/run/teardown model, config.local.json overrides, and resolution order. Use when working with superset.sh workspaces, run/dev-server config, or the `superset` CLI.
---

# superset.sh project configuration

superset.sh is an agentic code IDE (CLI: `superset`, home: `~/.superset`)
for running coding agents across isolated workspaces/worktrees. Each
project defines lifecycle scripts in a committed `.superset/config.json`
at the repo root.

> This is **not** Apache Superset (the BI/dashboard tool). Different
> product, same name. Docs: https://docs.superset.sh

## config.json

Three fields, each an **array of shell-command strings** (run
sequentially). Strings only — no object/label form in `config.json`.

```json
{
  "setup": ["npm install", "cp .env.example .env"],
  "run": ["npm run dev"],
  "teardown": ["docker-compose down -v"]
}
```

- **setup** — runs when a workspace is created. Install deps, seed env,
  migrate. Keep idempotent (re-runs on each new workspace).
- **run** — triggered **on-demand by the Run button**, not at workspace
  creation. Runs in its own dedicated, **restartable** terminal pane
  (stop/restart from the UI without recreating the workspace). This is
  where the dev server / long-running process goes.
- **teardown** — runs when a workspace is deleted. Free ports, stop
  containers, drop volumes.

Useful env var inside these scripts: `$SUPERSET_ROOT_PATH` (the original
repo root) — handy for copying secrets into a worktree, e.g.
`cp "$SUPERSET_ROOT_PATH/.env" .env`.

## config.local.json — personal overrides

Create `.superset/config.local.json` next to `config.json`. It is
**gitignored automatically** and **merges atop** whichever base config
wins. Use it for per-developer tweaks (different port, extra env) without
touching the committed file.

Unlike `config.json`, the local file supports an object form with
`before`/`after` to extend rather than replace a phase:

```json
{
  "setup": {
    "before": ["echo pre-setup"],
    "after":  ["npm run custom-seed"]
  },
  "run": ["npm run dev -- --port 4000"]
}
```

## Resolution order

First match wins — **no merging between these levels** (then
`config.local.json` merges on top of the winner):

1. `~/.superset/projects/<project-id>/config.json` — user override
2. `<worktree>/.superset/config.json` — worktree-specific
3. `<repo>/.superset/config.json` — committed project default

## CLI quick reference

`superset <command>` (v0.2.x):

- `superset projects {create,list,setup}` — manage/adopt projects
- `superset workspaces` (`ws`) — manage workspaces
- `superset terminals` (`term`) create — terminal session in a workspace
- `superset tasks` (`t`) — tasks; `agents` — run agents; `automations`
  (`auto`) — scheduled automations
- `superset {start,stop,status}` — host service daemon
- Global: `--json` (auto-on under agents/CI), `--quiet` (IDs only),
  `--api-key sk_live_…` / `$SUPERSET_API_KEY`

## Authoring a config (typical Node/Next project)

```json
{
  "setup": ["npm install"],
  "run": ["npm run dev"]
}
```

Match the project's real scripts: read `package.json` `scripts` (or
Makefile/Procfile) for the dev/serve target, put deps in `setup`, the
long-running serve command in `run`, and any container/port cleanup in
`teardown`.

## References

- Setup, Teardown & Run scripts: https://docs.superset.sh/setup-teardown-scripts
- CLI reference: https://docs.superset.sh/cli/cli-reference
- Overview / getting started: https://docs.superset.sh/overview
