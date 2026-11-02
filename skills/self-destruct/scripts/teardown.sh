#!/bin/sh
# Detached teardown for the self-destruct skill. Launch ONLY via nohup, as the
# last act of the session being destroyed:
#
#   nohup sh <this-script> WS_ID TERMINAL_ID ROOT WORKTREE BRANCH SHA >/dev/null 2>&1 &
#
# Everything here runs after the calling session is gone, so the only record of
# what happened is the log. Env knobs (tests only): SELF_DESTRUCT_DELAY,
# SELF_DESTRUCT_LOG, SELF_DESTRUCT_DRY_RUN=1 (print instead of run).

ws=$1 term=$2 root=$3 wt=$4 branch=$5 sha=$6
[ -n "$ws" ] && [ -n "$term" ] && [ -n "$root" ] && [ -n "$wt" ] && [ -n "$branch" ] && [ -n "$sha" ] || {
  echo "usage: teardown.sh WS_ID TERMINAL_ID ROOT WORKTREE BRANCH SHA" >&2
  exit 2
}

log=${SELF_DESTRUCT_LOG:-$HOME/.claude/state/self-destruct.log}
mkdir -p "$(dirname "$log")"
exec >>"$log" 2>&1

# The worktree is about to vanish; never stay inside it.
cd / || exit 1

run() {
  if [ -n "$SELF_DESTRUCT_DRY_RUN" ]; then echo "DRY: $*"; else "$@"; fi
}

echo "$(date '+%F %T') self-destruct $branch @ $sha ($wt)"
echo "  recover: git -C $root branch $branch $sha   # reflog keeps the SHA ~90 days"

# Let the session finish writing its final message and exit its turn.
sleep "${SELF_DESTRUCT_DELAY:-4}"

# Resolve our tab now: after the archive the workspace no longer lists panes.
pane=$(superset panes list --workspace "$ws" --json 2>/dev/null |
  jq -r --arg t "$term" 'first(.. | objects | select(.terminalId? == $t) | .id) // empty')

# Archive removes the worktree folder and registration but keeps the branch.
# --local is mandatory: without it the CLI targets cloud workspaces.
run superset workspaces archive "$ws" --local || {
  echo "  archive FAILED; branch and worktree left in place"
  exit 1
}

# Belt and braces: if the folder survived, remove it (a checked-out branch can't be deleted).
if [ -d "$wt" ]; then
  echo "  worktree folder survived the archive; removing"
  run git -C "$root" worktree remove --force "$wt"
fi
run git -C "$root" worktree prune -v
run git -C "$root" branch -D "$branch"

# Archive alone can leave a dead, unresponsive tab; close the pane explicitly.
if [ -n "$pane" ]; then
  run superset panes close --workspace "$ws" --pane "$pane" || echo "  pane close failed (tab may need closing by hand)"
else
  echo "  no pane found for terminal $term"
fi
echo "  done"
