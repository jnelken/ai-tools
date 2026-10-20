#!/usr/bin/env bash
# Stop hook: after a turn that did UI styling work, send Claude back ONCE to run
# a design-consistency pass over what it just changed and to OFFER (not apply) a
# follow-up that unifies any inconsistencies or gaps it finds.
#
# Origin: the Folio sidebar re-skin (woodrow#1713). Reviewing one change turned
# up a family of inconsistencies (pane shadows vs borders, radius scale, a
# hand-rolled panel that ignored the shared card style, active vs hover pill
# weight, a gutter hard-coded in five places). This hook makes that review a
# habit instead of something Jake has to ask for.
#
# "UI work" = in the current turn (everything after the last real user prompt)
# the assistant edited/wrote a UI file (.tsx/.jsx/.css/.scss/.vue/.svelte, a
# tailwind/globals config) AND the edited text contains styling tokens
# (className/tailwind utilities, CSS vars, hover:/dark: variants, ...); any edit
# to a tailwind config counts. Pure
# logic edits, tests, stories and generated files don't trigger it.
#
# Fires at most once per user prompt (marker file), so the follow-up review turn
# and the sign-off hook never loop. Fails open (exit 0) on anything unexpected.
#
# Optional per-repo hints: <cwd>/.claude/ui-hygiene-hints.md (first 2KB) is
# appended to the review prompt, e.g. "radius scale: panes xl, items lg".
#
# Env:
#   UI_HYGIENE_DISABLE=1      turn the hook off
#   UI_HYGIENE_STATE_DIR      marker dir (default ~/.claude/state/ui-hygiene)
#   UI_HYGIENE_MIN_FILES      distinct UI files needed to trigger (default 1)

set -u  # NOT -e — graceful no-op on any failure

[ "${UI_HYGIENE_DISABLE:-0}" = "1" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat 2>/dev/null || true)
[ -z "$input" ] && exit 0

transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)
session=$(printf '%s' "$input" | jq -r '.session_id // "nosession"' 2>/dev/null || echo nosession)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
[ -n "$transcript" ] && [ -f "$transcript" ] || exit 0

state_dir=${UI_HYGIENE_STATE_DIR:-$HOME/.claude/state/ui-hygiene}
min_files=${UI_HYGIENE_MIN_FILES:-1}

# One pass over the transcript, main thread only (sidechain entries belong to
# subagents). Emits a JSON object: the turn key (uuid of the last real user
# prompt) and the UI files whose edited text contained styling tokens.
summary=$(jq -rs --argjson min "$min_files" '
  def is_prompt:
    .type == "user" and ((.isSidechain // false) | not) and ((.isMeta // false) | not)
    and (.message.content
         | if type == "string" then (startswith("Stop hook feedback") | not)
           elif type == "array" then (any(.[]; .type == "text") and (any(.[]; .type == "tool_result") | not))
           else false end);
  def ui_path: test("\\.(tsx|jsx|css|scss|vue|svelte)$|tailwind\\.config\\.[cm]?[jt]s$")
               and (test("\\.(test|spec|stories)\\.|/__generated__/|/generated/|\\.generated\\.|node_modules/") | not);
  def style_text: test("className|class=|\\b(rounded|shadow|border|bg|text|ring|gap|space|divide|outline|opacity|ease|duration)-[A-Za-z0-9\\[]|\\b(hover|focus|focus-visible|active|disabled|dark|group-hover|peer-hover|data-\\[[^\\]]*\\]):|--[a-z][a-z0-9-]*\\s*:|\\b(padding|margin|border-radius|box-shadow|background)\\s*:|var\\(--");
  [ .[] | select((.isSidechain // false) | not) ] as $e
  | ( [ $e | to_entries[] | select(.value | is_prompt) | .key ] | last // -1 ) as $idx
  | ( if $idx >= 0 then $e[$idx].uuid // ($idx | tostring) else "none" end ) as $key
  | [ $e[($idx + 1):][]
      | select(.type == "assistant")
      | .message.content[]?
      | select(.type == "tool_use" and (.name | IN("Edit", "Write", "MultiEdit", "NotebookEdit")))
      | .input as $in
      | ($in.file_path // $in.notebook_path // "") as $p
      | select($p | ui_path)
      | ( [ $in.new_string?, $in.content?, $in.new_source?, ($in.edits[]?.new_string?) ] | map(select(type == "string")) | join("\n") ) as $text
      | select(($p | test("tailwind\\.config\\.")) or ($text | style_text))
      | $p ] | unique as $files
  | { key: $key, files: $files, trigger: (($files | length) >= $min) }
' "$transcript" 2>/dev/null || true)
[ -n "$summary" ] || exit 0

trigger=$(printf '%s' "$summary" | jq -r '.trigger' 2>/dev/null || echo false)
[ "$trigger" = "true" ] || exit 0

key=$(printf '%s:%s' "$session" "$(printf '%s' "$summary" | jq -r '.key')" | shasum 2>/dev/null | cut -c1-16)
[ -n "$key" ] || exit 0
marker="$state_dir/$key"
[ -e "$marker" ] && exit 0
mkdir -p "$state_dir" 2>/dev/null || exit 0
: > "$marker" 2>/dev/null || exit 0
find "$state_dir" -type f -mtime +7 -delete 2>/dev/null || true

files=$(printf '%s' "$summary" | jq -r '.files | map("- " + .) | join("\n")')
hints=""
if [ -n "$cwd" ] && [ -f "$cwd/.claude/ui-hygiene-hints.md" ]; then
  hints=$(head -c 2048 "$cwd/.claude/ui-hygiene-hints.md" 2>/dev/null || true)
fi

reason=$(cat <<EOF
UI hygiene pass. This turn you changed styling in:
$files

Before you finish, do a short READ-ONLY design-consistency review of that work against its siblings and the shared primitives. Check, and only mention what you actually find:
- radius scale (outer containers vs inner items vs controls), border and shadow/elevation (one shared treatment per surface type), spacing and gutter values (a number repeated in several places should be one variable)
- colour: raw hex or palette values where a semantic token exists; light AND dark; text/icon contrast
- state parity: rest, hover, active/selected, focus-visible, disabled (does one state look heavier or shift layout?)
- variants of the same surface: collapsed, narrow/drawer, empty, loading, long content
- hand-rolled one-offs that duplicate an existing component or card style; stale class assertions in tests/stories
$hints

Reply with at most ~10 lines: either "Inconsistencies found" (file:line, what differs, the one-line unification) or "No design inconsistencies found". Do NOT apply any fix and do not widen scope. If you found some, end by offering to unify them as a follow-up: as a ridealong commit on this PR, or a separate PR (say which you recommend and why). Then give your normal sign-off line (💡 if there are follow-ups).
EOF
)

jq -n --arg r "$reason" '{decision: "block", reason: $r}'
exit 0
