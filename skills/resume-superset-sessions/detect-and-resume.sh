#!/bin/bash
# resume-superset-sessions: after a machine crash/power loss, Superset
# restores terminal tabs (same terminal_id, a fresh idle `zsh -l`, and a
# frozen visual snapshot that is renderer-side xterm state, NOT recoverable
# data) but the `claude` process that used to live in each tab is gone.
#
# This script deterministically figures out which restored Superset tabs
# held a now-dead claude session, and relaunches `claude --resume <id>` in
# the SAME tab. It also sweeps for crashed sessions that were never in a
# Superset tab at all (plain terminal windows) and prints manual resume
# commands for those - it never touches those automatically.
#
# ---------------------------------------------------------------------------
# Ground truth this script relies on (see SKILL.md for the fuller writeup):
#
#   Superset host DB: ~/.superset/host/<orgId>/host.db (sqlite, WAL).
#   Always opened READ-ONLY (file:...?mode=ro). Never written.
#
#     terminal_agent_bindings(terminal_id PK, workspace_id, agent_id,
#       agent_session_id, definition_id, started_at, last_event_at,
#       last_event_type)
#     - agent_session_id IS the Claude Code session UUID.
#     - Keyed on terminal_id, so launching a NEW agent in a restored tab
#       OVERWRITES agent_session_id. That is why this script snapshots every
#       binding row for the target workspace(s) to
#       ~/.claude/state/session-recovery/bindings-<epoch>.json BEFORE it
#       sends a single resume command in --apply mode.
#
#     terminal_sessions(id PK, origin_workspace_id, status, created_at,
#       last_attached_at, ended_at, dispose_requested_at)
#     - status='active' means "never explicitly disposed", NOT "alive".
#
#   Live terminal list (ground truth for which tabs exist right now):
#     `superset terminals list --workspace <id> --json` ->
#       {sessions: [{terminalId, workspaceId, createdAt (epoch ms), exited,
#                    attached, title}]}
#     `superset workspaces list --json` ->
#       [{id, name, branch, worktreePath, projectName, hostId}]
#     (this machine's daemon only, at 127.0.0.1:48214)
#
#   A restored tab is a RESUME candidate iff ALL of:
#     (a) it is in the live terminals list (closed tabs are excluded there)
#     (b) a terminal_agent_bindings row exists with non-null agent_session_id
#     (c) binding.started_at < tab's createdAt  (the pty was re-created AFTER
#         the agent was bound -> the bound agent cannot be running in it; a
#         healthy tab has the opposite order)
#     (d) no LIVE claude process owns that session (~/.claude/sessions/*.json
#         pid check + kill -0, belt-and-suspenders pgrep)
#     (e) a transcript exists: ~/.claude/projects/*/<session-id>.jsonl
#     (f) the tab's shell is currently idle (never type into a running
#         program)
#
#   Relaunch primitive (same tab, never --fork-session):
#     superset terminals send --workspace <ws> --terminal <id> \
#       --text 'cd <worktreePath> && claude --resume <session-id>'
#
# This script never runs `terminals close`, never deletes anything, and
# never writes to host.db.
# ---------------------------------------------------------------------------
#
# Usage:
#   detect-and-resume.sh                 dry run, current workspace only
#   detect-and-resume.sh --all           dry run, every workspace on this host
#   detect-and-resume.sh --workspace <q> dry run, one workspace by id, name, or
#                                        branch (partial, case-insensitive;
#                                        ambiguity errors out with the matches)
#   detect-and-resume.sh --apply         snapshot bindings, then actually send
#   detect-and-resume.sh --all --apply   both
#   detect-and-resume.sh --window 48     global-sweep lookback in hours (def 24)
#   detect-and-resume.sh --no-sweep      skip the non-Superset global sweep
#
# Must run on macOS default bash 3.2: no associative arrays, no mapfile, no
# ${var,,}. All JSON handled via jq. set -e is guarded everywhere a grep/pgrep
# "no match" is an expected, non-fatal outcome (`|| true`, or inside `if`).

