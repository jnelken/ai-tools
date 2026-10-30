# Agent count menu bar

## Status

Complete. Implemented, tested, pushed, deployed, and installed on this Mac.

## Decisions

- Count actively working agents in the menu bar (confirmed by Jake); show waiting and idle separately.
- Use open-source SwiftBar as the native menu bar host; keep the collector in ai-tools.
- Reuse agenton's local Claude registry and Codex lifecycle sources, plus Superset host snapshots.
- Avoid counting local Claude/Codex sessions twice when they also appear in Superset.
- Report unreachable/offline hosts as incomplete coverage; never silently retain their old count.
- Local polling every 5 seconds; remote polling every 30 seconds with bounded timeouts.

## Findings (with evidence)

- `superset snapshot --json` and `--host <id>` return workspaces, terminal agents, and working/idle/blocked states. Verified both registered hosts on 2026-10-30.
- `superset agents list` lists installed agent configurations, not running sessions.
- `snapshot` has no `--local` flag: omit `--host` for this machine.
- Claude's registry includes a process id and status. Codex lifecycle hook state lacks process ids, so correlate live CLI processes with open rollout files when possible.
- Existing so-agentbar and AgentBar projects focus on local agents; SwiftBar supports a custom collector spanning hosts without maintaining a new native app.
- Live collector result: 1 working, 1 waiting, 14 open across This Mac and jake2, with no coverage errors. Eight behavioral tests pass, including dead/reused pids, helpers, duplicate local sources, long turns, interruption, unavailable hosts, and output markup.
- Coverage is local Claude Code registries and Codex processes with open rollouts, plus Superset terminal sessions. Chat-only sessions, remote agents outside Superset, and closed-rollout desktop threads are explicitly outside coverage.
- Installed SwiftBar 2.1.1; native menu accessibility readback confirms the `1` menu title and the working/waiting/open breakdown for This Mac and jake2. SwiftBar logs confirm successful plugin execution.
- Plugin symlink resolves to `~/.ai-tools/apps/agent-count/agents.5s.sh`; the login LaunchAgent is loaded and its launch command exited successfully (0). No sleep behavior or agent configuration was changed.

## Open threads

- [x] Confirm definition of active: actively working.
- [x] Implement collector, caching, and native menu output.
- [x] Verify count semantics, deduplication, stale activity, and unreachable hosts.
- [x] Install, launch, and verify SwiftBar with deployed source symlinks.
- [x] Commit, push, and deploy.

## Approach

Use Python standard library for local status and bounded concurrent Superset snapshots. Preserve metadata only. The SwiftBar plugin shows an SF Symbol and count with a breakdown and coverage timestamps in its dropdown. Installation preserves existing SwiftBar plugins and provides login startup.
