#!/bin/zsh
# End-to-end: run.sh + worker.sh with fake providers → one runs.jsonl row per run,
# outcomes from result files and exit codes (never log text), and the error alert.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-record-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM

ROOT="$TMP/root"; SKILLS="$TMP/skills"; BIN="$TMP/bin"
mkdir -p "$ROOT/lib" "$SKILLS" "$BIN" "$TMP/code/demo"
cp "$HERE/run.sh" "$HERE/worker.sh" "$ROOT/"
cp "$HERE/lib/runrecord.py" "$HERE/lib/tokens.py" "$HERE/lib/workspaces.py" "$ROOT/lib/"
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
# Codex's closing line (no rollout under this fake home, so tokens.py uses the total).
print -r -- "tokens used"; print -r -- "12,345"
[ "$FAKE_ORCH" = "garbage" ] && { print "no fence here"; exit 0; }
kind=feature; [ "$FAKE_ORCH" = "health" ] && kind=code_health
cat > "$last" <<EOF
\`\`\`ORCHESTRATOR_RESULT_JSON
{"action": "dispatch_worker", "outcome_token": null, "repo": "demo", "item": "DEV-1 thing", "worker_mode": "standard", "work_kind": "$kind"}
\`\`\`
EOF
SH

# Fake Cursor worker: behaviour from $FAKE_WORKER.
cat > "$BIN/agent" <<'SH'
#!/bin/zsh
prompt="${@[-1]}"
rf="$(print -r -- "$prompt" | sed -n 's/^(raw JSON, no fence) to: //p')"
res='{"outcome":"shipped","provider":"cursor","repo":"demo","item":"DEV-1 thing","merge_commit":"abc1234","summary":"ok","slack_summary":"Did the thing. DEV-1 Done."}'
case "$FAKE_WORKER" in
  file)  print -r -- "$res" > "$rf"; print "=== advance-roadmap exit=2 finished (decoy) ==="
         print -r -- '{"type":"result","result":"done","usage":{"inputTokens":100,"outputTokens":50,"cacheReadTokens":900,"cacheWriteTokens":10}}' ;;
  fence) print -r -- '```WORKER_RESULT_JSON'; print -r -- "$res"; print -r -- '```' ;;
  fail)  print "boom"; exit 1 ;;
  reviewed) print -r -- "$prompt" | grep -q -- '--worker cursor --round <N> --log .*/reviews.jsonl' || exit 9
         print -r -- '{"round":1,"worker":"cursor","reviewer":"codex","verdict":"findings"}' >> "${rf:h}/reviews.jsonl"
         print -r -- '{"round":2,"worker":"cursor","reviewer":"codex","verdict":"clean"}' >> "${rf:h}/reviews.jsonl"
         print -r -- "$res" > "$rf" ;;
  chain) n=$(( $(cat "$CHAIN_COUNT" 2>/dev/null || echo 0) + 1 )); print $n > "$CHAIN_COUNT"
         [ $n -le 2 ] && print -r -- "$res" > "$rf" || { print "boom"; exit 1; } ;;
  ws)    # Superset mode: must run in the workspace, pointed at it, under Cursor's agent identity.
         [ "${PWD:A}" = "${SUPERSET_WORKSPACE_PATH:A}" ] && [ "$SUPERSET_AGENT_ID" = cursor-agent ] \
           && [[ " $* " == *" --workspace $SUPERSET_WORKSPACE_PATH "* ]] \
           || { print "not in the workspace: pwd=$PWD agent=$SUPERSET_AGENT_ID"; exit 9; }
         print -r -- "$res" > "$rf" ;;
  deploy) print -r -- '{"outcome":"shipped-deploy-failed","provider":"cursor","repo":"demo","item":"DEV-1 thing","merge_commit":"abc1234","deploy":{"status":"failed","url":"https://demo.vercel.app","attempts":3},"summary":"red"}' > "$rf" ;;
esac
SH
# Fake Superset CLI: runs a new workspace's --command in the background, in the "worktree",
# with the env a Superset terminal sets. FAKE_SUPERSET=down fails every call.
cat > "$BIN/superset" <<'SH'
#!/bin/zsh
[ "$FAKE_SUPERSET" = "down" ] && { print "host service not running" >&2; exit 1; }
case "$1 $2" in
  "auth whoami") print '{"organizationId":"org"}' ;;
  "projects list") print -r -- "[{\"id\":\"p1\",\"path\":\"$FAKE_CODE/demo\"}]" ;;
  "workspaces list") print '[]' ;;
  "workspaces create")
    cmd=""; while [ $# -gt 0 ]; do [ "$1" = "--command" ] && { cmd="$2"; shift; }; shift; done
    mkdir -p "$FAKE_WS"
    ( cd "$FAKE_WS" && SUPERSET_WORKSPACE_PATH="$FAKE_WS" SUPERSET_TERMINAL_ID=t1 zsh -c "$cmd" ) >/dev/null 2>&1 &!
    print '{"workspace":{"id":"w1"},"terminals":[{"terminalId":"t1"}],"alreadyExists":false}' ;;
  *) print '{}' ;;
esac
SH
chmod +x "$BIN/codex" "$BIN/agent" "$BIN/superset"

# The results path must be on its own line for the fake to find it.
grep -q '^(raw JSON, no fence) to: ' "$ROOT/worker.sh" || { echo "FAIL: prompt format changed"; exit 1; }

run() {
  env -u SLACK_CCUSAGE_WEBHOOK_URL -u ADVANCE_ROADMAP_SLACK_WEBHOOK \
    ADVANCE_ROADMAP_SLACKAGENT_DIR="$TMP/no-slackagent" PERSONAL_CODE_DIR="$TMP/code" DOTFILES_PERSONAL_MACHINE=1 \
    ADVANCE_ROADMAP_ROOT="$ROOT" ADVANCE_ROADMAP_SKILL_DIR="$SKILLS" ADVANCE_ROADMAP_CONDUCTOR_DIR="$SKILLS" \
    ADVANCE_ROADMAP_SECRETS=/dev/null ADVANCE_ROADMAP_NOTIFY=0 \
    ADVANCE_ROADMAP_SUPERSET="${SUPERSET:-0}" ADVANCE_ROADMAP_SUPERSET_BIN="$BIN/superset" ADVANCE_ROADMAP_WORKER_POLL_S=1 \
    FAKE_SUPERSET="${FAKE_SUPERSET:-up}" FAKE_CODE="$TMP/code" FAKE_WS="$TMP/ws" ADVANCE_ROADMAP_TEST_TOKEN=s3cret \
    ADVANCE_ROADMAP_CODEX_BIN="$BIN/codex" ADVANCE_ROADMAP_AGENT_BIN="$BIN/agent" \
    ADVANCE_ROADMAP_CADENCE_HOURS="${CADENCE_HOURS:-1}" \
    ADVANCE_ROADMAP_CHAIN_MINUTES="${CHAIN_MINUTES:-0}" CHAIN_COUNT="$TMP/chain-count" \
    FAKE_ORCH="$1" FAKE_WORKER="$2" zsh "$ROOT/run.sh" || true
  sleep 1.1   # stamps are per-second
}
last() { python3 -c "import json,sys; r=[json.loads(l) for l in open('$ROOT/runs.jsonl')][-1]; print(r['$1'])"; }
check() { [ "$2" = "$3" ] || { echo "FAIL [$1]: expected '$3', got '$2'"; tail -40 "$ROOT"/logs/latest.log; exit 1; }; echo "ok   $1"; }

run good file;    check "agent-written result → shipped"       "$(last outcome)" shipped
                  check "result source is the agent's file"     "$(last worker_result_source)" agent
                  check "merge commit carried through"          "$(last merge_commit)" abc1234
                  check "slack summary carried through"         "$(last slack_summary)" "Did the thing. DEV-1 Done."
                  check "unreviewed ship is flagged"            "$(last detail)" "merged without a logged review"
                  check "run tokens = orchestrator total + worker new in/out" \
                        "$(python3 -c "import json; print(json.loads(open('$ROOT/runs.jsonl').readlines()[-1])['tokens']['total'])")" 12505
                  check "worker tokens carry provider/model" \
                        "$(python3 -c "import json; c=json.loads(open('$ROOT/runs.jsonl').readlines()[-1])['tokens']['calls']; print(sorted((x['role'],x['provider'],x['model']) for x in c))")" \
                        "[('orchestrator', 'codex', 'gpt-5.6-sol'), ('worker', 'cursor', 'auto')]"
run good reviewed; check "reviewed ship → shipped"             "$(last outcome)" shipped
                  check "review rounds recorded"                "$(last review_rounds)" 2
                  check "reviewer recorded"                     "$(last reviewer)" codex
                  check "reviewed ship has no flag"             "$(last detail)" ""
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
run good deploy;  check "deploy cap hit → shipped-deploy-failed" "$(last outcome)" shipped-deploy-failed
                  check "deploy detail names the URL"          "$(last detail)" "deploy failed: https://demo.vercel.app"
check "exactly one record per run" "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" 7

# Chain: ships twice, third link fails → three rows from one tick, then it stops.
CHAIN_MINUTES=40 run good chain
check "chain runs until a link fails to ship" "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" 10
check "chain's last link is the failure"      "$(last outcome)" error
grep -q "starting link 3" "$ROOT"/logs/run-*.log && echo "ok   chain logged its links" || { echo "FAIL: no chain log line"; exit 1; }
check "a feature ship records its kind"     "$(last work_kind)" feature
# A code-health ship never chains, even well inside the time cap.
rm -f "$TMP/chain-count"
CHAIN_MINUTES=40 run health chain
check "code-health ship does not chain"      "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" 11
check "code-health kind recorded"            "$(last work_kind)" code_health
check "code-health ship is still shipped"    "$(last outcome)" shipped
# Past the time cap, a shipped run does not chain.
ADVANCE_ROADMAP_CHAIN_START=$(( $(date +%s) - 2401 )) CHAIN_MINUTES=40 run good file
check "no chain past the time cap"            "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" 12
# Lane mode: a dispatch to a repo another lane holds is refused, not trusted to the prompt.
ADVANCE_ROADMAP_LANE=2 ADVANCE_ROADMAP_BUSY_REPOS="demo" run good file
check "busy repo is not dispatched"            "$(last action)" blocked_no_item
grep -q "which another lane holds" "$ROOT/logs/latest.log" && echo "ok   refusal logged" || { echo "FAIL: no refusal line"; exit 1; }
check "lane recorded"                          "$(last lane)" 2
check "a refused dispatch runs no worker"      "$(last worker)" none
check "and records no work"                    "$(last outcome)" blocked-no-item
# Superset mode: the worker runs in the item's workspace terminal, not in-process.
SUPERSET=1 run good ws
check "workspace run → shipped"               "$(last outcome)" shipped
check "a workspace ship with no PR is flagged" "$(last detail)" "shipped without a PR URL — not credited"
grep -q ADVANCE_ROADMAP_ROOT "$ROOT"/runs/*/worker-launch.zsh || { echo "FAIL: launcher lost the run's env"; exit 1; }
grep -q s3cret "$ROOT"/runs/*/worker-launch.zsh && { echo "FAIL: launcher carries a secret"; exit 1; } || echo "ok   launcher carries no secrets"
grep -q "worker running in Superset workspace w1" "$ROOT/logs/latest.log" \
  && echo "ok   worker ran in the workspace" || { echo "FAIL: no workspace launch"; tail -40 "$ROOT/logs/latest.log"; exit 1; }
# Superset down: no worker runs, the tick is a skip (not an error), and nothing alerts.
FAKE_SUPERSET=down SUPERSET=1 run good ws
check "Superset down → skipped-superset"      "$(last outcome)" skipped-superset
check "skip names why"                        "$(last detail)" "Superset app or host service not reachable"
check "a skip does not alert"                 "$(python3 "$ROOT/lib/runrecord.py" --root "$ROOT" alert)" ""
# Sprint windows: a tick outside every window (a catch-up fire on wake) leaves no trace.
now_min=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
if [ "$now_min" -ge 5 ] && [ "$now_min" -lt 1430 ]; then
  rows_before=$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')
  ADVANCE_ROADMAP_WINDOWS="23:55-23:59" run good file
  check "out-of-window tick records nothing"   "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" "$rows_before"
  ADVANCE_ROADMAP_WINDOWS="00:00-00:01 00:02-23:59" run good file
  check "in-window tick runs"                  "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" $(( rows_before + 1 ))
  # A chain link that lands after the window closes finishes its item but starts no more.
  rm -f "$TMP/chain-count"
  ADVANCE_ROADMAP_CHAIN_LINK=2 ADVANCE_ROADMAP_WINDOWS="23:55-23:59" CHAIN_MINUTES=40 run good chain
  check "link past the window runs once"       "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" $(( rows_before + 2 ))
  grep -q "past the sprint window" "$ROOT/logs/latest.log" \
    && echo "ok   no chaining past the window" || { echo "FAIL: chained past the window"; exit 1; }
fi
# Cadence: a tick off the base grid leaves no trace at all (24h grid = 03:45 only).
if [ "$(date +%H)" != "03" ]; then
  logs_before=$(ls "$ROOT"/logs | wc -l)
  rows_before=$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')
  CADENCE_HOURS=24 run good file
  check "off-grid tick records nothing"        "$(wc -l < "$ROOT/runs.jsonl" | tr -d ' ')" "$rows_before"
  check "off-grid tick leaves no log"          "$(ls "$ROOT"/logs | wc -l)" "$logs_before"
fi
echo "all passed"
