#!/bin/zsh
# Write-capable worker for advance-roadmap. Invoked by run.sh.
#
#   worker.sh --stamp STAMP --request-file PATH [--providers "cursor codex claude"]
#             [--result-file PATH] [--status-file PATH]
#
# --result-file: where the agent writes its WORKER_RESULT_JSON (the record run.sh
#   keeps). If the agent doesn't, we fall back to the last valid fence in that
#   provider's final answer — never its transcript, which echoes WORKER.md's examples.
# --status-file: what this script observed (provider used, exit, fatal reason).
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
# Unattended: the status-glyph Stop hook must not force a sign-off turn over a result fence.
export ADVANCE_ROADMAP_HEADLESS=1
[ -r "$ROOT/lib/models.sh" ] || { echo "worker: missing model defaults: $ROOT/lib/models.sh" >&2; exit 1; }
. "$ROOT/lib/models.sh"
# Where the personal repos live differs by machine; ai-tools' personal-code-dir
# decides (never ~/code, which holds work checkouts). Unresolved = nothing to do.
CODE_DIR="${PERSONAL_CODE_DIR:-$("${0:A:h:h:h}/bin/personal-code-dir" 2>/dev/null)}" || CODE_DIR=""
[ -n "$CODE_DIR" ] || { echo "worker: no personal code dir on this machine (set PERSONAL_CODE_DIR)" >&2; exit 1; }
SKILL_DIR="${ADVANCE_ROADMAP_SKILL_DIR:-/Users/jake/.claude/skills/advance-roadmap}"
USAGE_PY="$ROOT/lib/usage.py"
REVIEW_SH="$ROOT/review.sh"
TOKENS_PY="$ROOT/lib/tokens.py"

CLAUDE="${ADVANCE_ROADMAP_CLAUDE_BIN:-/Users/jake/.local/bin/claude}"
AGENT="${ADVANCE_ROADMAP_AGENT_BIN:-/Users/jake/.local/bin/agent}"
CODEX="${ADVANCE_ROADMAP_CODEX_BIN:-/opt/homebrew/bin/codex}"

CLAUDE_WORKER_MODEL="${ADVANCE_ROADMAP_CLAUDE_WORKER_MODEL:-$CLAUDE_WORKER_MODEL_DEFAULT}"
CLAUDE_WORKER_EFFORT="${ADVANCE_ROADMAP_CLAUDE_WORKER_EFFORT:-$CLAUDE_WORKER_EFFORT_DEFAULT}"
CODEX_WORKER_MODEL="${ADVANCE_ROADMAP_CODEX_WORKER_MODEL:-$CODEX_WORKER_MODEL_DEFAULT}"
CODEX_WORKER_EFFORT="${ADVANCE_ROADMAP_CODEX_WORKER_EFFORT:-$CODEX_WORKER_EFFORT_DEFAULT}"
CURSOR_WORKER_MODEL="${ADVANCE_ROADMAP_CURSOR_WORKER_MODEL:-auto}"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

STAMP=""
REQUEST_FILE=""
PROVIDERS_STR=""
RESULT_FILE=""
STATUS_FILE=""
USAGE_LOG=""   # set once --result-file is known: model-usage.jsonl beside it, shared with run.sh and review.sh

while [ $# -gt 0 ]; do
  case "$1" in
    --stamp) STAMP="$2"; shift 2 ;;
    --request-file) REQUEST_FILE="$2"; shift 2 ;;
    --providers) PROVIDERS_STR="$2"; shift 2 ;;
    --provider) PROVIDERS_STR="$2"; shift 2 ;;
    --result-file) RESULT_FILE="$2"; shift 2 ;;
    --status-file) STATUS_FILE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

LIMITED_LOG=""   # one JSON line per provider skipped over its usage gate or stopped by a limit
MISSING=""       # providers whose CLI was not installed

