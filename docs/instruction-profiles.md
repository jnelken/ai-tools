# Instruction profiles

Source is maintained in ai-tools; installed entry points link to the deploy clone at `~/.ai-tools`. Dotfiles continues to own development settings and procedure references. No `code/AGENTS.md` inheritance is required.

## Maintaining profiles

Use [instruction-profile-maintenance](../skills/instruction-profile-maintenance/SKILL.md) when changing recipes, bootstraps, selection, or harness loading/isolation. It directs the maintenance workflow; [ADR 0001](../adr/0001-compose-instructions-by-role-and-repository.md) owns the rationale and evaluation criteria. The normal installer exposes this skill to Claude and Codex through links to the deploy clone.

## Starting and inspecting sessions

```sh
agent-profile resolve --target "$PWD" --harness claude --explain
agent-run --profile development/auto -- claude --remote-control
agent-run --profile development/personal -- codex
agent-run --profile runtime/slackagent -- claude -p 'Your request'
agent-run --profile runtime/text -- codex exec -m gpt-6.1-sol 'Return OK'
```

Direct Claude/Codex starts (including desktop) receive a short global bootstrap instructing the model to run the resolver. This is model-mediated, not a pre-launch guarantee. The wrapper composes before launching. Desktop tool configuration is unchanged. A remote-control session can stay at a code root: resolve each target repository again before working there, and read local/nested repository instructions. Policies from another target confer no permissions on the current target. Begin new sessions after changing profile sources; existing prompts are not rewritten.

Claude's bootstrap also carries the minimum status-line contract required by its always-active development Stop hook. Even a trivial first turn that skips resolution uses the answer-only default. Full status meanings and priorities remain in the provider fragment, with an unambiguous `AI_TOOLS_HOME`-aware reference. Wrapped development sessions can still load the bootstrap and skip redundant resolution; explicit runtimes suppress it and the hook. Codex has no corresponding bootstrap sign-off requirement.

Explicit profile (`--profile`, then `AGENT_PROFILE`) wins. Otherwise choose `development/auto`: explicit repository mapping, recognized remote owner, then configured root for a directory without an origin. An unrecognized remote remains unknown even under a personal root. Bare/unclassified directories load shared guidance only. Personal roots reuse the existing `PERSONAL_CODE_DIR`/Dropbox conventions. `~/code` is a work-root default, not a personal root.

Configure a machine locally at `~/.config/agent-profiles/config.json` (or `AGENT_PROFILE_CONFIG`). Example:

```json
{
  "roots": {"~/code": "work", "~/Dropbox/code": "personal"},
  "repositories": {"~/code/others/personal-project": "personal"},
  "work_owners": ["Concentro"],
  "runtime": {
    "allowed_mcp_names": ["mailcrush", "linear", "lunch-money", "gmail", "google-calendar"],
    "claude": {"mcp_files": []},
    "codex": {"mcp_files": []}
  }
}
```

MCP files use the existing `mcpServers` JSON format and live locally; refer to credentials through existing supported mechanisms. Do not put credentials in ai-tools. Only configure existing integrations. Runtime text processing ignores all optional integrations. An explicit `--mcp-config` from the application is subject to the same server-name allowlist. Do not populate global runtime MCP files with owner-only connectors in a multi-user application: Slackagent supplies its owner mail config per call, while household/background calls use runtime/text. Mail/calendar may be unavailable for Codex until an existing connector is explicitly configured; app-supplied finance and Linear commands remain available.

## Runtime boundary

Claude uses safe mode for text-only calls. Connector-enabled calls use restricted mode, no settings sources, disabled hooks and skills, and explicit MCP configuration. Both retain subscription authentication (`--bare` is not suitable). Connector-enabled calls set the installed CLI’s `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` controls; these require a fresh isolation smoke test after CLI upgrades. Connector-enabled Claude launches fail closed outside verified version 2.1.303; after a successful smoke check, set local `runtime.claude.verified_connector_version` to the newly verified version. Safe mode alone suppresses explicit MCP connectors and therefore cannot serve the owner mail runtime. Built-in tools are removed in both variants. Codex ignores user config/rules, disables project instruction loading, treats the target checkout as untrusted for project config, disables hooks, skills, memory, shell, apps, and delegation, and injects composed guidance as additional developer instructions. Built-in harness instructions and managed policy remain in force. Runtime arguments cannot override these controls. Missing required CLI flags fail the launch.

Profiles organize context and configuration; they are not an OS security sandbox. Runtime processes retain existing authentication and working directories. The launcher uses exec so cancellation reaches the model process directly. CLI upgrades require repeating the runtime smoke checks below.

## Migration, checks and rollback

`instructions/MIGRATION.md` maps every previous global Claude section to its new destination. Provider-specific procedures remain provider-specific; frontend guidance is on demand. Existing dotfiles procedure references remain installed. Policy intent is preserved, not redesigned.

Run:

```sh
python3 -m unittest discover -s lib -p 'test_agent_profiles.py'
python3 bin/install-agent-profiles --dry-run
```

Install through the normal ai-tools deploy process: push the source commit, then run `~/.ai-tools/install.sh`. A local commit alone is not proof of deployment; do not assume a post-commit redeploy hook is installed. The installer preserves replaced files/symlinks as `*.before-profiles-<timestamp>`, verifies links, and retires only its known parent CLAUDE.md symlink. It leaves arbitrary parent AGENTS.md files alone. Re-running installation must make no replacements.

Before cutover, start fresh sessions through both providers and verify: runtime-only instructions, intended MCP tools, no development skills/hooks or checkout instructions, existing subscription login, and no external writes. Compare text/profile sizes and startup time using a fixed short prompt. Smoke-check direct CLI and desktop starts and one code-root remote-control session switching targets. Work-machine rollout remains pending until this check runs there; local fixtures do not prove that deployment.

Rollback: restore the preserved global symlinks/files, restore the previous Slackagent runner configuration, and start fresh sessions. The installer will reapply profiles on future runs; roll back the source commits too when reverting the feature permanently. No database/log/cache/report moves are involved.
