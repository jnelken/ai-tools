#!/bin/bash
# resume-sswts: after a machine crash/power loss, Superset
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
# This script never deletes anything and never writes to host.db. The one
# tab-closing behavior is strictly opt-in: --close-ghosts closes an old
# dormant ghost tab ONLY after its session is verified alive in the new tab
# `--create-tabs` opened for it (closing clears the tab's binding row, so
# order matters).
# ---------------------------------------------------------------------------
#
# Usage:
#   detect-and-resume.sh                 dry run, current workspace only
#                                        (errors out if $PWD isn't inside a
#                                        workspace worktree - never silently
#                                        widens to all workspaces)
#   detect-and-resume.sh --all           dry run, every workspace on this host
#                                        (the workspace matching $PWD, if any,
#                                        is processed - and therefore resumed
#                                        first under --apply)
#   detect-and-resume.sh --workspace <q> dry run, one workspace by id, name, or
#                                        branch (partial, case-insensitive;
#                                        ambiguity errors out with the matches)
#   detect-and-resume.sh --apply         snapshot bindings, then actually send
#   detect-and-resume.sh --all --apply   both
#   detect-and-resume.sh --create-tabs   also resume DORMANT tabs (workspaces
#                                        not yet viewed in Superset since
#                                        reboot, so the original tab can't be
#                                        typed into) by opening NEW tabs
#   detect-and-resume.sh --close-ghosts  with --create-tabs --apply: close each
#                                        old dormant ghost tab once its session
#                                        is verified alive in the new tab
#   detect-and-resume.sh --window 48     global-sweep lookback in hours (def 24)
#   detect-and-resume.sh --no-sweep      skip the non-Superset global sweep
#
# Must run on macOS default bash 3.2: no associative arrays, no mapfile, no
# ${var,,}. All JSON handled via jq. set -e is guarded everywhere a grep/pgrep
# "no match" is an expected, non-fatal outcome (`|| true`, or inside `if`).
#
# Two perf shortcuts, both correctness-preserving (never serve stale liveness
# data - only skip re-deriving facts that cannot have changed):
#   - check_live_session() consults a live-sessions index built ONCE per
#     invocation (build_live_sessions_index) instead of re-globbing and
#     re-parsing ~/.claude/sessions/*.json on every call - a single run can
#     call it dozens of times.
#   - The global sweep's per-transcript cwd/title extraction (a 200KB read +
#     jq/grep per file) is cached across INVOCATIONS in
#     ~/.claude/state/session-recovery/transcript-metadata-cache.json, keyed
#     by path with mtime folded into each entry so a changed or deleted file
#     just misses. Rebuilt fresh each run from whatever's actually touched,
#     so it also self-prunes. Superset-side state (workspaces/terminals/
#     bindings/process liveness) is intentionally NEVER cached this way - that
#     data must always reflect what's true right now, which is the entire
#     point of a RESUME classification.

set -uo pipefail
shopt -s nullglob

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

WORKSPACE_MODE="current"
WS_QUERY=""
APPLY=0
CREATE_TABS=0
CLOSE_GHOSTS=0
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
    --create-tabs) CREATE_TABS=1 ;;
    --close-ghosts) CLOSE_GHOSTS=1 ;;
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
# kern.boottime looks like: { sec = 1786543978, usec = 69662 } Tue Aug 12 ...
# ("usec = " contains "sec = " as a substring, hence the brace anchor).
BOOT_EPOCH=$(sysctl -n kern.boottime 2>/dev/null | sed -n 's/.*{ sec = \([0-9]*\).*/\1/p' || true)
CANDIDATES_FILE=$(mktemp -t resume-candidates)
DORMANT_FILE=$(mktemp -t resume-dormant)
LIVE_SESSIONS_FILE=$(mktemp -t resume-live-sessions)
OVERALL_EXIT=0

# Persistent, self-invalidating cache of the global sweep's per-transcript
# metadata (cwd / ai-title extraction). Keyed by absolute path with the
# file's mtime folded into each entry, so a changed (or deleted, on rewrite)
# transcript just misses instead of ever serving stale data. This is the
# expensive part of a re-run across a fleet of old sessions: each miss reads
# up to 200KB off disk and pipes it through jq/grep.
SWEEP_CACHE_FILE="$STATE_DIR/transcript-metadata-cache.json"

