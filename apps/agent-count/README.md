# Active agents

A SwiftBar menu bar counter for **actively working** agents. Click the lightning
bolt/count to see working, waiting, and open sessions by provider and host.
Uses the same lifecycle metadata as `agenton`, without changing sleep settings.

## Install

```sh
brew install --cask swiftbar
~/.ai-tools/install.sh
python3 ~/.ai-tools/apps/agent-count/install.py
```

The plugin links to the maintained deploy clone. Installation preserves existing
SwiftBar plugins and adds a LaunchAgent that opens SwiftBar at login. Superset
uses your existing CLI login and active organization; no new credentials are needed.
The installer captures its absolute CLI path for GUI launches.

## Coverage and counting

- **This Mac, Claude Code:** live `claude` processes with Claude's session
  registry (`~/.claude/sessions`). Busy counts as working; waiting is separate.
- **This Mac, Codex:** live `codex` processes with open rollout files. Latest
  turn-start, completion, or interruption determines working state, falling back
  to the existing `~/.codex/turn-state` hook metadata. Helpers are excluded.
  A long turn stays active for as long as the live session owns its rollout.
- **Superset:** terminal agents from this Mac and every other registered host in
  the active organization. Working/running counts as working; blocked is waiting.
  Local provider counts use the greater of process/registry and terminal evidence,
  so Claude/Codex also running inside Superset are not added a second time.
- Local status refreshes every **5 seconds**. Remote host discovery and snapshots
  refresh every **30 seconds**. Each CLI call has a 7-second timeout.
- **`!` means incomplete coverage.** Offline or unreachable hosts contribute no
  count and are explicitly marked unknown, rather than retaining stale numbers.
  The visible count is then the known working count, not a guaranteed fleet total.

This counts sessions, not nested in-process workers. Superset chat-only sessions
and remote agents outside Superset terminals are outside its coverage. A Codex
desktop app-server is covered only when it holds its session rollout open;
there is no claim of complete desktop-app thread enumeration. No prompts,
responses, or credentials are stored; cache files contain counts and host metadata.

The collector only reads local session files, the process table, and Superset
snapshots. Its menu has refresh/open actions; it never sends agent instructions.

## Verify and remove

```sh
python3 -m unittest discover -s apps/agent-count -p 'test_*.py'
~/.ai-tools/apps/agent-count/agents.5s.sh --json
```

To remove: delete `agents.5s.sh` from SwiftBar's configured plugin folder,
unload the login job with `launchctl bootout gui/$(id -u)/com.jnelken.agent-count`,
and remove `~/Library/LaunchAgents/com.jnelken.agent-count.plist`. Configuration
is in `~/.config/agent-count`; cache is in `~/Library/Caches/ai-tools-agent-count`.

SwiftBar is [open source](https://github.com/swiftbar/SwiftBar). Local-only
alternatives considered were [so-agentbar](https://github.com/sotthang/so-agentbar)
and [AgentBar](https://github.com/CenCiviC/AgentBar); using SwiftBar lets this
counter include Superset hosts without maintaining a native app fork.
