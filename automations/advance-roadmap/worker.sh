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

CLAUDE_WORKER_MODEL="${ADVANCE_ROADMAP_CLAUDE_WORKER_MODEL:-claude-opus-5}"
CODEX_WORKER_MODEL="${ADVANCE_ROADMAP_CODEX_WORKER_MODEL:-$CODEX_MODEL_DEFAULT}"
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
  python3 - "$STATUS_FILE" "$1" "$2" "${3:-}" "$LIMITED_LOG" "$MISSING" "$quota" "${tmpdir:-}/${1:-none}.log.session" <<'PY2'
import json, sys
path, used, rc, fatal, limited_log, missing, quota_path, session_path = sys.argv[1:9]
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
           "session_id": session},
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
Code-health passes and the Step 7c retrospective also follow $SKILL_DIR/CODE_HEALTH.md.

This is an unattended scheduled worker run (stamp=$STAMP). You are write-capable.
Assignment JSON (read fully first):
$REQUEST_FILE

Print a \`\`\`WORKER_RESULT_JSON fence at the end per WORKER.md, plus the plain 5-line summary.
EOF
)"
BASE_PROMPT="$BASE_PROMPT

$REVIEW_CONTEXT"
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
  BASE_PROMPT="$BASE_PROMPT

Your checkout is the Superset workspace $WORKTREE (a git worktree, already on the assigned
branch). Build, commit, review (review.sh --repo $WORKTREE) and open/merge the PR from there,
per WORKER.md Steps 3 and 7. Touch the repo's root checkout under $CODE_DIR only for Step 2c's
directive commit and Step 7's post-merge pull."
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
    -c model_reasoning_effort=high \
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
    --effort high \
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
for provider in "${providers[@]}"; do
  out="$tmpdir/$provider.log"
  # Fresh usage before every attempt — an explicit --providers list included. Exit 3 is the
  # only "over its gate"; a usage.py failure (1, 2) is no reading, which never blocks.
  gate="$(python3 "$USAGE_PY" --root "$ROOT" gate --role worker --provider "$provider" 2>/dev/null)"
  if [ $? -eq 3 ]; then
    echo "(skipping $provider — over its usage gate: $gate)"
    print -r -- "$gate" >> "$LIMITED_LOG"
    continue
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
    codex)  run_codex "$out"  || rc=$? ;;
    claude) run_claude "$out" || rc=$? ;;
    *) echo "unknown provider: $provider" >&2; continue ;;
  esac
  cat "$out"
  echo "=== worker provider=$provider exit=$rc ==="

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
    continue
  fi

  if [ "$rc" -eq 127 ]; then
    echo "(binary missing for $provider — failing over)"
    MISSING="$MISSING $provider"
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