cleanup() { rm -f "$CANDIDATES_FILE" "$DORMANT_FILE" "$LIVE_SESSIONS_FILE"; }
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

# Built ONCE per invocation (see build_live_sessions_index below) instead of
# re-globbing + re-parsing every ~/.claude/sessions/*.json file on every call.
# A single run can call check_live_session dozens of times (once per
# candidate tab, per dormant binding, per global-sweep transcript) - without
# this index that's O(sessions-on-disk) work repeated that many times.
build_live_sessions_index() {
  : > "$LIVE_SESSIONS_FILE"
  local f sid pid
  for f in "$HOME"/.claude/sessions/*.json; do
    sid=$(jq -r '.sessionId // empty' "$f" 2>/dev/null || true)
    [ -z "$sid" ] && continue
    pid=$(jq -r '.pid // empty' "$f" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      printf '%s\n' "$sid" >> "$LIVE_SESSIONS_FILE"
    fi
  done
}

# Is there a currently-running claude process actually attached to this
# session id? Checked two ways (belt-and-suspenders per spec):
#   1. The precomputed index above (~/.claude/sessions/<pid>.json only
#      exists for LIVE sessions - dead ones are reaped - confirmed via
#      kill -0 at index-build time).
#   2. pgrep -f "claude.*<session-id>" as a fallback signal.
check_live_session() {
  local session_id="$1"
  if grep -qxF "$session_id" "$LIVE_SESSIONS_FILE" 2>/dev/null; then
    echo "live"
    return
  fi
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
# Dormant tabs
# ---------------------------------------------------------------------------
# Superset materializes a workspace's ptys LAZILY - a restored tab does not
# exist in the daemon (and `terminals list` omits it, `terminals send` returns
# "Not found") until the user first views that workspace in the UI. The
# binding row in host.db is there the whole time, though, so a crashed
# session in a never-viewed-since-reboot workspace is still identifiable -
# it just can't be typed into yet.
#
# For every binding in the workspace whose terminal is NOT in the daemon's
# live list (and not disposed), classify:
#   - session live      -> already resumed somewhere; leave alone
#   - dead + transcript -> TO-BE-RESUMED: either open the workspace in
#     Superset and rerun (the tab materializes, created_at updates, and the
#     normal deterministic path takes over), or with --create-tabs --apply
#     spawn a NEW tab in that workspace running the resume.
# Appends every dormant session id to DORMANT_FILE so the global sweep
# doesn't re-report it, and TO-BE-RESUMED rows to CANDIDATES_FILE tagged
# "create" when --create-tabs is on.

check_dormant_tabs() {
  local ws_id="$1" ws_worktree="$2" live_ids="$3"
  local esc rows count i row term_id session_id status live transcript short_t short_s

  [ -z "$DB" ] && return
  esc=$(sql_escape "$ws_id")
  rows=$(sqlite_json "SELECT b.terminal_id, b.agent_session_id, COALESCE(t.status,'') AS status
    FROM terminal_agent_bindings b LEFT JOIN terminal_sessions t ON t.id = b.terminal_id
    WHERE b.workspace_id='${esc}' AND b.agent_session_id IS NOT NULL;")
  [ -z "$rows" ] && return
  count=$(printf '%s' "$rows" | jq 'length' 2>/dev/null || echo 0)
  [ "$count" -eq 0 ] && return

  i=0
  while [ "$i" -lt "$count" ]; do
    row=$(printf '%s' "$rows" | jq -r ".[$i] | [.terminal_id, .agent_session_id, .status] | @tsv")
    i=$((i + 1))
    term_id=$(printf '%s' "$row" | cut -f1)
    session_id=$(printf '%s' "$row" | cut -f2)
    status=$(printf '%s' "$row" | cut -f3)

    # Tab is live in the daemon -> already handled by classify_tab.
    printf '%s\n' "$live_ids" | grep -qx "$term_id" && continue
    # Tab was closed on purpose -> its binding is history, not a candidate.
    [ "$status" != "active" ] && continue

    short_t=$(short_id "$term_id")
    short_s=$(short_id "$session_id")
    printf '%s\n' "$session_id" >> "$DORMANT_FILE"

    live=$(check_live_session "$session_id")
    if [ "$live" = "live" ]; then
      echo "  tab=$short_t  session=$short_s  (dormant)  -> LIVE (session already running elsewhere; leaving alone)"
      continue
    fi
    transcript=$(transcript_exists "$session_id")
    if [ "$transcript" != "yes" ]; then
      echo "  tab=$short_t  session=$short_s  (dormant)  -> NEEDS-MANUAL (no transcript found for this session id)"
      continue
    fi

    # A dormant tab lacks the created_at ordering proof (the pty was never
    # re-created), so substitute: the transcript must have been written in the
    # window shortly before boot, i.e. the session was actually alive when the
    # machine went down. Idle-but-alive sessions rewrite their transcript
    # hourly, so anything alive at the crash has an mtime within ~1h of it.
    local tf tmtime
    tf=$(ls "$HOME"/.claude/projects/*/"${session_id}.jsonl" 2>/dev/null | head -1)
    tmtime=$(stat -f %m "$tf" 2>/dev/null || echo 0)
    # No kill event after the session's last activity => it ended on its own
    # (user exit / tab close), NOT a crash. Never presume a session dead just
    # because no process is running now - only classify TO-BE-RESUMED when a
    # detected kill event (the boot) postdates its last write.
    if [ -n "$BOOT_EPOCH" ] && [ "$tmtime" -ge "$BOOT_EPOCH" ]; then
      echo "  tab=$short_t  session=$short_s  (dormant)  -> CLEAN (last activity was after boot, no crash event since - it exited deliberately; resume manually if wanted)"
      continue
    fi
    if [ -n "$BOOT_EPOCH" ] && [ "$tmtime" -lt $((BOOT_EPOCH - WINDOW_HOURS * 3600)) ]; then
      echo "  tab=$short_t  session=$short_s  (dormant)  -> CLEAN (went quiet more than ${WINDOW_HOURS}h before the crash - wasn't alive when the machine went down; resume manually if wanted)"
      continue
    fi

    if [ "$CREATE_TABS" -eq 1 ]; then
      echo "  tab=$short_t  session=$short_s  (dormant)  -> TO-BE-RESUMED (will open a NEW tab via terminals create)"
      printf '%s\t%s\t%s\t%s\t%s\n' "$ws_id" "$term_id" "$session_id" "$ws_worktree" "create" >> "$CANDIDATES_FILE"
    else
      echo "  tab=$short_t  session=$short_s  (dormant)  -> TO-BE-RESUMED (tab not materialized: this workspace hasn't been viewed in Superset since reboot)"
      echo "      either: open the workspace in Superset, then rerun this skill"
      echo "      or:     rerun with --create-tabs --apply to resume it in a new tab now"
    fi
  done
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
    printf '%s\t%s\t%s\t%s\t%s\n' "$ws_id" "$term_id" "$session_id" "$ws_worktree" "send" >> "$CANDIDATES_FILE"
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
      # No silent scope-widening: an --apply run from a non-workspace cwd must
      # never touch every workspace on the host just because the cwd didn't
      # match. Scanning everything is always an explicit choice (--all).
      echo "Current directory ($PWD) doesn't match any workspace worktreePath." >&2
      echo "Rerun with --all to scan every workspace, or --workspace <query> for one of:" >&2
      printf '%s' "$workspaces_json" | jq -r '.[] | "  \(.projectName // "?")/\(.name)  (\(.branch))  [\(.id)]"' >&2
      exit 1
    else
      target_json=$(printf '%s' "$workspaces_json" | jq --arg id "$match_id" '[.[] | select(.id == $id)]')
    fi
  else
    target_json="$workspaces_json"
    # --all scans every workspace, but reviving is highest-value where the
    # user is actually sitting right now. Reorder (never drop) so the
    # workspace matching $PWD is classified - and therefore resumed - before
    # any other workspace, without changing what gets covered.
    local cur_id
    cur_id=$(printf '%s' "$workspaces_json" | jq -r --arg pwd "$PWD" \
      '[.[] | (.worktreePath // "") as $w | select($w != "" and (($pwd == $w) or ($pwd | startswith($w + "/"))))][0].id // empty' 2>/dev/null || true)
    if [ -n "$cur_id" ]; then
      target_json=$(printf '%s' "$target_json" | jq --arg id "$cur_id" 'sort_by(.id != $id)')
      echo "(current workspace matched by \$PWD - processing it first)"
    fi
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
      # No materialized tabs - but dormant bindings may still exist (the
      # workspace just hasn't been viewed in Superset since reboot), so the
      # dormant check below must still run.
      echo "  (no live terminal tabs)"
    fi

    while IFS=$'\t' read -r term_id created_at exited title; do
      [ -z "$term_id" ] && continue
      [ "$exited" = "true" ] && continue
      classify_tab "$ws_id" "$ws_worktree" "$term_id" "$created_at" "$title"
    done < <(printf '%s' "$terms_json" | jq -r '.sessions[] | [.terminalId, (.createdAt|tostring), (.exited|tostring), (.title // "")] | @tsv')

    # Bindings whose tab the daemon doesn't know about (yet): lazily-restored
    # workspaces that haven't been viewed since reboot.
    local live_ids
    live_ids=$(printf '%s' "$terms_json" | jq -r '.sessions[].terminalId' 2>/dev/null || true)
    check_dormant_tabs "$ws_id" "$ws_worktree" "$live_ids"
  done < <(printf '%s' "$target_json" | jq -r '.[] | [.id, .name, .branch, .worktreePath] | @tsv')
}

