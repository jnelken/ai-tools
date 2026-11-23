#!/bin/zsh
# Weekly Decisions audit for THIS Mac (memex only sees local sessions; SSH between the Macs does not work).
# launchd fires it Mon and Tue 9:00; the ISO-week marker makes Tue a retry only. `run.sh --force` ignores the marker.
# Runtime files: ~/.claude/automations/decisions-audit/{logs,reports,state,cwd}. Reports are the run's stdout.
set -u
SRC="${0:A:h}"
ROOT="$HOME/.claude/automations/decisions-audit"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0   # headless -p otherwise kills the workflow after 10 minutes
CLAUDE="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
WEEK="$(date +%G-W%V)"; TODAY="$(date +%F)"; HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
MARKER="$ROOT/state/$WEEK.done"
mkdir -p "$ROOT/state" "$ROOT/logs" "$ROOT/reports" "$ROOT/cwd"
[ "${1:-}" = "--force" ] || { [ -e "$MARKER" ] && exit 0; }
LOG="$ROOT/logs/run-$TODAY.log"
notify_fail() { osascript -e "display notification \"Decisions audit failed on $HOST; see logs/run-$TODAY.log\" with title \"Weekly audit\"" 2>/dev/null || true; }
find "$ROOT/logs" -name 'run-*.log' -mtime +90 -delete 2>/dev/null
{
  echo "=== decisions-audit $TODAY on $HOST ($(date)) ==="
  [ -x "$CLAUDE" ] || { echo "FATAL: claude not found at $CLAUDE"; notify_fail; exit 1; }
  PROMPT="$(sed -e "s|__SRC__|$SRC|g" -e "s|__HOST__|$HOST|g" "$SRC/prompt.md")"
  OUT="$(cd "$ROOT/cwd" && "$CLAUDE" -p "$PROMPT" --model claude-sonnet-5-5 --permission-mode dontAsk \
    --allowedTools "Workflow Read Bash(date *) Bash(memex *) Bash(ls *) Bash(grep *) mcp__memex__sessions mcp__memex__search mcp__memex__session mcp__memex__show mcp__memex__context mcp__memex__hydrate Read(//$HOME/.claude/projects/**)" \
    --add-dir "$HOME/.claude/projects" --add-dir "$SRC" --output-format text < /dev/null)"
  rc=$?
  printf '%s\n' "$OUT" > "$ROOT/reports/$TODAY.md"
  echo "=== claude exit=$rc finished $(date) ==="
  if [ "$rc" -eq 0 ] && printf '%s\n' "$OUT" | grep -q '^AUTOMATION_RESULT: ok'; then
    : > "$MARKER"
    osascript -e 'display notification "Decisions audit report is ready" with title "Weekly audit"' 2>/dev/null || true
    echo "=== ok ==="
  else
    echo "=== NOT ok; no marker, next trigger retries ==="; notify_fail; exit 1
  fi
} >> "$LOG" 2>&1
