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

# Cheap pre-check, no process: personal-code-dir is a python script, and on a
# non-Windows machine with no $PERSONAL_CODE_DIR and no Dropbox/code it can only
# answer "none" (see lib/personal_code_dir.py), so skip spawning it there.
case "${OSTYPE:-}" in
  msys*|cygwin*|win32*) ;;
  *)
    if [[ -z "${PERSONAL_CODE_DIR:-}" && ! -d "$HOME/Dropbox/code" && ! -d "$HOME/Library/CloudStorage/Dropbox/code" ]]; then
      exit 0
    fi ;;
esac

pcd_bin="${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/personal-code-dir"
code_dir=$("$pcd_bin" 2>/dev/null) || exit 0
[[ -n "$code_dir" ]] || exit 0

input=""
while IFS= read -r input_line || [[ -n "$input_line" ]]; do
  input="$input$input_line"$'\n'
done
cwd_re='"cwd"[[:space:]]*:[[:space:]]*"([^"\\]*)"'
# A backslash means JSON decoding is needed (including escaped quotes).
if [[ "$input" =~ $cwd_re ]]; then
  cwd=${BASH_REMATCH[1]}
else
  cwd=$(jq -r '.cwd // empty' 2>/dev/null <<< "$input")
fi
[[ -n "$cwd" ]] || cwd="$PWD"

# Resolve symlinks so a Dropbox path reached via a link still matches.
original_dir=$PWD
cd -P "$code_dir" 2>/dev/null || exit 0
code_dir=$PWD
cd "$original_dir" 2>/dev/null || exit 0
cd -P "$cwd" 2>/dev/null || exit 0
cwd=$PWD

case "$cwd/" in
  "$code_dir"/*) ;;
  *) exit 0 ;;
esac

msg="This session is in the personal code dir. Before you delegate work to a sub-agent or pick a provider/model for it, read \`$router\` -- it governs delegation here (Cursor first, then Codex, then Claude), subject to its own scope and preconditions."

# Escape every JSON control character as well as quotes and backslashes.
# Keep the result in a variable so callers do not need a command substitution.
json_escape() {
  JSON_STRING=${1//\\/\\\\}
  JSON_STRING=${JSON_STRING//\"/\\\"}
  local i char escaped oct
  for ((i=1; i<32; i++)); do
    printf -v oct '%03o' "$i"
    printf -v char '%b' "\\$oct"
    case "$i" in
      8) escaped='\b' ;; 9) escaped='\t' ;; 10) escaped='\n' ;;
      12) escaped='\f' ;; 13) escaped='\r' ;;
      *) printf -v escaped '\\u%04x' "$i" ;;
    esac
    JSON_STRING=${JSON_STRING//"$char"/"$escaped"}
  done
}

json_escape "$msg"
printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$JSON_STRING"
