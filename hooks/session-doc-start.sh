#!/usr/bin/env bash
# SessionStart hook: create/refresh this session's handoff doc at
# .claude-sessions/<session_id>.md (worktree root, globally gitignored —
# see ~/.gitignore_global), and sweep stale docs from prior sessions.
#
# Staleness, not liveness: a doc is swept purely by updated_at age
# (default SESSION_DOC_STALE_HOURS=6), never by checking whether the
# session that wrote it is still running. This makes crashed / kill -9
# sessions self-heal without needing any liveness check.
#
# Also mirrors the same doc to a durable, worktree-independent location
# keyed by repo identity (~/.claude/state/repo-sessions/<repo_key>/), so a
# worktree torn down mid-session (sswt teardown, `git worktree remove`)
# doesn't take the only record of that in-progress work with it. See
# session-doc-end.sh for how that mirror's lifecycle differs from the
# local doc's.

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
msg="Session handoff doc: $rel — keep its body updated with 2-4 sentences on current work and next steps, especially before pausing or ending the session. It is removed automatically on clean exit, and swept automatically if it goes stale (crash/force-quit case)."

# --- Durable, worktree-independent mirror -----------------------------
# repo_key is derived from the repo's git-common-dir, which resolves to
# the same path from any linked worktree of this repo (and the main
# checkout) — so the mirror lands in the same place no matter which
# worktree wrote it.
main_repo_root=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
main_repo_root="${main_repo_root%/.git}"

if [ -n "$main_repo_root" ]; then
  repo_key="$(basename "$main_repo_root")-$(printf '%s' "$main_repo_root" | shasum -a 256 | cut -c1-8)"
  global_dir="$HOME/.claude/state/repo-sessions/$repo_key"
  mkdir -p "$global_dir" 2>/dev/null

  if [ -d "$global_dir" ]; then
    global_this_file="$global_dir/$session_id.md"

    # Sweep stale global docs — but only ones already marked ended_cleanly:
    # true, and only after a much longer threshold than the local sweep.
    # An unresolved (crashed/killed) entry is never swept by age alone —
    # that's the whole point of the durable mirror.
    global_stale_days="${SESSION_DOC_GLOBAL_STALE_DAYS:-30}"
    global_stale_seconds=$(( global_stale_days * 86400 ))
    for f in "$global_dir"/*.md; do
      [ -e "$f" ] || continue
      [ "$f" = "$global_this_file" ] && continue
      ended_cleanly=$(grep -m1 '^ended_cleanly:' "$f" 2>/dev/null | sed 's/^ended_cleanly: *//')
      [ "$ended_cleanly" = "true" ] || continue
      updated_epoch=$(grep -m1 '^updated_at_epoch:' "$f" 2>/dev/null | sed 's/^updated_at_epoch: *//')
      case "$updated_epoch" in ''|*[!0-9]*) updated_epoch=0 ;; esac
      if [ "$now_epoch" -ge "$(( updated_epoch + global_stale_seconds ))" ]; then
        rm -f "$f" 2>/dev/null || true
      fi
    done

    # Discovery nudge: other sessions in this repo whose global doc isn't
    # marked ended_cleanly, and which look orphaned — either their
    # worktree is gone from disk, or they've gone quiet for a while (same
    # 15-minute liveness threshold wrapup-repos uses), so a live sibling
    # session in another worktree isn't falsely flagged.
    nudge_count=0
    for f in "$global_dir"/*.md; do
      [ -e "$f" ] || continue
      [ "$f" = "$global_this_file" ] && continue
      ended_cleanly=$(grep -m1 '^ended_cleanly:' "$f" 2>/dev/null | sed 's/^ended_cleanly: *//')
      [ "$ended_cleanly" = "true" ] && continue
      wt=$(grep -m1 '^worktree_path:' "$f" 2>/dev/null | sed 's/^worktree_path: *//')
      upd=$(grep -m1 '^updated_at_epoch:' "$f" 2>/dev/null | sed 's/^updated_at_epoch: *//')
      case "$upd" in ''|*[!0-9]*) upd=0 ;; esac
      orphaned=0
      if [ -z "$wt" ] || [ ! -d "$wt" ]; then
        orphaned=1
      elif [ "$now_epoch" -ge "$(( upd + 900 ))" ]; then
        orphaned=1
      fi
      [ "$orphaned" -eq 1 ] && nudge_count=$(( nudge_count + 1 ))
    done
    if [ "$nudge_count" -gt 0 ]; then
      msg="$msg $nudge_count unresolved session doc(s) for this repo may reflect interrupted work — see ~/.claude/state/repo-sessions/$repo_key/."
    fi

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
      echo "---"
      if [ -n "$body" ]; then
        printf '%s\n' "$body"
      else
        echo ""
        echo "_No active-work summary yet._"
      fi
    } > "$global_this_file"
  fi
fi

jq -n --arg m "$msg" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$m}}'
