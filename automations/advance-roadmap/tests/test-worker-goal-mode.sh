#!/bin/zsh
set -eu

TEST_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../../skills/advance-roadmap" && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-goal-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT INT TERM

mkdir -p "$TMP_ROOT/root/lib" "$TMP_ROOT/bin"
: > "$TMP_ROOT/root/lib/usage.py"

cat > "$TMP_ROOT/bin/fake-provider" <<'SH'
#!/bin/zsh
set -eu
provider="$(basename "$0")"
case "$provider" in
  agent|codex) prompt="${@[-1]}" ;;
  claude)
    prompt=""
    args=("$@")
    for ((i = 1; i <= ${#args[@]}; i++)); do
      if [ "${args[$i]}" = "-p" ]; then
        prompt="${args[$((i + 1))]}"
        break
      fi
    done
    ;;
  *) exit 2 ;;
esac
print -r -- "$prompt" > "$CAPTURE"
print -r -- "fake provider completed"
SH
chmod +x "$TMP_ROOT/bin/fake-provider"
ln -s fake-provider "$TMP_ROOT/bin/agent"
ln -s fake-provider "$TMP_ROOT/bin/codex"
ln -s fake-provider "$TMP_ROOT/bin/claude"

cat > "$TMP_ROOT/standard.json" <<'JSON'
{"mode":"implement","orchestrator":{"worker_mode":"standard"}}
JSON
cat > "$TMP_ROOT/goal.json" <<'JSON'
{"mode":"implement","orchestrator":{"worker_mode":"goal"}}
JSON

run_case() {
  local provider="$1" request="$2" expected="$3"
  local capture="$TMP_ROOT/$provider-${request:t}.prompt"
  CAPTURE="$capture" \
  ADVANCE_ROADMAP_ROOT="$TMP_ROOT/root" \
  ADVANCE_ROADMAP_SKILL_DIR="$SKILL_DIR" \
  ADVANCE_ROADMAP_AGENT_BIN="$TMP_ROOT/bin/agent" \
  ADVANCE_ROADMAP_CODEX_BIN="$TMP_ROOT/bin/codex" \
  ADVANCE_ROADMAP_CLAUDE_BIN="$TMP_ROOT/bin/claude" \
    "$TEST_DIR/worker.sh" --stamp test --request-file "$request" --provider "$provider" >/dev/null

  local first_line
  first_line="$(head -n 1 "$capture")"
  if [ "$first_line" != "$expected" ]; then
    print -u2 -- "$provider: expected '$expected', got '$first_line'"
    return 1
  fi
}

for provider in cursor codex claude; do
  run_case "$provider" "$TMP_ROOT/standard.json" \
    "Read and follow $SKILL_DIR/WORKER.md and $SKILL_DIR/SAFETY.md exactly."
done
run_case cursor "$TMP_ROOT/goal.json" "/goal Read and follow $SKILL_DIR/WORKER.md and $SKILL_DIR/SAFETY.md exactly."
run_case codex "$TMP_ROOT/goal.json" '$goal Read and follow '"$SKILL_DIR"'/WORKER.md and '"$SKILL_DIR"'/SAFETY.md exactly.'
run_case claude "$TMP_ROOT/goal.json" "/goal Read and follow $SKILL_DIR/WORKER.md and $SKILL_DIR/SAFETY.md exactly."

print -- "worker goal-mode routing: ok"
