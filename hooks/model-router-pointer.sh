#!/bin/bash
# SessionStart hook: points the agent at ~/.claude/MODEL_ROUTER.md when the
# session's cwd is inside the personal code dir (~/Dropbox/code on the personal
# machines). The router governs provider/model choice for delegated work in
# personal repos only, so every other session -- Concentro repos, the work
# laptop -- gets nothing. Injects a one-line pointer, not the file: the agent
# reads the policy itself, just before it first delegates.

command -v jq &>/dev/null || exit 0

router="$HOME/.claude/MODEL_ROUTER.md"
[[ -f "$router" ]] || exit 0

pcd_bin="${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/personal-code-dir"
code_dir=$("$pcd_bin" 2>/dev/null) || exit 0
[[ -n "$code_dir" ]] || exit 0

cwd=$(jq -r '.cwd // empty' 2>/dev/null)
[[ -n "$cwd" ]] || cwd="$PWD"

# Resolve symlinks so a Dropbox path reached via a link still matches.
code_dir=$(cd "$code_dir" 2>/dev/null && pwd -P) || exit 0
cwd=$(cd "$cwd" 2>/dev/null && pwd -P) || exit 0

case "$cwd/" in
  "$code_dir"/*) ;;
  *) exit 0 ;;
esac

msg="This session is in the personal code dir. Before you delegate work to a sub-agent or pick a provider/model for it, read \`$router\` -- it governs delegation here (Cursor first, then Codex, then Claude), subject to its own scope and preconditions."

jq -n --arg msg "$msg" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $msg
  }
}'
