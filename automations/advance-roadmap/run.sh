#!/bin/zsh
# Scheduled roadmap advance — launched by launchd (com.jake.advance-roadmap).
#
# Architecture:
#   1. Refresh providers-usage.json (lib/usage.py — Claude/Codex/Cursor quota probes ∪ prior limits).
#   2. Read-only orchestrator: Codex Sol (high) default; Claude Opus (high) fallback.
#   3. Parse ORCHESTRATOR_RESULT_JSON; dispatch worker.sh (Cursor → Codex → Claude) in the
#      item's own Superset workspace terminal, so the PR it merges is credited (lib/workspaces.py).
#   4. Record limit hits for the next tick.
#   5. Append exactly one row to runs.jsonl (lib/runrecord.py) — the run record the
#      dashboard, state.json, Slack and the failure alert all read. Built from exit
#      codes seen here and result files the agents write; never from the log text.
#   6. Chain: if this run shipped a feature and the chain is under $CHAIN_MINUTES old, re-exec
#      for another item. Each link is a full run with its own stamp, log and row.
#
# Guardrails live in skills/advance-roadmap/SAFETY.md.
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
# Personal machines only (OMEN, JXIV): this commits across the personal repos.
if [ -x "$HOME/dotfiles/bin/is-personal-machine" ]; then
  "$HOME/dotfiles/bin/is-personal-machine"
  if [ $? -eq 1 ]; then
    echo "advance-roadmap: work machine — runs on the personal machines only."
    exit 0
  fi
fi

# Where the personal repos live differs by machine; ai-tools' personal-code-dir
# decides (never ~/code, which holds work checkouts). Unresolved = nothing to do.
CODE_DIR="${PERSONAL_CODE_DIR:-$("${0:A:h:h:h}/bin/personal-code-dir" 2>/dev/null)}" || CODE_DIR=""
if [ -z "$CODE_DIR" ]; then
  echo "advance-roadmap: no personal code dir on this machine (set PERSONAL_CODE_DIR) — nothing to do."
  exit 0
fi
# Children (worker.sh, lib/*.py) resolve the same dir without re-deciding.
export PERSONAL_CODE_DIR="$CODE_DIR"
SKILL_DIR="${ADVANCE_ROADMAP_SKILL_DIR:-/Users/jake/.claude/skills/advance-roadmap}"
# ORCHESTRATOR.md lives in the conductor skill (skills/conductor); everything else stays in SKILL_DIR.
CONDUCTOR_DIR="${ADVANCE_ROADMAP_CONDUCTOR_DIR:-$(dirname "$SKILL_DIR")/conductor}"
USAGE_PY="$ROOT/lib/usage.py"
RECORD_PY="$ROOT/lib/runrecord.py"
PLAN_PY="$ROOT/lib/pendingplan.py"
TOKENS_PY="$ROOT/lib/tokens.py"
LINEAR_SNAP_PY="$ROOT/lib/linearsnap.py"
VERDICT_PY="$ROOT/lib/verdict.py"
LAST_VERDICT="${ADVANCE_ROADMAP_LAST_VERDICT:-$CODE_DIR/.advance-roadmap/last-verdict.json}"
# Provider-neutral: the plan belongs to the repos, not to whichever CLI orchestrates.
PENDING_PLAN="${ADVANCE_ROADMAP_PENDING_PLAN:-$CODE_DIR/.advance-roadmap/pending-plan.json}"
WORKER_SH="$ROOT/worker.sh"
WORKSPACES_PY="$ROOT/lib/workspaces.py"
# 1 = each dispatched item runs in its own Superset workspace and ships as a merged PR
# (the only output the Production Run leaderboard credits). 0 = run worker.sh in-process,
# for tests. With 1 and the Superset app down, a dispatch is skipped and its plan kept.
SUPERSET_MODE="${ADVANCE_ROADMAP_SUPERSET:-1}"
WORKER_TIMEOUT_MIN="${ADVANCE_ROADMAP_WORKER_TIMEOUT_MIN:-360}"
WORKER_POLL_S="${ADVANCE_ROADMAP_WORKER_POLL_S:-10}"

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

