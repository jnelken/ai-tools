#!/bin/bash
# SessionStart hook: surfaces unresolved IN_PROGRESS.md content so it isn't
# missed at the start of a fresh session -- the counterpart to the pick-up
# skill's own lookup order (.claude/IN_PROGRESS.md, then repo-root).
#
# "Unresolved" means at least one open checklist item ("- [ ]"). Once none
# remain (and no "## Urgent" pointer either), this hook also resets the file to
# the empty template in place -- stale [x] entries and the close-out session log
# have no value once there's nothing left in-progress to trace back to.
#
# Weekly sweep: when open items exist but the file's "_Last updated:" date is
# outside the current ISO week, it asks for `/pick-up sweep` in a background
# agent instead of just offering /pick-up.

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
# An "## Urgent, tracked elsewhere" pointer ("- CON-1234 — why") is real content
# even though it isn't a checkbox, so a file holding only those is not empty.
urgent_items=$(awk '/^## /{inside = ($0 ~ /^## Urgent/)} inside && /^[[:space:]]*- /{n++} END{print n+0}' "$target")

rel="${target#$repo_root/}"

# Hygiene: once every item is resolved, reset the file to the empty template
# in place -- including clearing the Close-out sessions log, which has no
# value once there's nothing left in-progress to trace back to.
if [[ "$open_items" -eq 0 && "$urgent_items" -eq 0 ]]; then
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
# Only urgent pointers left: nothing to pick up, nothing to reset.
[[ "$open_items" -eq 0 ]] && exit 0

# Weekly sweep. Every reconcile (close-out, pick-up, wrapup-repos, the sweep
# itself) bumps "_Last updated: YYYY-MM-DD", so a date outside the current ISO
# week means nobody has checked these items against reality this week.
# PICK_UP_NUDGE_TODAY overrides today's date for tests.
iso_week() { date -j -f %F "$1" +%G-%V 2>/dev/null || date -d "$1" +%G-%V 2>/dev/null; }
last_updated=$(grep -m1 -oE '_Last updated: [0-9]{4}-[0-9]{2}-[0-9]{2}' "$target" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
this_week=$(iso_week "${PICK_UP_NUDGE_TODAY:-$(date +%F)}")
last_week=""
[[ -n "$last_updated" ]] && last_week=$(iso_week "$last_updated")
plural=""
[[ "$open_items" -ne 1 ]] && plural="s"
open_item_count_text="$open_items open item$plural"

msg="This repo has unresolved work recorded in \`$rel\` ($open_items open item$plural). Mention this to the user and offer to run \`/pick-up\` to resume it -- don't read the file or act on its items yourself; /pick-up owns that flow (plan-mode read, verification against current state, then user approval)."

if [[ -z "$last_week" || "$last_week" != "$this_week" ]]; then
  msg="\`$rel\` has $open_item_count_text and hasn't been reconciled this week (last: ${last_updated:-never}). Run the weekly sweep without blocking the user: launch one background Agent (subagent_type \"fork\") whose prompt is to invoke the \`pick-up\` skill with args \`sweep\` for $repo_root, then carry on with the user's request. When it reports back, relay its result (each removed item with its evidence, and how many are still open) and offer \`/pick-up\` for what's left. Don't read the file or act on its items yourself."
fi

jq -n --arg msg "$msg" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $msg
  }
}'

exit 0