# ---------------------------------------------------------------------------
# Global sweep: crashed sessions that were never in a Superset tab
# ---------------------------------------------------------------------------

run_global_sweep() {
  echo
  echo "== Global sweep: sessions outside Superset tabs (last ${WINDOW_HOURS}h before boot) =="

  local boot_epoch window_start
  boot_epoch="$BOOT_EPOCH"
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

  # Self-invalidating cache of the expensive part below: extracting cwd/title
  # from a transcript means reading up to 200KB off disk and piping it
  # through jq/grep. Keyed by path with mtime folded into each entry, so a
  # file that changed since the last run (or was deleted) just misses rather
  # than ever serving stale data. Rebuilt from scratch each run (only entries
  # actually touched this run are written back), so it also self-prunes.
  local sweep_cache_json="{}" cache_hits=0 cache_misses=0
  if [ -f "$SWEEP_CACHE_FILE" ]; then
    sweep_cache_json=$(cat "$SWEEP_CACHE_FILE" 2>/dev/null || echo '{}')
    printf '%s' "$sweep_cache_json" | jq -e . >/dev/null 2>&1 || sweep_cache_json='{}'
  fi
  local new_cache_entries_file
  new_cache_entries_file=$(mktemp -t resume-sweep-cache-entries)

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
      # Already reported as a dormant Superset tab in a workspace section.
      if [ -s "$DORMANT_FILE" ] && grep -qx "$session_id" "$DORMANT_FILE"; then
        continue
      fi

      # A session can look "non-Superset" here purely because its workspace
      # hasn't been viewed since reboot (tabs materialize lazily and this run
      # may not have scanned that workspace). The binding row knows better.
      if [ -n "$DB" ]; then
        local origin
        origin=$(sqlite_json "SELECT w.name AS name, w.branch AS branch, b.workspace_id AS wsid
          FROM terminal_agent_bindings b LEFT JOIN workspaces w ON w.id = b.workspace_id
          WHERE b.agent_session_id='$(sql_escape "$session_id")' LIMIT 1;" | jq -r '.[0] // empty | "\(.name)\t\(.branch)\t\(.wsid)"' 2>/dev/null || true)
        if [ -n "$origin" ]; then
          found=1
          echo
          echo "  session $session_id"
          echo "    origin: SUPERSET workspace \"$(printf '%s' "$origin" | cut -f1)\" ($(printf '%s' "$origin" | cut -f2)) [$(printf '%s' "$origin" | cut -f3)]"
          echo "    dormant tab - not materialized because that workspace hasn't been viewed in Superset since reboot."
          echo "    to resume: open the workspace in Superset and rerun this skill, or rerun with --all --create-tabs --apply"
          continue
        fi
      fi

      found=1
      birth=$(stat -f %B "$f" 2>/dev/null || echo "$mtime")
      offset=$(( (mtime - birth) % 3600 ))

      local cached_entry cached_mtime
      cached_entry=$(printf '%s' "$sweep_cache_json" | jq -c --arg f "$f" '.[$f] // empty' 2>/dev/null || true)
      cached_mtime=""
      if [ -n "$cached_entry" ] && [ "$cached_entry" != "null" ]; then
        cached_mtime=$(printf '%s' "$cached_entry" | jq -r '.mtime // empty' 2>/dev/null || true)
      fi

      if [ -n "$cached_mtime" ] && [ "$cached_mtime" = "$mtime" ]; then
        cwd=$(printf '%s' "$cached_entry" | jq -r '.cwd // empty' 2>/dev/null || true)
        title=$(printf '%s' "$cached_entry" | jq -r '.title // empty' 2>/dev/null || true)
        cache_hits=$((cache_hits + 1))
      else
        # The first line may be a summary/title entry without cwd - take the
        # first entry in the file that carries one.
        cwd=$(head -50 "$f" 2>/dev/null | jq -r '.cwd // empty' 2>/dev/null | grep -m1 . || true)
        title=$(head -c 200000 "$f" 2>/dev/null | grep -m1 -o '"type":"ai-title"[^}]*' 2>/dev/null || true)
        cache_misses=$((cache_misses + 1))
      fi
      jq -cn --arg f "$f" --argjson mtime "$mtime" --arg cwd "$cwd" --arg title "$title" \
        '{($f): {mtime: $mtime, cwd: $cwd, title: $title}}' >> "$new_cache_entries_file" 2>/dev/null || true

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

  if [ -s "$new_cache_entries_file" ]; then
    mkdir -p "$STATE_DIR"
    jq -s 'add // {}' "$new_cache_entries_file" > "$SWEEP_CACHE_FILE" 2>/dev/null || true
  fi
  rm -f "$new_cache_entries_file"
  echo
  echo "(transcript metadata cache: ${cache_hits} hit, ${cache_misses} miss)"

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
    while IFS=$'\t' read -r ws_id term_id session_id worktree mode; do
      if [ "$mode" = "create" ]; then
        echo "superset terminals create --workspace $ws_id --cwd ${worktree} --command 'claude --resume ${session_id}'"
      else
        echo "superset terminals send --workspace $ws_id --terminal $term_id --text 'cd ${worktree} && claude --resume ${session_id}'"
      fi
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
  while IFS=$'\t' read -r ws_id term_id session_id worktree mode; do
    [ -z "$term_id" ] && continue
    echo
    if [ "$mode" = "create" ]; then
      # Dormant tab: the daemon can't reach the original terminal until the
      # workspace is viewed in the UI, so resume in a NEW tab instead. The
      # dormant tab's binding row is untouched (different terminal_id).
      echo "-> workspace=$(short_id "$ws_id") NEW TAB: claude --resume ${session_id}"
      if superset terminals create --workspace "$ws_id" --cwd "$worktree" --command "claude --resume ${session_id}"; then
        echo "   created."
        if [ "$CLOSE_GHOSTS" -eq 1 ]; then
          # Close the old dormant ghost tab - but only once the resumed
          # session is verifiably alive in its new tab. `terminals close`
          # works on dormant terminals (unlike `send`) and CLEARS the
          # binding row, so closing before the resume is confirmed would
          # destroy the only pointer to the session (snapshot aside).
          local waited=0 resumed_live="dead"
          while [ "$waited" -lt 15 ]; do
            sleep 3; waited=$((waited + 3))
            resumed_live=$(check_live_session "$session_id")
            [ "$resumed_live" = "live" ] && break
          done
          if [ "$resumed_live" = "live" ]; then
            if superset terminals close --workspace "$ws_id" --terminal "$term_id" >/dev/null 2>&1; then
              echo "   ghost tab $(short_id "$term_id") closed."
            else
              echo "   could not close ghost tab $(short_id "$term_id") - close it manually."
            fi
          else
            echo "   resumed session not confirmed live after ${waited}s - leaving ghost tab $(short_id "$term_id") alone."
          fi
        fi
      else
        echo "   FAILED to create a tab in this workspace."
        fail_count=$((fail_count + 1))
      fi
      continue
    fi
    local cmd="cd ${worktree} && claude --resume ${session_id}"
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

echo "resume-sswts: mode=$WORKSPACE_MODE${WS_QUERY:+ query=\"$WS_QUERY\"} apply=$APPLY window=${WINDOW_HOURS}h sweep=$SWEEP"

build_live_sessions_index
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
