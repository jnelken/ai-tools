# Instruction profiles

Shared preferences: read `${AI_TOOLS_HOME:-$HOME/.ai-tools}/instructions/shared.md` when relevant.

If a composed profile already covers the current target in this session, use it without loading it twice. Otherwise, before substantive work, run `python3 "${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/agent-profile" resolve --harness codex --target "$PWD" --text` and follow the returned guidance. Repeat with the actual repository path when entering or switching repositories; a code-root session can work across many repositories. Do not rely on parent code/AGENTS.md discovery. If the resolver is unavailable, report that and follow local repository instructions without inventing environment permissions.

An explicit runtime launch supplies its own guidance and must not load this bootstrap. Profile selection in direct desktop/CLI sessions is model-mediated; use agent-run for composition before model startup. Start a new session after changing profiles.