write_status() {
  # write_status USED EXIT [FATAL] [STOPPED_ON] — STOPPED_ON: the provider whose limit ended the run.
  [ -n "$STATUS_FILE" ] || return 0
  local quota="" primary=()
  if [ -s "$LIMITED_LOG" ] && { [ -z "$1" ] || [ -n "${4:-}" ]; }; then
    # Every provider in the worker role, not only this (possibly quota-filtered) chain.
    quota="$tmpdir/quota.json"
    [ -n "${4:-}" ] && primary=(--primary "$4")
    python3 "$USAGE_PY" --root "$ROOT" quota-record --role worker --attempts "$LIMITED_LOG" \
      "${primary[@]}" > "$quota" 2>/dev/null \
      || python3 - "$USAGE_PY" "$ROOT" "$LIMITED_LOG" "${4:-}" > "$quota" 2>/dev/null <<'PY' || quota=""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
import usage
# quota-record refreshes first; the gates as last saved still name every worker provider.
print(json.dumps(usage.saved_quota_record(Path(sys.argv[2]), "worker", usage.read_attempts(sys.argv[3], []),
                                          sys.argv[4] or None)))
PY
  fi
  python3 - "$STATUS_FILE" "$1" "$2" "${3:-}" "$LIMITED_LOG" "$MISSING" "$quota" "${tmpdir:-}/${1:-none}.log.session" "${RESULT_FILE:+${RESULT_FILE:h}/chunk-lifecycle.json}" <<'PY2'
import json, sys
path, used, rc, fatal, limited_log, missing, quota_path, session_path, lifecycle_path = sys.argv[1:10]
session = None
if used:
    try:
        session = open(session_path).read().strip() or None
    except OSError:
        pass
quota = None
try:
    quota = json.load(open(quota_path)) if quota_path else None
except (OSError, ValueError):
    pass
try:
    lifecycle = json.load(open(lifecycle_path)) if lifecycle_path else None
except (OSError, ValueError):
    lifecycle = None
limited = []
try:
    for line in open(limited_log) if limited_log else ():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            limited.append(entry)
except OSError:
    pass
json.dump({"used": used or None, "exit": int(rc) if rc.lstrip("-").isdigit() else None,
           "fatal": fatal or None, "limited": limited, "missing": missing.split(),
           "quota": quota if isinstance(quota, dict) else None,
           # The CLI session the used provider ran (Cursor/Claude JSON session_id, Codex header).
           "session_id": session, "lifecycle": lifecycle},
          open(path, "w"), indent=1)
PY2
}

[ -n "$RESULT_FILE" ] && USAGE_LOG="${RESULT_FILE:h}/model-usage.jsonl"

# Superset workspace mode: run.sh launched us in a workspace terminal (SAFETY.md, "Ship as
# PRs"). Build in that worktree, never the repo's root checkout — Superset only credits a
# merged PR to a workspace that ran an agent, and the worktree lives outside Dropbox.
WORKTREE="${SUPERSET_WORKSPACE_PATH:-}"
if [ -n "$WORKTREE" ]; then
  cd "$WORKTREE" || { echo "FATAL: cannot cd to workspace $WORKTREE" >&2; write_status "" 2 "cannot cd to workspace $WORKTREE"; exit 2; }
fi
WORKDIR="${WORKTREE:-$CODE_DIR}"
# The root checkouts stay readable/writable for Step 2c and Step 7's post-merge pull.
extra_dirs=()
[ -n "$WORKTREE" ] && extra_dirs=(--add-dir "$CODE_DIR")
[ -n "$STAMP" ] || { echo "FATAL: --stamp required" >&2; exit 2; }
[ -n "$REQUEST_FILE" ] && [ -f "$REQUEST_FILE" ] || { echo "FATAL: --request-file required" >&2; exit 2; }
export ADVANCE_ROADMAP_REQUEST_FILE="$REQUEST_FILE"
[ -f "$USAGE_PY" ] || { echo "FATAL: missing $USAGE_PY" >&2; exit 2; }
[ -f "$SKILL_DIR/WORKER.md" ] || { echo "FATAL: missing WORKER.md" >&2; exit 2; }
[ -f "$SKILL_DIR/SAFETY.md" ] || { echo "FATAL: missing SAFETY.md" >&2; exit 2; }

WORKER_MODE="$(python3 - "$REQUEST_FILE" <<'PY'
import json, sys
try:
    request = json.load(open(sys.argv[1], encoding="utf-8"))
    mode = (request.get("orchestrator") or {}).get("worker_mode", "standard")
except (OSError, json.JSONDecodeError):
    mode = "invalid"
print(mode)
PY
)"
case "$WORKER_MODE" in
  standard|goal) ;;
  *) echo "FATAL: invalid worker_mode: $WORKER_MODE" >&2
     write_status "" 2 "invalid worker_mode: $WORKER_MODE"; exit 2 ;;
esac

if [ -z "$PROVIDERS_STR" ]; then
  PROVIDERS_STR="$(python3 "$USAGE_PY" --root "$ROOT" pick-worker-chain 2>/dev/null || true)"
fi