# A tick keeps shipping items back to back until one fails to ship or the chain
# passes this many minutes. The check is at the end of a link, so the last item
# can run past it. 0 = one item per tick.
CHAIN_MINUTES="${ADVANCE_ROADMAP_CHAIN_MINUTES:-40}"
CHAIN_START="${ADVANCE_ROADMAP_CHAIN_START:-$(date +%s)}"
CHAIN_LINK="${ADVANCE_ROADMAP_CHAIN_LINK:-1}"

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
# Every model call this run makes (orchestrator here, worker and reviewers in worker.sh /
# review.sh) appends a line; runrecord.py folds it into the run row.
USAGE_LOG="$RUN_DIR/model-usage.jsonl"

RECORDED=0
ORCH=""
orch_rc=""
record() {
  # record STATUS [DETAIL] [EXIT] — exactly once per run.
  [ "$RECORDED" -eq 0 ] || return 0
  RECORDED=1
  local -a extra=()
  [ -n "${3:-}" ] && extra+=(--exit "$3")
  [ -f "$RUN_DIR/workspace.json" ] && extra+=(--expect-pr)  # a workspace run must ship a PR
  [ -n "${orch_rc:-}" ] && extra+=(--orch-exit "$orch_rc")
  python3 "$RECORD_PY" --root "$ROOT" append --stamp "$STAMP" --status "$1" \
    --detail "${2:-}" "${extra[@]}" \
    --orch-provider "$ORCH" \
    --orch-result "$ORCH_RESULT" \
    --worker-status "$WORKER_STATUS" --worker-result "$WORKER_RESULT" \
    --usage-log "$USAGE_LOG" >> "$LOG" 2>&1 || true
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

# ── Cadence ───────────────────────────────────────────────────────────────────
# launchd fires once an hour inside two daily sprint windows (10:30–15:30, 16:00–21:00;
# the tick list is in the plist). ADVANCE_ROADMAP_CADENCE_HOURS (plist: 1, so every tick
# runs) is the base. gen-dashboard.py owns the backoff and writes cadence_hours to
# state.json: the base normally, 2× after 4 consecutive blocked runs, 4× after 8, capped at
# 24h. Any run that ships resets it. A tick runs when (hour - 3) is a multiple of the
# cadence, so backoff thins the window's ticks (2× keeps the odd hours) rather than
# moving them. Pick a divisor of 24 or the grid drifts at midnight.
# A Linear ticket edit since the last no-work verdict bypasses the backoff (below).
export ADVANCE_ROADMAP_CADENCE_HOURS="${ADVANCE_ROADMAP_CADENCE_HOURS:-1}"
BASE_CADENCE="$ADVANCE_ROADMAP_CADENCE_HOURS"
case "$BASE_CADENCE" in ''|0|*[!0-9]*) BASE_CADENCE=1 ;; esac
CADENCE="$BASE_CADENCE"
if [ -r "$STATE_FILE" ] && command -v jq >/dev/null 2>&1; then
  CADENCE="$(jq -r ".cadence_hours // $BASE_CADENCE" "$STATE_FILE" 2>/dev/null || echo "$BASE_CADENCE")"
