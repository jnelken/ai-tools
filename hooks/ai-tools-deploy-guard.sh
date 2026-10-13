#!/usr/bin/env bash
# Claude Code PreToolUse hook: block file edits into the ai-tools deploy clone
# ($AI_TOOLS_HOME, default ~/.ai-tools), including writes through ~/.claude
# symlinks. install.sh overwrites that clone; edits belong in the dev checkout.
# Gitignored runtime files (such as memory.md and lib/__pycache__/) stay writable.
#
# Wiring: install.sh adds matcher Edit|Write|MultiEdit|NotebookEdit with command
# bash "$HOME/.claude/hooks/ai-tools-deploy-guard.sh" to settings.json.
# Denials exit 2 with a reason on stderr. Missing jq, malformed JSON, or an
# unresolvable path fail open (exit 0). This only sees Claude's file tools;
# Bash commands and Codex/other subprocess writes are outside its scope.
#
# Bash 3.2 / macOS / Linux compatible: no GNU-only realpath/readlink -f.
# Tests: bash hooks/tests/ai-tools-deploy-guard.test.sh

command -v jq >/dev/null 2>&1 || exit 0
target_path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty | select(type == "string")' 2>/dev/null) || exit 0
[ -n "$target_path" ] || exit 0

AI_TOOLS_HOME="${AI_TOOLS_HOME:-$HOME/.ai-tools}"
while [ "${AI_TOOLS_HOME%/}" != "$AI_TOOLS_HOME" ] && [ "$AI_TOOLS_HOME" != "/" ]; do
  AI_TOOLS_HOME="${AI_TOOLS_HOME%/}"
done

# Like ai-tools-sync.sh's resolve_path, use cd -P for existing directories.
# Walk up to the nearest existing directory for new files, then re-append
# the remainder. Follow final file symlinks too (agents/commands/hooks),
# resolving relative link targets from the link's parent directory.
resolve_path() {
  local p="$1" depth="${2:-0}" dir base parent target
  [ "$depth" -lt 40 ] || return 1
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  if [ -L "$p" ]; then
    target="$(readlink "$p")" || return 1
    case "$target" in /*) ;; *) target="$(dirname "$p")/$target" ;; esac
    resolve_path "$target" "$((depth + 1))"
    return
  fi
  if [ -d "$p" ]; then
    (cd -P "$p" 2>/dev/null && pwd -P)
    return
  fi
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  [ "$dir" != "$p" ] || return 1
  parent="$(resolve_path "$dir" "$depth")" || return 1
  case "$base" in
    .) printf '%s\n' "$parent" ;;
    ..) dirname "$parent" ;;
    *)
      p="${parent%/}/$base"
      if [ -L "$p" ]; then
        resolve_path "$p" "$((depth + 1))"
      else
        printf '%s\n' "$p"
      fi
      ;;
  esac
}

clone_root="$(resolve_path "$AI_TOOLS_HOME")" || exit 0
resolved_target="$(resolve_path "$target_path")" || exit 0
case "$resolved_target" in
  "$clone_root") relpath="." ;;
  "${clone_root%/}/"*) relpath="${resolved_target#"${clone_root%/}/"}" ;;
  *) exit 0 ;;
esac

git -C "$clone_root" check-ignore -q -- "$relpath" 2>/dev/null && exit 0

# Mirror install.sh's dev-checkout discovery without sourcing the installer:
# AI_TOOLS_DEV overrides the script's checkout and the common clone locations.
resolve_ai_tools_dev() {
  local script_path candidate root url
  script_path="$(resolve_path "${BASH_SOURCE[0]}")" || return 0
  for candidate in "$(dirname "$(dirname "$script_path")")" "$HOME/code/ai-tools" "$HOME/Dropbox/code/ai-tools"; do
    [ -e "$candidate/.git" ] || continue
    root="$(resolve_path "$candidate")" || continue
    [ "$root" != "$clone_root" ] || continue
    url="$(git -C "$root" remote get-url origin 2>/dev/null)" || continue
    case "$url" in */ai-tools|*/ai-tools.git) printf '%s\n' "$root"; return ;; esac
  done
}
dev_checkout="${AI_TOOLS_DEV:-$(resolve_ai_tools_dev)}"
dev_checkout="$(resolve_path "${dev_checkout:-$HOME/code/ai-tools}")" || dev_checkout="$HOME/code/ai-tools"

printf 'ai-tools-deploy-guard: %s is in the ~/.ai-tools deploy clone (install.sh overwrites it). Edit %s/%s instead, commit on main, and the post-commit hook redeploys.\n' \
  "$target_path" "$dev_checkout" "$relpath" >&2
exit 2
