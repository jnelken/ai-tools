#!/bin/sh
# Close the Superset tab a /self-destruct session runs in, without touching the
# workspace (the root-checkout path; formerly the /done skill). Launch detached,
# as the last tool call of the turn:
#
#   nohup sh ~/.claude/skills/self-destruct/scripts/close-tab.sh "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" >/dev/null 2>&1 &
#
# Prefers `panes close`, which removes the tab from the desktop layout. That
# needs a desktop app attached to this host; when the tab is viewed from
# another machine's app, `panes` reports "no desktop app is attached" and the
# fallback `terminals close` disposes the terminal on the host but leaves a
# dead tab in that app (upstream; close it by hand).
ws=$1 term=$2
[ -n "$ws" ] && [ -n "$term" ] || { echo "usage: close-tab.sh WS_ID TERMINAL_ID" >&2; exit 2; }
log=${SELF_DESTRUCT_LOG:-$HOME/.claude/state/self-destruct.log}
mkdir -p "$(dirname "$log")"
exec >>"$log" 2>&1
cd / || exit 1

# Let the final message render and the turn end first.
sleep "${SELF_DESTRUCT_DELAY:-2}"

echo "$(date '+%F %T') close terminal $term"
pane=$(superset panes list --workspace "$ws" --json 2>/dev/null |
  jq -r --arg t "$term" 'first(.. | objects | select(.terminalId? == $t) | .id) // empty' 2>/dev/null)
if [ -n "$pane" ] && superset panes close --workspace "$ws" --pane "$pane"; then
  echo "  closed pane $pane"
  exit 0
fi
superset terminals close --workspace "$ws" --terminal "$term" --local