fi
case "$CADENCE" in ''|0|*[!0-9]*) CADENCE="$BASE_CADENCE" ;; esac
[ "$CADENCE" -lt "$BASE_CADENCE" ] && CADENCE="$BASE_CADENCE"  # state.json from an older, faster base
HOUR_NOW=$(( 10#$(date +%H) ))
on_grid() { [ $(( (HOUR_NOW - 3 + 24) % $1 )) -eq 0 ]; }
if [ "$CHAIN_LINK" -eq 1 ] && ! on_grid "$BASE_CADENCE"; then
  # An hourly tick between slots: not a run, so no record, log or dashboard churn.
  rm -f "$LOG"; rm -rf "$RUN_DIR"; RECORDED=1
  exit 0
fi
run_this_tick=1
if [ "$CHAIN_LINK" -gt 1 ]; then
  :  # the tick already passed this gate; a shipped link resets cadence anyway
elif ! on_grid "$CADENCE"; then
  run_this_tick=0  # a base slot the backoff skips
fi
# A ticket edit since the last no-work verdict (a repo label added, a blocker answered,
# a ticket moved out of Needs Input) is Jake unblocking something: run now rather than wait
# out the backoff. Read-only peek — repo churn doesn't bypass, and no verdict means no bypass.
# (A run's own Needs-Input/Paused writes don't cause a false bypass at the *next* tick: the
# fingerprint saved below is taken after the worker runs, so those writes are already baked in.)
if [ "$run_this_tick" -eq 0 ]; then
  peek_snap="$RUN_DIR/backoff-peek-snapshot.json"
  peek_fp="$RUN_DIR/backoff-peek-fingerprint.json"
  python3 "$LINEAR_SNAP_PY" --out "$peek_snap" >> "$LOG" 2>&1
  python3 "$VERDICT_PY" fingerprint --snapshot "$peek_snap" --out "$peek_fp" >> "$LOG" 2>&1
  if ticket_changes="$(python3 "$VERDICT_PY" ticket-changes --file "$LAST_VERDICT" --fingerprint "$peek_fp")"; then
    echo "(backoff bypassed — ticket changes since last verdict: $ticket_changes)" >> "$LOG"
    run_this_tick=1
  fi
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

# Workers run in Superset workspace terminals, so with the app down there's nothing to
# dispatch into: skip before spending an orchestrator pass. Any pending plan waits.
if [ "$SUPERSET_MODE" = "1" ] && ! python3 "$WORKSPACES_PY" available >> "$LOG" 2>&1; then
  echo "=== advance-roadmap $STAMP: skipping — Superset app or host service not reachable ===" >> "$LOG"
  record skipped-superset "Superset app or host service not reachable"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi

# Workspaces whose PR Superset has seen merged are done; delete them so the sidebar and
# ~/.superset/worktrees don't pile up. Never earlier — see lib/workspaces.py.
if [ "$SUPERSET_MODE" = "1" ] && [ -f "$WORKSPACES_PY" ]; then
  python3 "$WORKSPACES_PY" cleanup >> "$LOG" 2>&1 || echo "(workspace cleanup failed — non-fatal)" >> "$LOG"
fi

# run_worker_in_workspace REQ — run worker.sh in a Superset workspace terminal for the
# request's repo/branch and wait for it. Sets worker_rc. Returns 0 when the worker ran,
# 1 when Superset couldn't host it (sets superset_skip to why; the plan is kept).
run_worker_in_workspace() {
  local req="$1" repo branch slug name launcher="$RUN_DIR/worker-launch.zsh"
  repo="$(jq -r '.orchestrator.repo // empty' "$req")"
  branch="$(jq -r '.orchestrator.branch // empty' "$req")"
  if [ -z "$repo" ] || [ ! -d "$CODE_DIR/$repo" ]; then
    superset_skip="dispatch names no repo directory ($repo)"; return 1
  fi
  [ -n "$branch" ] || branch="roadmap/$STAMP"
  slug="${branch#roadmap/}"
  name="roadmap $repo $(jq -r '.orchestrator.linear_id // empty' "$req") ${slug}"
  # A worker an earlier run timed out on may still be going in this branch's workspace;
  # never start a second one beside it.
  local inflight="$ROOT/inflight/${repo}--${slug//\//-}.pid"
  mkdir -p "$ROOT/inflight"
  if [ -s "$inflight" ] && kill -0 "$(cat "$inflight")" 2>/dev/null; then
    superset_skip="a worker from an earlier run is still running on $branch"; return 1
  fi
  # The terminal runs the user's shell, not launchd's env: carry over what this run set.
  {
    print -r -- '#!/bin/zsh'
    local v
    for v in ${(k)parameters[(I)ADVANCE_ROADMAP_*]} PERSONAL_CODE_DIR; do
      case "$v" in *WEBHOOK*|*SECRET*|*TOKEN*|*KEY*) continue ;; esac  # secrets stay in secrets.env
      [ -n "${(P)v-}" ] && print -r -- "export $v=${(q)${(P)v}}"
    done
    print -r -- "print -r -- \$\$ > ${(q)RUN_DIR}/worker.pid; print -r -- \$\$ > ${(q)inflight}"
    print -r -- "${(q)WORKER_SH} --stamp ${(q)STAMP} --request-file ${(q)req} --providers ${(q)WORKERS} --result-file ${(q)WORKER_RESULT} --status-file ${(q)WORKER_STATUS} 2>&1 | tee ${(q)RUN_DIR}/worker-output.log"
    print -r -- "rc=\${pipestatus[1]}; print -r -- \$rc > ${(q)RUN_DIR}/worker.exit; rm -f ${(q)inflight}"
    # Settle here too: if run.sh timed out and moved on, nothing else would, and the next
    # tick would relaunch an item this worker already shipped.
    print -r -- "python3 ${(q)PLAN_PY} --file ${(q)PENDING_PLAN} settle --worker-result ${(q)WORKER_RESULT} --worker-rc \$rc --request-file ${(q)req} >/dev/null 2>&1"
  } > "$launcher"
  chmod 700 "$launcher"
  rm -f "$RUN_DIR/worker.pid" "$RUN_DIR/worker.exit"
  if ! python3 "$WORKSPACES_PY" launch --repo "$CODE_DIR/$repo" --branch "$branch" --name "${name//  / }" \
       --command "/bin/zsh ${(q)launcher}" --out "$RUN_DIR/workspace.json" >> "$LOG" 2>&1; then
    superset_skip="could not create the Superset workspace for $repo ($branch)"; return 1
  fi
  echo "(worker running in Superset workspace $(jq -r '.workspace_id' "$RUN_DIR/workspace.json") on $branch)"
  local waited=0 pid=""
  while [ ! -f "$RUN_DIR/worker.exit" ]; do
    sleep "$WORKER_POLL_S"; waited=$(( waited + WORKER_POLL_S ))
    [ -z "$pid" ] && [ -s "$RUN_DIR/worker.pid" ] && pid="$(cat "$RUN_DIR/worker.pid")"
    if [ -z "$pid" ] && [ "$waited" -ge 180 ]; then
      echo "(the workspace terminal never started the worker)"; worker_rc=1; return 0
    fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null && [ ! -f "$RUN_DIR/worker.exit" ]; then
      echo "(worker terminal exited without an exit code — closed or killed)"; worker_rc=1; break
    fi
    if [ "$waited" -ge $(( WORKER_TIMEOUT_MIN * 60 )) ]; then
      echo "(worker still running after ${WORKER_TIMEOUT_MIN}m — leaving it, recording a timeout)"; worker_rc=124; break
    fi
  done
  [ -f "$RUN_DIR/worker.exit" ] && worker_rc="$(cat "$RUN_DIR/worker.exit")"
  case "$worker_rc" in ''|*[!0-9]*) worker_rc=1 ;; esac
  cat "$RUN_DIR/worker-output.log" 2>/dev/null
  return 0
}

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
    "$prompt" >"$out" 2>&1 </dev/null
  local rc=$?
  python3 "$TOKENS_PY" codex "$out" --role orchestrator --model "$ORCH_CODEX_MODEL" --log "$USAGE_LOG" >> "$LOG" 2>&1
  return $rc
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
    --output-format json >"$out.json" 2>&1
  local rc=$?
  # json carries the per-model token counts; tokens.py turns it back into plain text at $out.
  python3 "$TOKENS_PY" claude-json "$out.json" "$out" --role orchestrator --model "$ORCH_CLAUDE_MODEL" \
    --log "$USAGE_LOG" >> "$LOG" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null   # tokens.py missing or broken: keep the raw output
  return $rc
}