set -uo pipefail
shopt -s nullglob

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

WORKSPACE_MODE="current"
WS_QUERY=""
APPLY=0
WINDOW_HOURS=24
SWEEP=1

while [ $# -gt 0 ]; do
  case "$1" in
    --all) WORKSPACE_MODE="all" ;;
    --workspace)
      shift
      WS_QUERY="${1:-}"
      if [ -z "$WS_QUERY" ]; then
        echo "--workspace expects an id, name, or branch (may be partial)" >&2
        exit 1
      fi
      WORKSPACE_MODE="one"
      ;;
    --apply) APPLY=1 ;;
    --window)
      shift
      WINDOW_HOURS="${1:-24}"
      ;;
    --no-sweep) SWEEP=0 ;;
    -h|--help)
      sed -n '1,70p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

case "$WINDOW_HOURS" in
  ''|*[!0-9]*)
    echo "--window expects an integer number of hours, got: $WINDOW_HOURS" >&2
    exit 1
    ;;
esac

STATE_DIR="$HOME/.claude/state/session-recovery"
CANDIDATES_FILE=$(mktemp -t resume-candidates)
OVERALL_EXIT=0

cleanup() { rm -f "$CANDIDATES_FILE"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

for tool in jq sqlite3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Required tool '$tool' not found on PATH." >&2
    exit 1
  fi
done

HAVE_SUPERSET=1
if ! command -v superset >/dev/null 2>&1; then
  HAVE_SUPERSET=0
  echo "Notice: 'superset' CLI not found on PATH - Superset tab detection will be skipped."
  echo "        The global sweep for non-Superset sessions will still run."
fi

DB=""
ORG_DIR=""
db_candidates=("$HOME"/.superset/host/*/host.db)
if [ ${#db_candidates[@]} -eq 0 ]; then
  echo "Notice: no Superset host.db found under ~/.superset/host/ - Superset tab detection will be skipped."
else
  DB="${db_candidates[0]}"
  ORG_DIR=$(dirname "$DB")
  if [ ${#db_candidates[@]} -gt 1 ]; then
    echo "Notice: multiple host.db files found under ~/.superset/host/, using: $DB"
  fi
fi

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

# Fresh, read-only query. Never writes to host.db.
sqlite_json() {
  sqlite3 -json "file:${DB}?mode=ro" "$1" 2>/dev/null || true
}

get_binding_json() {
  local tid="$1" esc rows
  [ -z "$DB" ] && { echo "null"; return; }
  esc=$(sql_escape "$tid")
  rows=$(sqlite_json "SELECT * FROM terminal_agent_bindings WHERE terminal_id='${esc}' LIMIT 1;")
  if [ -z "$rows" ]; then
    echo "null"
  else
    printf '%s' "$rows" | jq '.[0] // null' 2>/dev/null || echo "null"
  fi
}

# Is there a currently-running claude process actually attached to this
# session id? Checked two ways (belt-and-suspenders per spec):
#   1. ~/.claude/sessions/<pid>.json only exists for LIVE sessions (dead ones
#      are reaped) - match sessionId, then confirm the pid is really alive.
#   2. pgrep -f "claude.*<session-id>" as a fallback signal.
check_live_session() {
  local session_id="$1" f sid pid
  for f in "$HOME"/.claude/sessions/*.json; do
    sid=$(jq -r '.sessionId // empty' "$f" 2>/dev/null || true)
    if [ "$sid" = "$session_id" ]; then
      pid=$(jq -r '.pid // empty' "$f" 2>/dev/null || true)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "live"
        return
      fi
    fi
  done
  if pgrep -f "claude.*${session_id}" >/dev/null 2>&1; then
    echo "live"
    return
  fi
  echo "dead"
}

transcript_exists() {
  local session_id="$1"
  local matches=("$HOME"/.claude/projects/*/"${session_id}.jsonl")
  if [ ${#matches[@]} -gt 0 ]; then
    echo "yes"
  else
    echo "no"
  fi
}

get_pty_daemon_pid() {
  local pid=""
  if [ -n "$ORG_DIR" ] && [ -f "$ORG_DIR/pty-daemon-manifest.json" ]; then
    pid=$(jq -r '.pid // empty' "$ORG_DIR/pty-daemon-manifest.json" 2>/dev/null || true)
  fi
  if [ -z "$pid" ]; then
    pid=$(pgrep -f 'pty-daemon' 2>/dev/null | head -1 || true)
  fi
  echo "$pid"
}

ancestry_contains() {
  local pid="$1" target="$2" depth=0
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ "$depth" -lt 10 ]; do
    if [ "$pid" = "$target" ]; then
      return 0
    fi
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    depth=$((depth + 1))
  done
  return 1
}

# Find the `/bin/zsh -l` under the pty daemon whose start time matches the
# tab's createdAt to the second. Best-effort: on any parse failure we just
# move on to the buffer-based fallback in idle_check.
find_tab_shell_pid() {
  local created_at_ms="$1" pty_pid="$2" created_at_sec zpid lstart zepoch diff
  created_at_sec=$(( created_at_ms / 1000 ))
  for zpid in $(pgrep -x zsh 2>/dev/null || true); do
    if ancestry_contains "$zpid" "$pty_pid"; then
      lstart=$(ps -o lstart= -p "$zpid" 2>/dev/null || true)
      [ -z "$lstart" ] && continue
      zepoch=$(date -j -f "%a %b %e %T %Y" "$lstart" +%s 2>/dev/null || true)
      [ -z "$zepoch" ] && continue
      diff=$(( zepoch - created_at_sec ))
      [ "$diff" -lt 0 ] && diff=$(( -diff ))
      if [ "$diff" -le 1 ]; then
        echo "$zpid"
        return 0
      fi
    fi
  done
  return 1
}

# idle / busy / inconclusive. Primary check: does the matched shell have any
# children? Fallback (used whenever the pty daemon or the shell can't be
# matched): does the last non-empty line of the live buffer look like a bare
# shell prompt?
idle_check() {
  local ws_id="$1" term_id="$2" created_at_ms="$3"
  local pty_pid zpid children buffer last_line

  pty_pid=$(get_pty_daemon_pid)
  if [ -n "$pty_pid" ]; then
    zpid=$(find_tab_shell_pid "$created_at_ms" "$pty_pid" || true)
    if [ -n "$zpid" ]; then
      children=$(pgrep -P "$zpid" 2>/dev/null || true)
      if [ -z "$children" ]; then
        echo "idle"
      else
        echo "busy"
      fi
      return
    fi
  fi

  [ "$HAVE_SUPERSET" -eq 0 ] && { echo "inconclusive"; return; }
  buffer=$(superset terminals read --workspace "$ws_id" --terminal "$term_id" 2>/dev/null || true)
  last_line=$(printf '%s\n' "$buffer" | grep -v '^[[:space:]]*$' | tail -1 || true)
  if [ -z "$last_line" ]; then
    echo "inconclusive"
    return
  fi
  if printf '%s' "$last_line" | grep -qE '[❯$][[:space:]]*$'; then
    echo "idle"
  else
    echo "inconclusive"
  fi
}

# Secondary/fallback signal only: for a live tab with NO binding row (claude
# launched manually, not via a Superset agent), the user's statusline prints
# 🪪<session-uuid> on its first line. After a full reboot the daemon's buffer
# is empty (the frozen screen is renderer-side xterm state, not persisted),
# so this mostly helps after app-restarts / future crashes where the daemon
# survives - not after a full machine crash.
statusline_fallback() {
  local ws_id="$1" term_id="$2" buffer marker
  [ "$HAVE_SUPERSET" -eq 0 ] && return
  buffer=$(superset terminals read --workspace "$ws_id" --terminal "$term_id" 2>/dev/null || true)
  marker=$(printf '%s' "$buffer" | grep -Eo '🪪[0-9a-f-]{36}' | tail -1 || true)
  if [ -n "$marker" ]; then
    printf '%s' "${marker#🪪}"
  fi
}

short_id() {
  printf '%s' "$1" | cut -c1-8
}

# ---------------------------------------------------------------------------
# Per-tab classification
# ---------------------------------------------------------------------------
# Appends a tab-separated line to CANDIDATES_FILE for every RESUME verdict:
#   workspace_id <TAB> terminal_id <TAB> session_id <TAB> worktreePath

classify_tab() {
  local ws_id="$1" ws_worktree="$2" term_id="$3" created_at="$4" title="$5"
  local binding session_id started_at live transcript idle
  local statusline_id short_t short_s

  short_t=$(short_id "$term_id")
  binding=$(get_binding_json "$term_id")
  session_id=$(printf '%s' "$binding" | jq -r 'if . == null then empty else (.agent_session_id // empty) end' 2>/dev/null || true)

  if [ -z "$session_id" ]; then
    idle=$(idle_check "$ws_id" "$term_id" "$created_at")
    if [ "$idle" = "idle" ]; then
      echo "  tab=$short_t  binding=none  title=\"$title\"  -> IDLE-NO-AGENT (idle shell, nothing to resume)"
      return
    fi
    statusline_id=$(statusline_fallback "$ws_id" "$term_id")
    if [ -n "$statusline_id" ]; then
      echo "  tab=$short_t  binding=none  title=\"$title\"  -> LIVE (no binding row, but statusline shows a running session $(short_id "$statusline_id"); leaving alone)"
    else
      echo "  tab=$short_t  binding=none  title=\"$title\"  -> NEEDS-MANUAL (busy shell, no binding row, no statusline marker found - can't identify what's running)"
    fi
    return
  fi

  started_at=$(printf '%s' "$binding" | jq -r '.started_at // empty' 2>/dev/null || true)
  short_s=$(short_id "$session_id")

  if [ -z "$started_at" ]; then
    echo "  tab=$short_t  session=$short_s  title=\"$title\"  -> NEEDS-MANUAL (binding row missing started_at, can't order-check)"
    return
  fi

  live=$(check_live_session "$session_id")

  if [ "$started_at" -ge "$created_at" ]; then
    # Normal order: agent was bound at/after this pty's creation.
    if [ "$live" = "live" ]; then
      echo "  tab=$short_t  session=$short_s  title=\"$title\"  order=normal  d=live  -> LIVE (healthy, running)"
    else
      echo "  tab=$short_t  session=$short_s  title=\"$title\"  order=normal  d=dead  -> CLEAN (previously exited on its own; resume manually if wanted)"
    fi
    return
  fi

  # Reversed order (c): the shell was re-created AFTER the binding -> this is
  # the interrupted-tab shape. Keep checking d/e/f.
  if [ "$live" = "live" ]; then
    # Expected after a successful recovery: resuming via `terminals send`
    # does not refresh the binding row, so the stale order sticks around
    # while the session is alive again. Leave it alone.
    echo "  tab=$short_t  session=$short_s  title=\"$title\"  c=reversed  d=live  -> LIVE (session re-attached after restore - already resumed; leaving alone)"
    return
  fi

  transcript=$(transcript_exists "$session_id")
  if [ "$transcript" != "yes" ]; then
    echo "  tab=$short_t  session=$short_s  title=\"$title\"  c=reversed  d=dead  e=missing  -> NEEDS-MANUAL (no transcript file found for this session id)"
    return
  fi

  idle=$(idle_check "$ws_id" "$term_id" "$created_at")
  if [ "$idle" = "idle" ]; then
    echo "  tab=$short_t  session=$short_s  title=\"$title\"  c=reversed  d=dead  e=present  f=idle  -> RESUME"
    printf '%s\t%s\t%s\t%s\n' "$ws_id" "$term_id" "$session_id" "$ws_worktree" >> "$CANDIDATES_FILE"
  else
    echo "  tab=$short_t  session=$short_s  title=\"$title\"  c=reversed  d=dead  e=present  f=${idle}  -> NEEDS-MANUAL (shell doesn't look idle; not sending into it automatically)"
  fi
}

# ---------------------------------------------------------------------------
# Workspace resolution + main per-workspace pass
# ---------------------------------------------------------------------------

process_workspaces() {
  [ "$HAVE_SUPERSET" -eq 0 ] && return
  [ -z "$DB" ] && { echo "Skipping Superset tab detection (no host.db found)."; return; }

  local workspaces_json target_json match_id
  workspaces_json=$(superset workspaces list --json 2>/dev/null || echo '[]')

  if [ "$WORKSPACE_MODE" = "one" ]; then
    # --workspace <query>: exact id first, then case-insensitive substring
    # match on id / name / branch. Ambiguity is an error, not a guess.
    target_json=$(printf '%s' "$workspaces_json" | jq --arg q "$WS_QUERY" '
      ([.[] | select(.id == $q)]) as $exact |
      if ($exact | length) > 0 then $exact
      else [.[] | select(
        ((.id // "") | ascii_downcase | contains($q | ascii_downcase)) or
        ((.name // "") | ascii_downcase | contains($q | ascii_downcase)) or
        ((.branch // "") | ascii_downcase | contains($q | ascii_downcase)) or
        ((.projectName // "") | ascii_downcase | contains($q | ascii_downcase)) or
        ((.worktreePath // "") | ascii_downcase | contains($q | ascii_downcase))
      )]
      end' 2>/dev/null || echo '[]')
    local match_count
    match_count=$(printf '%s' "$target_json" | jq 'length' 2>/dev/null || echo 0)
    if [ "$match_count" -eq 0 ]; then
      echo "No workspace matches --workspace '$WS_QUERY'. Available:" >&2
      printf '%s' "$workspaces_json" | jq -r '.[] | "  \(.projectName // "?")/\(.name)  (\(.branch))  [\(.id)]"' >&2
      exit 1
    fi
    if [ "$match_count" -gt 1 ]; then
      echo "--workspace '$WS_QUERY' is ambiguous ($match_count matches) - be more specific:" >&2
      printf '%s' "$target_json" | jq -r '.[] | "  \(.projectName // "?")/\(.name)  (\(.branch))  [\(.id)]"' >&2
      exit 1
    fi
  elif [ "$WORKSPACE_MODE" = "current" ]; then
    # Bind worktreePath before piping to startswith - inside `$pwd | ...` the
    # context is the $pwd string, so a bare .worktreePath there is an error.
    match_id=$(printf '%s' "$workspaces_json" | jq -r --arg pwd "$PWD" \
      '[.[] | (.worktreePath // "") as $w | select($w != "" and (($pwd == $w) or ($pwd | startswith($w + "/"))))][0].id // empty' 2>/dev/null || true)
    if [ -z "$match_id" ]; then
      echo "Notice: current directory ($PWD) doesn't match any workspace worktreePath - falling back to --all."
      target_json="$workspaces_json"
    else
      target_json=$(printf '%s' "$workspaces_json" | jq --arg id "$match_id" '[.[] | select(.id == $id)]')
    fi
  else
    target_json="$workspaces_json"
  fi

  local count
  count=$(printf '%s' "$target_json" | jq 'length' 2>/dev/null || echo 0)
  if [ "$count" -eq 0 ]; then
    echo "No workspaces to scan."
    return
  fi

  while IFS=$'\t' read -r ws_id ws_name ws_branch ws_worktree; do
    [ -z "$ws_id" ] && continue
    echo
    echo "== Workspace: $ws_name ($ws_branch) [$ws_id] =="
    echo "   worktree: $ws_worktree"

    local terms_json sessions_count
    terms_json=$(superset terminals list --workspace "$ws_id" --json 2>/dev/null || echo '{"sessions":[]}')
    sessions_count=$(printf '%s' "$terms_json" | jq '.sessions | length' 2>/dev/null || echo 0)
    if [ "$sessions_count" -eq 0 ]; then
      echo "  (no live terminal tabs)"
      continue
    fi

    while IFS=$'\t' read -r term_id created_at exited title; do
      [ -z "$term_id" ] && continue
      [ "$exited" = "true" ] && continue
      classify_tab "$ws_id" "$ws_worktree" "$term_id" "$created_at" "$title"
    done < <(printf '%s' "$terms_json" | jq -r '.sessions[] | [.terminalId, (.createdAt|tostring), (.exited|tostring), (.title // "")] | @tsv')
  done < <(printf '%s' "$target_json" | jq -r '.[] | [.id, .name, .branch, .worktreePath] | @tsv')
}

# ---------------------------------------------------------------------------
# Global sweep: crashed sessions that were never in a Superset tab
# ---------------------------------------------------------------------------

run_global_sweep() {
  echo
  echo "== Global sweep: sessions outside Superset tabs (last ${WINDOW_HOURS}h before boot) =="

  local boot_line boot_epoch window_start
  boot_line=$(sysctl -n kern.boottime 2>/dev/null || true)
  # kern.boottime looks like: { sec = 1786543978, usec = 69662 } Tue Aug 12 ...
  # Note "usec = " contains "sec = " as a substring, so anchor on the brace.
  boot_epoch=$(printf '%s' "$boot_line" | sed -n 's/.*{ sec = \([0-9]*\).*/\1/p' || true)
  if [ -z "$boot_epoch" ]; then
    echo "Could not determine boot time via sysctl kern.boottime - skipping global sweep."
    return
  fi
  window_start=$(( boot_epoch - WINDOW_HOURS * 3600 ))

  local files=("$HOME"/.claude/projects/*/*.jsonl)
  if [ ${#files[@]} -eq 0 ]; then
    echo "No session transcripts found under ~/.claude/projects/."
    return
  fi

  local found=0 f mtime session_id live birth offset cwd title
  for f in "${files[@]}"; do
    mtime=$(stat -f %m "$f" 2>/dev/null || echo 0)
    if [ "$mtime" -gt "$window_start" ] && [ "$mtime" -lt "$boot_epoch" ]; then
      session_id=$(basename "$f" .jsonl)

      live=$(check_live_session "$session_id")
      [ "$live" = "live" ] && continue

      if [ -s "$CANDIDATES_FILE" ] && cut -f3 "$CANDIDATES_FILE" | grep -qx "$session_id"; then
        continue
      fi

      found=1
      birth=$(stat -f %B "$f" 2>/dev/null || echo "$mtime")
      offset=$(( (mtime - birth) % 3600 ))
      # The first line may be a summary/title entry without cwd - take the
      # first entry in the file that carries one.
      cwd=$(head -50 "$f" 2>/dev/null | jq -r '.cwd // empty' 2>/dev/null | grep -m1 . || true)
      title=$(head -c 200000 "$f" 2>/dev/null | grep -m1 -o '"type":"ai-title"[^}]*' 2>/dev/null || true)

      echo
      echo "  session $session_id"
      [ -n "$title" ] && echo "    title-ish: $title"
      [ -n "$cwd" ] && echo "    cwd: $cwd"
      echo "    transcript last written: $(date -r "$mtime" 2>/dev/null || echo "$mtime") (${offset}s past the hour; <=1s suggests killed-while-idle)"
      if [ -n "$cwd" ]; then
        echo "    manual resume: cd $cwd && claude --resume $session_id"
      else
        echo "    manual resume: claude --resume $session_id   (cwd unknown - check the transcript's first line)"
      fi
    fi
  done

  if [ "$found" -eq 0 ]; then
    echo "No candidate crashed sessions found outside Superset tabs in the lookback window."
  fi
}

# ---------------------------------------------------------------------------
# Apply / dry-run of RESUME candidates
# ---------------------------------------------------------------------------

apply_or_report_candidates() {
  echo
  if [ ! -s "$CANDIDATES_FILE" ]; then
    echo "No RESUME candidates found."
    return
  fi

  if [ "$APPLY" -eq 0 ]; then
    echo "== Dry run: commands that would be sent (rerun with --apply) =="
    while IFS=$'\t' read -r ws_id term_id session_id worktree; do
      echo "superset terminals send --workspace $ws_id --terminal $term_id --text 'cd ${worktree} && claude --resume ${session_id}'"
    done < "$CANDIDATES_FILE"
    return
  fi

  echo "== Applying resume commands =="
  mkdir -p "$STATE_DIR"

  # CRITICAL: snapshot every binding row for the affected workspace(s) BEFORE
  # sending a single resume command. terminal_agent_bindings is keyed on
  # terminal_id, so launching claude in a restored tab overwrites
  # agent_session_id - this is the only record of what it was.
  local snapshot_epoch snapshot_file in_list w
  snapshot_epoch=$(date +%s)
  snapshot_file="$STATE_DIR/bindings-${snapshot_epoch}.json"
  in_list=""
  for w in $(cut -f1 "$CANDIDATES_FILE" | sort -u); do
    if [ -z "$in_list" ]; then
      in_list="'$(sql_escape "$w")'"
    else
      in_list="${in_list},'$(sql_escape "$w")'"
    fi
  done
  if [ -n "$DB" ] && [ -n "$in_list" ]; then
    sqlite_json "SELECT * FROM terminal_agent_bindings WHERE workspace_id IN (${in_list});" > "$snapshot_file"
    [ -s "$snapshot_file" ] || echo "[]" > "$snapshot_file"
  else
    echo "[]" > "$snapshot_file"
  fi
  echo "Snapshotted current bindings to: $snapshot_file"

  local fail_count=0
  while IFS=$'\t' read -r ws_id term_id session_id worktree; do
    [ -z "$term_id" ] && continue
    local cmd="cd ${worktree} && claude --resume ${session_id}"
    echo
    echo "-> workspace=$(short_id "$ws_id") terminal=$(short_id "$term_id"): $cmd"
    if superset terminals send --workspace "$ws_id" --terminal "$term_id" --text "$cmd"; then
      sleep 2
      echo "   tail after send:"
      superset terminals read --workspace "$ws_id" --terminal "$term_id" 2>/dev/null | tail -6 | sed 's/^/   | /'
    else
      echo "   FAILED to send to this terminal."
      fail_count=$((fail_count + 1))
    fi
  done < "$CANDIDATES_FILE"

  if [ "$fail_count" -gt 0 ]; then
    echo
    echo "$fail_count send(s) failed - see above."
    OVERALL_EXIT=1
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "resume-superset-sessions: mode=$WORKSPACE_MODE${WS_QUERY:+ query=\"$WS_QUERY\"} apply=$APPLY window=${WINDOW_HOURS}h sweep=$SWEEP"

process_workspaces
apply_or_report_candidates

if [ "$SWEEP" -eq 1 ]; then
  run_global_sweep
fi

echo
resume_count=$( [ -s "$CANDIDATES_FILE" ] && wc -l < "$CANDIDATES_FILE" || echo 0 )
echo "== Summary =="
echo "RESUME candidates found: $(printf '%s' "$resume_count" | tr -d ' ')"
if [ "$APPLY" -eq 1 ]; then
  echo "Mode: applied (resume commands sent where possible)."
else
  echo "Mode: dry run (nothing was sent - rerun with --apply)."
fi

exit "$OVERALL_EXIT"
