#!/bin/sh
# Close the Superset tab a /self-destruct session runs in, without touching the
# workspace (the root-checkout path; formerly the /done skill). Launch detached,
# as the last tool call of the turn:
#
#   nohup sh ~/.claude/skills/self-destruct/scripts/close-tab.sh "$SUPERSET_WORKSPACE_ID" "$SUPERSET_TERMINAL_ID" "$PPID" >/dev/null 2>&1 &
#
# The third argument is the Claude process (the tool shell's parent). Tool
# shells have no controlling tty of their own, so its tty is how this script
# finds the terminal to hang up.
#
# Order of attempts:
# 1. `panes close`: removes the tab from the desktop layout. Needs a desktop
#    app attached to this host; a remote app (jake2's tabs viewed from the
#    MacBook) answers "no desktop app is attached".
# 2. Hang up the terminal's own tty, so Claude and the login shell exit the way
#    they would if the tab were closed by hand, and the PTY ends naturally. This
#    script survives the hangup because nohup ignores SIGHUP.
# 3. `terminals close`: disposes the terminal from the host side, if it is still
#    listed. A remote desktop app keeps a dead tab after this one.
ws=$1 term=$2 owner=$3
[ -n "$ws" ] && [ -n "$term" ] || { echo "usage: close-tab.sh WS_ID TERMINAL_ID [CLAUDE_PID]" >&2; exit 2; }
log=${SELF_DESTRUCT_LOG:-$HOME/.claude/state/self-destruct.log}
mkdir -p "$(dirname "$log")"
tty=$( [ -n "$owner" ] && ps -o tty= -p "$owner" 2>/dev/null | tr -d ' ')
exec >>"$log" 2>&1
cd / || exit 1

listed() {
  superset terminals list --workspace "$ws" --local --json 2>/dev/null |
    jq -e --arg t "$term" 'any(.sessions[]; .terminalId == $t and .exited != true)' >/dev/null 2>&1
}

# Let the final message render and the turn end first.
sleep "${SELF_DESTRUCT_DELAY:-2}"
echo "$(date '+%F %T') close terminal $term (tty ${tty:-none})"

pane=$(superset panes list --workspace "$ws" --json 2>/dev/null |
  jq -r --arg t "$term" 'first(.. | objects | select(.terminalId? == $t) | .id) // empty' 2>/dev/null)
if [ -n "$pane" ] && superset panes close --workspace "$ws" --pane "$pane"; then
  echo "  closed pane $pane"
  exit 0
fi

case $tty in
  ttys*)
    # BSD pkill -t silently matches nothing for ttysNNN names; signal the pids.
    kill -HUP $(ps -t "$tty" -o pid=) 2>/dev/null
    for _ in 1 2 3 4 5; do
      sleep 1
      listed || { echo "  hung up $tty; terminal exited"; exit 0; }
    done
    ;;
esac

superset terminals close --workspace "$ws" --terminal "$term" --local
