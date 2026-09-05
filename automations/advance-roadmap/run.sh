#!/bin/zsh
# Nightly roadmap advance — launched by launchd (com.jake.advance-roadmap).
# Runs claude headless over ~/Dropbox/code. Safe to run manually to test.
#
# Unlike the wrapup-repos sibling, this job PUSHES `main` on personal repos.
# The guardrails that make that safe (jnelken-only origins, clean-tree
# requirement, no-force rule, all-or-nothing verification) live in the skill,
# not here — see skills/advance-roadmap/SKILL.md.
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
CODE_DIR="/Users/jake/Dropbox/code"
CLAUDE="${ADVANCE_ROADMAP_CLAUDE_BIN:-/Users/jake/.local/bin/claude}"
MODEL="claude-opus-5"            # this job writes and tests real features — worth the quota
MAX_SEVEN_DAY_PCT=80             # skip the run above this weekly usage — the scarce budget
MAX_FIVE_HOUR_PCT=70             # skip the run above this 5-hour usage

# launchd gives a bare environment — set an explicit PATH so git/node/npm resolve.
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

LOGDIR="$ROOT/logs"
PROBE_FLAG="$ROOT/.dispatch-probe-done"   # rm to re-arm the one-time PushNotification test
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/run-$STAMP.log"

# Prune logs older than 30 days.
find "$LOGDIR" -name 'run-*.log' -mtime +30 -delete 2>/dev/null

# ── Quota gate ────────────────────────────────────────────────────────────────
# ~/.claude/state/claude-usage.json is written by the statusline
# (statusline/awesome-statusline.sh, "for agent access") from the official
# rate_limits block Claude Code passes it. An Opus run every 6h can eat a weekly
# budget quietly, so sit out when usage is already high.
#
# Staleness: only an INTERACTIVE session refreshes that file — a headless run
# like this one never does. So don't trust the percentage on its own; each window
# carries a reset_epoch, and once that's in the past the window has rolled over
# and the cached number is meaningless. Treat those as 0 rather than guessing
# from file mtime. Missing or unparseable file means run anyway: it goes stale
# precisely when Claude ISN'T being used, which is when quota is most likely fine.
USAGE_FILE="${ADVANCE_ROADMAP_USAGE_FILE:-$HOME/.claude/state/claude-usage.json}"
skip_reason=""
if [ -r "$USAGE_FILE" ] && command -v jq >/dev/null 2>&1; then
  now=$(date +%s)
  read -r f5 r5 f7 r7 <<<"$(jq -r '[
      (.five_hour.used_percentage // 0), (.five_hour.reset_epoch // 0),
      (.seven_day.used_percentage // 0), (.seven_day.reset_epoch // 0)
    ] | @tsv' "$USAGE_FILE" 2>/dev/null | tr '\t' ' ')"
  # A window past its reset has rolled over — its cached percentage is stale.
  [ -n "${r5:-}" ] && [ "${r5:-0}" -gt 0 ] && [ "$r5" -lt "$now" ] && f5=0
  [ -n "${r7:-}" ] && [ "${r7:-0}" -gt 0 ] && [ "$r7" -lt "$now" ] && f7=0
  if [ "${f7:-0}" -ge "$MAX_SEVEN_DAY_PCT" ]; then
    skip_reason="7-day usage ${f7}% >= ${MAX_SEVEN_DAY_PCT}%"
  elif [ "${f5:-0}" -ge "$MAX_FIVE_HOUR_PCT" ]; then
    skip_reason="5-hour usage ${f5}% >= ${MAX_FIVE_HOUR_PCT}%"
  fi
fi
if [ -n "$skip_reason" ]; then
  echo "=== advance-roadmap $STAMP: skipping — $skip_reason ===" >> "$LOG"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  exit 0
fi

# Single-instance lock. A run that branches, builds and pushes must never overlap
# another one — two concurrent runs would fight over the same repo's `main`.
# mkdir is atomic; a lock older than 4h is assumed stale (crashed run) and reclaimed.
LOCK="$ROOT/run.lock"
if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin +240 2>/dev/null)" ]; then
  echo "=== advance-roadmap $STAMP: another run holds $LOCK — skipping ===" >> "$LOG"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  exit 0
fi
rm -rf "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { echo "=== advance-roadmap $STAMP: could not take lock — skipping ===" >> "$LOG"; exit 0; }
trap 'rm -rf "$LOCK"' EXIT INT TERM

{
  echo "=== advance-roadmap run $STAMP ($(date)) ==="
  echo "model=$MODEL  cwd=$CODE_DIR"
  cd "$CODE_DIR" || { echo "FATAL: cannot cd to $CODE_DIR"; exit 1; }
  [ -x "$CLAUDE" ] || { echo "FATAL: claude not found at $CLAUDE"; exit 1; }

  # Single source of truth = the advance-roadmap skill. Reference it explicitly so the
  # headless run follows the exact same workflow as the on-demand /advance-roadmap.
  PROMPT="Read and follow /Users/jake/.claude/skills/advance-roadmap/SKILL.md exactly. This is an unattended scheduled run: ship ONE roadmap item now per that workflow, then print your final summary. If no repo qualifies, say so and stop — do not substitute other work."

  # One-time Dispatch connectivity probe. PushNotification is suppressed whenever an
  # interactive Claude terminal is active, so it could never be proven from a hands-on
  # test — only a real unattended run at 4:45am can. Step 2b already notifies on blocked
  # runs, but that may not happen for days, so the first scheduled run attempts it
  # regardless of outcome and records the verdict. Self-disabling: delete the flag file
  # to re-arm it.
  if [ ! -f "$PROBE_FLAG" ]; then
    echo "(one-time Dispatch probe armed — flag absent: $PROBE_FLAG)"
    PROMPT="$PROMPT

ONE-TIME CONNECTIVITY PROBE, this run only: whatever the outcome above — shipped, blocked, archived, or nothing to do — call PushNotification exactly once with status \"proactive\" and a message under 200 characters summarising that outcome, prefixed 'advance-roadmap:'. If the workflow already sent a notification this run (Step 2b), do NOT send a second — reuse that result. Then print the tool's verbatim result on its own final line, prefixed 'PUSH_PROBE: '."
  fi

  "$CLAUDE" -p "$PROMPT" \
    --model "$MODEL" \
    --dangerously-skip-permissions \
    --add-dir "$CODE_DIR" \
    --output-format text
  rc=$?

  # Burn the probe only once claude actually ran, so a failed launch keeps it armed.
  [ -f "$PROBE_FLAG" ] || { touch "$PROBE_FLAG"; echo "(Dispatch probe fired — disarmed for future runs)"; }
  echo ""
  echo "=== claude exit=$rc  finished $(date) ==="
} >> "$LOG" 2>&1

# Keep a stable pointer to the latest log for easy checking.
ln -sf "$LOG" "$LOGDIR/latest.log"
