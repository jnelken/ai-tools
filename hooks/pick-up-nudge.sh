#!/bin/bash
# SessionStart hook: surfaces unresolved IN_PROGRESS.md content so it isn't
# missed at the start of a fresh session -- the counterpart to the pick-up
# skill's own lookup order (.claude/IN_PROGRESS.md, then repo-root).
#
# "Unresolved" means at least one open checklist item ("- [ ]"). Once none
# remain, this hook also resets the file to the empty template in place --
# stale [x] entries and the close-out session log have no value once there's
# nothing left in-progress to trace back to.

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

target=""
for candidate in "$repo_root/.claude/IN_PROGRESS.md" "$repo_root/IN_PROGRESS.md"; do
  if [[ -f "$candidate" ]]; then
    target="$candidate"
    break
  fi
done

[[ -z "$target" ]] && exit 0
command -v jq &>/dev/null || exit 0

open_items=$(grep -c '^[[:space:]]*- \[ \]' "$target" 2>/dev/null)
open_items=${open_items:-0}

rel="${target#$repo_root/}"

# Hygiene: once every item is resolved, reset the file to the empty template
# in place -- including clearing the Close-out sessions log, which has no
# value once there's nothing left in-progress to trace back to.
if [[ "$open_items" -eq 0 ]]; then
  template=$'# In Progress\n\n_Nothing outstanding._\n'
  if ! printf '%s' "$template" | cmp -s - "$target"; then
    printf '%s' "$template" > "$target"
    msg="Reset \`$rel\` to the empty template -- all items were already resolved, so the stale close-out log was cleared too."
    jq -n --arg msg "$msg" '{
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $msg
      }
    }'
  fi
  exit 0
fi
plural=""
[[ "$open_items" -ne 1 ]] && plural="s"

msg="This repo has unresolved work recorded in \`$rel\` ($open_items open item$plural). Mention this to the user and offer to run \`/pick-up\` to resume it -- don't read the file or act on its items yourself; /pick-up owns that flow (plan-mode read, verification against current state, then user approval)."

jq -n --arg msg "$msg" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $msg
  }
}'

exit 0
