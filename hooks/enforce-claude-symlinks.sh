#!/bin/bash
# Pre-commit hook: ensure all version-tracked files in ~/.claude/ are symlinks to ai-tools
#
# Rationale: ~/.claude/ should be a thin layer of local symlinks pointing to
# version-controlled content in ai-tools (or sensitive local config). This hook
# fails the commit if a regular file exists in ~/.claude/ when ai-tools/ is the
# source-of-truth. Only ai-tools/ commits are checked (not other repos).
#
# Sensitive files that should stay local (not symlinked):
# - block-push-to-main.allowlist (user-specific repo allowlist)
# - *.json (settings, backups — often contain sensitive data)
# - Anything under .ccusage-state/, .last-cleanup, daemon.log, history.jsonl, etc.

set -e

# Only run this hook in the ai-tools repo itself
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ ! "$repo_root" =~ ai-tools ]]; then
  exit 0
fi

# Allowlist of local files that should NOT be symlinks (contain sensitive data or runtime state)
allowlist=(
  "block-push-to-main.allowlist"     # user-specific repo allowlist
  "settings.json"                     # may contain personal settings
  "settings.local.json"               # user-local overrides
  ".DS_Store"                         # macOS metadata
)

# These are directories/patterns that are runtime state — skip them entirely
skip_patterns=(
  ".ccusage-state"
  ".last-cleanup"
  ".claude.json.backup*"
  "backups"
  "cache"
  "chrome"
  "daemon"
  "history.jsonl"
  "jobs"
  "paste-cache"
  "image-cache"
  "state"
  ".claude-projects.db*"
)

# Find all non-symlink regular files under ~/.claude/hooks, ~/.claude/commands, ~/.claude/agents
offenders=()

for dir in hooks commands agents; do
  claude_dir="$HOME/.claude/$dir"
  [[ ! -d "$claude_dir" ]] && continue

  while IFS= read -r file; do
    # Skip hidden files, skip if doesn't exist
    [[ ! -e "$file" ]] && continue

    # Is it a symlink? If yes, it's fine.
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

    # Check if it matches a skip pattern
    if [[ $skip -eq 0 ]]; then
      for pattern in "${skip_patterns[@]}"; do
        if [[ "$file" =~ $pattern ]]; then
          skip=1
          break
        fi
      done
    fi

    # If not skipped, it's an offender
    [[ $skip -eq 0 ]] && offenders+=("$file")
  done < <(find "$claude_dir" -type f 2>/dev/null)
done

if [[ ${#offenders[@]} -gt 0 ]]; then
  cat >&2 <<EOF
❌ Pre-commit hook: version-tracked files must be symlinks to ai-tools

The following regular files exist in ~/.claude/ but should be symlinks
to /code/ai-tools/ (or removed if they shouldn't be version-tracked):

$(printf '  - %s\n' "${offenders[@]}")

Fix:
1. Move the file to ai-tools/hooks|commands|agents/ (or delete if temp)
2. Create a symlink: ln -s /path/to/ai-tools/... ~/.claude/...
3. Then run: git add <changed files in this commit>
4. Re-attempt the commit

If the file contains sensitive data (secrets, local config, allowlists),
add its basename to the \`allowlist\` array in this hook instead, and
keep it as a local regular file.
EOF
  exit 1
fi

exit 0