# ── Unchanged-world gate ──────────────────────────────────────────────────────
# Linear goes through the `linear` CLI only. Its key is in the keychain, which the
# read-only Codex sandbox can't read — so snapshot the queue here and hand over the file.
LINEAR_SNAPSHOT="$RUN_DIR/linear-snapshot.json"
# Label issues filed without a repo/* label when their text names exactly one repo
# (jnelken-linear skill). Before the snapshot, so they're plannable this run and their
# label change moves the fingerprint. Resolved via SKILL_DIR so tests never reach Linear.
JLIN_PY="$(dirname "$SKILL_DIR")/jnelken-linear/jlin.py"
if [ -f "$JLIN_PY" ]; then
  echo "=== repo-label inference ===" >> "$LOG"
  python3 "$JLIN_PY" infer --apply >> "$LOG" 2>&1 || echo "(repo-label inference failed — non-fatal)" >> "$LOG"
fi
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
  [ "$CHAIN_LINK" -gt 1 ] && echo "(chain link $CHAIN_LINK, started $(date -r "$CHAIN_START" '+%H:%M:%S'))"
  cat "$RUN_DIR/verdict-check.txt" 2>/dev/null
  echo "orchestrator=$ORCH  workers=${WORKERS:-none}  cwd=$CODE_DIR"
  cd "$CODE_DIR" || { echo "FATAL: cannot cd to $CODE_DIR"; exit 1; }
  [ -f "$CONDUCTOR_DIR/ORCHESTRATOR.md" ] || { echo "FATAL: missing ORCHESTRATOR.md"; exit 1; }
  [ -f "$SKILL_DIR/SAFETY.md" ] || { echo "FATAL: missing SAFETY.md"; exit 1; }

  ORCH_PROMPT="Read and follow $CONDUCTOR_DIR/ORCHESTRATOR.md and $SKILL_DIR/SAFETY.md exactly.
