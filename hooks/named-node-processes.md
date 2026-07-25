# Named Node Processes (Activity Monitor Labels)

Every node process spawned by a coding agent normally shows up in Activity Monitor as `node`, making it impossible to tell which branch, agent, or tool owns it. This pair of hooks labels them:

| Process | Shows up as |
|---|---|
| Claude running `npm run dev` on branch `my-feature` | `c-dev-my-feature` |
| Claude running `npx eslint …` | `c-eslint-my-feature` |
| Codex running the same | `x-eslint-my-feature` |
| MCP servers (per agent session) | `mcp-datadog`, `context7-mcp`, `playwright-mcp`, … |

Anything still showing bare `node` is genuinely foreign — that's the point.

## How it works

Two mechanisms, both feeding one tiny injector:

1. [`set-process-name.sh`](set-process-name.sh) — a **PreToolUse hook** that rewrites every Bash command Claude runs to export `PROCESS_NAME={agent-initial}-{script}-{branch}` and append `--require <injector>` to `NODE_OPTIONS`. The agent initial comes from `SUPERSET_AGENT_ID` when launched by [Superset](https://superset.sh) agent wrappers (c=claude, x=codex, …), defaulting to `c`.
2. [`set-process-title.cjs`](set-process-title.cjs) — the **injector**, loaded via `NODE_OPTIONS=--require` so Node runs it before any main module. It sets `process.title` from `PROCESS_NAME` when present, else from the script's basename (guarded to absolute paths that exist, so subcommand argv like `claude daemon run` is never mistaken for a script).

Because `--require` is plain Node behavior, this labels eslint, vitest, tsx, MCP servers — anything node-based — without those tools knowing about it.

## Setup

`install.sh` symlinks both scripts into `~/.claude/hooks/`. Then wire `~/.claude/settings.json`:

```json
{
  "env": {
    "NODE_OPTIONS": "--require /Users/YOU/.claude/hooks/set-process-title.cjs"
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/set-process-name.sh\"" }
        ]
      }
    ]
  }
}
```

- The `hooks` entry labels everything Claude runs in its shell.
- The `env.NODE_OPTIONS` entry covers processes that never pass through the Bash hook — MCP servers especially, which self-label from their script basename. **`env` values are literal — no `$HOME` expansion — so use your real absolute path.**

Requires `jq` on `PATH`.

### Optional: explicit names for user-scope MCP servers

MCP server configs accept an `env` block, which beats the basename fallback:

```bash
claude mcp add-json datadog '{"command":"npx","args":["-y","datadog-mcp"],"env":{"PROCESS_NAME":"mcp-datadog","NODE_OPTIONS":"--require /Users/YOU/.claude/hooks/set-process-title.cjs"}}' -s user
```

### Optional: Codex

Codex has no input-rewriting hook, so it gets the env-only tier in `~/.codex/config.toml` — shell commands self-label by script basename:

```toml
[shell_environment_policy.set]
NODE_OPTIONS = "--require /Users/YOU/.claude/hooks/set-process-title.cjs"

# and per MCP server:
[mcp_servers.koyeb.env]
PROCESS_NAME = "mcp-koyeb"
NODE_OPTIONS = "--require /Users/YOU/.claude/hooks/set-process-title.cjs"
```

### Optional: your app's entrypoint

Long-running servers can fold the label in directly, so agent-started instances are distinguishable from your own:

```ts
const agent = process.env.SUPERSET_AGENT_ID;
process.title = process.env.PROCESS_NAME ?? (agent ? `${agent}-my-api` : "my-api");
```

## Verify

Ask Claude to run `npx eslint .` (or anything node-based), then:

```bash
ps -axo pid,comm | grep -E 'c-|mcp-'
```

New agent sessions are needed before MCP servers pick up names — already-running sessions keep their old unlabeled processes until restarted.

## Gotchas (learned the hard way)

- **`hookEventName` is required.** A PreToolUse hook's JSON output is silently ignored without `"hookEventName": "PreToolUse"` alongside `updatedInput`.
- **Don't `read -r` the hook input** — it stops at the first newline and truncates multi-line commands.
- **The wrapped command needs its own lines.** Appending `)` directly after the command breaks anything ending in a heredoc terminator (`EOF )` never terminates).
- **`ps` truncating `comm` to ~16 chars is a display artifact** (only when other columns follow), not a kernel limit. Activity Monitor shows the full title.