# shellcheck disable=SC2206
providers=(${=PROVIDERS_STR})
if [ ${#providers[@]} -eq 0 ]; then
  # Bookkeeping with Claude is still possible if the chain came back empty — but only if
  # Claude is under its gate, which the loop below checks like any other provider.
  providers=(claude)
  echo "(worker chain empty — falling back to claude for bookkeeping attempt)"
fi

# A batch supplies the strategy whose reviewers it reserved. For standalone runs,
# resolve it from the same usage.py decision review.sh uses.
WORKER_REVIEW_STRATEGY="${ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY:-}"
WORKER_REVIEW_FALLBACK_REASON="${ADVANCE_ROADMAP_BUDGET_REVIEW_FALLBACK_REASON:-}"
if [ -z "$WORKER_REVIEW_STRATEGY" ]; then
  configured_strategy="${ADVANCE_ROADMAP_REVIEW_STRATEGY:-cursor-then-codex}"
  effective_json="$(python3 "$USAGE_PY" --root "$ROOT" effective-review-strategy \
    --strategy "$configured_strategy" 2>/dev/null || true)"
  WORKER_REVIEW_STRATEGY="$configured_strategy"
  if [ -n "$effective_json" ]; then
    WORKER_REVIEW_STRATEGY="$(print -r -- "$effective_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin).get("strategy") or "")' 2>/dev/null || true)"
    WORKER_REVIEW_FALLBACK_REASON="$(print -r -- "$effective_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin).get("reason") or "")' 2>/dev/null || true)"
    [ -n "$WORKER_REVIEW_STRATEGY" ] || WORKER_REVIEW_STRATEGY="$configured_strategy"
  fi
fi
case "$WORKER_REVIEW_STRATEGY" in
  cursor-then-codex|cross-provider) ;;
  *) echo "worker: invalid effective review strategy: $WORKER_REVIEW_STRATEGY" >&2; exit 2 ;;
esac
export ADVANCE_ROADMAP_EFFECTIVE_REVIEW_STRATEGY="$WORKER_REVIEW_STRATEGY"
export ADVANCE_ROADMAP_REVIEW_FALLBACK_REASON="$WORKER_REVIEW_FALLBACK_REASON"
REVIEW_CONTEXT="Effective review strategy for this run: $WORKER_REVIEW_STRATEGY."
if [ -n "$WORKER_REVIEW_FALLBACK_REASON" ]; then
  REVIEW_CONTEXT="$REVIEW_CONTEXT
Fallback reason: $WORKER_REVIEW_FALLBACK_REASON."
fi
if [ "$WORKER_REVIEW_STRATEGY" = cross-provider ]; then
  REVIEW_CONTEXT="$REVIEW_CONTEXT
For review findings, follow the cross-provider remediation rules in WORKER.md: address them
yourself or use another under-gate provider, never a gated provider; the next reviewer must
differ from the worker and use cross-provider."
fi

BASE_PROMPT="$(cat <<EOF
Read and follow $SKILL_DIR/WORKER.md and $SKILL_DIR/SAFETY.md exactly.
The Step 7c retrospective also follows $SKILL_DIR/CODE_HEALTH.md.

This is an unattended scheduled worker run (stamp=$STAMP). You are write-capable.
Assignment JSON (read fully first):
$REQUEST_FILE

