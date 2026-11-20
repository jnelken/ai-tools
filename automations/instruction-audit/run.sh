#!/bin/zsh
# Monthly instruction-overhead audit — launched by launchd (com.jake.instruction-audit),
# optionally also by a Superset automation. Report-only: the headless claude run gets
# Read/Grep/Glob and nothing else. Idempotent per month, so extra triggers are harmless.
#
#   run.sh           run unless this month's report already exists
#   run.sh --force   re-run and overwrite this month's report
set -u

ROOT="$HOME/.claude/automations/instruction-audit"
SRC="${0:A:h}"                       # :A resolves the symlink back into the ai-tools clone
CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
MODEL="${INSTRUCTION_AUDIT_MODEL:-claude-sonnet-5-5}"
MONTH="$(date +%Y-%m)"

# launchd gives a bare environment — set an explicit PATH so python3/git/node resolve.
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
export CLAUDE_BIN="$CLAUDE"

mkdir -p "$ROOT/logs" "$ROOT/reports" "$ROOT/snapshots" "$ROOT/probe-cwd"
REPORT="$ROOT/reports/$MONTH.md"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
if [ -s "$REPORT" ] && [ "$FORCE" -eq 0 ]; then
  exit 0                              # already done this month; launchd fires on days 1-7 as a safety net
fi

LOG="$ROOT/logs/run-$(date +%Y%m%d-%H%M%S).log"
find "$ROOT/logs" -name 'run-*.log' -mtime +90 -delete 2>/dev/null
ln -sf "$LOG" "$ROOT/logs/latest.log"

{
  echo "=== instruction-audit $MONTH ($(date)) model=$MODEL ==="
  [ -x "$CLAUDE" ] || { echo "FATAL: claude not found at $CLAUDE"; exit 1; }

  MEAS="$(python3 "$SRC/audit.py" --root "$ROOT" --month "$MONTH" --model "$MODEL")" \
    || { echo "FATAL: audit.py failed"; exit 1; }
  echo "measurements: $MEAS"

  SNAP="$ROOT/snapshots/$MONTH.json"
  PREV="$(ls "$ROOT"/snapshots/*.json 2>/dev/null | grep -v "/$MONTH.json" | tail -1)"

  ANALYSIS="$(cd "$ROOT/probe-cwd" && "$CLAUDE" -p \
    "$(cat "$SRC/analysis-prompt.md")

Measurement table: $MEAS
This month's snapshot: $SNAP
Last month's snapshot: ${PREV:-none (this is the baseline)}" \
    --model "$MODEL" \
    --allowedTools "Read,Grep,Glob" \
    --disallowedTools "Bash,Edit,Write,NotebookEdit" \
    --add-dir "$HOME/.claude" --add-dir "$HOME/.ai-tools" --add-dir "$ROOT" \
    --output-format text)"
  rc=$?

  if [ "$rc" -ne 0 ] || [ -z "$ANALYSIS" ]; then
    echo "analysis failed (exit=$rc); measurements kept at $MEAS, no report written so a retry re-runs"
    exit 1
  fi

  { cat "$MEAS"; echo; echo "# Analysis"; echo; echo "$ANALYSIS"; } > "$REPORT"
  echo "report: $REPORT"
  osascript -e "display notification \"Report: $MONTH.md\" with title \"Instruction audit\"" 2>/dev/null || true
  echo "=== finished $(date) ==="
} >> "$LOG" 2>&1
