#!/bin/bash
# SessionStart hook: nudges about unsymlinked files in ~/.claude/
#
# Checks ~/.claude/{hooks,commands,agents}/ for regular files that should
# be symlinks to ai-tools. Nudges the user to move them if found.
# Non-blocking; runs once per SESSION_START to avoid nagging every turn.

set -e

claude_dir="$HOME/.claude"

# Allowlist of files that are OK to be local (not symlinked)
allowlist=(
  "block-push-to-main.allowlist"
  ".DS_Store"
)

# Check if jq is available (used by other hooks)
if ! command -v jq &>/dev/null; then
  exit 0
fi

offenders=()

for dir in hooks commands agents; do
  target_dir="$claude_dir/$dir"
  [[ ! -d "$target_dir" ]] && continue

  while IFS= read -r file; do
    # Skip if doesn't exist or is a symlink
    [[ ! -e "$file" ]] && continue
    [[ -L "$file" ]] && continue

    # It's a regular file. Check if it's on the allowlist.
    basename=$(basename "$file")
    skip=0
    for allowed in "${allowlist[@]}"; do
      if [[ "$basename" == "$allowed" ]]; then
        skip=1
        break
      fi
    done

    [[ $skip -eq 0 ]] && offenders+=("$file")
  done < <(find "$target_dir" -maxdepth 1 -type f 2>/dev/null)
done

if [[ ${#offenders[@]} -gt 0 ]]; then
  # Cooldown: only nudge once per day per file
  state_dir="$HOME/.claude/state"
  mkdir -p "$state_dir"
  state_file="$state_dir/claude-symlink-nudge.txt"

  # Hash the offenders list to detect changes
  current_hash=$(printf '%s\n' "${offenders[@]}" | shasum | awk '{print $1}')
  last_hash=""
  last_time=0

  if [[ -f "$state_file" ]]; then
    read -r last_hash last_time < "$state_file" 2>/dev/null || true
  fi

  # If same offenders as last time and less than 24h have passed, skip nudging
  if [[ "$current_hash" == "$last_hash" && $(($(date +%s) - last_time)) -lt 86400 ]]; then
    exit 0
  fi

  # Record this nudge
  echo "$current_hash $(date +%s)" > "$state_file"

  # Build the message
  msg="⚠️ Unsymlinked files in ~/.claude/ detected:

$(printf '  - %s\n' "${offenders[@]}")

These should be symlinks to /code/ai-tools/ so they stay in sync across devices.

Fix:
1. \`cd /code/ai-tools\` and create the file there (hooks/, commands/, or agents/)
2. \`rm ~/.claude/<subdir>/<file>\`
3. \`ln -s /code/ai-tools/<subdir>/<file> ~/.claude/<subdir>/<file>\`
4. \`cd /code/ai-tools && git add <files> && git commit\`

Or, if the file is sensitive (secrets, local config), add its basename to
the \`allowlist\` array in ~/.claude/hooks/claude-symlink-hygiene.sh and
commit that change instead."

  jq -n --arg msg "$msg" '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: $msg
    }
  }'
fi

exit 0
