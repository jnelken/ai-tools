#!/usr/bin/env bash
# Behaviour matrix for status-glyph-check.sh. Run: bash (or zsh) hooks/tests/status-glyph-check.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/status-glyph-check.sh}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/status-glyph-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# BLOCK only on exit 0 with a block decision; PASS only on exit 0 with empty
# stdout; anything else is ERROR so a crash never counts as a pass.
classify() {
  local out="$1" hook_rc="$2"
  if (( hook_rc == 0 )) && [[ -z "$out" ]]; then echo PASS
  elif (( hook_rc == 0 )) && printf '%s' "$out" | jq -e '.decision == "block"' >/dev/null 2>&1; then echo BLOCK
  else echo "ERROR(status=$hook_rc)"; fi
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

# Session-only schedule warning (CronCreate / ScheduleWakeup still pending).
# WARN = exit 0, a systemMessage, and no block decision.
warn_class() {
  local out="$1" hook_rc="$2"
  if (( hook_rc == 0 )) && printf '%s' "$out" | jq -e 'has("systemMessage") and (has("decision") | not)' >/dev/null 2>&1; then echo WARN
  else classify "$out" "$hook_rc"; fi
}
E() { date -u -j -f "%Y-%m-%dT%H:%M:%S" "$1" +%s 2>/dev/null || date -u -d "$1Z" +%s; }
sched() { # sched <now ISO UTC> <jsonl> [message] [stop_hook_active] — hook run with TZ=UTC
  printf '%s\n' "$2" > "$TMP/s.jsonl"
  local out
  out=$(jq -n --arg p "$TMP/s.jsonl" --arg m "${3:-done
✅ ok}" --argjson a "${4:-false}" '{transcript_path:$p,last_assistant_message:$m,stop_hook_active:$a}' \
    | TZ=UTC STATUS_GLYPH_NOW_EPOCH="$(E "$1")" bash "$HOOK")
  warn_class "$out" $?
}
sched_msg() { # like sched but prints the systemMessage text
  printf '%s\n' "$2" > "$TMP/s.jsonl"
  jq -n --arg p "$TMP/s.jsonl" '{transcript_path:$p,last_assistant_message:"done\n✅ ok"}' \
    | TZ=UTC STATUS_GLYPH_NOW_EPOCH="$(E "$1")" bash "$HOOK" | jq -r '.systemMessage // empty'
}
CC='{"type":"assistant","timestamp":"2026-10-15T17:20:10.257Z","message":{"content":[{"type":"tool_use","id":"toolu_c1","name":"CronCreate","input":{"cron":"2 12 16 10 *","recurring":false}}]}}'
CR='{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_c1","content":"Scheduled one-shot task c0882707 (2 12 16 10 *). Next run: Oct 16 12:02pm. Session-only"}]}}'
CD='{"type":"assistant","timestamp":"2026-10-15T18:00:00.000Z","message":{"content":[{"type":"tool_use","id":"toolu_d1","name":"CronDelete","input":{"id":"c0882707"}}]}}'
RC='{"type":"assistant","timestamp":"2026-10-15T17:20:10.257Z","message":{"content":[{"type":"tool_use","id":"toolu_r1","name":"CronCreate","input":{"cron":"*/5 * * * *"}}]}}'
RR='{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_r1","content":"Scheduled recurring job abcd1234 (*/5 * * * *). Next run: soon"}]}}'
W='{"type":"assistant","timestamp":"2026-10-15T17:20:10.257Z","message":{"content":[{"type":"tool_use","id":"toolu_w1","name":"ScheduleWakeup","input":{"delaySeconds":1200,"reason":"watching CI","prompt":"x"}}]}}'
WSTOP='{"type":"assistant","timestamp":"2026-10-15T17:30:00.000Z","message":{"content":[{"type":"tool_use","id":"toolu_w2","name":"ScheduleWakeup","input":{"stop":true}}]}}'
SIDE_CC="${CC/\"type\":\"assistant\"/\"type\":\"assistant\",\"isSidechain\":true}"
expect WARN  "sched: one-shot pending"            "$(sched 2026-10-15T18:00:00 "$CC"$'\n'"$CR")"
expect PASS  "sched: one-shot already fired"      "$(sched 2026-10-16T13:00:00 "$CC"$'\n'"$CR")"
expect PASS  "sched: one-shot deleted"            "$(sched 2026-10-15T18:30:00 "$CC"$'\n'"$CR"$'\n'"$CD")"
expect WARN  "sched: recurring within 7 days"     "$(sched 2026-10-16T17:00:00 "$RC"$'\n'"$RR")"
expect PASS  "sched: recurring expired"           "$(sched 2026-10-24T17:00:00 "$RC"$'\n'"$RR")"
expect WARN  "sched: wakeup pending"              "$(sched 2026-10-15T17:30:00 "$W")"
expect PASS  "sched: wakeup already fired"        "$(sched 2026-10-15T18:00:00 "$W")"
expect PASS  "sched: wakeup cancelled (stop)"     "$(sched 2026-10-15T17:30:00 "$W"$'\n'"$WSTOP")"
expect PASS  "sched: sidechain schedule ignored"  "$(sched 2026-10-15T18:00:00 "$SIDE_CC"$'\n'"$CR")"
expect BLOCK "sched: missing glyph still blocks"  "$(sched 2026-10-15T18:00:00 "$CC"$'\n'"$CR" "no glyph here")"
expect WARN  "sched: warns after the retry pass"  "$(sched 2026-10-15T18:00:00 "$CC"$'\n'"$CR" "no glyph here" true)"
msgtext=$(sched_msg 2026-10-15T18:00:00 "$CC"$'\n'"$CR")
[[ "$msgtext" == *"c0882707"* && "$msgtext" == *"one-shot"* && "$msgtext" == *"lost if this session closes"* ]] \
  && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: sched message text — got: $msgtext"; }

# Headless advance-roadmap runs are exempt: no forced sign-off turn, no schedule warning.
headless() { # headless <value|-> <message> — hook run with ADVANCE_ROADMAP_HEADLESS set (or unset for -)
  local out
  if [[ "$1" == "-" ]]; then
    out=$(jq -n --arg m "$2" '{last_assistant_message:$m, stop_hook_active:false}' | bash "$HOOK")
  else
    out=$(jq -n --arg m "$2" '{last_assistant_message:$m, stop_hook_active:false}' | ADVANCE_ROADMAP_HEADLESS="$1" bash "$HOOK")
  fi
  classify "$out" $?
}
expect PASS  "headless: no glyph is fine"        "$(headless 1 $'planner answer\n```ORCHESTRATOR_RESULT_JSON\n{}\n```')"
expect BLOCK "headless: unset still blocks"      "$(headless - 'planner answer, no glyph')"
expect BLOCK "headless: only 1 exempts"          "$(headless 0 'planner answer, no glyph')"

echo "status-glyph-check: $pass passed, $fail failed"
(( fail == 0 ))
