#!/usr/bin/env bash
# Behaviour matrix for post-yesterdays-ccusage.sh. Run: bash hooks/tests/post-yesterdays-ccusage.test.sh
# Stubs npx/npm/curl/osascript on PATH and isolates HOME, so no real Slack POST,
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
  *)            printf '{}' ;;
esac
EOF
printf '#!/usr/bin/env bash\necho "$NPM_CACHE"\n' > "$STUBS/npm"
# curl: CURL_RC controls the exit status; every payload is recorded
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
echo x >> "$TEST_DIR/curl-calls"
exit "${CURL_RC:-0}"
EOF
printf '#!/usr/bin/env bash\necho x >> "$TEST_DIR/notify-calls"\n' > "$STUBS/osascript"
chmod +x "$STUBS"/*

# run <npx mode> [curl rc] — fresh HOME and cache each time; prints nothing, sets $H
run() {
  export TEST_DIR="$TMP/case-$((++n))" NPX_MODE="$1" CURL_RC="${2:-0}" CLAUDE_JSON
  H="$TEST_DIR/home"; export NPM_CACHE="$TEST_DIR/npmcache"
  mkdir -p "$H" "$NPM_CACHE/_npx/stale" "$NPM_CACHE/_npx/other"
  echo '{"_npx":{"packages":["ccusage@latest"]}}' > "$NPM_CACHE/_npx/stale/package.json"
  echo '{"_npx":{"packages":["ccusage@18"]}}'     > "$NPM_CACHE/_npx/other/package.json"
  HOME="$H" SLACK_CCUSAGE_WEBHOOK_URL=http://hook.invalid PATH="$STUBS:$PATH" bash "$HOOK" 2>/dev/null
}
rerun() { HOME="$H" SLACK_CCUSAGE_WEBHOOK_URL=http://hook.invalid PATH="$STUBS:$PATH" bash "$HOOK" 2>/dev/null; }
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

echo "post-yesterdays-ccusage: $pass passed, $fail failed"
(( fail == 0 ))