Step 2d's code-health fallback uses the rubric in $SKILL_DIR/CODE_HEALTH.md.

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

  superset_skip=""
  if [ "$SUPERSET_MODE" = "1" ] && [ -n "${WORKERS:-}" ] && [ "$action" = "dispatch_worker" -o "$action" = "resume_worker" ]; then
    # Keep the request where the workspace terminal can read it after this block's cleanup.
    cp "$req" "$RUN_DIR/worker-request.json"; req="$RUN_DIR/worker-request.json"
    run_worker_in_workspace "$req" || true
  else
    "$WORKER_SH" --stamp "$STAMP" --request-file "$req" --providers "${WORKERS:-}" \
      --result-file "$WORKER_RESULT" --status-file "$WORKER_STATUS" || worker_rc=$?
  fi
  if [ -n "$superset_skip" ]; then
    # No worker ran: keep the pending plan for the next run, give back a reused plan's
    # attempt (nothing was tried), and save no verdict.
    echo "=== worker not launched: $superset_skip ==="
    [ "$reused" -eq 1 ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" refund
    final_rc=0
    echo "=== advance-roadmap exit=0 finished $(date) (skipped: $superset_skip) ==="
  else
  echo "=== worker exit=$worker_rc ==="
  python3 "$PLAN_PY" --file "$PENDING_PLAN" settle --worker-result "$WORKER_RESULT" --worker-rc "$worker_rc"
  # Re-fingerprint after the worker, not before: a bookkeeping worker's own Step 2b can flip a
  # ticket to Needs Input or Paused, and saving the pre-worker $FINGERPRINT would make the next
  # tick's backoff peek see that as "Jake changed something" and bypass the backoff for no reason. Never
  # overwrites $LINEAR_SNAPSHOT — that's the record of what the orchestrator actually triaged.
  POST_SNAPSHOT="$RUN_DIR/linear-snapshot-post.json"
  POST_FINGERPRINT="$RUN_DIR/fingerprint-post.json"
  python3 "$LINEAR_SNAP_PY" --out "$POST_SNAPSHOT" >> "$LOG" 2>&1
  python3 "$VERDICT_PY" fingerprint --snapshot "$POST_SNAPSHOT" --out "$POST_FINGERPRINT" >> "$LOG" 2>&1
  python3 "$VERDICT_PY" save --file "$LAST_VERDICT" --fingerprint "$POST_FINGERPRINT" \
    --orch-result "$ORCH_RESULT" --worker-result "$WORKER_RESULT" --stamp "$STAMP"

  [ -f "$PROBE_FLAG" ] || { touch "$PROBE_FLAG"; echo "(Dispatch probe flag set)"; }

  final_rc=0
  [ "$orch_rc" -eq 0 ] || final_rc=$orch_rc
  [ "$worker_rc" -eq 0 ] || final_rc=$worker_rc
  echo "=== advance-roadmap exit=$final_rc finished $(date) ==="
  fi
  rm -f "$result"
  [ "$req" = "$RUN_DIR/worker-request.json" ] || rm -f "$req"
} >> "$LOG" 2>&1
# zsh runs a redirected { } in this shell, so final_rc is still set here.
if [ -n "${superset_skip:-}" ]; then
  record skipped-superset "$superset_skip"
