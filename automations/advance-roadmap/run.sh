#!/bin/zsh
# Scheduled roadmap advance — launched by launchd (com.jake.advance-roadmap).
#
# Architecture:
#   1. Refresh providers-usage.json (lib/usage.py — Claude/Codex/Cursor quota probes ∪ prior limits).
#   2. Read-only orchestrator: Codex Sol (high) default; Claude Opus (high) fallback.
#   3. Parse ORCHESTRATOR_RESULT_JSON; dispatch worker.sh (Cursor → Codex → Claude).
#   4. Record limit hits for the next tick.
#   5. Append exactly one row to runs.jsonl (lib/runrecord.py) — the run record the
#      dashboard, state.json, Slack and the failure alert all read. Built from exit
#      codes seen here and result files the agents write; never from the log text.
#
# Guardrails live in skills/advance-roadmap/SAFETY.md.
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
CODE_DIR="/Users/jake/Dropbox/code"
SKILL_DIR="${ADVANCE_ROADMAP_SKILL_DIR:-/Users/jake/.claude/skills/advance-roadmap}"
USAGE_PY="$ROOT/lib/usage.py"
RECORD_PY="$ROOT/lib/runrecord.py"
PLAN_PY="$ROOT/lib/pendingplan.py"
LINEAR_SNAP_PY="$ROOT/lib/linearsnap.py"
VERDICT_PY="$ROOT/lib/verdict.py"
LAST_VERDICT="${ADVANCE_ROADMAP_LAST_VERDICT:-$CODE_DIR/.advance-roadmap/last-verdict.json}"
# Provider-neutral: the plan belongs to the repos, not to whichever CLI orchestrates.
PENDING_PLAN="${ADVANCE_ROADMAP_PENDING_PLAN:-$CODE_DIR/.advance-roadmap/pending-plan.json}"
WORKER_SH="$ROOT/worker.sh"

CLAUDE="${ADVANCE_ROADMAP_CLAUDE_BIN:-/Users/jake/.local/bin/claude}"
CODEX="${ADVANCE_ROADMAP_CODEX_BIN:-/opt/homebrew/bin/codex}"

ORCH_CODEX_MODEL="${ADVANCE_ROADMAP_ORCH_CODEX_MODEL:-gpt-5.6-sol}"
ORCH_CLAUDE_MODEL="${ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL:-claude-opus-5}"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

LOGDIR="$ROOT/logs"
PROBE_FLAG="$ROOT/.dispatch-probe-done"
STATE_FILE="$ROOT/state.json"

# Secrets live outside this repo — the plist is committed, so a webhook URL must
# never go in it. launchd gives a bare environment, so ~/.zshrc is never read
# either; this file is the only thing that reaches a scheduled run.
SECRETS="${ADVANCE_ROADMAP_SECRETS:-$HOME/.claude/automations/secrets.env}"
[ -r "$SECRETS" ] && . "$SECRETS"

# Incoming webhook for the per-run summary. Unset = feature off, silently.
SLACK_WEBHOOK="${ADVANCE_ROADMAP_SLACK_WEBHOOK:-${SLACK_CCUSAGE_WEBHOOK_URL:-}}"

regen_dashboard() {
  [ -f "$ROOT/gen-dashboard.py" ] || return 0
  python3 "$ROOT/gen-dashboard.py" >> "$LOG" 2>&1
}

mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/run-$STAMP.log"
find "$LOGDIR" -name 'run-*.log' -mtime +30 -delete 2>/dev/null

# Per-run scratch the agents write into; runs.jsonl is the durable copy.
RUN_DIR="$ROOT/runs/$STAMP"
mkdir -p "$RUN_DIR"
find "$ROOT/runs" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} + 2>/dev/null
ORCH_RESULT="$RUN_DIR/orchestrator-result.json"
WORKER_RESULT="$RUN_DIR/worker-result.json"
WORKER_STATUS="$RUN_DIR/worker-status.json"

