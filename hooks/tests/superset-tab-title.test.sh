#!/usr/bin/env bash
# Behaviour matrix for superset-tab-title.py. Run: bash tests/superset-tab-title.test.sh
# Everything runs in dry-run mode (no host service): the hook prints the title
# it would set, or nothing when it would leave the tab alone.
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/superset-tab-title.py}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/superset-tab-title-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

export SUPERSET_TAB_TITLE_DRY_RUN=1
export SUPERSET_TAB_TITLE_STATE_DIR="$TMP/state"
export SUPERSET_TERMINAL_ID="term-1"
export SUPERSET_WORKSPACE_ID="ws-1"

run() { # run <event> <harness> <existing-title> <json>  -> stdout (title or empty), status in $?
  SUPERSET_TAB_TITLE_EXISTING="$3" python3 "$HOOK" --event "$1" --harness "$2" <<<"$4"
}
expect() { # expect <want> <label> <got>
  if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want [$1], got [$3]"; fi
}
reset() { rm -rf "$SUPERSET_TAB_TITLE_STATE_DIR"; }
FE0F=$'\xef\xb8\x8f'

# ── slug from the first prompt ──
reset
expect "◐ see-if-hook-renaming-working" "first prompt slug" \
  "$(run prompt claude "" '{"session_id":"s1","prompt":"let'"'"'s see if my hook renaming is working. what'"'"'s the name of this current tab?"}')"
expect "◐ see-if-hook-renaming-working" "second prompt keeps slug, resets glyph" \
  "$(run prompt claude "✅ see-if-hook-renaming-working" '{"session_id":"s1","prompt":"now do something else entirely"}')"
expect "" "same title → no write" \
  "$(run prompt claude "◐ see-if-hook-renaming-working" '{"session_id":"s1","prompt":"again"}')"
expect "◐ something-else-entirely" "new session in same terminal re-slugs" \
  "$(run prompt claude "✅ see-if-hook-renaming-working" '{"session_id":"s2","prompt":"now do something else entirely"}')"

# ── manual names are kept, only the glyph moves ──
reset
expect "◐ #1234 CON-9 my-feature" "manual name kept on prompt" \
  "$(run prompt claude "#1234 CON-9 my-feature" '{"session_id":"s1","prompt":"fix the thing"}')"
expect "✅ #1234 CON-9 my-feature" "manual name kept on stop" \
  "$(run stop claude "◐ #1234 CON-9 my-feature" '{"session_id":"s1","last_assistant_message":"done\n\n✅ shipped"}')"

# ── stop glyphs ──
reset
run prompt claude "" '{"session_id":"s1","prompt":"ship the widget"}' >/dev/null
expect "✅ ship-widget" "stop: done"      "$(run stop claude "◐ ship-widget" '{"last_assistant_message":"Did it.\n\n✅ shipped"}')"
expect "⏳💡 ship-widget" "stop: combo"   "$(run stop claude "◐ ship-widget" '{"last_assistant_message":"⏳💡 CI running; follow-ups noted"}')"
expect "⚠${FE0F} ship-widget" "stop: caution emoji form" "$(run stop claude "◐ ship-widget" "{\"last_assistant_message\":\"body\\n⚠${FE0F} tests red\"}")"
expect "⛏${FE0F} ship-widget" "stop: pickaxe emoji form" "$(run stop claude "◐ ship-widget" "{\"last_assistant_message\":\"body\\n⛏${FE0F} reapable\"}")"
expect "✅ ship-widget" "stop: leading dash" "$(run stop claude "◐ ship-widget" '{"last_assistant_message":"body\n— ✅ done"}')"
expect "" "stop: claude, no glyph → wait for the re-sent turn" "$(run stop claude "◐ ship-widget" '{"last_assistant_message":"All done, nothing else."}')"
expect "○ ship-widget" "stop: codex, no glyph → idle marker" "$(run stop codex "◐ ship-widget" '{"last_assistant_message":"All done, nothing else."}')"
expect "" "stop: nothing named yet → no-op" "$(run stop claude "" '{"last_assistant_message":"✅ done"}')"

# ── transcript fallback ──
tx() { printf '%s\n' "$1" > "$TMP/t.jsonl"; run stop claude "◐ ship-widget" "{\"transcript_path\":\"$TMP/t.jsonl\"}"; }
A_OK='{"type":"assistant","message":{"content":[{"type":"text","text":"done\n✅ ok"}]}}'
A_BAD='{"type":"assistant","message":{"content":[{"type":"text","text":"done, no glyph"}]}}'
A_ASK='{"type":"assistant","message":{"content":[{"type":"tool_use","name":"AskUserQuestion","input":{}}]}}'
A_TOOL='{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{}}]}}'
SIDE='{"type":"assistant","isSidechain":true,"message":{"content":[{"type":"text","text":"🛑 subagent"}]}}'
expect "✅ ship-widget" "transcript: glyph"              "$(tx "$A_OK")"
expect "" "transcript: no glyph → wait"                  "$(tx "$A_BAD")"
expect "🛑 ship-widget" "transcript: AskUserQuestion → blocked" "$(tx "$A_BAD"$'\n'"$A_ASK")"
expect "○ ship-widget" "transcript: ends on plain tool_use → idle" "$(tx "$A_BAD"$'\n'"$A_TOOL")"
expect "✅ ship-widget" "transcript: sidechain ignored"  "$(tx "$A_OK"$'\n'"$SIDE")"
expect "" "transcript: missing file" "$(run stop claude "◐ ship-widget" '{"transcript_path":"/nonexistent/x.jsonl"}')"

# ── tolerant prompt parsing (Codex shapes) ──
reset
expect "◐ widget-codex-items" "codex items[].text" "$(run prompt codex "" '{"session_id":"c1","items":[{"type":"text","text":"the widget for codex items"}]}')"
reset
expect "◐ widget-userturn" "codex payload.UserTurn" "$(run prompt codex "" '{"session_id":"c1","payload":{"UserTurn":{"items":[{"text":"a widget userturn"}]}}}')"

# ── never breaks a prompt ──
reset
out=$(printf 'not json' | SUPERSET_TAB_TITLE_EXISTING="" python3 "$HOOK" --event prompt); st=$?
expect "0" "malformed input exits 0" "$st"
expect "" "malformed input is silent" "$out"
out=$(SUPERSET_TERMINAL_ID="" run prompt claude "" '{"prompt":"x"}'); expect "" "outside superset → no-op" "$out"

echo "superset-tab-title: $pass passed, $fail failed"
(( fail == 0 ))
