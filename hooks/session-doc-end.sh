#!/usr/bin/env bash
# SessionEnd hook: remove this session's handoff doc on a clean exit.
# Best-effort — if this is missed (crash, kill -9), the doc self-heals via
# the staleness sweep in session-doc-start.sh next time this worktree
# starts a session.
#
# The durable global mirror (see session-doc-start.sh) is handled
# differently: it is never deleted here, only marked ended_cleanly: true.
# `superset workspaces delete` kills every terminal in a workspace, and
# whether SessionEnd fires cleanly on that kill the same way it does on a
# normal /exit is unverified — an unconditional delete would risk erasing
# the exact interrupted-work record this mirror exists to preserve, at
# the moment it matters most. Marking clean-vs-not makes retention depend
# on how the session actually ended, not on an assumption about hook
# timing.

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

# --- Durable, worktree-independent mirror -----------------------------
main_repo_root=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
main_repo_root="${main_repo_root%/.git}"
[ -z "$main_repo_root" ] && exit 0

repo_key="$(basename "$main_repo_root")-$(printf '%s' "$main_repo_root" | shasum -a 256 | cut -c1-8)"
global_file="$HOME/.claude/state/repo-sessions/$repo_key/$session_id.md"
[ -f "$global_file" ] || exit 0

get_field() { grep -m1 "^$1:" "$global_file" 2>/dev/null | sed "s/^$1: *//"; }
started_at=$(get_field started_at)
started_epoch=$(get_field started_at_epoch)
branch=$(get_field branch)
host=$(get_field host)
body=$(awk '/^---$/{c++; next} c>=2' "$global_file")

now_epoch=$(date +%s)
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

global_tmp="$global_file.tmp.$$"
{
  echo "---"
  echo "session_id: $session_id"
  echo "started_at: $started_at"
  echo "started_at_epoch: $started_epoch"
  echo "updated_at: $now_iso"
  echo "updated_at_epoch: $now_epoch"
  echo "branch: $branch"
  echo "host: $host"
  echo "cwd: $repo_root"
  echo "main_repo_root: $main_repo_root"
  echo "worktree_path: $repo_root"
  echo "ended_cleanly: true"
  echo "---"
  printf '%s\n' "$body"
} > "$global_tmp" && mv "$global_tmp" "$global_file"