RECORDED=0
ORCH=""
orch_rc=""
record() {
  # record STATUS [DETAIL] [EXIT] — exactly once per run.
  [ "$RECORDED" -eq 0 ] || return 0
  RECORDED=1
  local -a extra=()
  [ -n "${3:-}" ] && extra+=(--exit "$3")
  [ -n "${orch_rc:-}" ] && extra+=(--orch-exit "$orch_rc")
  python3 "$RECORD_PY" --root "$ROOT" append --stamp "$STAMP" --status "$1" \
    --detail "${2:-}" "${extra[@]}" \
    --orch-provider "$ORCH" \
    --orch-result "$ORCH_RESULT" \
    --worker-status "$WORKER_STATUS" --worker-result "$WORKER_RESULT" >> "$LOG" 2>&1 || true
}
on_exit() {
  local rc=$?
  [ -n "${LOCK:-}" ] && [ "${HOLDS_LOCK:-0}" -eq 1 ] && rm -rf "$LOCK"
  [ "$RECORDED" -eq 1 ] && return
  record aborted "run.sh exited $rc before recording" "$rc"
  regen_dashboard
}
trap on_exit EXIT
trap 'exit 143' INT TERM

# ── Cadence backoff ───────────────────────────────────────────────────────────
# launchd always fires four times a day; backoff is enforced here instead, so the
# schedule never has to be rewritten. gen-dashboard.py owns the classification
# and writes cadence_hours to state.json: 6h normally, 12h after 4 consecutive
# blocked runs, 24h after 8. Any run that ships resets it.
#   6h  → every slot          (04:45 10:45 16:45 22:45)
#   12h → 04:45 and 16:45
#   24h → 04:45 only
CADENCE=6
if [ -r "$STATE_FILE" ] && command -v jq >/dev/null 2>&1; then
  CADENCE="$(jq -r '.cadence_hours // 6' "$STATE_FILE" 2>/dev/null || echo 6)"
fi
case "$CADENCE" in ''|*[!0-9]*) CADENCE=6 ;; esac
HOUR_NOW=$(date +%H)
run_this_tick=1
if [ "$CADENCE" -ge 24 ]; then
  [ "$HOUR_NOW" = "04" ] || run_this_tick=0
elif [ "$CADENCE" -ge 12 ]; then
  case "$HOUR_NOW" in 04|16) ;; *) run_this_tick=0 ;; esac
fi
if [ "$run_this_tick" -eq 0 ]; then
  streak="$(jq -r '.blocked_streak // 0' "$STATE_FILE" 2>/dev/null || echo '?')"
  echo "=== advance-roadmap $STAMP: skipping — backed off to every ${CADENCE}h after ${streak} blocked runs ===" >> "$LOG"
  record skipped-backoff "cadence ${CADENCE}h after ${streak} blocked runs"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi

if [ ! -f "$USAGE_PY" ]; then
  echo "=== advance-roadmap $STAMP: FATAL missing $USAGE_PY ===" >> "$LOG"
  record aborted "missing $USAGE_PY" 1
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 1
fi

python3 "$USAGE_PY" --root "$ROOT" refresh >> "$LOG" 2>&1 || true

ORCH="$(python3 "$USAGE_PY" --root "$ROOT" pick-orchestrator 2>/dev/null | tr -d '\r')"
orch_pick_rc=$?
if [ "$orch_pick_rc" -ne 0 ] || [ -z "$ORCH" ] || [ "$ORCH" = "none" ]; then
  echo "=== advance-roadmap $STAMP: skipping — no orchestrator available ===" >> "$LOG"
  ORCH=""
  record skipped-quota "no orchestrator available"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi

WORKERS="$(python3 "$USAGE_PY" --root "$ROOT" pick-worker-chain 2>/dev/null | tr -d '\r' || true)"

