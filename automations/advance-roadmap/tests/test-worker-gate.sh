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
if "parse-limit" in args:
    print(json.dumps({"provider": args[args.index("--provider") + 1], "exact_cli_text": args[args.index("--text") + 1],
                      "reset_epoch": 1760403600, "reset_at": "2025-10-13T21:00:00-04:00"}))
if "record-limit" in args:
    provider, text = args[args.index("--provider") + 1], args[args.index("--text") + 1]
    with open(os.environ["LIMITS"], "a") as f:
        f.write(f"{provider}|{text}\n")
PY

cat > "$TMP_ROOT/bin/fake-provider" <<'SH'
#!/bin/zsh
print -r -- "$(basename "$0")" >> "$CALLS"
[ -n "${FAKE_RESULT:-}" ] && print -r -- "$FAKE_RESULT" > "$RESULT_PATH"
print -r -- "fake provider completed"
SH
chmod +x "$TMP_ROOT/bin/fake-provider"
for p in agent codex claude; do ln -s fake-provider "$TMP_ROOT/bin/$p"; done
print -r -- '{"mode":"implement","orchestrator":{"worker_mode":"standard"}}' > "$TMP_ROOT/request.json"

run_worker() {
  local gated="$1" status_file="$2"
  : > "$TMP_ROOT/calls"
  : > "$TMP_ROOT/limits"
  FAKE_GATED="$gated" FAKE_CRASH="${FAKE_CRASH:-}" CALLS="$TMP_ROOT/calls" LIMITS="$TMP_ROOT/limits" \
  FAKE_RESULT="${FAKE_RESULT:-}" RESULT_PATH="$TMP_ROOT/result.json" \
  PERSONAL_CODE_DIR="$TMP_ROOT" \
  ADVANCE_ROADMAP_ROOT="$TMP_ROOT/root" \
  ADVANCE_ROADMAP_SKILL_DIR="$SKILL_DIR" \
  ADVANCE_ROADMAP_AGENT_BIN="$TMP_ROOT/bin/agent" \
  ADVANCE_ROADMAP_CODEX_BIN="$TMP_ROOT/bin/codex" \
  ADVANCE_ROADMAP_CLAUDE_BIN="$TMP_ROOT/bin/claude" \
    "$TEST_DIR/worker.sh" --stamp test --request-file "$TMP_ROOT/request.json" \
      --providers "cursor codex claude" --status-file "$status_file" \
      --result-file "$TMP_ROOT/result.json" >/dev/null
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

# A limit_hit reported only in the result JSON (no banner on stdout) is still recorded, with its reset.
FAKE_RESULT='{"outcome": "limit_hit", "provider": "cursor", "limit_text": "usage limit, resets 5pm"}' \
  run_worker "" "$TMP_ROOT/status-4.json" || fail "a reported limit_hit failed the worker"
[ "$(cat "$TMP_ROOT/limits")" = "cursor|usage limit, resets 5pm" ] \
  || fail "result-only limit was not recorded: $(cat "$TMP_ROOT/limits")"
python3 - "$TMP_ROOT/status-4.json" <<'PY' || fail "status for a result-only limit is wrong"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["used"] == "cursor", s
assert s["limited"] == [{"provider": "cursor", "state": "limited", "cause": "usage limit, resets 5pm",
                         "reset_epoch": 1760403600, "reset_at": "2025-10-13T21:00:00-04:00"}], s
PY

# Any other outcome records nothing.
FAKE_RESULT='{"outcome": "shipped", "provider": "cursor"}' run_worker "" "$TMP_ROOT/status-5.json" \
  || fail "a shipped result failed the worker"
[ ! -s "$TMP_ROOT/limits" ] || fail "a shipped result recorded a limit: $(cat "$TMP_ROOT/limits")"

print -- "worker usage gate: ok"