else
  record ran "" "${final_rc:-1}"
fi

ln -sf "$LOG" "$LOGDIR/latest.log"
regen_dashboard

# ── Slack summary ─────────────────────────────────────────────────────────────
# Slackagent posts this run to #eng as its bot, using the worker's short
# slack_summary (falling back to the full drawer summary). It exits 0 for a deliberate skip
# (skipped-* ticks are dashboard-only) and nonzero on a real failure — only then
# does the old one-line webhook post below run as a fallback.
SLACKAGENT_DIR="${ADVANCE_ROADMAP_SLACKAGENT_DIR:-$CODE_DIR/slackagent}"
slackagent_posted=0
if [ -f "$SLACKAGENT_DIR/scripts/post-roadmap-summary.mjs" ]; then
  if node "$SLACKAGENT_DIR/scripts/post-roadmap-summary.mjs" --stamp "$STAMP" --runs "$ROOT/runs.jsonl" >> "$LOG" 2>&1; then
    slackagent_posted=1
  else
    echo "=== slack: slackagent post failed; falling back to webhook ===" >> "$LOG"
  fi
fi

# Fallback: one short line per run. state.json was just rewritten by regen_dashboard,
# so it describes THIS run. No webhook configured = no-op, not an error.
if [ "$slackagent_posted" -eq 0 ] && [ -n "$SLACK_WEBHOOK" ] && [ -r "$STATE_FILE" ] && command -v jq >/dev/null 2>&1; then
  summary="$(jq -r '.last_summary // ""' "$STATE_FILE" 2>/dev/null)"
  cadence="$(jq -r ".cadence_hours // $BASE_CADENCE" "$STATE_FILE" 2>/dev/null)"
  [ -n "$summary" ] && {
    text="*advance-roadmap* $(date '+%a %H:%M') — ${summary}"
    [ "$cadence" != "$BASE_CADENCE" ] && text="$text  _(backed off to every ${cadence}h)_"
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

# ── Chain ─────────────────────────────────────────────────────────────────────
# Only a clean feature ship continues: blocked, failed, red deploys and quota
# skips all mean the next link would likely hit the same wall, and a code-health
# ship means the queue was empty — chaining would just be a refactor spree. The
# next link re-picks providers and re-plans from scratch, exactly like a fresh tick.
last_outcome="$(python3 -c 'import json,sys; r=json.loads(open(sys.argv[1]).readlines()[-1]); print(r.get("outcome",""), r.get("work_kind") or "feature")' "$ROOT/runs.jsonl" 2>/dev/null || true)"
elapsed=$(( $(date +%s) - CHAIN_START ))
if [ "$last_outcome" = "shipped code_health" ]; then
  echo "=== chain: code-health pass shipped — not chaining ===" >> "$LOG"
elif [ "$last_outcome" = "shipped feature" ] && [ "$elapsed" -lt $(( CHAIN_MINUTES * 60 )) ]; then
  echo "=== chain: shipped at $(( elapsed / 60 ))m of ${CHAIN_MINUTES}m — starting link $(( CHAIN_LINK + 1 )) ===" >> "$LOG"
  trap - EXIT
  rm -rf "$LOCK"
  export ADVANCE_ROADMAP_CHAIN_START="$CHAIN_START" ADVANCE_ROADMAP_CHAIN_LINK=$(( CHAIN_LINK + 1 ))
  sleep 1  # stamps are per-second; the next link must not reuse this one's
  exec /bin/zsh "${0:A}"
fi
