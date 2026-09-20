#!/bin/zsh
# Write-capable worker for advance-roadmap. Invoked by run.sh.
#
#   worker.sh --stamp STAMP --request-file PATH [--providers "cursor codex claude"]
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
CODE_DIR="/Users/jake/Dropbox/code"
SKILL_DIR="${ADVANCE_ROADMAP_SKILL_DIR:-/Users/jake/.claude/skills/advance-roadmap}"
USAGE_PY="$ROOT/lib/usage.py"

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

while [ $# -gt 0 ]; do
  case "$1" in
    --stamp) STAMP="$2"; shift 2 ;;
    --request-file) REQUEST_FILE="$2"; shift 2 ;;
    --providers) PROVIDERS_STR="$2"; shift 2 ;;
    --provider) PROVIDERS_STR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$STAMP" ] || { echo "FATAL: --stamp required" >&2; exit 2; }
[ -n "$REQUEST_FILE" ] && [ -f "$REQUEST_FILE" ] || { echo "FATAL: --request-file required" >&2; exit 2; }
[ -f "$USAGE_PY" ] || { echo "FATAL: missing $USAGE_PY" >&2; exit 2; }
[ -f "$SKILL_DIR/WORKER.md" ] || { echo "FATAL: missing WORKER.md" >&2; exit 2; }
[ -f "$SKILL_DIR/SAFETY.md" ] || { echo "FATAL: missing SAFETY.md" >&2; exit 2; }

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

PROMPT="$(cat <<EOF
Read and follow $SKILL_DIR/WORKER.md and $SKILL_DIR/SAFETY.md exactly.

This is an unattended scheduled worker run (stamp=$STAMP). You are write-capable.
Assignment JSON (read fully first):
$REQUEST_FILE

Print a \`\`\`WORKER_RESULT_JSON fence at the end per WORKER.md, plus the plain 5-line summary.
EOF
)"

run_cursor() {
  local out="$1"
  [ -x "$AGENT" ] || return 127
  "$AGENT" -p --force --trust --model "$CURSOR_WORKER_MODEL" \
    --workspace "$CODE_DIR" \
    --output-format text \
    "$PROMPT" >"$out" 2>&1
}

run_codex() {
  local out="$1"
  command -v "$CODEX" >/dev/null 2>&1 || return 127
  # CODE_DIR is a multi-repo parent, not a git checkout — skip the repo check.
  "$CODEX" exec \
    -m "$CODEX_WORKER_MODEL" \
    -c model_reasoning_effort=high \
    -C "$CODE_DIR" \
    --add-dir "$CODE_DIR" \
    --skip-git-repo-check \
    --dangerously-bypass-approvals-and-sandbox \
    "$PROMPT" >"$out" 2>&1
}

run_claude() {
  local out="$1"
  [ -x "$CLAUDE" ] || return 127
  "$CLAUDE" -p "$PROMPT" \
    --model "$CLAUDE_WORKER_MODEL" \
    --effort high \
    --dangerously-skip-permissions \
    --add-dir "$CODE_DIR" \
    --output-format text >"$out" 2>&1
}

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-worker.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT INT TERM

final_rc=1
used=""
for provider in "${providers[@]}"; do
  out="$tmpdir/$provider.log"
  echo "=== worker try provider=$provider stamp=$STAMP ($(date)) ==="
  rc=0
  case "$provider" in
    cursor) run_cursor "$out" || rc=$? ;;
    codex)  run_codex "$out"  || rc=$? ;;
    claude) run_claude "$out" || rc=$? ;;
    *) echo "unknown provider: $provider" >&2; continue ;;
  esac
  cat "$out"
  echo "=== worker provider=$provider exit=$rc ==="

  if python3 "$USAGE_PY" --root "$ROOT" detect-limit --file "$out"; then
    text="$(rg -m1 -i 'session limit|usage limit|rate.?limit|hit your limit|quota exceeded|out of (usage|credits|quota)' "$out" || true)"
    [ -n "$text" ] || text="limit detected for $provider"
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
  exit 3
fi

echo "=== worker used=$used exit=$final_rc finished $(date) ==="
exit $final_rc
