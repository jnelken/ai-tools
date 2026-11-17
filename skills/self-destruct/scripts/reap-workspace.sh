#!/bin/sh
# Remove one Superset worktree workspace completely: archive it, make sure the
# worktree folder and registration are gone, delete its local branch, close its
# tabs, and log the command that recovers the branch.
#
#   sh reap-workspace.sh WS_ID ROOT WORKTREE BRANCH [TERMINAL_ID] [SHA]
#
# ROOT is the owning project's root checkout. With TERMINAL_ID only that tab is
# closed (self-destruct closing its own tab); without it every tab in the
# workspace is. SHA defaults to the branch tip and is only used for the log.
#
# Shared by self-destruct's teardown.sh (detached, for the session's own
# workspace) and clean-sswts (inline, one call per workspace). The caller owns
# every safety check (root checkout, merged, clean, held); this script trusts it.
#
# Everything goes to $REAP_LOG (default ~/.claude/state/self-destruct.log); one
# summary line goes to stdout. REAP_DRY_RUN=1 prints commands instead.

ws=$1 root=$2 wt=$3 branch=$4 term=$5 sha=$6
[ -n "$ws" ] && [ -n "$root" ] && [ -n "$wt" ] && [ -n "$branch" ] || {
  echo "usage: reap-workspace.sh WS_ID ROOT WORKTREE BRANCH [TERMINAL_ID] [SHA]" >&2
  exit 2
}
[ "$wt" = "$root" ] && {
  echo "refusing: worktree $wt is the root checkout" >&2
  exit 2
}

log=${REAP_LOG:-$HOME/.claude/state/self-destruct.log}
mkdir -p "$(dirname "$log")"
exec 3>&1 >>"$log" 2>&1

# The worktree is about to vanish; never stay inside it.
cd / || exit 1

run() {
  if [ -n "$REAP_DRY_RUN" ]; then echo "DRY: $*"; else "$@"; fi
}
summary() { echo "$*" >&3; }

[ -n "$sha" ] || sha=$(git -C "$root" rev-parse --verify -q "refs/heads/$branch")
echo "$(date '+%F %T') reap $branch @ ${sha:-?} ($wt)"
[ -n "$sha" ] && echo "  recover: git -C $root branch $branch $sha   # reflog keeps the SHA ~90 days"

# Resolve tabs now: after the archive the workspace no longer lists panes.
if [ -n "$term" ]; then
  filter='first(.. | objects | select(.terminalId? == $t) | .id) // empty'
else
  filter='[.. | objects | select(.terminalId?) | .id] | unique | .[]'
fi
panes=$(superset panes list --workspace "$ws" --json 2>/dev/null | jq -r --arg t "$term" "$filter")

# Archive removes the worktree folder and registration but keeps the branch.
# --local is mandatory: without it the CLI targets cloud workspaces.
run superset workspaces archive "$ws" --local || {
  echo "  archive FAILED; branch and worktree left in place"
  summary "FAILED $branch: archive failed, nothing removed (see $log)"
  exit 1
}

# If the folder survived, remove it (a checked-out branch can't be deleted).
if [ -d "$wt" ]; then
  echo "  worktree folder survived the archive; removing"
  run git -C "$root" worktree remove --force "$wt"
fi
run git -C "$root" worktree prune -v
if git -C "$root" show-ref --verify -q "refs/heads/$branch"; then
  run git -C "$root" branch -D "$branch"
else
  echo "  no local branch $branch"
fi

# Archive alone can leave a dead, unresponsive tab; close panes explicitly.
for pane in $panes; do
  run superset panes close --workspace "$ws" --pane "$pane" || echo "  pane close failed: $pane"
done
[ -n "$panes" ] || echo "  no panes found${term:+ for terminal $term}"

echo "  done"
summary "reaped $branch${sha:+ @ $sha} (recover: git -C $root branch $branch ${sha:-<sha>})"
