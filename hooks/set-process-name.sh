#!/bin/bash
# Set PROCESS_NAME and NODE_OPTIONS for all Node processes spawned by Claude.
#
# Scheme: {agent-initial}-{script}-{branch}, front-loaded so the diagnostic
# bits (which tool, which branch) survive macOS Activity Monitor's ~16-char
# truncation of the process name. NODE_OPTIONS=--require injects the title
# into any Node process (eslint, vitest, tsx, etc.) without those tools
# needing to know about PROCESS_NAME. The subshell keeps the exports alive
# across semicolons/newlines in compound commands.

TITLE_SCRIPT="$HOME/.claude/hooks/set-process-title.cjs"

# Capture the FULL command. `read -r` would stop at the first newline and
# silently drop the rest of a multi-line command (heredocs, scripts, etc.).
cmd=$(jq -r '.tool_input.command')

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's/[^a-zA-Z0-9._-]/-/g' || echo 'main')

# Single-char agent initial (first letters collide: claude/codex/copilot/cursor).
agent="${SUPERSET_AGENT_ID:-claude}"
case "$agent" in
  claude)             init="c" ;;
  codex)              init="x" ;;
  copilot)            init="p" ;;
  cursor|cursor-agent) init="u" ;;
  gemini)             init="g" ;;
  amp)                init="a" ;;
  droid)              init="d" ;;
  opencode)           init="o" ;;
  mastracode)         init="m" ;;
  *)                  init="${agent:0:1}" ;;
esac

# Extract the tool/script name for `npm run X` / `npm test|start` / `npx <tool>`,
# searching ANYWHERE in the command — real commands are usually prefixed
# (`cd … ; npx eslint …`), so anchoring to the start would miss them.
script=""
m=$(echo "$cmd" | grep -oE 'npm (run +[a-zA-Z0-9:_-]+|test|start)' | head -1)
if [ -n "$m" ]; then
  script=$(echo "$m" | sed -E 's/npm (run +)?//')
else
  # `npx` + optional flags (--no-install, -y, …) + the tool name (last token).
  m=$(echo "$cmd" | grep -oE 'npx +([-]{1,2}[a-zA-Z0-9=_-]+ +)*[a-zA-Z0-9@._/-]+' | head -1)
  if [ -n "$m" ]; then
    script=$(echo "$m" | awk '{print $NF}')
    script="${script##*/}"   # strip @scope/ and any path
    script="${script%@*}"     # strip @version
  fi
fi

# Keep only well-formed names; otherwise drop the segment.
if [ -n "$script" ] && echo "$script" | grep -qE '^[a-zA-Z0-9:_.-]+$'; then
  pname="$init-$script-$branch"
else
  pname="$init-$branch"
fi

# $cmd and the closing paren get their own lines so commands ending in a
# heredoc terminator (e.g. `EOF`) still parse — `EOF )` would never terminate.
new_cmd="( export PROCESS_NAME=$pname; export NODE_OPTIONS=\"\${NODE_OPTIONS:+\$NODE_OPTIONS }--require $TITLE_SCRIPT\"
$cmd
)"

jq -n --arg cmd "$new_cmd" '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":$cmd}}}'