Print a \`\`\`WORKER_RESULT_JSON fence at the end per WORKER.md, plus the plain 5-line summary.
EOF
)"
BASE_PROMPT="$BASE_PROMPT

$REVIEW_CONTEXT

Continuation rules:
- Reconcile existing commits, review logs and PR/merge state before acting. Resume the saved phase.
- Continue the exact saved session when available. Do not write a recap at every milestone;
  retain existing commits, test and review artifacts as evidence. If native continuation is
  unavailable and those artifacts lack essential recovery context, write a compact handoff
  under the run directory and save its path with workqueue.py's checkpoint command.
- A crash, quota limit, failing test or merge conflict is recoverable work; handle it automatically.
- Only a decision or access that requires Jake is a human blocker. In that case include
  needs_input: true and one specific input_question in the worker result; otherwise needs_input: false.
- Do not repeat a completed merge or trust test/review evidence for a different HEAD."
if [ "${ADVANCE_ROADMAP_NO_MESSAGES:-0}" = 1 ]; then
  BASE_PROMPT="$BASE_PROMPT

Do not send Slack, email, or other messages to people during this invocation. Record any
message that would have been needed in the local result instead."
fi
if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ]; then
  BASE_PROMPT="$BASE_PROMPT

This invocation is one bounded unit, reserved phase: ${ADVANCE_ROADMAP_INCREMENTAL_PHASE:-implementing}.
Choose one concrete natural milestone in that phase, make durable progress on the assigned branch,
and return outcome yielded when that
milestone is reached without shipping; include next_phase (implementing, reviewing,
shipping, or verifying) in the existing result. Do not start the next phase unless its capacity was
explicitly reserved. Preserve the branch and existing artifacts; no recap is required.
For yielded, the result needs only outcome, provider and next_phase; a prose summary,
result fence and five-line sign-off are optional.
The watchdog may interrupt this invocation after ${ADVANCE_ROADMAP_CHUNK_TIMEOUT_S:-900} seconds;
this is a trial time boundary, not a subscription quota guarantee. Shipping still requires
the normal current-HEAD tests, review, merge and deployment checks."
  if [[ "${ADVANCE_ROADMAP_INCREMENTAL_PHASE:-implementing}" == (reviewing|shipping) ]] \
      && [ -n "${ADVANCE_ROADMAP_BUDGET_REVIEWERS:-}" ]; then
    BASE_PROMPT="$BASE_PROMPT
This unit reserves worker and reviewer capacity, so after the required current-HEAD review
and verification pass you may complete merge and deployment within this same unit."
  elif [ "${ADVANCE_ROADMAP_INCREMENTAL_PHASE:-implementing}" = implementing ]; then
    BASE_PROMPT="$BASE_PROMPT
This implementation unit has no reviewer reservation: do not merge, push to the default
branch, deploy, or close the ticket. Leave the branch unmerged and yield for review."
  fi
fi
BASE_PROMPT="$BASE_PROMPT

Invariants: classify every review finding as an instance (one defect, local fix) or an invariant (a
rule every path to a side effect must obey) before fixing it. For an invariant: before fixing, list
every code path that reaches this side effect. If more than one, propose a single owner. Prefer one
owner and mechanical enforcement over repeated patches; record the invariant and its owner, check
changed paths against it, test the owner's policy once with table-driven cases, and test entry-point
delegation. Escalate only when the owner decision exceeds the approved scope."
if [ -n "${ADVANCE_ROADMAP_TARGET_REPO:-}" ]; then
  BASE_PROMPT="$BASE_PROMPT

This daily batch assigns you ONLY repository $ADVANCE_ROADMAP_TARGET_REPO and the one
item in the assignment JSON. Scope all implementation, reconciliation, archives, and
bookkeeping to that repository. Do not start another ticket or fall back to another repo."
fi
if [ -n "$RESULT_FILE" ]; then
  BASE_PROMPT="$BASE_PROMPT

When you are finished — whatever the outcome — write that same WORKER_RESULT_JSON object
(raw JSON, no fence) to: $RESULT_FILE
run.sh records this run from that file. Without it the run is recorded as incomplete."
fi
if [ -n "$WORKTREE" ]; then
  if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ] && [ "${ADVANCE_ROADMAP_INCREMENTAL_PHASE:-implementing}" = implementing ]; then
    BASE_PROMPT="$BASE_PROMPT

Your checkout is the Superset workspace $WORKTREE, on the assigned branch. Make the bounded
implementation progress there and leave the branch unmerged; review and shipping are later phases.
Touch the root checkout under $CODE_DIR only for an authorized Step 2c directive commit."
  else
    BASE_PROMPT="$BASE_PROMPT

Your checkout is the Superset workspace $WORKTREE (a git worktree, already on the assigned
branch). Complete only the reserved phase there, including the required review and verification
before any merge. Touch the root checkout under $CODE_DIR only for Step 2c's directive commit and
Step 7's post-merge pull."
  fi
fi

# Step 5b's cross-model review. The log is what run.sh reads to prove it ran.
REVIEW_LOG="${RESULT_FILE:+${RESULT_FILE:h}/reviews.jsonl}"
REVIEW_LOG="${REVIEW_LOG:-${TMPDIR:-/tmp}/advance-roadmap-reviews-$STAMP.jsonl}"

prompt_for_provider() {
  local provider="$1" prompt
  prompt="$BASE_PROMPT

Step 5b review command (you are provider '$provider'):
$REVIEW_SH --repo <repo path> --worker $provider --round <N> --log $REVIEW_LOG"
  if [ "$WORKER_MODE" != "goal" ]; then
    print -r -- "$prompt"
    return
  fi
  case "$provider" in
    codex)  print -r -- "\$goal $prompt" ;;
    cursor|claude) print -r -- "/goal $prompt" ;;
    *) return 2 ;;
  esac
}

run_cursor() {
  local out="$1"
  local prompt
  prompt="$(prompt_for_provider cursor)" || return 2
  [ -x "$AGENT" ] || return 127
  "$AGENT" -p --force --trust --model "$CURSOR_WORKER_MODEL" \
    --workspace "$WORKDIR" \
    "${extra_dirs[@]}" \
    --add-dir "$ROOT" \
    --output-format json \
    "$prompt" >"$out.json" 2>&1
  local rc=$?
  python3 "$TOKENS_PY" cursor-json "$out.json" "$out" --final "$out.last" --role worker \
    --model "$CURSOR_WORKER_MODEL" --log "$USAGE_LOG" --session-out "$out.session" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null   # tokens.py missing or broken: keep the raw output
  return $rc
}

run_codex() {
  local out="$1"
  local prompt
  prompt="$(prompt_for_provider codex)" || return 2
  command -v "$CODEX" >/dev/null 2>&1 || return 127
  # WORKDIR may be the multi-repo parent, not a git checkout — skip the repo check.
  "$CODEX" exec \
    -o "$out.last" \
    -m "$CODEX_WORKER_MODEL" \
    -c model_reasoning_effort="$CODEX_WORKER_EFFORT" \
    -C "$WORKDIR" \
    --add-dir "$CODE_DIR" \
    --add-dir "$ROOT" \
    --skip-git-repo-check \
    --dangerously-bypass-approvals-and-sandbox \
    "$prompt" >"$out" 2>&1 </dev/null
  local rc=$?
  python3 "$TOKENS_PY" codex "$out" --role worker --model "$CODEX_WORKER_MODEL" --log "$USAGE_LOG" \
    --session-out "$out.session" 2>&1
  return $rc
}

run_claude() {
  local out="$1"
  local prompt
  prompt="$(prompt_for_provider claude)" || return 2
  [ -x "$CLAUDE" ] || return 127
  python3 "${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/claude-session" run --label "roadmap:worker:${STAMP:-unknown}" -- "$CLAUDE" -p "$prompt" \
    --model "$CLAUDE_WORKER_MODEL" \
    --effort "$CLAUDE_WORKER_EFFORT" \
    --dangerously-skip-permissions \
    --add-dir "$CODE_DIR" \
    --add-dir "$ROOT" \
    --output-format json >"$out.json" 2>&1
  local rc=$?
  python3 "$TOKENS_PY" claude-json "$out.json" "$out" --final "$out.last" --role worker \
    --model "$CLAUDE_WORKER_MODEL" --log "$USAGE_LOG" --session-out "$out.session" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null   # tokens.py missing or broken: keep the raw output
  return $rc
}

# Incremental routing is enabled only by a batch that has admitted a bounded unit.
# The exact owned session is selected by work identity, auth and checkout, never
# by a provider's global "most recent" selector.
incremental_session() {
  local provider="$1" decision="${RESULT_FILE:h}/continuation-$1.json"
  python3 "$ROOT/lib/continuation.py" --root "$ROOT" --request "$REQUEST_FILE" \
    --provider "$provider" --workspace "$WORKDIR" > "$decision" || return 1
  jq -r 'if .mode == "native" then .session_id else "" end' "$decision"
}

native_unavailable() {
  # Only retry when the CLI explicitly rejected a missing session before any
  # session/model event. Other failures may have spent quota or changed work.
  python3 - "$1" <<'PY'
import json, re, sys
try:
    text = open(sys.argv[1], encoding='utf-8', errors='replace').read()
except OSError:
    raise SystemExit(1)
events = []
for line in text.splitlines():
    try:
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(value)
    except ValueError:
        pass
if any(e.get('type') in ('thread.started', 'turn.started', 'assistant') or
       (e.get('type') == 'system' and e.get('subtype') == 'init') for e in events):
    raise SystemExit(1)
pattern = r'no (?:conversation|session|rollout) found|(?:session|conversation|rollout)(?: id)?[^\n]{0,100}not found|cannot find (?:session|conversation|rollout)'
raise SystemExit(0 if re.search(pattern, text, re.I) else 1)
PY
}

record_native_fallback() {
  python3 - "${RESULT_FILE:h}/continuation-$1.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
data.update(mode='fallback', reason='native session unavailable before model work',
            attempted_session=data.get('session_id'))
try:
    lifecycle = json.load(open(os.path.join(os.path.dirname(path), 'chunk-lifecycle.json')))
    data['native_failure_seconds'] = lifecycle.get('elapsed_seconds')
except (OSError, ValueError):
    pass
data.pop('session_id', None)
tmp = path + '.tmp'
open(tmp, 'w').write(json.dumps(data) + '\n')
os.replace(tmp, path)
PY
}

run_incremental_codex() {
  local out="$1" prompt sid
  prompt="$(prompt_for_provider codex)" || return 2
  sid="$(incremental_session codex)" || return 1
  local base=("$CODEX" exec -o "$out.last" -m "$CODEX_WORKER_MODEL"
    -c "model_reasoning_effort=$CODEX_WORKER_EFFORT" -C "$WORKDIR"
    --add-dir "$CODE_DIR" --add-dir "$ROOT" --skip-git-repo-check
    --dangerously-bypass-approvals-and-sandbox --json)
  local cmd=("${base[@]}")
  [ -n "$sid" ] && cmd+=(resume "$sid")
  cmd+=("$prompt")
  python3 "$ROOT/lib/session_stream.py" --provider codex --root "$ROOT" \
    --request "$REQUEST_FILE" --workspace "$WORKDIR" --output "$out" \
    --session-out "$out.session" --timeout "${ADVANCE_ROADMAP_CHUNK_TIMEOUT_S:-900}" \
    --lifecycle "${RESULT_FILE:h}/chunk-lifecycle.json" --budget "${RESULT_FILE:h}/batch-budget.json" -- "${cmd[@]}"
  local rc=$?
  if [ -n "$sid" ] && [ "$rc" -ne 0 ] && native_unavailable "$out"; then
    cp "$out" "${RESULT_FILE:h}/continuation-codex-error.log"
    record_native_fallback codex
    rc=0
    python3 "$ROOT/lib/session_stream.py" --provider codex --root "$ROOT" \
      --request "$REQUEST_FILE" --workspace "$WORKDIR" --output "$out" \
      --session-out "$out.session" --timeout "${ADVANCE_ROADMAP_CHUNK_TIMEOUT_S:-900}" \
      --lifecycle "${RESULT_FILE:h}/chunk-lifecycle.json" --budget "${RESULT_FILE:h}/batch-budget.json" \
      -- "${base[@]}" "$prompt" || rc=$?
  fi
  python3 "$TOKENS_PY" codex "$out" --role worker --model "$CODEX_WORKER_MODEL" \
    --log "$USAGE_LOG" --session-out "$out.session" 2>&1
  return $rc
}

run_incremental_claude() {
  local out="$1" prompt sid
  prompt="$(prompt_for_provider claude)" || return 2
  sid="$(incremental_session claude)" || return 1
  local base=(python3 "${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/claude-session" run
    --label "roadmap:worker:${STAMP:-unknown}" -- "$CLAUDE" -p "$prompt"
    --model "$CLAUDE_WORKER_MODEL" --effort "$CLAUDE_WORKER_EFFORT"
    --dangerously-skip-permissions --add-dir "$CODE_DIR" --add-dir "$ROOT"
    --verbose --output-format stream-json)
  local cmd=("${base[@]}")
  [ -n "$sid" ] && cmd+=(--resume "$sid")
  python3 "$ROOT/lib/session_stream.py" --provider claude --root "$ROOT" \
    --request "$REQUEST_FILE" --workspace "$WORKDIR" --output "$out.json" \
    --session-out "$out.session" --timeout "${ADVANCE_ROADMAP_CHUNK_TIMEOUT_S:-900}" \
    --lifecycle "${RESULT_FILE:h}/chunk-lifecycle.json" --budget "${RESULT_FILE:h}/batch-budget.json" -- "${cmd[@]}"
  local rc=$?
  if [ -n "$sid" ] && [ "$rc" -ne 0 ] && native_unavailable "$out.json"; then
    cp "$out.json" "${RESULT_FILE:h}/continuation-claude-error.log"
    record_native_fallback claude
    rc=0
    python3 "$ROOT/lib/session_stream.py" --provider claude --root "$ROOT" \
      --request "$REQUEST_FILE" --workspace "$WORKDIR" --output "$out.json" \
      --session-out "$out.session" --timeout "${ADVANCE_ROADMAP_CHUNK_TIMEOUT_S:-900}" \
      --lifecycle "${RESULT_FILE:h}/chunk-lifecycle.json" --budget "${RESULT_FILE:h}/batch-budget.json" \
      -- "${base[@]}" || rc=$?
  fi
  python3 "$TOKENS_PY" claude-json "$out.json" "$out" --final "$out.last" --role worker \
    --model "$CLAUDE_WORKER_MODEL" --log "$USAGE_LOG" --session-out "$out.session" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null
  return $rc
}

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-worker.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT INT TERM
LIMITED_LOG="$tmpdir/limited.jsonl"

record_limit() {
  # record_limit PROVIDER TEXT — note the limit and its reset in the status, then gate the provider
  # for later lanes. The note comes first, so a failed shared-state update can't lose the reset.
  python3 "$USAGE_PY" parse-limit --provider "$1" --text "$2" --scope worker > "$tmpdir/hit-$1.json" 2>/dev/null \
    || rm -f "$tmpdir/hit-$1.json"
  python3 -c 'import json, sys
hit = {}
try:
    hit = json.load(open(sys.argv[2]))
except (OSError, ValueError):
    pass
print(json.dumps({"provider": sys.argv[1], "state": "limited", "cause": sys.argv[3],
                  "reset_epoch": hit.get("reset_epoch"), "reset_at": hit.get("reset_at")}))' \
    "$1" "$tmpdir/hit-$1.json" "$2" >> "$LIMITED_LOG"
  python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$1" --text "$2" --scope worker || true
}

final_rc=1
used=""
stopped_on=""   # the provider whose result-only limit_hit ended the run
last_hit=""     # the last provider stopped by a limit banner
# A released daily-batch worker is reserved on one provider. We may route past that
# reservation only when its CLI cannot start (auth/missing); a live limit still ends here.
LEASED_WORKER="${ADVANCE_ROADMAP_BUDGET_WORKER:-}"
for provider in "${providers[@]}"; do
  if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ] && [ -n "$LEASED_WORKER" ] \
      && [ "$provider" != "$LEASED_WORKER" ]; then
    echo "(skipping $provider — no reservation for an incremental fallback)"
    continue
  fi
  out="$tmpdir/$provider.log"
  # Fresh usage before every attempt — an explicit --providers list included. Exit 3 is the
  # only "over its gate"; a usage.py failure (1, 2) is no reading, which never blocks.
  gate="$(python3 "$USAGE_PY" --root "$ROOT" gate --role worker --provider "$provider" 2>/dev/null)"
  if [ $? -eq 3 ]; then
    if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ] && [ "${ADVANCE_ROADMAP_JOB_OVERRIDE:-0}" = 1 ] \
       && [ "$provider" = "${ADVANCE_ROADMAP_BUDGET_WORKER:-}" ] \
       && print -r -- "$gate" | python3 -c 'import json,sys; g=json.load(sys.stdin); raise SystemExit(0 if g.get("state") == "over" and "rate limit reached" not in (g.get("reason") or "").lower() else 1)' \
       && python3 - "${RESULT_FILE:h}/batch-budget.json" "$provider" <<'PY'
import json, sys, time
try:
    b = json.load(open(sys.argv[1]))
    reading = b['baseline']
    ok = (b.get('job_override') is True and b.get('worker') == sys.argv[2]
          and 0 <= time.time() - reading['observed_at'] <= 60
          and bool(reading['windows']) and all(isinstance(w.get('live'), (int, float))
                                                and w['live'] < 100 and w.get('cap') == 100
                                                for w in reading['windows'].values()))
except (OSError, ValueError, TypeError, KeyError):
    ok = False
raise SystemExit(0 if ok else 1)
PY
    then
      echo "(job-only override admitted $provider above its normal forecast gate)"
    else
    echo "(skipping $provider — over its usage gate: $gate)"
    print -r -- "$gate" >> "$LIMITED_LOG"
    continue
    fi
  fi
  echo "=== worker try provider=$provider mode=$WORKER_MODE stamp=$STAMP ($(date)) ==="
  rc=0
  # Per-provider out paths already isolate attempts; still clear so a retry never sees stale JSON.
  rm -f "$out.last" "$out.json" "$out.session"
  [ -n "$RESULT_FILE" ] && rm -f "$RESULT_FILE"   # a failed-over attempt's result must not count
  rm -f "$REVIEW_LOG"                              # nor its review rounds
  if [ -n "${SUPERSET_TERMINAL_ID:-}" ]; then
    # What Superset's own wrappers export: names this terminal's agent, so the nested
    # review.sh reviewer's hook events are dropped instead of relabeling the binding.
    case "$provider" in cursor) export SUPERSET_AGENT_ID=cursor-agent ;; *) export SUPERSET_AGENT_ID="$provider" ;; esac
  fi
  case "$provider" in
    cursor) run_cursor "$out" || rc=$? ;;
    codex)  if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ]; then
              run_incremental_codex "$out" || rc=$?
            else run_codex "$out" || rc=$?; fi ;;
    claude) if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ]; then
              run_incremental_claude "$out" || rc=$?
            else run_claude "$out" || rc=$?; fi ;;
    *) echo "unknown provider: $provider" >&2; continue ;;
  esac
  cat "$out"
  echo "=== worker provider=$provider exit=$rc ==="

  # Watchdog, quota and operator stops are intentional yields. A provider's
  # transcript can quote authentication text, so do not mistake it for a
  # startup failure or try another provider after a bounded stop.
  if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ] \
     && python3 - "${RESULT_FILE:h}/chunk-lifecycle.json" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    value = {}
raise SystemExit(0 if value.get('forced') else 1)
PY
  then
    used="$provider"
    final_rc="$rc"
    break
  fi

  # Live-limit rule (usage.py live-limit): a banner in the transcript counts only when this
  # call failed — non-zero exit or no final answer — never when a successful answer merely
  # quoted docs or prior logs. Claude/Cursor JSON with is_error:true is a live limit even on
  # exit 0 (the result text is also written to .last). Result-only limit_hit is below.
  live_json=()
  [ -f "$out.json" ] && live_json=(--result-json "$out.json")
  if text="$(python3 "$USAGE_PY" live-limit --transcript-file "$out" --exit "$rc" \
      --final-file "$out.last" "${live_json[@]}" 2>/dev/null)" && [ -n "$text" ]; then
    echo "(limit for $provider — recording and failing over)"
    record_limit "$provider" "$text"
    last_hit="$provider"
    if [ -n "$LEASED_WORKER" ]; then
      echo "(batch reserved $LEASED_WORKER — not spending an unleased fallback after a limit)"
      used="$provider"
      final_rc=$rc
      stopped_on="$provider"
      break
    fi
    continue
  fi

  if [ "$rc" -eq 127 ]; then
    echo "(binary missing for $provider — failing over)"
    MISSING="$MISSING $provider"
    if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ]; then used="$provider"; final_rc="$rc"; break; fi
    continue
  fi

  # An auth/login rejection happens before the agent starts and cannot produce a result.
  # Treat it like a missing CLI so a daily batch can use its next eligible provider.
  if [ "$rc" -ne 0 ] && [ ! -s "$out.last" ] && grep -Eiq \
    'Authentication required|agent login|set CURSOR_API_KEY|not (logged|signed) in|failed to authenticate|API key is invalid|invalid (API )?key' \
    "$out" 2>/dev/null; then
    echo "(auth/startup failure for $provider — failing over)"
    MISSING="$MISSING $provider"
    if [ "${ADVANCE_ROADMAP_INCREMENTAL:-0}" = 1 ]; then used="$provider"; final_rc="$rc"; break; fi
    continue
  fi

  used="$provider"
  final_rc=$rc
  break
done

if [ -z "$used" ]; then
  echo "=== worker exhausted all providers ==="
  write_status "" 3 "" "$last_hit"
  exit 3
fi

# The agent's file is the record. Fall back to this provider's own final answer only.
if [ -n "$RESULT_FILE" ]; then
  python3 - "$RESULT_FILE" "$tmpdir/$used.log.last" <<'PY2'
import json, sys
path, out = sys.argv[1], sys.argv[2]
try:
    if isinstance(json.load(open(path, encoding="utf-8")), dict):
        sys.exit(0)
except (OSError, ValueError):
    pass
try:
    text = open(out, encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
label, end = "```WORKER_RESULT_JSON", len(text)
while (i := text.rfind(label, 0, end)) >= 0:
    end = i
    nl = text.find("\n", i)
    j = text.find("```", nl + 1) if nl >= 0 else -1
    if j < 0:
        continue
    try:
        obj = json.loads(text[nl + 1:j])
    except ValueError:
        continue
    if isinstance(obj, dict):
        obj["_source"] = "stdout-fence"
        json.dump(obj, open(path, "w"), indent=1)
        print("(worker result file missing — recovered from its final answer's fence)")
        sys.exit(0)
print("(worker wrote no result file and no valid fence)")
PY2
  # A limit the agent only reported in its result (no banner on stdout) must still gate it.
  limit_text="$(python3 -c 'import json, sys
try:
    r = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(0)
if isinstance(r, dict) and r.get("outcome") == "limit_hit":
    print(" ".join(str(r.get("limit_text") or "").split()) or "worker reported limit_hit")' "$RESULT_FILE")"
  if [ -n "$limit_text" ]; then
    echo "(worker result reports a limit for $used — recording it)"
    record_limit "$used" "$limit_text"
    stopped_on="$used"
  fi
fi
write_status "$used" "$final_rc" "" "$stopped_on"

echo "=== worker used=$used exit=$final_rc finished $(date) ==="
exit $final_rc
