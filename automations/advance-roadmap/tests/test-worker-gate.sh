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
cp "$TEST_DIR/lib/models.sh" "$TEST_DIR/lib/tokens.py" "$TMP_ROOT/root/lib/"
# Real usage.py for CLI assertions; the gate/live-limit stub below shadows it for worker.sh.

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
if "live-limit" in args:
    import re
    transcript = ""
    if "--transcript-file" in args:
        transcript = open(args[args.index("--transcript-file") + 1]).read()
    elif "--text" in args:
        transcript = args[args.index("--text") + 1]
    final = args[args.index("--final") + 1] if "--final" in args else ""
    if "--final-file" in args:
        try:
            final = open(args[args.index("--final-file") + 1]).read()
        except OSError:
            pass
    exit_code = int(args[args.index("--exit") + 1]) if "--exit" in args else None
    result = None
    if "--result-json" in args:
        raw = open(args[args.index("--result-json") + 1]).read()
        try:
            result = json.loads(raw)
        except ValueError:
            for line in reversed(raw.splitlines()):
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and "result" in obj:
                    result = obj
                    break
        if not isinstance(result, dict):
            result = None
    if isinstance(result, dict):
        if result.get("outcome") == "limit_hit":
            print(" ".join(str(result.get("limit_text") or "").split()) or "provider reported limit_hit")
            sys.exit(0)
        err = result.get("is_error") or result.get("subtype") in ("error", "error_during_execution")
        if err:
            blob = "\n".join(str(result.get(k) or "") for k in ("result", "error", "error_message", "message"))
            m = re.search(
                r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
                r"|quota exceeded|out of (?:usage|credits|quota)\b)", blob, re.I | re.M)
            if m:
                end = blob.find("\n", m.start())
                print(blob[m.start():end if end >= 0 else len(blob)].strip())
                sys.exit(0)
            if re.search(r"usage.?limit|hit your session limit|quota.?exceeded", blob, re.I) and blob.strip():
                print(blob.strip().splitlines()[0].strip())
                sys.exit(0)
    m = re.search(
        r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
        r"|quota exceeded|out of (?:usage|credits|quota)\b)", transcript, re.I | re.M)
    if not m:
        sys.exit(1)
    end = transcript.find("\n", m.start())
    banner = transcript[m.start():end if end >= 0 else len(transcript)].strip()
    if final.strip() and (exit_code is None or exit_code == 0):
        sys.exit(1)
    print(banner)
    sys.exit(0)
if "record-limit" in args:
    provider, text = args[args.index("--provider") + 1], args[args.index("--text") + 1]
    with open(os.environ["LIMITS"], "a") as f:
        f.write(f"{provider}|{text}\n")
PY

cat > "$TMP_ROOT/bin/fake-provider" <<'SH'
#!/bin/zsh
print -r -- "$(basename "$0")" >> "$CALLS"
[ -n "${FAKE_RESULT:-}" ] && print -r -- "$FAKE_RESULT" > "$RESULT_PATH"
# Zero-exit Claude/Cursor JSON with is_error:true (banner only in the result object).
# Codex is plain-text; only the JSON CLIs (agent/claude) emit this shape.
if [ -n "${FAKE_JSON_LIMIT:-}" ] && [ "$(basename "$0")" != "codex" ]; then
  python3 -c 'import json; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":"You'\''ve hit your usage limit. Resets 5pm.","session_id":"x"}))'
  exit 0
fi
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

# Cursor JSON is_error:true on exit 0 must fail over (needs --result-json on the live-limit call).
: > "$TMP_ROOT/calls"; : > "$TMP_ROOT/limits"
FAKE_JSON_LIMIT=1 FAKE_GATED="" CALLS="$TMP_ROOT/calls" LIMITS="$TMP_ROOT/limits" \
  PERSONAL_CODE_DIR="$TMP_ROOT" \
  ADVANCE_ROADMAP_ROOT="$TMP_ROOT/root" \
  ADVANCE_ROADMAP_SKILL_DIR="$SKILL_DIR" \
  ADVANCE_ROADMAP_AGENT_BIN="$TMP_ROOT/bin/agent" \
  ADVANCE_ROADMAP_CODEX_BIN="$TMP_ROOT/bin/codex" \
  ADVANCE_ROADMAP_CLAUDE_BIN="$TMP_ROOT/bin/claude" \
  "$TEST_DIR/worker.sh" --stamp test --request-file "$TMP_ROOT/request.json" \
    --providers "cursor codex" --status-file "$TMP_ROOT/status-json-limit.json" \
    --result-file "$TMP_ROOT/result.json" >/dev/null \
  || fail "json is_error worker failed over incorrectly"
[ "$(cat "$TMP_ROOT/calls")" = $'agent\ncodex' ] \
  || fail "json is_error did not fail over cursor→codex: $(cat "$TMP_ROOT/calls")"
case "$(cat "$TMP_ROOT/limits")" in
  *"cursor|You've hit your usage limit. Resets 5pm."*) ;;
  *) fail "json is_error was not recorded: $(cat "$TMP_ROOT/limits")" ;;
esac

# Real usage.py live-limit CLI: echoed banner suppressed; is_error JSON counts on exit 0.
cp "$TEST_DIR/lib/usage.py" "$TMP_ROOT/real-usage.py"
banner=$'echoed prior log\nYou\'ve hit your usage limit. Resets 5pm.\n'
python3 "$TMP_ROOT/real-usage.py" live-limit --text "$banner" --exit 0 --final "done" >/dev/null 2>&1 \
  && fail "live-limit treated a successful final answer as limited"
python3 "$TMP_ROOT/real-usage.py" live-limit --text "$banner" --exit 1 --final "" >/dev/null 2>&1 \
  || fail "live-limit missed a real limit failure"
print -r -- '{"type":"result","is_error":true,"result":"You'\''ve hit your usage limit. Resets 5pm."}' \
  > "$TMP_ROOT/err.json"
got="$(python3 "$TMP_ROOT/real-usage.py" live-limit --text ok --exit 0 --final "You'\''ve hit your usage limit. Resets 5pm." \
  --result-json "$TMP_ROOT/err.json")" \
  || fail "live-limit missed zero-exit is_error JSON"
[ "$got" = "You've hit your usage limit. Resets 5pm." ] \
  || fail "live-limit is_error text wrong: $got"

print -- "worker usage gate: ok"
