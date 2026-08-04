#!/usr/bin/env bash
# SessionStart hook: create/refresh this session's handoff doc at
# .claude-sessions/<session_id>.md (worktree root, globally gitignored —
# see ~/.gitignore_global), and sweep stale docs from prior sessions.
#
# Staleness, not liveness: a doc is swept purely by updated_at age
# (default SESSION_DOC_STALE_HOURS=6), never by checking whether the
# session that wrote it is still running. This makes crashed / kill -9
# sessions self-heal without needing any liveness check.

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

dir="$repo_root/.claude-sessions"
mkdir -p "$dir" 2>/dev/null || exit 0

this_file="$dir/$session_id.md"
stale_hours="${SESSION_DOC_STALE_HOURS:-6}"
stale_seconds=$(( stale_hours * 3600 ))
now_epoch=$(date +%s)

for f in "$dir"/*.md; do
  [ -e "$f" ] || continue
  [ "$f" = "$this_file" ] && continue
  updated_epoch=$(grep -m1 '^updated_at_epoch:' "$f" 2>/dev/null | sed 's/^updated_at_epoch: *//')
  case "$updated_epoch" in ''|*[!0-9]*) updated_epoch=0 ;; esac
  if [ "$now_epoch" -ge "$(( updated_epoch + stale_seconds ))" ]; then
    rm -f "$f" 2>/dev/null || true
  fi
done

branch=$(cd "$repo_root" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')
host=$(hostname -s 2>/dev/null || echo 'unknown')
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -f "$this_file" ]; then
  # resuming the same session_id — preserve started_at, keep the body
  started_at=$(grep -m1 '^started_at:' "$this_file" | sed 's/^started_at: *//')
  started_epoch=$(grep -m1 '^started_at_epoch:' "$this_file" | sed 's/^started_at_epoch: *//')
  body=$(awk '/^---$/{c++; next} c>=2' "$this_file")
else
  started_at=""
  started_epoch=""
  body=""
fi
[ -z "$started_at" ] && started_at="$now_iso"
[ -z "$started_epoch" ] && started_epoch="$now_epoch"

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
  echo "---"
  if [ -n "$body" ]; then
    printf '%s\n' "$body"
  else
    echo ""
    echo "_No active-work summary yet._"
  fi
} > "$this_file"

rel="${this_file#"$repo_root"/}"
jq -n --arg p "$rel" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":("Session handoff doc: " + $p + " — keep its body updated with 2-4 sentences on current work and next steps, especially before pausing or ending the session. It is removed automatically on clean exit, and swept automatically if it goes stale (crash/force-quit case).")}}'
