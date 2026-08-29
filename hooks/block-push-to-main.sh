#!/usr/bin/env bash
# Claude Code PreToolUse hook on Bash: blocks any `git push` whose target
# resolves to the `main` branch. Pushes from the user's own terminal are
# unaffected — this only fires when Claude initiates the bash call.
# Opt-in per repo via a denylist (see step 0) — off everywhere else.
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

# 0. Only enforce in repos listed in the denylist. One repo name per line;
# matched against the `origin` remote's repo name (works across worktrees
# and Superset workspaces, which live at unrelated paths), falling back to
# the repo root directory name when there's no remote. '#' comments and
# blank lines ignored. No denylist file, no match, or no repo at all -> allow.
denylist="$HOME/.claude/hooks/block-push-to-main.denylist"
[[ -f "$denylist" ]] || exit 0

repo_root=$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$repo_root" ]] || exit 0

remote_url=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
if [[ -n "$remote_url" ]]; then
  repo_name="${remote_url%.git}"
  repo_name="${repo_name##*/}"
else
  repo_name="${repo_root##*/}"
fi

matched=0
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
  [[ -z "$line" ]] && continue
  if [[ "$line" == "$repo_name" ]]; then
    matched=1
    break
  fi
done < "$denylist"
[[ "$matched" -eq 1 ]] || exit 0

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
