#!/usr/bin/env bash
# Behaviour matrix for post-yesterdays-ccusage.sh. Run: bash hooks/tests/post-yesterdays-ccusage.test.sh
# Stubs npx/npm/curl/osascript/superset on PATH and isolates HOME, so no real Slack POST,
# npm cache or notification is ever touched.
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/post-yesterdays-ccusage.sh}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/ccusage-hook-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT
STUBS="$TMP/stubs"; mkdir -p "$STUBS"

CLAUDE_JSON='{"daily":[{"totalCost":1.5,"inputTokens":10,"outputTokens":5,"cacheCreationTokens":1,"cacheReadTokens":2,"modelBreakdowns":[]}]}'

# npx: NPX_MODE=ok | native (fails while the stale cache entry exists) |
#      stuck (native-binary error that eviction never fixes) | broken (generic failure)
cat > "$STUBS/npx" <<'EOF'
#!/usr/bin/env bash
echo x >> "$TEST_DIR/npx-calls"
case "$NPX_MODE" in
  broken) echo "boom: network unreachable" >&2; exit 1 ;;
  stuck)  echo "ccusage native binary is not available for darwin-arm64." >&2; exit 1 ;;
  native) if [ -d "$NPM_CACHE/_npx/stale" ]; then
            echo "ccusage native binary is not available for darwin-arm64." >&2; exit 1
          fi ;;
esac
case " $* " in
  *" claude "*) printf '%s' "$CLAUDE_JSON" ;;
  *" codex "*)  empty='{}'; printf '%s' "${CODEX_JSON:-$empty}" ;;
  *)            printf '{}' ;;
esac
EOF
printf '#!/usr/bin/env bash\necho "$NPM_CACHE"\n' > "$STUBS/npm"
# curl: CURL_RC controls the exit status; every payload is recorded
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
echo x >> "$TEST_DIR/curl-calls"
while [ $# -gt 0 ]; do
  [ "$1" = "-d" ] && { printf '%s' "$2" > "$TEST_DIR/curl-payload"; break; }
  shift
done
exit "${CURL_RC:-0}"
EOF
printf '#!/usr/bin/env bash\necho x >> "$TEST_DIR/notify-calls"\n' > "$STUBS/osascript"
# superset: SUPERSET_MODE=ok | offline (jake2 listed offline) | badhash (one
# base64 character changed on screen). `terminals read` shows $PEER_JSON framed
# the way a peer's --collect prints it, wrapped at 120 columns between prompts.
cat > "$STUBS/superset" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$TEST_DIR/superset-calls"
case "$1 $2" in
  "hosts list")
    online=yes; [ "${SUPERSET_MODE:-ok}" = offline ] && online=no
    printf '[{"id":"h2","name":"jake2","online":"%s"}]' "$online" ;;
  "ws list") printf '[{"id":"ws-old","archivedAt":"x"},{"id":"ws1","archivedAt":null,"worktreeExists":true}]' ;;
  "terminals create") printf '{"terminalId":"t1","status":"active"}' ;;
  "terminals wait") exit 0 ;;
  "terminals read")
    frame=$(printf '%s' "$PEER_JSON" | /usr/bin/python3 "$MERGE" --frame | fold -w 120)  # a mise shim fails under the test HOME
    if [ "${SUPERSET_MODE:-ok}" = badhash ]; then
      frame=$(printf '%s\n' "$frame" | awk 'NR==2 { $0 = (substr($0,1,1) == "A" ? "B" : "A") substr($0,2) } 1')
    fi
    jq -n --arg t "$(printf '~/code ❯ bash hook --collect\n%s\n~/code ❯' "$frame")" '{text: $t}' ;;
  "terminals close") echo x >> "$TEST_DIR/close-calls" ;;
