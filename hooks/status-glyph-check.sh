#!/usr/bin/env bash
# Stop hook: every final assistant message must end with a status line that
# starts with one of the sign-off glyphs defined in the global CLAUDE.md
# ("Response Sign-off Glyphs"). If it doesn't, block once so Claude appends
# one. Fails open (exit 0) on anything unexpected — this must never wedge a
# session.
#
# ⚠️ and ⛏️ count only in their emoji form (followed by U+FE0F); the bare
# text-presentation glyphs render monochrome and are rejected on purpose.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat 2>/dev/null || true)
[ -z "$input" ] && exit 0

# Already sent back once this turn — never loop.
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null || echo false)
[ "$active" = "true" ] && exit 0

# Prefer the message the harness hands us; fall back to the transcript. In
# the transcript each content block can be its own entry, so only the LAST
# assistant entry counts: if it's a tool_use (AskUserQuestion, ExitPlanMode)
# there is no final text to check.
text=$(printf '%s' "$input" | jq -r '.last_assistant_message // empty | if type == "string" then . else empty end' 2>/dev/null || true)
if [ -z "$text" ]; then
  transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)
  [ -n "$transcript" ] && [ -f "$transcript" ] || exit 0
  text=$(jq -rs '
    [ .[] | select(.type == "assistant" and (.isSidechain | not)) ] | last
    | (.message.content // [])
    | if type == "array" then [ .[] | select(.type == "text") | .text ] | join("\n") else . end
  ' "$transcript" 2>/dev/null || true)
fi
[ -z "$text" ] && exit 0

ok=$(printf '%s' "$text" | jq -Rrs '
  [ split("\n")[] | sub("\\s+$"; "") | select(length > 0) ] | last // ""
  | sub("^[—–-]\\s*"; "")
  | test("^(🛑|⚠️|⏳|💡|✅|⛏️|💬)")
' 2>/dev/null || echo true)
[ "$ok" = "true" ] && exit 0

jq -n '{
  decision: "block",
  reason: "Your final message is missing its sign-off line. Reply with ONLY a single line: <glyph> <label, 8 words max>. Glyphs (priority order): 🛑 blocked on Jake · ⚠️ stopped short/caveat · ⏳ idle, watching, resumes on its own · 💡 done, follow-ups worth a look · ⛏️ done + sswt reapable · ✅ done · 💬 answer only. Write ⚠️ and ⛏️ in emoji form (with U+FE0F)."
}'
exit 0
