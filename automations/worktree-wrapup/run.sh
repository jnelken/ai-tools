#!/bin/zsh
# Off-peak repo wrap-up — launched by launchd (com.jake.worktree-wrapup).
# Runs claude headless over ~/Dropbox/code. Safe to run manually to test.
set -u

ROOT="/Users/jake/.claude/automations/worktree-wrapup"
CODE_DIR="/Users/jake/Dropbox/code"
CLAUDE="/Users/jake/.local/bin/claude"
MODEL="claude-sonnet-5"          # change to claude-opus-4-8 for max quality (higher quota cost)

# launchd gives a bare environment — set an explicit PATH so git/node/npm resolve.
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/run-$STAMP.log"

# Prune logs older than 30 days.
find "$LOGDIR" -name 'run-*.log' -mtime +30 -delete 2>/dev/null

{
  echo "=== worktree-wrapup run $STAMP ($(date)) ==="
  echo "model=$MODEL  cwd=$CODE_DIR"
  cd "$CODE_DIR" || { echo "FATAL: cannot cd to $CODE_DIR"; exit 1; }
  [ -x "$CLAUDE" ] || { echo "FATAL: claude not found at $CLAUDE"; exit 1; }

  # Single source of truth = the wrapup-repos skill. Reference it explicitly so the
  # headless run follows the exact same workflow as the on-demand /wrapup-repos.
  "$CLAUDE" -p "Read and follow /Users/jake/.claude/skills/wrapup-repos/SKILL.md exactly. This is an unattended scheduled run: wrap up ONE repo now per that workflow, then print your final summary." \
    --model "$MODEL" \
    --dangerously-skip-permissions \
    --add-dir "$CODE_DIR" \
    --output-format text
  rc=$?
  echo ""
  echo "=== claude exit=$rc  finished $(date) ==="
} >> "$LOG" 2>&1

# Keep a stable pointer to the latest log for easy checking.
ln -sf "$LOG" "$LOGDIR/latest.log"
