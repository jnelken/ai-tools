#!/bin/sh
# Reap ⛏️ Superset sessions — launched by launchd (com.jake.reap-sswts) every
# 10 minutes. Safe to run by hand; pass --dry-run to see what it would do.
set -u

# launchd gives a bare environment: put superset, gh, git and jq on PATH,
# in the same order an interactive shell has them.
export PATH="$HOME/superset/bin:$HOME/.superset/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

LOGDIR="$HOME/.claude/automations/reap-sswts/logs"
mkdir -p "$LOGDIR"
here=$(cd "$(dirname "$0")" && pwd)

if [ -t 1 ]; then
  exec python3 -I "$here/reap.py" "$@"
fi
exec python3 -I "$here/reap.py" "$@" >>"$LOGDIR/reap.log" 2>&1
