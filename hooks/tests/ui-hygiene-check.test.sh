#!/usr/bin/env bash
# Behaviour matrix for ui-hygiene-check.sh. Run: bash hooks/tests/ui-hygiene-check.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/ui-hygiene-check.sh}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/ui-hygiene-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT
export UI_HYGIENE_STATE_DIR="$TMP/state"

classify() { # BLOCK only on a block decision; PASS only on empty stdout + exit 0
  local out="$1" status="$2"
  if (( status == 0 )) && [[ -z "$out" ]]; then echo PASS
  elif (( status == 0 )) && printf '%s' "$out" | jq -e '.decision == "block"' >/dev/null 2>&1; then echo BLOCK
  else echo "ERROR(status=$status)"; fi
}
expect() { if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want $1, got $3"; fi; }

# --- transcript builders (one JSON object per line) ---------------------------
prompt()    { jq -nc --arg u "$1" '{type:"user",uuid:$u,message:{role:"user",content:"do the thing"}}'; }
feedback()  { jq -nc '{type:"user",uuid:"fb",message:{role:"user",content:"Stop hook feedback: UI hygiene pass"}}'; }
toolres()   { jq -nc '{type:"user",message:{role:"user",content:[{type:"tool_result",tool_use_id:"t1",content:"ok"}]}}'; }
edit()      { jq -nc --arg p "$1" --arg s "$2" '{type:"assistant",message:{content:[{type:"tool_use",name:"Edit",input:{file_path:$p,old_string:"x",new_string:$s}}]}}'; }
write()     { jq -nc --arg p "$1" --arg s "$2" '{type:"assistant",message:{content:[{type:"tool_use",name:"Write",input:{file_path:$p,content:$s}}]}}'; }
multiedit() { jq -nc --arg p "$1" --arg s "$2" '{type:"assistant",message:{content:[{type:"tool_use",name:"MultiEdit",input:{file_path:$p,edits:[{old_string:"a",new_string:$s}]}}]}}'; }
sidechain() { edit "$1" "$2" | jq -c '. + {isSidechain:true}'; }

run() { # run <session> <transcript> [cwd] — extra env passes through
  local out; out=$(jq -n --arg t "$2" --arg s "$1" --arg c "${3:-}" '{transcript_path:$t,session_id:$s,cwd:$c,stop_hook_active:false}' | bash "$HOOK"); classify "$out" $?
}
mk() { local f="$TMP/$1.jsonl"; shift; : > "$f"; for l in "$@"; do printf '%s\n' "$l" >> "$f"; done; echo "$f"; }

STYLE='<div className="rounded-xl border shadow hover:bg-muted">'
LOGIC='const total = items.reduce((a, b) => a + b, 0);'

expect BLOCK "tsx styling edit"        "$(run s1 "$(mk t1 "$(prompt u1)" "$(edit app/Pane.tsx "$STYLE")" "$(toolres)")")"
expect BLOCK "css var write"           "$(run s2 "$(mk t2 "$(prompt u2)" "$(write app/globals.css ':root { --pane-gap: 1rem; }')")")"
expect BLOCK "multiedit styling"       "$(run s3 "$(mk t3 "$(prompt u3)" "$(multiedit app/Card.tsx "$STYLE")")")"
expect BLOCK "tailwind config"         "$(run s4 "$(mk t4 "$(prompt u4)" "$(edit tailwind.config.ts 'boxShadow: { pane: "0 1px 2px #000" }')")")"
expect PASS  "no edits"                "$(run s5 "$(mk t5 "$(prompt u5)")")"
expect PASS  "ts file"                 "$(run s6 "$(mk t6 "$(prompt u6)" "$(edit app/lib/x.ts "$STYLE")")")"
expect PASS  "tsx logic only"          "$(run s7 "$(mk t7 "$(prompt u7)" "$(edit app/Pane.tsx "$LOGIC")")")"
expect PASS  "test file"               "$(run s8 "$(mk t8 "$(prompt u8)" "$(edit app/Pane.test.tsx "$STYLE")")")"
expect PASS  "story file"              "$(run s9 "$(mk t9 "$(prompt u9)" "$(edit app/Pane.stories.tsx "$STYLE")")")"
expect PASS  "generated file"          "$(run s10 "$(mk t10 "$(prompt u10)" "$(edit app/styles/generated/x.css 'a { border-radius: 4px; }')")")"
expect PASS  "edit before last prompt" "$(run s11 "$(mk t11 "$(prompt u11a)" "$(edit app/Pane.tsx "$STYLE")" "$(prompt u11b)")")"
expect PASS  "sidechain edit ignored"  "$(run s12 "$(mk t12 "$(prompt u12)" "$(sidechain app/Pane.tsx "$STYLE")")")"

# Fires once per prompt: same transcript again passes; a hook-feedback message
# is not a new prompt; a genuinely new prompt re-arms it.
T=$(mk t13 "$(prompt u13)" "$(edit app/Pane.tsx "$STYLE")")
expect BLOCK "first stop blocks"       "$(run s13 "$T")"
expect PASS  "second stop is silent"   "$(run s13 "$T")"
printf '%s\n' "$(feedback)" "$(edit app/Pane.tsx "$STYLE")" >> "$T"
expect PASS  "feedback is not a prompt" "$(run s13 "$T")"
printf '%s\n' "$(prompt u13b)" "$(edit app/Pane.tsx "$STYLE")" >> "$T"
expect BLOCK "new prompt re-arms"      "$(run s13 "$T")"

# Different session, same prompt uuid: independent.
expect BLOCK "other session independent" "$(run s14 "$(mk t14 "$(prompt u13)" "$(edit app/Pane.tsx "$STYLE")")")"

# Switches and knobs.
expect PASS  "disabled by env"         "$(UI_HYGIENE_DISABLE=1 run s15 "$(mk t15 "$(prompt u15)" "$(edit app/Pane.tsx "$STYLE")")")"
expect PASS  "min files 2, one file"   "$(UI_HYGIENE_MIN_FILES=2 run s16 "$(mk t16 "$(prompt u16)" "$(edit app/Pane.tsx "$STYLE")")")"
expect BLOCK "min files 2, two files"  "$(UI_HYGIENE_MIN_FILES=2 run s17 "$(mk t17 "$(prompt u17)" "$(edit app/A.tsx "$STYLE")" "$(edit app/B.tsx "$STYLE")")")"

# Fails open on junk.
expect PASS  "missing transcript"      "$(run s18 "$TMP/nope.jsonl")"
printf 'not json\n' > "$TMP/junk.jsonl"
expect PASS  "garbage transcript"      "$(run s19 "$TMP/junk.jsonl")"
out=$(printf '' | bash "$HOOK"); expect PASS "empty input" "$(classify "$out" $?)"

# Reason names the files and carries per-repo hints.
mkdir -p "$TMP/repo/.claude"; printf 'RADIUS-SCALE-HINT\n' > "$TMP/repo/.claude/ui-hygiene-hints.md"
reason=$(jq -n --arg t "$(mk t20 "$(prompt u20)" "$(edit app/Pane.tsx "$STYLE")")" --arg c "$TMP/repo" '{transcript_path:$t,session_id:"s20",cwd:$c}' | bash "$HOOK" | jq -r '.reason')
[[ "$reason" == *"- app/Pane.tsx"* ]]    && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: reason lists the file"; }
[[ "$reason" == *"RADIUS-SCALE-HINT"* ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: reason carries repo hints"; }
[[ "$reason" == *"READ-ONLY"* && "$reason" == *"follow-up"* ]] && pass=$((pass+1)) || { fail=$((fail+1)); echo "FAIL: reason asks for a read-only review plus a follow-up offer"; }

echo "ui-hygiene-check: $pass passed, $fail failed"
exit $(( fail > 0 ))
