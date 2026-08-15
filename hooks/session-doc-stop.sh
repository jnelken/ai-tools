#!/usr/bin/env bash
# Stop hook (fires after every assistant turn): mechanically refresh this
# session's handoff doc — updated_at + a one-line git diff --stat summary —
# so the staleness sweep in session-doc-start.sh has an accurate signal.
# No LLM call here on purpose: this fires every turn, so it has to be cheap.
#
# Also refreshes the durable global mirror (see session-doc-start.sh) at
# the same cadence, so its updated_at stays an accurate liveness signal too.

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
# scratch work, not a cloned company repo).
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

file="$repo_root/.claude-sessions/$session_id.md"
[ -f "$file" ] || exit 0

get_field() { grep -m1 "^$1:" "$file" 2>/dev/null | sed "s/^$1: *//"; }
started_at=$(get_field started_at)
started_epoch=$(get_field started_at_epoch)
branch=$(get_field branch)
host=$(get_field host)
body=$(awk '/^---$/{c++; next} c>=2' "$file")

[ -z "$branch" ] && branch=$(cd "$repo_root" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')
[ -z "$host" ] && host=$(hostname -s 2>/dev/null || echo 'unknown')
[ -z "$started_at" ] && started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
[ -z "$started_epoch" ] && started_epoch=$(date +%s)

now_epoch=$(date +%s)
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
diff_stat=$(cd "$repo_root" && git diff --stat 2>/dev/null | tail -1 | sed -e 's/^ *//' -e 's/"/\\"/g')

tmp="$file.tmp.$$"
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
  [ -n "$diff_stat" ] && echo "last_diff_stat: \"$diff_stat\""
  echo "---"
  printf '%s\n' "$body"
} > "$tmp" && mv "$tmp" "$file"

# --- Durable, worktree-independent mirror -----------------------------
main_repo_root=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
main_repo_root="${main_repo_root%/.git}"
[ -z "$main_repo_root" ] && exit 0

repo_key="$(basename "$main_repo_root")-$(printf '%s' "$main_repo_root" | shasum -a 256 | cut -c1-8)"
global_file="$HOME/.claude/state/repo-sessions/$repo_key/$session_id.md"
[ -f "$global_file" ] || exit 0

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
  [ -n "$diff_stat" ] && echo "last_diff_stat: \"$diff_stat\""
  echo "---"
  printf '%s\n' "$body"
} > "$global_tmp" && mv "$global_tmp" "$global_file"
