#!/bin/zsh
# Off-peak repo wrap-up — launched by launchd (com.jake.wrapup-repos).
# Runs claude headless over the personal code dir. Safe to run manually to test.
set -u

ROOT="/Users/jake/.claude/automations/wrapup-repos"
# Personal machines only (OMEN, JXIV): this commits across the personal repos.
if [ -x "$HOME/dotfiles/bin/is-personal-machine" ]; then
  "$HOME/dotfiles/bin/is-personal-machine"
  if [ $? -eq 1 ]; then
    echo "wrapup-repos: work machine — runs on the personal machines only."
    exit 0
  fi
fi

# Where the personal repos live differs by machine; ai-tools' personal-code-dir
# decides (never ~/code, which holds work checkouts). Unresolved = nothing to do.
CODE_DIR="${PERSONAL_CODE_DIR:-$("${0:A:h:h:h}/bin/personal-code-dir" 2>/dev/null)}" || CODE_DIR=""
if [ -z "$CODE_DIR" ]; then
  echo "wrapup-repos: no personal code dir on this machine (set PERSONAL_CODE_DIR) — nothing to do."
  exit 0
fi
export PERSONAL_CODE_DIR="$CODE_DIR"
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
  echo "=== wrapup-repos run $STAMP ($(date)) ==="
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

# Refresh the dashboard (scans logs, (auto) commits, and .claude/IN_PROGRESS.md files).
python3 "$ROOT/gen-dashboard.py" >> "$LOG" 2>&1
