#!/bin/bash
# reap-dev-servers: find dev-server-shaped processes squatting on a TCP port and
# classify each as actively-supervised (leave alone) or orphaned (safe to kill).
#
# Two processes can carry an identical-looking title (`{agent}-{script}-{branch}`)
# while one is abandoned and the other very much alive, so neither raw title
# matching nor a naive "ancestry reached launchd -> orphaned" check is safe alone:
#
#   Track A (terminal-owned processes): walk the parent chain looking for a
#   recognized terminal/multiplexer anchor (Superset's pty-daemon, Terminal.app,
#   iTerm, tmux, ...) before reaching launchd. Found one -> a real terminal
#   window owns this, leave it alone.
#
#   Track B (Claude-Bash-tool-spawned processes): these are NEVER real OS
#   descendants of the `claude` process - the harness detaches them straight
#   to launchd (PID 1) by design, whether or not the owning session is still
#   alive. Ancestry can't resolve this category at all (verified empirically:
#   a wrapping shell shows ppid=1 immediately even mid-session). Instead:
#   extract the branch encoded in the title and check whether ANY currently
#   running claude process's cwd is checked out on that same branch. A match
#   means a live session still owns it; no match anywhere means the session
#   that spawned it is gone.
#
# Anything that doesn't cleanly resolve via either track is reported as
# uncertain and never auto-killed.
#
# Usage:
#   reap.sh              scan, auto-kill high-confidence orphans, report everything
#   reap.sh --reap        (same as above, explicit)
#   reap.sh --report-only scan and report only; never kills. Used by SessionStart.
#   reap.sh --dry-run     same reporting as --reap, but never actually kills. For
#                         validating classification against real processes first.

set -uo pipefail

MODE="reap"
for arg in "$@"; do
  case "$arg" in
    --report-only) MODE="report" ;;
    --reap) MODE="reap" ;;
    --dry-run) MODE="dry-run" ;;
  esac
done

# Known agent initials from set-process-name.sh's labeling scheme. Deliberately
# a closed set (not "any lowercase letter") so an unrelated process whose title
# happens to start with "x-" can't be mistaken for one of ours.
AGENT_INITIALS='cxpugadom'

ANCHOR_PATTERN='pty-daemon|Terminal\.app|iTerm|tmux|screen|Ghostty|Alacritty|kitty|WezTerm|Hyper|Code Helper'
MAX_ANCESTRY_DEPTH=25

get_ppid() { ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }
# comm= is the (possibly process.title-overridden) short name - right for reading a
# candidate's own title, but WRONG for anchor matching: e.g. Superset's pty-daemon shows
# comm=".../MacOS/Superset" with no trace of "pty-daemon", which only appears as a launch
# argument. Anchor matching needs the full command line instead.
get_comm() { ps -o comm= -p "$1" 2>/dev/null; }
get_args() { ps -o args= -p "$1" 2>/dev/null; }

# Track A - walk parents looking for a recognized terminal/multiplexer anchor.
# Prints the matched anchor line and returns 0 if found before hitting launchd or the depth cap.
find_terminal_anchor() {
  local pid="$1" depth=0 args
  while [ -n "$pid" ] && [ "$pid" != "1" ] && [ "$depth" -lt "$MAX_ANCESTRY_DEPTH" ]; do
    args=$(get_args "$pid")
    if [[ "$args" =~ $ANCHOR_PATTERN ]]; then
      echo "$args"
      return 0
    fi
    pid=$(get_ppid "$pid")
    depth=$((depth + 1))
  done
  return 1
}

# Every live claude process's current branch, sanitized identically to
# set-process-name.sh, one per line, deduped. Machines here routinely run 20+
# concurrent sessions, and each lookup is an lsof + git spawn - sequentially
# that's well over a minute, so fan the per-pid lookups out concurrently.
live_session_branches() {
  local cpid tmpdir
  tmpdir=$(mktemp -d)
  for cpid in $(pgrep -f '/claude$|ClaudeCode\.app' 2>/dev/null); do
    (
      cwd=$(lsof -p "$cpid" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | tail -1)
      [ -z "$cwd" ] && exit 0
      git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's/[^a-zA-Z0-9._-]/-/g' > "$tmpdir/$cpid"
    ) &
  done
  wait
  cat "$tmpdir"/* 2>/dev/null | sort -u
  rm -rf "$tmpdir"
}

# Does the title's remainder contain a live branch at a `-`-delimited boundary?
# (plain substring would risk "main" matching inside "mainframe-migration")
title_matches_any_live_branch() {
  local remainder="$1" branches="$2" b escaped
  while read -r b; do
    [ -z "$b" ] && continue
    escaped=$(printf '%s' "$b" | sed 's/[.[\*^$]/\\&/g')
    if [[ "$remainder" =~ (^|-)$escaped(-|$) ]]; then
      return 0
    fi
  done <<< "$branches"
  return 1
}

# Recursively enumerate a process tree (all descendants, then self).
descendants() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    descendants "$child"
  done
  echo "$pid"
}

# Kill an entire orphaned subtree, verify the port actually freed up, escalate if not.
kill_tree() {
  local top="$1" port="$2" pids
  pids=$(descendants "$top")
  echo "$pids" | xargs -I{} kill -TERM {} 2>/dev/null
  sleep 1
  if lsof -i ":$port" -sTCP:LISTEN -n -P 2>/dev/null | grep -q LISTEN; then
    echo "$pids" | xargs -I{} kill -KILL {} 2>/dev/null
    sleep 1
  fi
}

live_branches_cache=""

classify_and_report() {
  local pid="$1" port="$2" title anchor remainder

  title=$(get_comm "$pid")

  if anchor=$(find_terminal_anchor "$pid"); then
    [ "$MODE" != "report" ] && echo "ACTIVE    pid=$pid port=$port title=$title  (owned by terminal: $anchor)"
    return
  fi

  if [[ "$title" =~ ^[$AGENT_INITIALS]-(.+)$ ]]; then
    remainder="${BASH_REMATCH[1]}"
    [ -z "$live_branches_cache" ] && live_branches_cache=$(live_session_branches)
    if title_matches_any_live_branch "$remainder" "$live_branches_cache"; then
      [ "$MODE" != "report" ] && echo "ACTIVE    pid=$pid port=$port title=$title  (matches a live claude session's branch)"
      return
    fi
    echo "ORPHANED  pid=$pid port=$port title=$title  (no live session references this branch)"
    if [ "$MODE" = "reap" ]; then
      kill_tree "$pid" "$port"
      echo "  -> killed"
    elif [ "$MODE" = "dry-run" ]; then
      echo "  -> would kill (dry run)"
    fi
    return
  fi

  echo "UNCERTAIN pid=$pid port=$port title=$title  (doesn't match the labeling scheme, and no terminal anchor found)"
}

main() {
  local found_any=0
  while read -r pid rest; do
    [ -z "$pid" ] && continue
    port=$(echo "$rest" | grep -oE ':[0-9]+ \(LISTEN\)' | grep -oE '[0-9]+' | head -1)
    [ -z "$port" ] && continue
    found_any=1
    classify_and_report "$pid" "$port"
  done < <(lsof -a -iTCP -sTCP:LISTEN -n -P -u "$(whoami)" 2>/dev/null | awk '$1=="node"{print $2, $0}')

  if [ "$found_any" -eq 0 ] && [ "$MODE" != "report" ]; then
    echo "No candidate dev-server processes found listening on any port."
  fi
}

main
