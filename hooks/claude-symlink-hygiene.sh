#!/bin/bash
# SessionStart hook: nudges about unsymlinked files in ~/.claude/
#
# Checks ~/.claude/{hooks,commands,agents}/ for regular files that should
# be symlinks to ai-tools. Nudges the user to move them if found.
# Non-blocking; runs once per SESSION_START to avoid nagging every turn.
# No processes at all when nothing is out of place (bash globs, no find/basename).

set -e

claude_dir="$HOME/.claude"

# Allowlist of files that are OK to be local (not symlinked)
allowlist=(
  "block-push-to-main.allowlist"     # user-specific repo skip-list (current name)
  "block-push-to-main.denylist"  # legacy name, kept for older checkouts
  ".DS_Store"
)

# Check if jq is available (used by other hooks)
if ! command -v jq &>/dev/null; then
  exit 0
fi

offenders=()

# Plain globs over the three fixed dirs instead of `find`: a healthy session
# (no offenders) spawns nothing. Equivalent to `find -maxdepth 1 -type f` minus
# symlinks: regular files only, dotfiles included.
for dir in hooks commands agents; do
  target_dir="$claude_dir/$dir"
  [[ ! -d "$target_dir" ]] && continue

  for file in "$target_dir"/* "$target_dir"/.[!.]* "$target_dir"/..?*; do
    # Skip if doesn't exist (unmatched glob), is a symlink, or isn't a file
    [[ ! -e "$file" ]] && continue
    [[ -L "$file" ]] && continue
    [[ -f "$file" ]] || continue

    # It's a regular file. Check if it's on the allowlist.
    basename="${file##*/}"
    skip=0
    for allowed in "${allowlist[@]}"; do
      if [[ "$basename" == "$allowed" ]]; then
        skip=1
        break
      fi
    done

    [[ $skip -eq 0 ]] && offenders+=("$file")
  done
done

if [[ ${#offenders[@]} -gt 0 ]]; then
  # Cooldown: only nudge once per day per file set
  state_dir="$HOME/.claude/state"
  [[ -d "$state_dir" ]] || mkdir -p "$state_dir"
  state_file="$state_dir/claude-symlink-nudge.txt"

  # Key the offenders list to detect changes. The key is the list itself (paths
  # joined with "|"), not a shasum of it: the state line is "<key> <epoch>", so
  # the epoch is whatever follows the last space.
  current_hash=""
  for file in "${offenders[@]}"; do current_hash="${current_hash:+$current_hash|}$file"; done
  last_hash=""
  last_time=0

  if [[ -f "$state_file" ]]; then
    state_line=""
    IFS= read -r state_line < "$state_file" 2>/dev/null || true
    if [[ "$state_line" == *" "* ]]; then
      last_hash="${state_line% *}"
      last_time="${state_line##* }"
    fi
    [[ "$last_time" =~ ^[0-9]+$ ]] || last_time=0
  fi

  now=$(date +%s)

  # If same offenders as last time and less than 24h have passed, skip nudging
  if [[ "$current_hash" == "$last_hash" && $((now - last_time)) -lt 86400 ]]; then
    exit 0
  fi

  # Record this nudge
  echo "$current_hash $now" > "$state_file"

  # Build the message
  msg="⚠️ Unsymlinked files in ~/.claude/ detected:

$(printf '  - %s\n' "${offenders[@]}")

These should be symlinks into the deploy clone (~/.ai-tools/, kept in sync
with origin/main) so they stay in sync across devices.

Fix:
1. \`cd ~/code/ai-tools\` (the DEV repo) and add/edit the file there
   (hooks/, commands/, or agents/) — never edit ~/.ai-tools directly, it's
   managed by install.sh
2. Commit and push from ~/code/ai-tools — that IS the deploy step
3. Run \`~/.ai-tools/install.sh\` (or wait for the next session's
   ai-tools-sync) to symlink ~/.claude/<subdir>/<file> into the deploy clone

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
