#!/bin/sh
# Release queued PRs to #pr-review — launched by launchd (com.jake.pr-review-batch)
# every 5 minutes; flush.py posts only when a window boundary (windowMin in
# ~/.claude/state/pr-review-batch.json, default 30) has passed. Safe to run by
# hand: --dry-run previews, --only <url> posts one PR now, --channel <id> (or
# env PR_REVIEW_BATCH_CHANNEL) overrides the channel for testing.
set -u

# launchd gives a bare environment: put gh (and security) on PATH,
# in the same order an interactive shell has them.
export PATH="$HOME/superset/bin:$HOME/.superset/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "${USER:=$(id -un)}"
export USER

LOGDIR="$HOME/.claude/automations/pr-review-batch/logs"
mkdir -p "$LOGDIR"
here=$(cd "$(dirname "$0")" && pwd)

if [ -t 1 ]; then
  exec python3 -I "$here/flush.py" "$@"
fi
exec python3 -I "$here/flush.py" "$@" >>"$LOGDIR/flush.log" 2>&1
