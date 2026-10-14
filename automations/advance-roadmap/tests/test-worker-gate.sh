#!/bin/zsh
# worker.sh checks every provider's usage gate before trying it — an explicit --providers
# list included — and exits 3 with the gated providers in its status when none remain.
set -eu

TEST_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../../skills/advance-roadmap" && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-gate-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT INT TERM
unset SUPERSET_TERMINAL_ID

mkdir -p "$TMP_ROOT/root/lib" "$TMP_ROOT/bin"
cp "$TEST_DIR/lib/models.sh" "$TMP_ROOT/root/lib/"

cat > "$TMP_ROOT/root/lib/usage.py" <<'PY'
import json, os, sys
args = sys.argv[1:]
if os.environ.get("FAKE_CRASH"):
    raise RuntimeError("state file unreadable")
if "gate" in args:
    provider = args[args.index("--provider") + 1]
    if provider in os.environ.get("FAKE_GATED", "").split():
        print(json.dumps({"provider": provider, "role": "worker", "available": False, "state": "over",
                          "reason": "5h 71% >= 70%", "reset_epoch": 1760400000, "reset_at": "x"}))
        sys.exit(3)
    print(json.dumps({"provider": provider, "available": True, "state": "ok"}))
PY

cat > "$TMP_ROOT/bin/fake-provider" <<'SH'
#!/bin/zsh
print -r -- "$(basename "$0")" >> "$CALLS"
print -r -- "fake provider completed"
SH
chmod +x "$TMP_ROOT/bin/fake-provider"
for p in agent codex claude; do ln -s fake-provider "$TMP_ROOT/bin/$p"; done
print -r -- '{"mode":"implement","orchestrator":{"worker_mode":"standard"}}' > "$TMP_ROOT/request.json"

run_worker() {
  local gated="$1" status_file="$2"
  : > "$TMP_ROOT/calls"
  FAKE_GATED="$gated" FAKE_CRASH="${FAKE_CRASH:-}" CALLS="$TMP_ROOT/calls" \
  PERSONAL_CODE_DIR="$TMP_ROOT" \
  ADVANCE_ROADMAP_ROOT="$TMP_ROOT/root" \
  ADVANCE_ROADMAP_SKILL_DIR="$SKILL_DIR" \
  ADVANCE_ROADMAP_AGENT_BIN="$TMP_ROOT/bin/agent" \
  ADVANCE_ROADMAP_CODEX_BIN="$TMP_ROOT/bin/codex" \
  ADVANCE_ROADMAP_CLAUDE_BIN="$TMP_ROOT/bin/claude" \
    "$TEST_DIR/worker.sh" --stamp test --request-file "$TMP_ROOT/request.json" \
      --providers "cursor codex claude" --status-file "$status_file" >/dev/null
}

fail() { print -u2 -- "worker gate: $1"; exit 1; }

# Cursor and Codex over their gates: only Claude runs.
run_worker "cursor codex" "$TMP_ROOT/status-1.json" || fail "expected success with claude under its gate"
[ "$(cat "$TMP_ROOT/calls")" = "claude" ] || fail "expected only claude to run, got: $(cat "$TMP_ROOT/calls")"
python3 - "$TMP_ROOT/status-1.json" <<'PY' || fail "status for a partial gate is wrong"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["used"] == "claude", s
assert [e["provider"] for e in s["limited"]] == ["cursor", "codex"], s
PY

# Everyone over: nothing runs, exit 3, every provider in "limited".
rc=0
run_worker "cursor codex claude" "$TMP_ROOT/status-2.json" || rc=$?
[ "$rc" -eq 3 ] || fail "expected exit 3 with every provider gated, got $rc"
[ ! -s "$TMP_ROOT/calls" ] || fail "a gated provider ran: $(cat "$TMP_ROOT/calls")"
python3 - "$TMP_ROOT/status-2.json" <<'PY' || fail "status for a full gate is wrong"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["used"] is None and s["exit"] == 3, s
assert [e["provider"] for e in s["limited"]] == ["cursor", "codex", "claude"], s
assert all(e["state"] == "over" for e in s["limited"]), s
PY

# A crashing usage.py (exit 1) is no reading, not a gate: the first provider still runs.
FAKE_CRASH=1 run_worker "" "$TMP_ROOT/status-3.json" || fail "a usage.py crash blocked the worker"
[ "$(cat "$TMP_ROOT/calls")" = "agent" ] || fail "expected cursor to run despite the crash, got: $(cat "$TMP_ROOT/calls")"

print -- "worker usage gate: ok"