LOCK="$ROOT/run.lock"
if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin +240 2>/dev/null)" ]; then
  echo "=== advance-roadmap $STAMP: another run holds $LOCK — skipping ===" >> "$LOG"
  record skipped-lock "another run held the lock"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi
rm -rf "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || {
  echo "=== advance-roadmap $STAMP: could not take lock — skipping ===" >> "$LOG"
  record skipped-lock "could not take lock"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
}
HOLDS_LOCK=1

extract_json_fence() {
  # extract_json_fence LABEL infile outfile
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
label, infile, outfile = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(infile, encoding="utf-8", errors="replace").read()
start = f"```{label}"
# Codex echoes the prompt and the ORCHESTRATOR.md it reads into stdout, and both
# contain this label — so take the last fence whose body actually parses.
end = len(text)
while (i := text.rfind(start, 0, end)) >= 0:
    end = i
    nl = text.find("\n", i)
    j = text.find("```", nl + 1) if nl >= 0 else -1
    if j < 0:
        continue
    body = text[nl + 1:j].strip()
    try:
        json.loads(body)
    except ValueError:
        continue
    open(outfile, "w", encoding="utf-8").write(body + "\n")
    sys.exit(0)
sys.exit(1)
PY
}

record_limit_from_log() {
  local provider="$1" logfile="$2"
  # Only treat as a live CLI limit if the message looks like a banner, not prose
  # inside ORCHESTRATOR.md / prior logs the model may have read (those contain
  # the phrase "session limit" and were falsely tripping same-run failover).
  local text
  text="$(rg -m1 -i \
    '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
    "$logfile" || true)"
  [ -n "$text" ] || return 1
  python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$provider" --text "$text" || true
  return 0
}

run_orchestrator_codex() {
  local out="$1" prompt="$2"
  # -o writes only the final message, so the fence can't be confused with the
  # prompt or ORCHESTRATOR.md that Codex echoes into stdout.
  rm -f "$out.last"
  # CODE_DIR is a multi-repo parent, not a git checkout — skip the repo check.
  "$CODEX" exec \
    -o "$out.last" \
    -m "$ORCH_CODEX_MODEL" \
    -c model_reasoning_effort=high \
    -s read-only \
    -C "$CODE_DIR" \
    --add-dir "$CODE_DIR" \
    --skip-git-repo-check \
    "$prompt" >"$out" 2>&1
}

run_orchestrator_claude() {
  local out="$1" prompt="$2"
  "$CLAUDE" -p "$prompt" \
    --model "$ORCH_CLAUDE_MODEL" \
    --effort high \
    --permission-mode plan \
    --permission-prompts none \
    --disallowedTools "Edit,Write,NotebookEdit" \
    --add-dir "$CODE_DIR" \
    --output-format text >"$out" 2>&1
}

# ── Unchanged-world gate ──────────────────────────────────────────────────────
# Linear goes through the `linear` CLI only. Its key is in the keychain, which the
# read-only Codex sandbox can't read — so snapshot the queue here and hand over the file.
LINEAR_SNAPSHOT="$RUN_DIR/linear-snapshot.json"
FINGERPRINT="$RUN_DIR/fingerprint.json"
VERDICT_CHANGES="$RUN_DIR/verdict-changes.json"
python3 "$LINEAR_SNAP_PY" --out "$LINEAR_SNAPSHOT" >> "$LOG" 2>&1
python3 "$VERDICT_PY" fingerprint --snapshot "$LINEAR_SNAPSHOT" --out "$FINGERPRINT" >> "$LOG" 2>&1
# The last run found no work and its bookkeeping landed. If no repo, issue, allowlist or skill
# doc has moved since, this run would re-derive the same verdict — skip planning and worker both.
# A pending plan always runs: it is work, not a verdict.
if [ ! -f "$PENDING_PLAN" ] && python3 "$VERDICT_PY" check --file "$LAST_VERDICT" \
     --fingerprint "$FINGERPRINT" --changes "$VERDICT_CHANGES" > "$RUN_DIR/verdict-check.txt" 2>&1; then
  echo "=== advance-roadmap $STAMP: skipping — $(cat "$RUN_DIR/verdict-check.txt") ===" >> "$LOG"
  record skipped-unchanged "$(cat "$RUN_DIR/verdict-check.txt")"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi

{
  echo "=== advance-roadmap run $STAMP ($(date)) ==="
  cat "$RUN_DIR/verdict-check.txt" 2>/dev/null
  echo "orchestrator=$ORCH  workers=${WORKERS:-none}  cwd=$CODE_DIR"
  cd "$CODE_DIR" || { echo "FATAL: cannot cd to $CODE_DIR"; exit 1; }
  [ -f "$SKILL_DIR/ORCHESTRATOR.md" ] || { echo "FATAL: missing ORCHESTRATOR.md"; exit 1; }
  [ -f "$SKILL_DIR/SAFETY.md" ] || { echo "FATAL: missing SAFETY.md"; exit 1; }

  ORCH_PROMPT="Read and follow $SKILL_DIR/ORCHESTRATOR.md and $SKILL_DIR/SAFETY.md exactly.

This is an unattended scheduled ORCHESTRATOR run (stamp=$STAMP). You are READ-ONLY.
Emit one \`\`\`ORCHESTRATOR_RESULT_JSON fence per ORCHESTRATOR.md. Do not implement.

providers-usage.json (consume only): $ROOT/providers-usage.json"

  if [ ! -f "$PROBE_FLAG" ]; then
    echo "(one-time Dispatch probe armed — flag absent: $PROBE_FLAG)"
    ORCH_PROMPT="$ORCH_PROMPT

If a PushNotification probe would have been useful, set a note in summary; the worker may attempt it. Do not call PushNotification yourself."
  fi

  result="$(mktemp "${TMPDIR:-/tmp}/advance-roadmap-result.XXXXXX")"
  req="$(mktemp "${TMPDIR:-/tmp}/advance-roadmap-req.XXXXXX")"
  # A plan whose worker died last run is handed straight to a worker — no second planning pass.
  reused=0
  if python3 "$PLAN_PY" --file "$PENDING_PLAN" reuse --out "$result"; then
    reused=1
    orch_rc=0
    ORCH=pending-plan  # the run record shows no planning pass ran
  else
    orch_out="$RUN_DIR/orchestrator-stdout.txt"  # kept, so a killed planning pass leaves a trace
    ORCH_PROMPT="$ORCH_PROMPT

Linear snapshot (read this; never call Linear yourself): $LINEAR_SNAPSHOT"
    if [ -s "$VERDICT_CHANGES" ]; then
      ORCH_PROMPT="$ORCH_PROMPT
Previous no-work verdict and what changed since (carry unchanged verdicts forward): $VERDICT_CHANGES"
    fi
    orch_rc=0
    case "$ORCH" in
      codex)
        command -v "$CODEX" >/dev/null 2>&1 || { echo "FATAL: codex not found"; exit 1; }
        run_orchestrator_codex "$orch_out" "$ORCH_PROMPT" || orch_rc=$?
        ;;
      claude)
        [ -x "$CLAUDE" ] || { echo "FATAL: claude not found"; exit 1; }
        run_orchestrator_claude "$orch_out" "$ORCH_PROMPT" || orch_rc=$?
        ;;
      *) echo "FATAL: unknown orchestrator $ORCH"; exit 1 ;;
    esac

    cat "$orch_out"
    echo ""
    echo "=== orchestrator provider=$ORCH exit=$orch_rc ==="

    # Limit failover only when the orchestrator failed to produce a result JSON.
    # Successful triage dumps skill text that mentions "session limit" historically
    # and must not burn a second orchestrator turn.
    had_json=0
    if [ -s "$orch_out.last" ] && extract_json_fence ORCHESTRATOR_RESULT_JSON "$orch_out.last" "$result"; then
      had_json=1
    elif extract_json_fence ORCHESTRATOR_RESULT_JSON "$orch_out" "$result"; then
      had_json=1
    fi

    if [ "$had_json" -eq 0 ] && record_limit_from_log "$ORCH" "$orch_out"; then
      echo "(orchestrator limit recorded — attempting one same-run failover)"
      alt=""
      case "$ORCH" in codex) alt=claude ;; claude) alt=codex ;; esac
      next="$(python3 "$USAGE_PY" --root "$ROOT" pick-orchestrator 2>/dev/null | tr -d '\r' || true)"
      if [ -n "$alt" ] && [ "$next" = "$alt" ]; then
        ORCH="$alt"
        orch_rc=0
        case "$ORCH" in
          codex) run_orchestrator_codex "$orch_out" "$ORCH_PROMPT" || orch_rc=$? ;;
          claude) run_orchestrator_claude "$orch_out" "$ORCH_PROMPT" || orch_rc=$? ;;
        esac
        cat "$orch_out"
        echo "=== orchestrator provider=$ORCH exit=$orch_rc ==="
        if [ -s "$orch_out.last" ] && extract_json_fence ORCHESTRATOR_RESULT_JSON "$orch_out.last" "$result"; then
          had_json=1
        elif extract_json_fence ORCHESTRATOR_RESULT_JSON "$orch_out" "$result"; then
          had_json=1
        else
          record_limit_from_log "$ORCH" "$orch_out" || true
        fi
      fi
    fi

    if [ "$had_json" -eq 0 ]; then
      echo "WARNING: missing ORCHESTRATOR_RESULT_JSON — wrapping stdout as blocked_no_item"
      python3 - "$orch_out" "$result" <<'PY'
import json, sys
summary = open(sys.argv[1], encoding="utf-8", errors="replace").read()[-8000:]
json.dump({
  "action": "blocked_no_item",
  "outcome_token": "blocked-no-item",
  "repo": None, "item": None, "branch": None,
  "roadmap_path": None, "linear_id": None, "directive_ticket": None,
  "worker_brief": None, "archives": [], "blockers": [],
  "considered": [], "summary": summary,
  "parse_error": "missing ORCHESTRATOR_RESULT_JSON",
}, open(sys.argv[2], "w"), indent=2)
open(sys.argv[2], "a").write("\n")
PY
    fi

  fi

  echo "(orchestrator result)"
  cat "$result"
  cp "$result" "$ORCH_RESULT"
  [ "$reused" -eq 1 ] || python3 "$PLAN_PY" --file "$PENDING_PLAN" save --result "$result" --stamp "$STAMP"

  action="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("action",""))' "$result")"
  case "$action" in
    dispatch_worker|resume_worker)
      python3 - "$result" "$req" "$STAMP" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
req = {
  "mode": "implement" if r.get("action") == "dispatch_worker" else "resume",
  "stamp": sys.argv[3],
  "orchestrator": r,
}
json.dump(req, open(sys.argv[2], "w"), indent=2)
open(sys.argv[2], "a").write("\n")
PY
      ;;
    *)
      python3 - "$result" "$req" "$STAMP" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
req = {"mode": "bookkeeping", "stamp": sys.argv[3], "orchestrator": r}
json.dump(req, open(sys.argv[2], "w"), indent=2)
open(sys.argv[2], "a").write("\n")
PY
      ;;
  esac

  worker_rc=0
  chmod +x "$WORKER_SH" 2>/dev/null || true
  if [ -z "${WORKERS:-}" ] && [ "$action" = "dispatch_worker" -o "$action" = "resume_worker" ]; then
    echo "WARNING: no workers available — cannot implement; recording via bookkeeping if possible"
    python3 - "$result" "$req" "$STAMP" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
r["summary"] = (r.get("summary") or "") + "\n(no workers available this tick)"
req = {"mode": "bookkeeping", "stamp": sys.argv[3], "orchestrator": r}
json.dump(req, open(sys.argv[2], "w"), indent=2)
open(sys.argv[2], "a").write("\n")
PY
  fi

  "$WORKER_SH" --stamp "$STAMP" --request-file "$req" --providers "${WORKERS:-}" \
    --result-file "$WORKER_RESULT" --status-file "$WORKER_STATUS" || worker_rc=$?
  echo "=== worker exit=$worker_rc ==="
  python3 "$PLAN_PY" --file "$PENDING_PLAN" settle --worker-result "$WORKER_RESULT" --worker-rc "$worker_rc"
  python3 "$VERDICT_PY" save --file "$LAST_VERDICT" --fingerprint "$FINGERPRINT" \
    --orch-result "$ORCH_RESULT" --worker-result "$WORKER_RESULT" --stamp "$STAMP"

  [ -f "$PROBE_FLAG" ] || { touch "$PROBE_FLAG"; echo "(Dispatch probe flag set)"; }

  final_rc=0
  [ "$orch_rc" -eq 0 ] || final_rc=$orch_rc
  [ "$worker_rc" -eq 0 ] || final_rc=$worker_rc
  rm -f "$result" "$req"
  echo "=== advance-roadmap exit=$final_rc finished $(date) ==="
} >> "$LOG" 2>&1
# zsh runs a redirected { } in this shell, so final_rc is still set here.
record ran "" "${final_rc:-1}"

ln -sf "$LOG" "$LOGDIR/latest.log"
regen_dashboard

# ── Slack summary ─────────────────────────────────────────────────────────────
# One short line per run. state.json was just rewritten by regen_dashboard, so it
# describes THIS run. No webhook configured = no-op, not an error.
if [ -n "$SLACK_WEBHOOK" ] && [ -r "$STATE_FILE" ] && command -v jq >/dev/null 2>&1; then
  summary="$(jq -r '.last_summary // ""' "$STATE_FILE" 2>/dev/null)"
  cadence="$(jq -r '.cadence_hours // 6' "$STATE_FILE" 2>/dev/null)"
  [ -n "$summary" ] && {
    text="*advance-roadmap* $(date '+%a %H:%M') — ${summary}"
    [ "$cadence" != "6" ] && text="$text  _(backed off to every ${cadence}h)_"
    payload="$(jq -n --arg t "$text" '{text:$t}')"
    if curl -sf -m 15 -X POST -H 'Content-Type: application/json' -d "$payload" "$SLACK_WEBHOOK" >/dev/null; then
      echo "=== slack: summary posted ===" >> "$LOG"
    else
      echo "=== slack: post failed (non-fatal) ===" >> "$LOG"
    fi
  }
fi

# ── Failure alert ─────────────────────────────────────────────────────────────
# A routine summary line saying "error" went unnoticed for 4.5 days (Sep 20–24).
# Consecutive failures get a louder, separate message plus a local notification.
alert="$(python3 "$RECORD_PY" --root "$ROOT" alert 2>>"$LOG" || true)"
if [ -n "$alert" ]; then
  echo "=== alert: $alert ===" >> "$LOG"
  [ "${ADVANCE_ROADMAP_NOTIFY:-1}" = "1" ] && osascript -e "display notification $(jq -rn --arg t "$alert" '$t|@json') with title \"advance-roadmap\" sound name \"Basso\"" >/dev/null 2>&1 || true
  if [ -n "$SLACK_WEBHOOK" ] && command -v jq >/dev/null 2>&1; then
    curl -sf -m 15 -X POST -H 'Content-Type: application/json' \
      -d "$(jq -n --arg t "$alert" '{text:$t}')" "$SLACK_WEBHOOK" >/dev/null \
      || echo "=== slack: alert post failed (non-fatal) ===" >> "$LOG"
  fi
fi
