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
[ -r "$ROOT/lib/models.sh" ] || { echo "advance-roadmap: missing model defaults: $ROOT/lib/models.sh" >&2; exit 1; }
. "$ROOT/lib/models.sh"
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
DEPLOY_SNAP_PY="$ROOT/lib/deploysnap.py"
VERDICT_PY="$ROOT/lib/verdict.py"
LAST_VERDICT="${ADVANCE_ROADMAP_LAST_VERDICT:-$CODE_DIR/.advance-roadmap/last-verdict.json}"
# Provider-neutral: the plan belongs to the repos, not to whichever CLI orchestrates. One file
# per repo (pending-plan-<repo>.json), so parallel lanes never pick up each other's plan.
PENDING_DIR="${ADVANCE_ROADMAP_PENDING_DIR:-$CODE_DIR/.advance-roadmap}"
PENDING_PLAN=""
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

ORCH_CODEX_MODEL="${ADVANCE_ROADMAP_ORCH_CODEX_MODEL:-$CODEX_MODEL_DEFAULT}"
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
# Lane mode: supervisor.py runs several of these at once in a scheduled window. It decides when
# a lane starts (no cadence gate here), holds the global lock itself, refills lanes instead of
# chaining, and tells each lane which repos the others are in.
LANE="${ADVANCE_ROADMAP_LANE:-}"
TARGET_REPO="${ADVANCE_ROADMAP_TARGET_REPO:-}"
if [ -n "$TARGET_REPO" ]; then
  LAST_VERDICT="${ADVANCE_ROADMAP_LAST_VERDICT:-$CODE_DIR/.advance-roadmap/last-verdict-$TARGET_REPO.json}"
fi
BATCH_PID="${ADVANCE_ROADMAP_BATCH_PID:-}"
# Thread N of a repo's work in one daily batch (daily_batch.py). Thread 1 is the repo's primary
# planner; later threads plan around the earlier ones' plans (PEER_PLANS) and own no bookkeeping.
THREAD="${ADVANCE_ROADMAP_THREAD:-1}"
case "$THREAD" in ''|*[!0-9]*|0) THREAD=1 ;; esac
PEER_PLANS="${ADVANCE_ROADMAP_PEER_PLANS:-}"
BATCH_SNAPSHOT="${ADVANCE_ROADMAP_BATCH_SNAPSHOT:-}"
BUSY_REPOS="${ADVANCE_ROADMAP_BUSY_REPOS:-}"
INFLIGHT_STAMPS="${ADVANCE_ROADMAP_INFLIGHT_STAMPS:-}"
[ -n "$LANE" ] && CHAIN_MINUTES=0
CHAIN_START="${ADVANCE_ROADMAP_CHAIN_START:-$(date +%s)}"
CHAIN_LINK="${ADVANCE_ROADMAP_CHAIN_LINK:-1}"

regen_dashboard() {
  [ -f "$ROOT/gen-dashboard.py" ] || return 0
  python3 "$ROOT/gen-dashboard.py" >> "$LOG" 2>&1
}

