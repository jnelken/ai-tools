#!/bin/sh
# Detached teardown for the self-destruct skill. Launch ONLY via nohup, as the
# last act of the session being destroyed:
#
#   nohup sh <this-script> WS_ID TERMINAL_ID ROOT WORKTREE BRANCH SHA >/dev/null 2>&1 &
#
# It waits for the session to finish its final message, then hands off to
# reap-workspace.sh (shared with clean-sswts), which does the actual removal and
# logs it. Everything runs after the calling session is gone, so the log is the
# only record. Env knobs (tests only): SELF_DESTRUCT_DELAY, SELF_DESTRUCT_LOG,
# SELF_DESTRUCT_DRY_RUN=1 (print instead of run).

ws=$1 term=$2 root=$3 wt=$4 branch=$5 sha=$6
[ -n "$ws" ] && [ -n "$term" ] && [ -n "$root" ] && [ -n "$wt" ] && [ -n "$branch" ] && [ -n "$sha" ] || {
  echo "usage: teardown.sh WS_ID TERMINAL_ID ROOT WORKTREE BRANCH SHA" >&2
  exit 2
}

reap="$(cd "$(dirname "$0")" && pwd)/reap-workspace.sh"

# The worktree is about to vanish; never stay inside it.
cd / || exit 1

# Let the session finish writing its final message and exit its turn.
sleep "${SELF_DESTRUCT_DELAY:-4}"

export REAP_LOG="${SELF_DESTRUCT_LOG:-$HOME/.claude/state/self-destruct.log}"
export REAP_DRY_RUN="$SELF_DESTRUCT_DRY_RUN"
exec sh "$reap" "$ws" "$root" "$wt" "$branch" "$term" "$sha"
