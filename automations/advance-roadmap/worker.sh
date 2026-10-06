#!/bin/zsh
# Write-capable worker for advance-roadmap. Invoked by run.sh.
#
#   worker.sh --stamp STAMP --request-file PATH [--providers "cursor codex claude"]
#             [--result-file PATH] [--status-file PATH]
#
# --result-file: where the agent writes its WORKER_RESULT_JSON (the record run.sh
#   keeps). If the agent doesn't, we fall back to the last valid fence in that
#   provider's own stdout — never the combined run log.
# --status-file: what this script observed (provider used, exit, fatal reason).
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
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
CODEX_WORKER_MODEL="${ADVANCE_ROADMAP_CODEX_WORKER_MODEL:-gpt-5.6-sol}"
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

write_status() {
  # write_status USED EXIT [FATAL]
  [ -n "$STATUS_FILE" ] || return 0
  python3 - "$STATUS_FILE" "$1" "$2" "${3:-}" <<'PY2'
import json, sys
path, used, rc, fatal = sys.argv[1:5]
json.dump({"used": used or None, "exit": int(rc) if rc.lstrip("-").isdigit() else None,
           "fatal": fatal or None}, open(path, "w"), indent=1)
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
  # Bookkeeping with Claude still possible if chain empty but claude binary exists
  providers=(claude)
  echo "(worker chain empty — falling back to claude for bookkeeping attempt)"
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
  python3 "$TOKENS_PY" cursor-json "$out.json" "$out" --role worker --model "$CURSOR_WORKER_MODEL" \
    --log "$USAGE_LOG" 2>&1
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
    -m "$CODEX_WORKER_MODEL" \
    -c model_reasoning_effort=high \
    -C "$WORKDIR" \
    --add-dir "$CODE_DIR" \
    --add-dir "$ROOT" \
    --skip-git-repo-check \
    --dangerously-bypass-approvals-and-sandbox \
    "$prompt" >"$out" 2>&1 </dev/null
  local rc=$?
  python3 "$TOKENS_PY" codex "$out" --role worker --model "$CODEX_WORKER_MODEL" --log "$USAGE_LOG" 2>&1
  return $rc
}

run_claude() {
  local out="$1"
  local prompt
  prompt="$(prompt_for_provider claude)" || return 2
  [ -x "$CLAUDE" ] || return 127
  "$CLAUDE" -p "$prompt" \
    --model "$CLAUDE_WORKER_MODEL" \
    --effort high \
    --dangerously-skip-permissions \
    --add-dir "$CODE_DIR" \
    --add-dir "$ROOT" \
    --output-format json >"$out.json" 2>&1
  local rc=$?
  python3 "$TOKENS_PY" claude-json "$out.json" "$out" --role worker --model "$CLAUDE_WORKER_MODEL" \
    --log "$USAGE_LOG" 2>&1
  [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null   # tokens.py missing or broken: keep the raw output
  return $rc
}

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-worker.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT INT TERM

final_rc=1
used=""
for provider in "${providers[@]}"; do
  out="$tmpdir/$provider.log"
  echo "=== worker try provider=$provider mode=$WORKER_MODE stamp=$STAMP ($(date)) ==="
  rc=0
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

  if text="$(rg -m1 -i \
      '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
      "$out" || true)" && [ -n "$text" ]; then
    echo "(limit for $provider — recording and failing over)"
    python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$provider" --text "$text" || true
    continue
  fi

  if [ "$rc" -eq 127 ]; then
    echo "(binary missing for $provider — failing over)"
    continue
  fi

  used="$provider"
  final_rc=$rc
  break
done

if [ -z "$used" ]; then
  echo "=== worker exhausted all providers ==="
  write_status "" 3
  exit 3
fi

# The agent's file is the record. Fall back to this provider's own stdout only.
if [ -n "$RESULT_FILE" ]; then
  python3 - "$RESULT_FILE" "$tmpdir/$used.log" <<'PY2'
import json, sys
path, out = sys.argv[1], sys.argv[2]
try:
    if isinstance(json.load(open(path, encoding="utf-8")), dict):
        sys.exit(0)
except (OSError, ValueError):
    pass
text = open(out, encoding="utf-8", errors="replace").read()
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
        print("(worker result file missing — recovered from stdout fence)")
        sys.exit(0)
print("(worker wrote no result file and no valid fence)")
PY2
fi
write_status "$used" "$final_rc"

echo "=== worker used=$used exit=$final_rc finished $(date) ==="
exit $final_rc