esac
EOF
chmod +x "$STUBS"/*
export MERGE="$(cd "$(dirname "$0")/.." && pwd -P)/lib/merge-usage.py"

# run <npx mode> [curl rc] [hook args] — fresh HOME and cache each time; sets $H and
# prints the hook's stdout (only --collect and --dry-run write any). POSTER and
# PEERS feed CCUSAGE_POSTER / CCUSAGE_PEERS; both default to empty (post alone).
run() {
  export TEST_DIR="$TMP/case-$((++n))" NPX_MODE="$1" CURL_RC="${2:-0}" CLAUDE_JSON CODEX_JSON PEER_JSON SUPERSET_MODE
  shift; [ $# -gt 0 ] && shift
  unset CODEX_HOME
  H="$TEST_DIR/home"; export NPM_CACHE="$TEST_DIR/npmcache"
  mkdir -p "$H" "$NPM_CACHE/_npx/stale" "$NPM_CACHE/_npx/other"
  echo '{"_npx":{"packages":["ccusage@latest"]}}' > "$NPM_CACHE/_npx/stale/package.json"
  echo '{"_npx":{"packages":["ccusage@18"]}}'     > "$NPM_CACHE/_npx/other/package.json"
  [ -n "${SEED_FN:-}" ] && "$SEED_FN"
  HOME="$H" SLACK_CCUSAGE_WEBHOOK_URL=http://hook.invalid PATH="$STUBS:$PATH" \
    CCUSAGE_POSTER="${POSTER-}" CCUSAGE_PEERS="${PEERS-}" bash "$HOOK" "$@" 2>/dev/null
}
rerun() { HOME="$H" SLACK_CCUSAGE_WEBHOOK_URL=http://hook.invalid PATH="$STUBS:$PATH" \
  CCUSAGE_POSTER="${POSTER-}" CCUSAGE_PEERS="${PEERS-}" bash "$HOOK" 2>/dev/null; }
count() { [ -f "$TEST_DIR/$1" ] && wc -l < "$TEST_DIR/$1" | tr -d ' ' || echo 0; }
yesterday=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d yesterday +%Y-%m-%d)
marker() { cat "$H/.claude/.ccusage-state/last-posted" 2>/dev/null || echo none; }
expect() { # expect <want> <label> <got>
  if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want '$1', got '$3'"; fi
}

# 1. healthy ccusage: posts once and advances the marker
run ok 0
expect "$yesterday" "healthy: marker advances" "$(marker)"
expect 1 "healthy: one Slack post" "$(count curl-calls)"
expect 0 "healthy: no notification" "$(count notify-calls)"
expect yes "healthy: cache untouched" "$([ -d "$NPM_CACHE/_npx/stale" ] && echo yes || echo no)"

# 2. half-installed cache: evicts only the ccusage@latest entry, retries, posts
run native 0
expect "$yesterday" "native: marker advances after retry" "$(marker)"
expect 1 "native: one Slack post" "$(count curl-calls)"
expect no "native: stale entry evicted" "$([ -d "$NPM_CACHE/_npx/stale" ] && echo yes || echo no)"
expect yes "native: other npx entries kept" "$([ -d "$NPM_CACHE/_npx/other" ] && echo yes || echo no)"
expect 0 "native: recovered silently" "$(count notify-calls)"

# 3. persistent failure: marker holds, error logged, one notification per day
run broken 0
expect none "broken: marker not advanced" "$(marker)"
expect 0 "broken: no Slack post" "$(count curl-calls)"
expect 1 "broken: generic failure is not retried" "$(count npx-calls)"
expect yes "broken: error logged with stderr" "$(grep -q 'ccusage claude failed' "$H/.claude/.ccusage-state/errors.log" && grep -q 'network unreachable' "$H/.claude/.ccusage-state/errors.log" && echo yes || echo no)"
expect 1 "broken: notified" "$(count notify-calls)"
rerun
expect 1 "broken: second run same day does not re-notify" "$(count notify-calls)"
expect none "broken: marker still held" "$(marker)"

# 4. native-binary error that eviction cannot fix: exactly one retry, then fail
run stuck 0
expect 2 "stuck native: one retry only" "$(count npx-calls)"
expect none "stuck native: marker not advanced" "$(marker)"
expect 1 "stuck native: notified" "$(count notify-calls)"

# 5. Slack POST failure: marker holds, failure logged
run ok 22
expect none "slack fail: marker not advanced" "$(marker)"
expect yes "slack fail: logged" "$(grep -q 'slack POST failed' "$H/.claude/.ccusage-state/errors.log" && echo yes || echo no)"

# 6. effort-mix columns: appended after CacheR, cost-weighted per model, only
#    the levels used that day; first six columns untouched
EFFORT_JSON_CLAUDE='{"daily":[{"totalCost":20,"inputTokens":10,"outputTokens":5,"cacheCreationTokens":1,"cacheReadTokens":2,"modelBreakdowns":[
  {"modelName":"claude-opus-5-5","cost":10,"inputTokens":4,"outputTokens":2000,"cacheCreationTokens":0,"cacheReadTokens":0},
  {"modelName":"claude-sonnet-5-5","cost":10,"inputTokens":6,"outputTokens":1000,"cacheCreationTokens":1,"cacheReadTokens":2}]}]}'
seed_transcripts() {
  mkdir -p "$H/.claude/projects/p"
  python3 - "$H/.claude/projects/p/s.jsonl" "$yesterday" <<'PY'
import datetime, json, sys
path, day = sys.argv[1], sys.argv[2]
ts = datetime.datetime.strptime(day + " 12:00", "%Y-%m-%d %H:%M").astimezone().isoformat()
def turn(mid, model, effort, out, extra=None):
    return {"type": "assistant", "timestamp": ts, "requestId": "r-" + mid, "effort": effort, "perTurnEffort": effort,
            "message": {"id": "m-" + mid, "model": model, "usage": dict({"output_tokens": out}, **(extra or {}))}}
rows = [
    turn("a", "claude-opus-5-5", "high", 1000),
    turn("a", "claude-opus-5-5", "high", 1000),   # same request written twice: counted once
    turn("b", "claude-opus-5-5", "xhigh", 1000),
    turn("c", "claude-sonnet-5-5", "medium", 1000),
    turn("d", "<synthetic>", "high", 9999),        # not a real model: ignored
]
open(path, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
PY
}
SEED_FN=seed_transcripts CLAUDE_JSON="$EFFORT_JSON_CLAUDE" run ok 0
post=$(jq -r .text < "$TEST_DIR/curl-payload")
expect yes "effort: header gets Med/High/XHigh after Output" "$(grep -q 'Output    Med   High  XHigh$' <<<"$post" && echo yes || echo no)"
expect yes "effort: opus row split 50/50 across high and xhigh" "$(grep -q '^Opus 5.5 .*  0%.* 50%.* 50%$' <<<"$post" && echo yes || echo no)"
expect yes "effort: sonnet row all medium" "$(grep -q '^Sonnet 5.5 .* 100%.*  0%.*  0%$' <<<"$post" && echo yes || echo no)"
expect yes "effort: total row is cost-weighted" "$(grep -q '^Claude  .*\$20.00.* 50%.* 25%.* 25%$' <<<"$post" && echo yes || echo no)"
expect 60 "effort: separator is 39 plus 7 per level (3 levels)" "$(grep -m1 '^─' <<<"$post" | tr -d '\n' | wc -m | tr -d ' ')"
unset SEED_FN

# 7. no transcripts (or a helper that fails): the post is the plain table, never "0%" cells
run ok 0
expect no "no effort data: no percent cells" "$(jq -r .text < "$TEST_DIR/curl-payload" | grep -q '%' && echo yes || echo no)"
expect 1 "no effort data: still posts" "$(count curl-calls)"

# 8. model names: every family renders as "Family N.M" so the first column stays 12 wide
FABLE_JSON='{"daily":[{"totalCost":5,"inputTokens":1,"outputTokens":1,"cacheCreationTokens":1,"cacheReadTokens":1,"modelBreakdowns":[{"modelName":"claude-fable-5-1","cost":5,"inputTokens":1,"outputTokens":1,"cacheCreationTokens":1,"cacheReadTokens":1}]}]}'
CLAUDE_JSON="$FABLE_JSON" run ok 0
expect yes "fable row is prettified" "$(jq -r .text < "$TEST_DIR/curl-payload" | grep -q '^Fable 5.1 ' && echo yes || echo no)"

# 9. Codex effort columns: read from the rollout's turn_context, counted by growth in
#    the cumulative token total (a repeated token_count adds nothing)
CODEX_DAY_JSON='{"daily":[{"costUSD":0.3,"inputTokens":3000,"outputTokens":300,"cacheReadTokens":0,"models":{"gpt-6-luna":{"inputTokens":3000,"outputTokens":300,"cacheReadTokens":0}}}]}'
seed_codex() {
  mkdir -p "$H/.codex/sessions/2026/01/01"
  python3 - "$H/.codex/sessions/2026/01/01/rollout-x.jsonl" "$yesterday" <<'PY'
import datetime, json, sys
path, day = sys.argv[1], sys.argv[2]
ts = datetime.datetime.strptime(day + " 12:00", "%Y-%m-%d %H:%M").astimezone().isoformat()
def ctx(effort):
    return {"timestamp": ts, "type": "turn_context", "payload": {"model": "gpt-6-luna", "collaboration_mode": {"settings": {"reasoning_effort": effort}}}}
def count(total):
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": total, "cached_input_tokens": 0, "output_tokens": total // 10}}}}
rows = [ctx("medium"), count(1000), count(1000),   # repeated event: counted once
        ctx("high"), count(2000), count(3000)]
open(path, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
PY
}
SEED_FN=seed_codex CLAUDE_JSON="$FABLE_JSON" CODEX_JSON="$CODEX_DAY_JSON" run ok 0
post=$(jq -r .text < "$TEST_DIR/curl-payload")
expect yes "codex effort: header gets Med/High after Output" "$(grep -q 'Output    Med   High$' <<<"$post" && echo yes || echo no)"
expect yes "codex effort: model row split by turn weight" "$(grep -q '^Luna 6 .* 33%.* 67%$' <<<"$post" && echo yes || echo no)"
expect yes "codex effort: total row carries the same split" "$(grep -q '^Codex  .*\$0.30.* 33%.* 67%$' <<<"$post" && echo yes || echo no)"
expect 53 "codex effort: separator is 39 plus 7 per level" "$(grep '^─' <<<"$post" | tail -1 | tr -d '\n' | wc -m | tr -d ' ')"
expect no "codex effort: claude rows get no cells without transcripts" "$(grep '^Fable\|^Claude  ' <<<"$post" | grep -q '%' && echo yes || echo no)"
unset SEED_FN
CODEX_JSON="$CODEX_DAY_JSON" run ok 0
expect no "codex without rollouts: no percent cells" "$(jq -r .text < "$TEST_DIR/curl-payload" | grep -q '%' && echo yes || echo no)"
unset CODEX_JSON

# 10. one table for both providers: models by output tokens, readable names, no cache
#     columns, no cache line, one total row per provider
BOTH_CLAUDE='{"daily":[{"totalCost":30,"inputTokens":10,"outputTokens":600,"cacheCreationTokens":5000,"cacheReadTokens":90000,"modelBreakdowns":[
  {"modelName":"claude-opus-5-5","cost":20,"inputTokens":4,"outputTokens":100,"cacheCreationTokens":2000,"cacheReadTokens":50000},
  {"modelName":"claude-sonnet-5-5","cost":10,"inputTokens":6,"outputTokens":500,"cacheCreationTokens":3000,"cacheReadTokens":40000}]}]}'
BOTH_CODEX='{"daily":[{"costUSD":0.5,"inputTokens":3000,"outputTokens":300,"cacheReadTokens":9000,"models":{
  "gpt-6-luna":{"inputTokens":2000,"outputTokens":200,"cacheReadTokens":6000},
  "gpt-5.3-codex":{"inputTokens":1000,"outputTokens":100,"cacheReadTokens":3000},
  "gpt-5.3-codex-spark":{"inputTokens":10,"outputTokens":550,"cacheReadTokens":0}}}]}'
CLAUDE_JSON="$BOTH_CLAUDE" CODEX_JSON="$BOTH_CODEX" run ok 0
post=$(jq -r .text < "$TEST_DIR/curl-payload")
order=$(grep -E '^(Opus|Sonnet|Luna|Codex [0-9]|Codex Spark)' <<<"$post" | grep -v '^Codex  ' | cut -c1-12 | sed 's/ *$//' | tr '\n' ',')
expect "Codex Spark,Sonnet 5.5,Luna 6,Opus 5.5,Codex 5.3," "all models sorted by output (ties keep Claude first), names cut to 12" "$order"
expect yes "header is Model Cost Input Output only" "$(grep -q '^Model  *Cost  *Input  *Output$' <<<"$post" && echo yes || echo no)"
expect no "no cache columns anywhere" "$(grep -q 'CacheW\|CacheR' <<<"$post" && echo yes || echo no)"
expect no "no cache line in the post" "$(grep -q '^_Cache' <<<"$post" && echo yes || echo no)"
expect no "no Claude/Codex subtotal line under the header" "$(grep -q '^_Claude ' <<<"$post" && echo yes || echo no)"
expect yes "one Claude total row" "$(grep -q '^Claude  *\$30.00 .*600$' <<<"$post" && echo yes || echo no)"
expect yes "one Codex total row, no cache figure" "$(grep -q '^Codex  *\$0.50 .*3.0K .*300$' <<<"$post" && echo yes || echo no)"
expect 2 "exactly two total rows" "$(grep -cE '^(Claude|Codex) +\$' <<<"$post")"
# a Codex-only day has no cache line and no Claude total
CLAUDE_JSON="{}" CODEX_JSON="$BOTH_CODEX" run ok 0
post=$(jq -r .text < "$TEST_DIR/curl-payload")
expect no "codex-only day: no cache line" "$(grep -q '^_Cache' <<<"$post" && echo yes || echo no)"
expect 1 "codex-only day: one total row" "$(grep -cE '^(Claude|Codex) +\$' <<<"$post")"
unset CODEX_JSON

# 11. one poster for every machine: jake2's day is collected over a Superset
#     terminal and summed into one post
me=$(hostname -s)
PEER_DAY='{"claude":{"daily":[{"totalCost":5,"inputTokens":1,"outputTokens":50,"cacheCreationTokens":1000,"cacheReadTokens":10000,"modelBreakdowns":[
  {"modelName":"claude-opus-5-5","cost":5,"inputTokens":1,"outputTokens":50,"cacheCreationTokens":1000,"cacheReadTokens":10000}]}]},
  "codex":[],"effort":{},"codex_effort":{}}'
PEER_JSON=$(jq -c --arg d "$yesterday" --argjson b "$PEER_DAY" -n '{($d): $b}')
POSTER="$me" PEERS=jake2 CLAUDE_JSON="$BOTH_CLAUDE" run ok 0
post=$(jq -r .text < "$TEST_DIR/curl-payload")
expect 1 "peers: one Slack post" "$(count curl-calls)"
expect "$yesterday" "peers: marker advances" "$(marker)"
expect yes "peers: header carries the summed total" "$(grep -q ': \$35.00\*$' <<<"$post" && echo yes || echo no)"
expect yes "peers: shared model rows are summed" "$(grep -q '^Opus 5.5  *\$25.00 .*150$' <<<"$post" && echo yes || echo no)"
expect no "peers: no cache line" "$(grep -q '^_Cache' <<<"$post" && echo yes || echo no)"
expect yes "peers: Claude total row is summed" "$(grep -q '^Claude  *\$35.00 .*650$' <<<"$post" && echo yes || echo no)"
expect yes "peers: asks jake2 for the day, in a live workspace" "$(grep -q "terminals create --host h2 --workspace ws1 .*--collect $yesterday $yesterday" "$TEST_DIR/superset-calls" && echo yes || echo no)"
expect 1 "peers: terminal closed" "$(count close-calls)"

# 12. a peer that is offline or returns a damaged frame holds the day
for m in offline badhash; do
  POSTER="$me" PEERS=jake2 SUPERSET_MODE=$m run ok 0
  expect 0 "peer $m: no Slack post" "$(count curl-calls)"
  expect none "peer $m: marker held" "$(marker)"
  expect yes "peer $m: logged" "$(grep -q 'collecting from jake2 failed' "$H/.claude/.ccusage-state/errors.log" && echo yes || echo no)"
done
expect 1 "badhash: terminal still closed" "$(count close-calls)"
unset SUPERSET_MODE

# 13. a machine that is not the poster never posts (or runs ccusage)
POSTER=not-this-host PEERS=jake2 run ok 0
expect 0 "not poster: no Slack post" "$(count curl-calls)"
expect 0 "not poster: no ccusage run" "$(count npx-calls)"
expect 0 "not poster: no peer contact" "$(count superset-calls)"

# 14. --collect prints a frame the poster can read back, without a webhook or state
out=$(POSTER=not-this-host CLAUDE_JSON='{"daily":[{"totalCost":1.5,"modelBreakdowns":[]}]}' run ok 0 --collect "$yesterday")
expect 1.5 "collect: frame round-trips the day" "$(printf '%s' "$out" | python3 "$MERGE" --unframe | jq -r --arg d "$yesterday" '.[$d].claude.daily[0].totalCost')"
expect 0 "collect: no Slack post" "$(count curl-calls)"
expect none "collect: no marker" "$(marker)"

# 15. --dry-run prints the merged post and touches nothing
out=$(POSTER="$me" PEERS=jake2 CLAUDE_JSON="$BOTH_CLAUDE" run ok 0 --dry-run "$yesterday")
expect yes "dry run: prints the summed post" "$(grep -q ': \$35.00\*$' <<<"$out" && echo yes || echo no)"
expect 0 "dry run: no Slack post" "$(count curl-calls)"
expect no "dry run: no state dir" "$([ -d "$H/.claude/.ccusage-state" ] && echo yes || echo no)"
unset PEER_JSON

# 16. effort splits merge by weight, not by adding percentages
A='{"effort":{"levels":["Med","High"],"rows":{"claude-opus-5-5":[0,100]},"total":[0,100],"weights":{"claude-opus-5-5":10}}}'
B='{"effort":{"levels":["High","XHigh"],"rows":{"claude-opus-5-5":[50,50],"claude-haiku-5-5":[100,0]},"total":[60,40],"weights":{"claude-opus-5-5":30,"claude-haiku-5-5":10}}}'
printf '%s' "$A" > "$TMP/a.json"; printf '%s' "$B" > "$TMP/b.json"
merged=$(python3 "$MERGE" "$TMP/a.json" "$TMP/b.json")
expect '["Med","High","XHigh"]' "merge: levels are the union, in effort order" "$(jq -c .effort.levels <<<"$merged")"
expect '[0,63,37]' "merge: a shared model is weight-averaged" "$(jq -c '.effort.rows["claude-opus-5-5"]' <<<"$merged")"
expect '[0,70,30]' "merge: total is weighted across every machine's models" "$(jq -c .effort.total <<<"$merged")"

echo "post-yesterdays-ccusage: $pass passed, $fail failed"
(( fail == 0 ))
