#!/usr/bin/env bash
# SessionEnd hook: remove this session's handoff doc on a clean exit.
# Best-effort — if this is missed (crash, kill -9), the doc self-heals via
# the staleness sweep in session-doc-start.sh next time this worktree
# starts a session.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat 2>/dev/null || true)
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null || true)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
cwd="${cwd:-$PWD}"
[ -z "$session_id" ] && exit 0

repo_root=$(cd "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)
[ -z "$repo_root" ] && exit 0

# Personal-repo gate: only run inside repos owned by an allowed GitHub
# owner (default: jnelken) — never Concentro-Inc or any other org. A repo
# with no configured origin remote is treated as personal (local-only
# scratch work, not a cloned company repo). No-op here either way if no
# doc was ever created (rm -f), so this gate is mostly a no-op safety net.
personal_owners="${PERSONAL_REPO_OWNERS:-jnelken}"
origin_url=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
if [ -n "$origin_url" ]; then
  is_personal=0
  old_ifs="$IFS"; IFS=','
  for owner in $personal_owners; do
    case "$origin_url" in
      *"github.com:$owner/"*|*"github.com/$owner/"*) is_personal=1 ;;
    esac
  done
  IFS="$old_ifs"
  [ "$is_personal" -eq 1 ] || exit 0
fi

rm -f "$repo_root/.claude-sessions/$session_id.md" 2>/dev/null || true
