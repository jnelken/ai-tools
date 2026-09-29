#!/usr/bin/env bash
# Behaviour matrix for status-glyph-check.sh. Run: bash tests/status-glyph-check.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/status-glyph-check.sh}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/status-glyph-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# BLOCK only on exit 0 with a block decision; PASS only on exit 0 with empty
# stdout; anything else is ERROR so a crash never counts as a pass.
classify() {
  local out="$1" status="$2"
  if (( status == 0 )) && [[ -z "$out" ]]; then echo PASS
  elif (( status == 0 )) && printf '%s' "$out" | jq -e '.decision == "block"' >/dev/null 2>&1; then echo BLOCK
  else echo "ERROR(status=$status)"; fi
}

msg() { # msg <text> — input carrying last_assistant_message
  local out; out=$(jq -n --arg t "$1" '{last_assistant_message:$t, stop_hook_active:false}' | bash "$HOOK"); classify "$out" $?
}

expect() { # expect <want> <label> <got>
  if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want $1, got $3"; fi
}

FE0F=$'\xef\xb8\x8f'
expect PASS  "done"                    "$(msg $'Did the thing.\n\n✅ shipped')"
expect PASS  "blocked"                 "$(msg $'Need a call.\n🛑 pick A or B')"
expect PASS  "watching"                "$(msg $'⏳ watching CI on #1629, ~8m')"
expect PASS  "follow-ups"              "$(msg $'💡 two follow-ups below')"
expect PASS  "answer only"             "$(msg $'It is 42.\n\n💬')"
expect PASS  "combo"                   "$(msg $'⏳💡 CI running; follow-ups noted')"
expect PASS  "leading dash"            "$(msg $'body\n— ✅ done')"
expect PASS  "trailing blank lines"    "$(msg $'body\n✅ done\n\n  \n')"
expect PASS  "caution emoji form"      "$(msg "body"$'\n'"⚠${FE0F} tests red")"
expect PASS  "pickaxe emoji form"      "$(msg "body"$'\n'"⛏${FE0F} reapable")"
expect BLOCK "caution bare text form"  "$(msg $'body\n\xe2\x9a\xa0 tests red')"
expect BLOCK "pickaxe bare text form"  "$(msg $'body\n\xe2\x9b\x8f reapable')"
expect BLOCK "missing glyph"           "$(msg $'All done, nothing else.')"
expect BLOCK "glyph not on last line"  "$(msg $'✅ done\nand one more thing')"
expect BLOCK "glyph mid-line"          "$(msg $'shipped it ✅')"

out=$(jq -n '{last_assistant_message:"no glyph", stop_hook_active:true}' | bash "$HOOK"); expect PASS "stop_hook_active" "$(classify "$out" $?)"
out=$(printf 'not json' | bash "$HOOK");  expect PASS "malformed input" "$(classify "$out" $?)"
out=$(printf '' | bash "$HOOK");          expect PASS "empty input"     "$(classify "$out" $?)"

# Transcript fallback.
tx() { # tx <jsonl> — input with only transcript_path
  printf '%s\n' "$1" > "$TMP/t.jsonl"
  local out; out=$(jq -n --arg p "$TMP/t.jsonl" '{transcript_path:$p}' | bash "$HOOK"); classify "$out" $?
}
A_TEXT_OK='{"type":"assistant","message":{"content":[{"type":"text","text":"done\n✅ ok"}]}}'
A_TEXT_BAD='{"type":"assistant","message":{"content":[{"type":"text","text":"done, no glyph"}]}}'
A_TOOL='{"type":"assistant","message":{"content":[{"type":"tool_use","name":"AskUserQuestion","input":{}}]}}'
SIDE='{"type":"assistant","isSidechain":true,"message":{"content":[{"type":"text","text":"subagent, no glyph"}]}}'
expect PASS  "transcript: glyph"            "$(tx "$A_TEXT_OK")"
expect BLOCK "transcript: missing"          "$(tx "$A_TEXT_BAD")"
expect PASS  "transcript: ends on tool_use" "$(tx "$A_TEXT_BAD"$'\n'"$A_TOOL")"
expect PASS  "transcript: sidechain ignored" "$(tx "$A_TEXT_OK"$'\n'"$SIDE")"
out=$(jq -n '{transcript_path:"/nonexistent/x.jsonl"}' | bash "$HOOK"); expect PASS "transcript: missing file" "$(classify "$out" $?)"

echo "status-glyph-check: $pass passed, $fail failed"
(( fail == 0 ))