mkdir -p "$LOGDIR"
STAMP="${ADVANCE_ROADMAP_STAMP:-$(date +%Y%m%d-%H%M%S)}"  # the supervisor picks unique lane stamps
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
planner_failed=0
record() {
  # record STATUS [DETAIL] [EXIT] — exactly once per run.
  [ "$RECORDED" -eq 0 ] || return 0
  RECORDED=1
  local -a extra=()
  [ -n "${3:-}" ] && extra+=(--exit "$3")
  [ -n "$LANE" ] && extra+=(--lane "$LANE")
  [ -f "$RUN_DIR/workspace.json" ] && extra+=(--expect-pr)  # a workspace run must ship a PR
  [ -n "${orch_rc:-}" ] && extra+=(--orch-exit "$orch_rc")
  [ -f "$RUN_DIR/quota-gates.json" ] && extra+=(--quota-gates "$RUN_DIR/quota-gates.json")
  python3 "$RECORD_PY" --root "$ROOT" append --stamp "$STAMP" --status "$1" \
    --detail "${2:-}" "${extra[@]}" \
    --orch-provider "$ORCH" \
    --orch-result "$ORCH_RESULT" \
    --worker-status "$WORKER_STATUS" --worker-result "$WORKER_RESULT" \
    --usage-log "$USAGE_LOG" >> "$LOG" 2>&1 || true
}
planner_quota_record() {
  # planner_quota_record OUT [--primary P] [--hit P=FILE]... — every planner who is out and until
  # when. When quota-record (which refreshes first) fails, the gates as last saved still name them.
  local out="$1"; shift
  python3 "$USAGE_PY" --root "$ROOT" quota-record --role orchestrator "$@" > "$out" 2>/dev/null && return 0
  python3 - "$USAGE_PY" "$ROOT" "$@" > "$out" 2>/dev/null <<'PY' && return 0
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
import usage
args, hits, primary = sys.argv[3:], [], None
for flag, value in zip(args[::2], args[1::2]):
    if flag == "--hit":
        hits.append(value)
    elif flag == "--primary":
        primary = value
print(json.dumps(usage.saved_quota_record(Path(sys.argv[2]), "orchestrator",
                                          usage.read_attempts(None, hits), primary)))
PY
  rm -f "$out"; return 1
}
save_quota_gates() {
  # So a batch's quota summary counts this repo's skip too.
  planner_quota_record "$RUN_DIR/quota-gates.json" || true
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
# Sprint windows ("HH:MM-HH:MM …", set in the plist; unset = always). launchd fires a tick
# missed during sleep when the Mac wakes, so a 2am wake would otherwise start a run.
in_window() {
  [ -n "${ADVANCE_ROADMAP_WINDOWS:-}" ] || return 0
  local now=$(( 10#$(date +%H) * 60 + 10#$(date +%M) )) w a b
  for w in ${=ADVANCE_ROADMAP_WINDOWS}; do
    a="${w%-*}"; b="${w#*-}"
    [ "$now" -ge $(( 10#${a%:*} * 60 + 10#${a#*:} )) ] && [ "$now" -lt $(( 10#${b%:*} * 60 + 10#${b#*:} )) ] && return 0
  done
  return 1
}
if [ -z "$LANE" ] && [ "$CHAIN_LINK" -eq 1 ] && ! in_window; then  # the supervisor owns lane timing
  # A catch-up fire outside the windows: not a run, so no record, log or dashboard churn.
  rm -f "$LOG"; rm -rf "$RUN_DIR"; RECORDED=1
  exit 0
fi
if [ -n "$LANE" ]; then
  :  # the supervisor's window replaces the cadence grid and its backoff
elif [ "$CHAIN_LINK" -eq 1 ] && ! on_grid "$BASE_CADENCE"; then
  # An hourly tick between slots: not a run, so no record, log or dashboard churn.
  rm -f "$LOG"; rm -rf "$RUN_DIR"; RECORDED=1
  exit 0
fi
run_this_tick=1
if [ -n "$LANE" ] || [ "$CHAIN_LINK" -gt 1 ]; then
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

[ -n "$BATCH_PID" ] || python3 "$USAGE_PY" --root "$ROOT" refresh >> "$LOG" 2>&1 || true

ORCH="$(python3 "$USAGE_PY" --root "$ROOT" pick-orchestrator 2>/dev/null | tr -d '\r')"
orch_pick_rc=$?
if [ "$orch_pick_rc" -ne 0 ] || [ -z "$ORCH" ] || [ "$ORCH" = "none" ]; then
  echo "=== advance-roadmap $STAMP: skipping — no orchestrator available ===" >> "$LOG"
  ORCH=""
  save_quota_gates
  record skipped-quota "no orchestrator available"
  ln -sf "$LOG" "$LOGDIR/latest.log"
  regen_dashboard
  exit 0
fi

WORKERS="$(python3 "$USAGE_PY" --root "$ROOT" pick-worker-chain 2>/dev/null | tr -d '\r' || true)"

# One scheduler at a time: a lone run holds this lock, and so does supervisor.py for its whole
# window (its lanes run under it). The real lock is a kernel fcntl lock on run.lock.f — released
# by the kernel when its holder dies, so never stale, and shared with supervisor.py
# (fcntl.lockf). -e keeps it across a chain link's exec. The run.lock directory beside it is only
# the "a run is in flight" marker serve.py reads; whoever holds the kernel lock owns the marker.
zmodload zsh/system
LOCK="$ROOT/run.lock"
if [ -z "$LANE" ]; then
  : >> "$ROOT/run.lock.f"  # zsystem flock won't create its file
  if ! zsystem flock -t 0 -e -f RUN_LOCK_FD "$ROOT/run.lock.f" 2>/dev/null; then
    echo "=== advance-roadmap $STAMP: another run holds $LOCK — skipping ===" >> "$LOG"
    record skipped-lock "another run held the lock"
    ln -sf "$LOG" "$LOGDIR/latest.log"
    regen_dashboard
    exit 0
  fi
  rm -rf "$LOCK"  # a dead holder's marker; the kernel lock says it's ours now
  mkdir "$LOCK" 2>/dev/null || {
    echo "=== advance-roadmap $STAMP: could not take lock — skipping ===" >> "$LOG"
    record skipped-lock "could not take lock"
    ln -sf "$LOG" "$LOGDIR/latest.log"
    regen_dashboard
    exit 0
  }
  print -r -- $$ > "$LOCK/pid"
  HOLDS_LOCK=1
fi

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
if [ "$SUPERSET_MODE" = "1" ] && [ -z "$LANE" ] && [ -f "$WORKSPACES_PY" ]; then  # lanes: the supervisor cleans up
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
# Callers pass a final answer only, never a transcript. That answer may still quote a
# malformed fence before its own, so take the last fence whose body actually parses.
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
  # record_limit_from_log PROVIDER LOGFILE [HIT_OUT] — HIT_OUT gets the recorded hit (reset time) as JSON.
  local provider="$1" logfile="$2" hit_out="${3:-}"
  # Only treat as a live CLI limit if the message looks like a banner, not prose
  # inside ORCHESTRATOR.md / prior logs the model may have read (those contain
  # the phrase "session limit" and were falsely tripping same-run failover).
  local text
  text="$(rg -m1 -i \
    '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
    "$logfile" || true)"
  [ -n "$text" ] || return 1
  # Saved before the shared usage file is touched, so the run record keeps the banner and its
  # reset even when that update fails.
  if [ -n "$hit_out" ]; then
    python3 "$USAGE_PY" parse-limit --provider "$provider" --text "$text" --scope orchestrator \
      > "$hit_out" 2>/dev/null \
      || python3 -c 'import json, sys; print(json.dumps({"provider": sys.argv[1], "exact_cli_text": sys.argv[2]}))' \
           "$provider" "$text" > "$hit_out"
  fi
  python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$provider" --text "$text" --scope orchestrator \
    || true
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
  rm -f "$out.last"
  "$CLAUDE" -p "$prompt" \
    --model "$ORCH_CLAUDE_MODEL" \
    --effort high \
    --permission-mode plan \
    --permission-prompts none \
    --disallowedTools "Edit,Write,NotebookEdit" \
    --add-dir "$CODE_DIR" \
    --output-format json >"$out.json" 2>&1
  local rc=$?
  # json carries the per-model token counts; tokens.py turns it back into plain text at $out,
  # and writes the json's final answer alone to $out.last, as Codex's -o does.
  python3 "$TOKENS_PY" claude-json "$out.json" "$out" --final "$out.last" --role orchestrator \
    --model "$ORCH_CLAUDE_MODEL" --log "$USAGE_LOG" >> "$LOG" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null   # tokens.py missing or broken: keep the raw output
  return $rc
}

run_orchestrator() {
  # run_orchestrator PROVIDER OUT PROMPT — 127 when that provider's CLI is missing.
  case "$1" in
    codex)  command -v "$CODEX" >/dev/null 2>&1 || { echo "codex not found: $CODEX" > "$2"; return 127; }
            run_orchestrator_codex "$2" "$3" ;;
    claude) [ -x "$CLAUDE" ] || { echo "claude not found: $CLAUDE" > "$2"; return 127; }
            run_orchestrator_claude "$2" "$3" ;;
    *) echo "unknown orchestrator $1" > "$2"; return 127 ;;
  esac
}

# ── Unchanged-world gate ──────────────────────────────────────────────────────
# Linear goes through the `linear` CLI only. Its key is in the keychain, which the
# read-only Codex sandbox can't read — so snapshot the queue here and hand over the file.
LINEAR_SNAPSHOT="$RUN_DIR/linear-snapshot.json"
# Label issues filed without a repo/* label when their text names exactly one repo
# (jnelken-linear skill). Before the snapshot, so they're plannable this run and their
# label change moves the fingerprint. Resolved via SKILL_DIR so tests never reach Linear.
JLIN_PY="$(dirname "$SKILL_DIR")/jnelken-linear/jlin.py"
if [ -f "$JLIN_PY" ] && [ -z "$BATCH_PID" ]; then
  echo "=== repo-label inference ===" >> "$LOG"
  python3 "$JLIN_PY" infer --apply >> "$LOG" 2>&1 || echo "(repo-label inference failed — non-fatal)" >> "$LOG"
fi
FINGERPRINT="$RUN_DIR/fingerprint.json"
VERDICT_CHANGES="$RUN_DIR/verdict-changes.json"
if [ -n "$BATCH_SNAPSHOT" ]; then
  cp "$BATCH_SNAPSHOT" "$LINEAR_SNAPSHOT" || exit 1
else
  python3 "$LINEAR_SNAP_PY" --out "$LINEAR_SNAPSHOT" >> "$LOG" 2>&1
fi
# The sandbox can't reach GitHub either, so production deploy state is snapshotted here too.
DEPLOY_SNAPSHOT="$RUN_DIR/deploy-snapshot.json"
deploy_scope=()
[ -n "$TARGET_REPO" ] && deploy_scope=(--repo "$TARGET_REPO")
python3 "$DEPLOY_SNAP_PY" --code-dir "$CODE_DIR" --out "$DEPLOY_SNAPSHOT" "${deploy_scope[@]}" >> "$LOG" 2>&1 \
  || echo "(deploy snapshot failed — non-fatal)" >> "$LOG"
python3 "$VERDICT_PY" fingerprint --snapshot "$LINEAR_SNAPSHOT" --deploys "$DEPLOY_SNAPSHOT" --out "$FINGERPRINT" >> "$LOG" 2>&1
# The last run found no work and its bookkeeping landed. If no repo, issue, allowlist or skill
# doc has moved since, this run would re-derive the same verdict — skip planning and worker both.
# A pending plan always runs: it is work, not a verdict.
if [ -z "$(print -l "$PENDING_DIR"/pending-plan*.json(N))" ] && python3 "$VERDICT_PY" check --file "$LAST_VERDICT" \
     --fingerprint "$FINGERPRINT" --changes "$VERDICT_CHANGES" --root "$ROOT" > "$RUN_DIR/verdict-check.txt" 2>&1; then
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
  if [ -n "$LANE" ]; then
    ORCH_PROMPT="$ORCH_PROMPT

This is lane $LANE of a parallel window (ORCHESTRATOR.md, *Parallel lanes*).
Busy repos — other lanes are working in them right now; skip them entirely: ${BUSY_REPOS:-none}
In-flight run stamps — still running, never interrupted: ${INFLIGHT_STAMPS:-none}"
  fi
  if [ -n "$TARGET_REPO" ]; then
    ORCH_PROMPT="$ORCH_PROMPT

This is a finite daily batch. Your ONLY repository is $TARGET_REPO. You are THREAD $THREAD of at most ${ADVANCE_ROADMAP_THREADS_PER_REPO:-2} in it (ORCHESTRATOR.md, *Threads*).
Select at most ONE existing actionable ticket/roadmap item for your thread.
For a dispatch, list in touchpoints every file, directory or glob the item will change, and put
anything you expect to overlap with a sibling thread in conflict_notes (ORCHESTRATOR.md, JSON protocol).
Do not choose another repo, invent roadmap features, reconcile another repo's prior run,
or include another repo's archives, blockers, directives, or bookkeeping.
Other batch planners own every other repo. Keep considered and summary scoped to $TARGET_REPO."
    if [ "$THREAD" -le 1 ]; then
      ORCH_PROMPT="$ORCH_PROMPT
If no feature item is actionable, apply Step 2d's bounded code-health (hygiene) fallback to this repo.
For this daily batch the fallback is PER REPO: feature work in another repo does not block it.
Prefer an existing code-health ticket; otherwise queue one ad-hoc pass under CODE_HEALTH.md.
Keep every Step 2d eligibility gate, including the clean tree and 24-hour cooldown.
Emit work_kind=code_health for a hygiene pass; if the repo is ineligible, return no work.
A code-health pass is only for a repo with no feature item; it never runs beside a feature thread.
Later threads of this repo are planned after yours and approved against it, so plan yours as if alone."
    else
      ORCH_PROMPT="$ORCH_PROMPT
Earlier threads of $TARGET_REPO already hold the items in $PEER_PLANS (read it). Never pick one of
those items or anything that needs their branch. Pick the next-ranked eligible item whose
touchpoints are as disjoint from theirs as you can make them; if the only candidates collide, say
so in conflict_notes rather than hiding it, and the plan approver will decide.
You own NO bookkeeping: no Step 2d code-health pass, no archives, no blockers, no Linear comments,
no directive. If no second independent item exists, return blocked_no_item with the summary
\"no second thread\" and nothing else."
    fi
  fi

  if [ ! -f "$PROBE_FLAG" ]; then
    echo "(one-time Dispatch probe armed — flag absent: $PROBE_FLAG)"
    ORCH_PROMPT="$ORCH_PROMPT

If a PushNotification probe would have been useful, set a note in summary; the worker may attempt it. Do not call PushNotification yourself."
  fi

  result="$(mktemp "${TMPDIR:-/tmp}/advance-roadmap-result.XXXXXX")"
  req="$(mktemp "${TMPDIR:-/tmp}/advance-roadmap-req.XXXXXX")"
  # A plan whose worker died last run is handed straight to a worker — no second planning pass.
  # Oldest first; another lane's repo is not ours to resume. A plan that can't be reused is
  # dead (too old, retried out, repo unsafe) — drop it, as a fresh plan would have replaced it.
  reused=0
  # Only a repo's first thread resumes a saved plan; a later thread always plans fresh.
  for f in "$PENDING_DIR"/pending-plan*.json(N.Om); do
    [ "$THREAD" -gt 1 ] && break
    prepo="$(jq -r '.orchestrator.repo // empty' "$f" 2>/dev/null)"
    [ -n "$TARGET_REPO" ] && [ "$prepo" != "$TARGET_REPO" ] && continue
    [[ " $BUSY_REPOS " == *" $prepo "* ]] && continue
    if python3 "$PLAN_PY" --file "$f" reuse --out "$result"; then
      PENDING_PLAN="$f"; reused=1; break
    fi
    python3 "$PLAN_PY" --file "$f" clear
  done
  if [ "$reused" -eq 1 ]; then
    orch_rc=0
    ORCH=pending-plan  # the run record shows no planning pass ran
  else
    orch_out="$RUN_DIR/orchestrator-stdout.txt"  # kept, so a killed planning pass leaves a trace
    ORCH_PROMPT="$ORCH_PROMPT

Linear snapshot (read this; never call Linear yourself): $LINEAR_SNAPSHOT
Production deploy snapshot (read this; never call GitHub yourself): $DEPLOY_SNAPSHOT"
    if [ -s "$VERDICT_CHANGES" ]; then
      ORCH_PROMPT="$ORCH_PROMPT
Previous no-work verdict and what changed since (carry unchanged verdicts forward): $VERDICT_CHANGES"
    fi
    # The planner was picked before the snapshots; another lane may have gated it since.
    python3 "$USAGE_PY" --root "$ROOT" gate --role orchestrator --provider "$ORCH" >/dev/null 2>&1
    if [ $? -eq 3 ]; then
      alt=""
      for p in ${=$(python3 "$USAGE_PY" --root "$ROOT" pick-orchestrators 2>/dev/null | tr -d '\r')}; do
        [ "$p" = "$ORCH" ] || { alt="$p"; break; }
      done
      if [ -z "$alt" ]; then
        echo "=== advance-roadmap $STAMP: skipping — planner $ORCH went over its gate before launch, no other is under its gate ==="
        ORCH=""
        save_quota_gates
        record skipped-quota "no orchestrator available at launch"
        ln -sf "$LOG" "$LOGDIR/latest.log"
        regen_dashboard
        exit 0
      fi
      echo "(planner $ORCH went over its gate before launch — using $alt)"
      ORCH="$alt"
    fi
    orch_rc=0
    tried=""
    planner_hit=""   # the last planner's recorded limit hit, when it stopped on one
    had_json=0
    while :; do
      tried="$tried $ORCH"
      orch_rc=0
      planner_hit=""   # only the planner that answers last decides quota vs invalid output
      rm -f "$orch_out.last"   # a failed-over planner's final message must not be read as the next one's
      run_orchestrator "$ORCH" "$orch_out" "$ORCH_PROMPT" || orch_rc=$?
      cat "$orch_out" 2>/dev/null
      echo ""
      echo "=== orchestrator provider=$ORCH exit=$orch_rc ==="
      # The plan comes only from the planner's final answer. The transcript echoes the prompt and
      # ORCHESTRATOR.md, whose example fences are valid plans; no final answer means no plan.
      if [ -s "$orch_out.last" ] && extract_json_fence ORCHESTRATOR_RESULT_JSON "$orch_out.last" "$result"; then
        had_json=1; break
      fi
      # Limit failover only when the orchestrator failed to produce a result JSON.
      # Successful triage dumps skill text that mentions "session limit" historically
      # and must not burn a second orchestrator turn.
      planner_hit="$RUN_DIR/orchestrator-limit-$ORCH.json"
      record_limit_from_log "$ORCH" "$orch_out" "$planner_hit" || { planner_hit=""; break; }
      # The planner that just hit its limit is gated now; try the next one still under its gate.
      alt=""
      for p in ${=$(python3 "$USAGE_PY" --root "$ROOT" pick-orchestrators 2>/dev/null | tr -d '\r')}; do
        [[ " $tried " == *" $p "* ]] || { alt="$p"; break; }
      done
      [ -n "$alt" ] || { echo "(orchestrator $ORCH hit its limit — no other planner is under its gate)"; break; }
      echo "(orchestrator $ORCH hit its limit — failing over to $alt)"
      ORCH="$alt"
    done

    invalid=""
    if [ "$had_json" -eq 1 ]; then
      invalid="$(python3 -c '
import json, sys
r = json.load(open(sys.argv[1]))
ok = isinstance(r, dict) and r.get("action") in ("dispatch_worker", "resume_worker", "blocked_no_item", "nothing_qualified")
ok or print("ORCHESTRATOR_RESULT_JSON has no valid action: %r" % (r.get("action") if isinstance(r, dict) else type(r).__name__))
sys.exit(0 if ok else 1)' "$result")" || had_json=0
    fi

    if [ "$had_json" -eq 0 ]; then
      # No plan: nothing may act on this run — no worker, no batch-ready, no pending plan, no
      # verdict. A limit banner is a quota pause (provider and reset kept); anything else is an error.
      planner_failed=1
      echo "WARNING: the planner produced no usable ORCHESTRATOR_RESULT_JSON — nothing dispatched"
      planner_quota=""
      if [ -n "$planner_hit" ]; then
        # Every planner in the role, with the limits the ones tried here hit and the gates of the rest.
        hits=()
        for p in ${=tried}; do
          [ -f "$RUN_DIR/orchestrator-limit-$p.json" ] && hits+=(--hit "$p=$RUN_DIR/orchestrator-limit-$p.json")
        done
        planner_quota="$RUN_DIR/planner-quota.json"
        planner_quota_record "$planner_quota" --primary "$ORCH" "${hits[@]}" || planner_quota=""
      fi
      python3 - "$orch_out" "$ORCH_RESULT" "$ORCH" "$tried" "$planner_hit" "$orch_rc" "$invalid" "$planner_quota" <<'PY'
import json, os, sys
out, dest, provider, tried, hit_path, rc, invalid, quota_path = sys.argv[1:9]
try:
    answered = os.path.getsize(out + ".last") > 0
except OSError:
    answered = False
hit, quota = {}, {}
try:
    hit = json.load(open(hit_path)) if hit_path else {}
except (OSError, ValueError):
    pass
try:
    quota = json.load(open(quota_path)) if quota_path else {}
except (OSError, ValueError):
    pass
r = {"action": None, "outcome_token": None, "repo": None, "item": None, "branch": None,
     "linear_id": None, "directive_ticket": None, "worker_brief": None,
     "archives": [], "blockers": [], "considered": []}
if hit_path:
    limits = quota.get("providers") or []
    if not limits:
        # No quota record at all: the limits this lane saved itself still name every planner it tried.
        for name in tried.split():
            try:
                h = json.load(open(os.path.join(os.path.dirname(hit_path), f"orchestrator-limit-{name}.json")))
            except (OSError, ValueError):
                continue
            if isinstance(h, dict):
                limits.append({"provider": name, "state": "limited", "cause": h.get("exact_cli_text"),
                               "reset_epoch": h.get("reset_epoch"), "reset_at": h.get("reset_at")})
    r["planner_error"] = {"kind": "quota", "provider": provider, "tried": tried.split(),
                          "limit_text": hit.get("exact_cli_text"), "reset_epoch": hit.get("reset_epoch"),
                          "reset_at": hit.get("reset_at"), "limits": limits}
    r["summary"] = (f"Planner {provider} hit its usage limit"
                    + (f" (resets {hit['reset_at']})" if hit.get("reset_at") else "")
                    + "; no other planner was under its gate, so nothing was dispatched.")
else:
    cause = invalid or ("the planner CLI was not found" if rc == "127"
                        else "missing ORCHESTRATOR_RESULT_JSON" if answered else "no final answer")
    try:
        tail = open(out, encoding="utf-8", errors="replace").read()[-2000:]
    except OSError:
        tail = ""
    r["planner_error"] = {"kind": "invalid_output", "provider": provider, "tried": tried.split(),
                          "exit": int(rc) if rc.lstrip("-").isdigit() else None, "cause": cause}
    r["parse_error"] = cause
    r["summary"] = f"Planner {provider} returned no usable plan ({cause}); nothing was dispatched.\n{tail}".strip()
json.dump(r, open(dest, "w"), indent=2)
open(dest, "a").write("\n")
PY
      final_rc=0
      [ -n "$planner_hit" ] || { final_rc=$orch_rc; [ "$final_rc" -ne 0 ] || final_rc=1; }
      rm -f "$result" "$req"
      echo "=== advance-roadmap exit=$final_rc finished $(date) (planner failed) ==="
    fi

  fi
} >> "$LOG" 2>&1
[ "$planner_failed" -eq 1 ] || {
  # The repo name becomes a path and a lock: it must be a top-level repo directory, and in lane
  # mode one no other lane holds. Anything else is turned into a no-dispatch decision.
  # Hold a kernel lock on the repo for the rest of this run (released when run.sh exits), so
  # even a supervisor that crashed and lost track can't put two lanes on one repo.
  pick="$(jq -r 'select(.action=="dispatch_worker" or .action=="resume_worker") | .repo // empty' "$result" 2>/dev/null)"
  if [[ "$pick" =~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' ]] && { [ -z "$TARGET_REPO" ] || [ "$pick" = "$TARGET_REPO" ]; }; then
    # One lock per repo and thread: two lanes on the same thread of a repo are still a bug, but a
    # repo's threads are meant to run side by side.
    repo_lock="$ROOT/repo-locks/$pick.lock"; [ "$THREAD" -gt 1 ] && repo_lock="$ROOT/repo-locks/$pick.t$THREAD.lock"
    mkdir -p "$ROOT/repo-locks"; : >> "$repo_lock"
    zsystem flock -t 0 -e -f REPO_LOCK_FD "$repo_lock" 2>/dev/null \
      || BUSY_REPOS="$BUSY_REPOS $pick"
  fi
  ORCH_RESULT="$ORCH_RESULT" python3 - "$result" "$CODE_DIR" "$BUSY_REPOS" "$TARGET_REPO" "$THREAD" <<'PY'
import json, os, re, sys
path, code, busy = sys.argv[1], sys.argv[2], sys.argv[3].split()
target, thread = sys.argv[4], int(sys.argv[5])
r = json.load(open(path))
repo = r.get("repo")
if thread > 1:
    # Bookkeeping belongs to a repo's first thread, and a code-health pass never runs beside a feature.
    r["blockers"], r["archives"] = [], []
    if r.get("action") == "resume_worker" or r.get("work_kind") == "code_health":
        r.update(action="blocked_no_item", outcome_token="blocked-no-item", repo=None, item=None,
                 branch=None, linear_id=None, directive_ticket=None, worker_brief=None,
                 summary="no second thread")
        json.dump(r, open(path, "w"), indent=2)
if target:
    # A target-scoped planner cannot authorize bookkeeping in another repo either.
    r["blockers"] = [b for b in r.get("blockers", []) if b.get("repo") == target]
    r["archives"] = [a for a in r.get("archives", [])
                     if a.startswith(target + "/") and ".." not in a.split("/")]
    if r.get("action") not in ("dispatch_worker", "resume_worker") and repo not in (None, target):
        r.update(repo=None, item=None, branch=None, linear_id=None, directive_ticket=None,
                 worker_brief=None, archives=[], blockers=[], considered=[],
                 summary=f"Planner returned bookkeeping outside assigned repo {target}; ignored")
if r.get("action") in ("dispatch_worker", "resume_worker"):
    why = None
    if not repo or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo) or not os.path.isdir(os.path.join(code, repo)):
        why = f"orchestrator named an invalid repo {repo!r}"
    elif repo in busy:
        why = f"orchestrator picked {repo}, which another lane holds"
    elif target and repo != target:
        why = f"orchestrator picked {repo}, outside its assigned repo {target}"
    if why:
        # Every actionable field goes too: no worker may touch another lane's repo.
        r.update(action="blocked_no_item", outcome_token="blocked-no-item", repo=None, item=None,
                 branch=None, linear_id=None, directive_ticket=None, worker_brief=None,
                 archives=[], blockers=[],
                 summary=(r.get("summary") or "") + f"\n({why} — not dispatched)")
        json.dump(r, open(path, "w"), indent=2)
        open(os.path.join(os.path.dirname(os.environ["ORCH_RESULT"]), "dispatch-rejected"), "w").write(why)
        print(f"WARNING: {why} — not dispatched")
if target:
    json.dump(r, open(path, "w"), indent=2)
PY
  echo "(orchestrator result)"
  cat "$result"
  cp "$result" "$ORCH_RESULT"
  if [ "$reused" -eq 0 ]; then
    plan_repo="$(jq -r '.repo // empty' "$result" 2>/dev/null)"
    if [ -n "$plan_repo" ]; then
      PENDING_PLAN="$PENDING_DIR/pending-plan-$plan_repo.json"
      [ "$THREAD" -gt 1 ] && PENDING_PLAN="$PENDING_DIR/pending-plan-$plan_repo.t$THREAD.json"
      python3 "$PLAN_PY" --file "$PENDING_PLAN" save --result "$result" --stamp "$STAMP"
    fi
  fi

  if [ -n "$BATCH_PID" ]; then
    # Persist the plan before announcing readiness. No implementation/bookkeeping model runs
    # until every planner finishes and the controller releases this slot in the finite batch.
    touch "$RUN_DIR/batch-ready"
    while [ ! -f "$RUN_DIR/batch-release" ]; do
      if [ -f "$RUN_DIR/batch-cancel" ]; then
        # A later thread's plan was judged against today's siblings; tomorrow it is planned afresh
        # (the plan may duplicate what thread 1 just shipped), so don't leave it to be replayed.
        [ "$THREAD" -gt 1 ] && [ -n "$PENDING_PLAN" ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" clear
        [ "$reused" -eq 1 ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" refund
        record skipped-quota "daily batch deferred before worker launch; pending plan retained"
        exit 0
      fi
      if ! kill -0 "$BATCH_PID" 2>/dev/null; then
        [ "$THREAD" -gt 1 ] && [ -n "$PENDING_PLAN" ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" clear
        [ "$reused" -eq 1 ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" refund
        record aborted "daily batch controller exited before worker launch; pending plan retained"
        exit 0
      fi
      sleep "${ADVANCE_ROADMAP_BATCH_POLL_S:-1}"
    done
    # The first plan may have waited for slower planners: route on the current pool state.
    WORKERS="$(python3 "$USAGE_PY" --root "$ROOT" pick-worker-chain 2>/dev/null | tr -d '\r' || true)"
  fi

  action="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("action",""))' "$result")"
  # A later thread owns no bookkeeping, so "nothing for me" ends here without a worker.
  if [ "$THREAD" -gt 1 ] && [ "$action" != "dispatch_worker" ] && [ ! -f "$RUN_DIR/dispatch-rejected" ]; then
    print -r -- "thread $THREAD found no second independent item" > "$RUN_DIR/dispatch-rejected"
  fi
  case "$action" in
    dispatch_worker|resume_worker)
      python3 - "$result" "$req" "$STAMP" "$RUN_DIR" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[1]))
note = None
try:
    note = json.load(open(os.path.join(sys.argv[4], "batch-approval.json")))
except (OSError, ValueError):
    pass
if note and note.get("siblings"):
    lines = [f"## Plan approval — thread {note.get('thread')} of {note.get('repo')}",
             f"Ruling: {note.get('verdict')}" + (f" (after {note['after']} merged)" if note.get("after") else "")
             + (f" — {note['reason']}" if note.get("reason") else ""),
             "Sibling threads in this repo today (each is a separate branch and workspace):"]
    for sib in note["siblings"]:
        lines.append(f"- thread {sib.get('thread')}: {sib.get('item')} on {sib.get('branch')}; "
                     f"touches {', '.join(sib.get('touchpoints') or []) or 'unspecified'}")
    if note.get("acknowledged_conflicts"):
        lines.append("Conflicts acknowledged in advance:")
        for c in note["acknowledged_conflicts"]:
            lines.append(f"- {c.get('area')}: {c.get('handling') or 'handle at merge'}")
    if note.get("standing_conflicts"):
        lines.append("Overlap every thread of a repo has:")
        for c in note["standing_conflicts"]:
            lines.append(f"- {c.get('area')}: {c.get('handling')}")
    lines.append("WORKER.md *Threads* applies: merge origin/main into your branch before your PR merges, "
                 "never overwrite a sibling's roadmap or doc edit, and judge only your own merge commit's deploy.")
    r["worker_brief"] = ((r.get("worker_brief") or "").rstrip() + "\n\n" + "\n".join(lines)).strip()
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
  if [ -f "$RUN_DIR/dispatch-rejected" ]; then
    # A refused dispatch is a no-op: no worker, nothing written to any repo or ticket.
    echo "(dispatch refused: $(cat "$RUN_DIR/dispatch-rejected") — no worker)"
    print -r -- '{"used":"none","exit":0,"fatal":null}' > "$WORKER_STATUS"
    jq -n --arg s "$(cat "$RUN_DIR/dispatch-rejected")" '{outcome:"bookkeeping",provider:"cursor",summary:$s}' > "$WORKER_RESULT"
  elif [ "$SUPERSET_MODE" = "1" ] && [ -n "${WORKERS:-}" ] && [ "$action" = "dispatch_worker" -o "$action" = "resume_worker" ]; then
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
    # A fresh plan made for a worker that never launched stays for the next run to reuse.
    final_rc=0
    echo "=== advance-roadmap exit=0 finished $(date) (skipped: $superset_skip) ==="
  else
  echo "=== worker exit=$worker_rc ==="
  [ -n "$PENDING_PLAN" ] && python3 "$PLAN_PY" --file "$PENDING_PLAN" settle --worker-result "$WORKER_RESULT" --worker-rc "$worker_rc"
  # Re-fingerprint after the worker, not before: a bookkeeping worker's own Step 2b can flip a
  # ticket to Needs Input or Paused, and saving the pre-worker $FINGERPRINT would make the next
  # tick's backoff peek see that as "Jake changed something" and bypass the backoff for no reason. Never
  # overwrites $LINEAR_SNAPSHOT — that's the record of what the orchestrator actually triaged.
  POST_SNAPSHOT="$RUN_DIR/linear-snapshot-post.json"
  POST_FINGERPRINT="$RUN_DIR/fingerprint-post.json"
  python3 "$LINEAR_SNAP_PY" --out "$POST_SNAPSHOT" >> "$LOG" 2>&1
  python3 "$VERDICT_PY" fingerprint --snapshot "$POST_SNAPSHOT" --out "$POST_FINGERPRINT" >> "$LOG" 2>&1
  # A no-work verdict reached while other lanes hold repos only covers the rest, so it isn't
  # one the next run may carry forward.
  if [ -z "$BUSY_REPOS" ] || [ -n "$TARGET_REPO" ]; then
    # Daily batches use a distinct verdict file for each target repo.
    python3 "$VERDICT_PY" save --file "$LAST_VERDICT" --fingerprint "$POST_FINGERPRINT" \
      --orch-result "$ORCH_RESULT" --worker-result "$WORKER_RESULT" --stamp "$STAMP"
  fi

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
# A run inside a daily batch posts nothing: daily_batch.py sends one summary when every thread has
# stopped. Claiming "posted" also keeps the one-line webhook fallback below from firing per run.
if [ -n "$BATCH_PID" ]; then
  slackagent_posted=1
  echo "=== slack: left to the batch summary ===" >> "$LOG"
fi
if [ "$slackagent_posted" -eq 0 ] && [ -f "$SLACKAGENT_DIR/scripts/post-roadmap-summary.mjs" ]; then
  if node "$SLACKAGENT_DIR/scripts/post-roadmap-summary.mjs" --stamp "$STAMP" --runs "$ROOT/runs.jsonl" >> "$LOG" 2>&1; then
    slackagent_posted=1
  else
    echo "=== slack: slackagent post failed; falling back to webhook ===" >> "$LOG"
  fi
fi

# Fallback: one short line per run. state.json was just rewritten by regen_dashboard,
# so it describes THIS run. No webhook configured = no-op, not an error.
if [ "$slackagent_posted" -eq 0 ] && [ -n "$SLACK_WEBHOOK" ] && [ -r "$STATE_FILE" ] && command -v jq >/dev/null 2>&1; then
  # This run's own record: with parallel lanes, state.json may already describe another run.
  summary="$(python3 -c 'import json,sys
s = ""
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r.get("stamp") == sys.argv[2]:
        s = r.get("slack_summary") or r.get("summary") or r.get("outcome") or ""
print(" ".join(s.split())[:300])' "$ROOT/runs.jsonl" "$STAMP" 2>/dev/null)"
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
# Lanes finishing together would each raise the same alert: send each distinct one once.
[ -n "$alert" ] && : >> "$ROOT/alert.lock"
if [ -n "$alert" ] && zsystem flock -t 30 -f ALERT_FD "$ROOT/alert.lock" 2>/dev/null; then
  if [ "$(cat "$ROOT/last-alert.txt" 2>/dev/null)" = "$alert" ]; then
    alert=""
  else
    print -r -- "$alert" > "$ROOT/last-alert.txt"
  fi
  zsystem flock -u "$ALERT_FD"
fi
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
elif [ "$last_outcome" = "shipped feature" ] && ! in_window; then
  echo "=== chain: past the sprint window — not chaining ===" >> "$LOG"
elif [ "$last_outcome" = "shipped feature" ] && [ "$elapsed" -lt $(( CHAIN_MINUTES * 60 )) ]; then
  echo "=== chain: shipped at $(( elapsed / 60 ))m of ${CHAIN_MINUTES}m — starting link $(( CHAIN_LINK + 1 )) ===" >> "$LOG"
  trap - EXIT
  rm -rf "$LOCK"
  export ADVANCE_ROADMAP_CHAIN_START="$CHAIN_START" ADVANCE_ROADMAP_CHAIN_LINK=$(( CHAIN_LINK + 1 ))
  sleep 1  # stamps are per-second; the next link must not reuse this one's
  exec /bin/zsh "${0:A}"
fi
