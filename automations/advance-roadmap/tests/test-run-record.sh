#!/bin/zsh
# End-to-end: run.sh + worker.sh with fake providers → one runs.jsonl row per run,
# outcomes from result files and exit codes (never log text), and the error alert.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-record-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM

ROOT="$TMP/root"; SKILLS="$TMP/skills"; BIN="$TMP/bin"
mkdir -p "$ROOT/lib" "$SKILLS" "$BIN"
cp "$HERE/run.sh" "$HERE/worker.sh" "$ROOT/"
cp "$HERE/lib/runrecord.py" "$ROOT/lib/"
for f in ORCHESTRATOR.md SAFETY.md WORKER.md; do echo stub > "$SKILLS/$f"; done

cat > "$ROOT/lib/usage.py" <<'PY'
import sys
cmd = sys.argv[-1]
print({"pick-orchestrator": "codex", "pick-worker-chain": "cursor"}.get(cmd, ""))
PY

# Fake Codex orchestrator: echoes a decoy fence into stdout (like the real one
# echoing ORCHESTRATOR.md), and puts the real answer only in the -o file.
cat > "$BIN/codex" <<'SH'
#!/bin/zsh
last=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { last="$2"; shift; }; shift; done
print -r -- '```ORCHESTRATOR_RESULT_JSON'
print -r -- '{ "action": <decoy from echoed prompt> }'
print -r -- '```'
print -r -- "=== advance-roadmap exit=2 finished (quoted from an old log) ==="
[ "$FAKE_ORCH" = "garbage" ] && { print "no fence here"; exit 0; }
cat > "$last" <<'EOF'
```ORCHESTRATOR_RESULT_JSON
{"action": "dispatch_worker", "outcome_token": null, "repo": "demo", "item": "DEV-1 thing", "worker_mode": "standard"}
```
EOF
SH

# Fake Cursor worker: behaviour from $FAKE_WORKER.
cat > "$BIN/agent" <<'SH'
#!/bin/zsh
prompt="${@[-1]}"
rf="$(print -r -- "$prompt" | sed -n 's/^(raw JSON, no fence) to: //p')"
res='{"outcome":"shipped","provider":"cursor","repo":"demo","item":"DEV-1 thing","merge_commit":"abc1234","summary":"ok"}'
case "$FAKE_WORKER" in
  file)  print -r -- "$res" > "$rf"; print "=== advance-roadmap exit=2 finished (decoy) ===" ;;
  fence) print -r -- '```WORKER_RESULT_JSON'; print -r -- "$res"; print -r -- '```' ;;
  fail)  print "boom"; exit 1 ;;
esac
SH
chmod +x "$BIN/codex" "$BIN/agent"

# The results path must be on its own line for the fake to find it.
grep -q '^(raw JSON, no fence) to: ' "$ROOT/worker.sh" || { echo "FAIL: prompt format changed"; exit 1; }

run() {
  env -u SLACK_CCUSAGE_WEBHOOK_URL -u ADVANCE_ROADMAP_SLACK_WEBHOOK \
    ADVANCE_ROADMAP_ROOT="$ROOT" ADVANCE_ROADMAP_SKILL_DIR="$SKILLS" \
    ADVANCE_ROADMAP_SECRETS=/dev/null ADVANCE_ROADMAP_NOTIFY=0 \
    ADVANCE_ROADMAP_CODEX_BIN="$BIN/codex" ADVANCE_ROADMAP_AGENT_BIN="$BIN/agent" \
    FAKE_ORCH="$1" FAKE_WORKER="$2" zsh "$ROOT/run.sh" || true
  sleep 1.1   # stamps are per-second
}
last() { python3 -c "import json,sys; r=[json.loads(l) for l in open('$ROOT/runs.jsonl')][-1]; print(r['$1'])"; }
check() { [ "$2" = "$3" ] || { echo "FAIL [$1]: expected '$3', got '$2'"; tail -40 "$ROOT"/logs/latest.log; exit 1; }; echo "ok   $1"; }

run good file;    check "agent-written result → shipped"       "$(last outcome)" shipped
                  check "result source is the agent's file"     "$(last worker_result_source)" agent
                  check "merge commit carried through"          "$(last merge_commit)" abc1234
run good fence;   check "stdout fence fallback → shipped"       "$(last outcome)" shipped
                  check "fallback is labelled"                  "$(last worker_result_source)" stdout-fence
run good fail;    check "worker exit 1, no result → error"      "$(last outcome)" error
alert1="$(python3 "$ROOT/lib/runrecord.py" --root "$ROOT" alert)"
check "one error does not alert" "$alert1" ""
run garbage file; check "orchestrator with no fence → error"    "$(last outcome)" error
alert2="$(python3 "$ROOT/lib/runrecord.py" --root "$ROOT" alert)"
case "$alert2" in *"failed 2 runs in a row"*) echo "ok   second consecutive error alerts" ;;
  *) echo "FAIL: expected alert, got '$alert2'"; exit 1 ;; esac
grep -q "=== alert:" "$ROOT/logs/latest.log" && echo "ok   run.sh raised the alert" || { echo "FAIL: run.sh did not alert"; exit 1; }
run good file;    check "recovery run → shipped"               "$(last outcome)" shipped
case "$(python3 "$ROOT/lib/runrecord.py" --root "$ROOT" alert)" in *recovered*) echo "ok   recovery message" ;;
  *) echo "FAIL: expected recovery message"; exit 1 ;; esac
check "exactly one record per run" "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" 5
echo "all passed"
