#!/usr/bin/env bash
# Claude Code PreToolUse hook on Bash: blocks any `git push` whose target
# resolves to the `main` branch. Pushes from the user's own terminal are
# unaffected — this only fires when Claude initiates the bash call.
set -u

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# 0. Repo root is explicitly allowlisted — skip every check below. One
# absolute repo path per line in the allowlist file; '#' comments and blank
# lines ignored. Keeps the block globally on for every other repo.
allowlist="$HOME/.claude/hooks/block-push-to-main.allowlist"
if [[ -f "$allowlist" ]]; then
  repo_root=$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --show-toplevel 2>/dev/null || true)
  if [[ -n "$repo_root" ]]; then
    while IFS= read -r line; do
      line="${line%%#*}"
      line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
      [[ -z "$line" ]] && continue
      [[ "$line" == "$repo_root" ]] && exit 0
    done < "$allowlist"
  fi
fi

# 1. Not a git push? Allow.
[[ "$cmd" =~ git[[:space:]]+push ]] || exit 0

# 2. --all / --mirror push every branch (incl. main).
if [[ "$cmd" =~ git[[:space:]]+push[[:space:]]+(.*[[:space:]])?(--all|--mirror)([[:space:]]|$|\;|\|\&) ]]; then
  deny "git push --all/--mirror would push main. Run this from your terminal yourself."
fi

# 3. `main` appears as a destination ref (standalone, or after a colon).
if [[ "$cmd" =~ (^|[^A-Za-z0-9_/.-]|:)main($|[[:space:]\;\|\&]) ]]; then
  deny "Detected a push targeting the main branch. Pushes to main are reserved for the user to run from the terminal."
fi

# 4. `refs/heads/main` as a destination.
if [[ "$cmd" =~ refs/heads/main($|[[:space:]\;\|\&]) ]]; then
  deny "Detected a push targeting refs/heads/main. Pushes to main are reserved for the user to run from the terminal."
fi

# 5. Plain `git push` (no refspec) — check if current branch is main.
# Take just the `git push ...` segment, drop everything before `push`, then
# scan remaining whitespace-separated tokens. If every token is a flag
# (-x or --foo, possibly --foo=value), there's no refspec → the push goes
# to the current branch's upstream.
push_segment=$(printf '%s' "$cmd" | grep -oE 'git[[:space:]]+push[^;&|]*' | head -1)
remaining="${push_segment#*push}"
has_refspec=0
for tok in $remaining; do
  case "$tok" in
    -*) ;;       # flag — keep scanning
    *) has_refspec=1; break;;
  esac
done
if (( has_refspec == 0 )); then
  branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" symbolic-ref --short HEAD 2>/dev/null || true)
  if [[ "$branch" == "main" ]]; then
    deny "Current branch is main and \`git push\` has no refspec. Pushes to main are reserved for the user to run from the terminal."
  fi
fi

exit 0
