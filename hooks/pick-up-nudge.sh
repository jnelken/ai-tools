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

cd -P . 2>/dev/null || exit 0
repo_root="$PWD"
while [[ ! -e "$repo_root/.git" ]]; do
  if [[ "$repo_root" == / || -z "$repo_root" ]]; then
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
    break
  fi
  repo_root=${repo_root%/*}
  [[ -n "$repo_root" ]] || repo_root=/
done

target=""
for candidate in "$repo_root/.claude/IN_PROGRESS.md" "$repo_root/IN_PROGRESS.md"; do
  if [[ -f "$candidate" ]]; then
    target="$candidate"
    break
  fi
done

[[ -z "$target" ]] && exit 0

# An "## Urgent, tracked elsewhere" pointer ("- CON-1234 — why") is real
# content even without a checkbox, so a file holding only those is not empty.
open_items=0 urgent_items=0 inside=0 last_updated=""
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*-\ \[\ \] ]] && open_items=$((open_items + 1))
  if [[ "$line" == '## '* ]]; then
    inside=0
    [[ "$line" == '## Urgent'* ]] && inside=1
  fi
  if [[ "$inside" -eq 1 && "$line" =~ ^[[:space:]]*-\  ]]; then
    urgent_items=$((urgent_items + 1))
  fi
  if [[ -z "$last_updated" && "$line" =~ _Last\ updated:\ ([0-9]{4}-[0-9]{2}-[0-9]{2}) ]]; then
    last_updated=${BASH_REMATCH[1]}
  fi
done < "$target"

# Escape every JSON control character as well as quotes and backslashes.
# Keep the result in a variable so callers do not need a command substitution.
json_escape() {
  JSON_STRING=${1//\\/\\\\}
  JSON_STRING=${JSON_STRING//\"/\\\"}
  local i char escaped oct
  for ((i=1; i<32; i++)); do
    printf -v oct '%03o' "$i"
    printf -v char '%b' "\\$oct"
    case "$i" in
      8) escaped='\b' ;; 9) escaped='\t' ;; 10) escaped='\n' ;;
      12) escaped='\f' ;; 13) escaped='\r' ;;
      *) printf -v escaped '\\u%04x' "$i" ;;
    esac
    JSON_STRING=${JSON_STRING//"$char"/"$escaped"}
  done
}

rel="${target#$repo_root/}"

# Hygiene: once every item is resolved, reset the file to the empty template
# in place -- including clearing the Close-out sessions log, which has no
# value once there's nothing left in-progress to trace back to.
if [[ "$open_items" -eq 0 && "$urgent_items" -eq 0 ]]; then
  template=$'# In Progress\n\n_Nothing outstanding._\n'
  # -d '' slurps the whole file (trailing newlines included) without a cmp process.
  current=""
  IFS= read -r -d '' current < "$target" || true
  if [[ "$current" != "$template" ]]; then
    printf '%s' "$template" > "$target"
    msg="Reset \`$rel\` to the empty template -- all items were already resolved, so the stale close-out log was cleared too."
    json_escape "$msg"
    printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$JSON_STRING"
  fi
  exit 0
fi
# Only urgent pointers left: nothing to pick up, nothing to reset.
[[ "$open_items" -eq 0 ]] && exit 0

# Weekly sweep. Every reconcile (close-out, pick-up, wrapup-repos, the sweep
# itself) bumps "_Last updated: YYYY-MM-DD", so a date outside the current ISO
# week means nobody has checked these items against reality this week.
# PICK_UP_NUDGE_TODAY overrides today's date for tests.
# Each date is one process: a $(...) around a bare command (no redirection of its
# own) execs it directly. Callers silence stderr for the whole function instead.
iso_week() {
  REPLY=$(date -j -f %F "$1" +%G-%V)
  [[ -n "$REPLY" ]] || REPLY=$(date -d "$1" +%G-%V)
}
if [[ -n "${PICK_UP_NUDGE_TODAY:-}" ]]; then
  iso_week "$PICK_UP_NUDGE_TODAY" 2>/dev/null
  this_week=$REPLY
else
  this_week=$(date +%G-%V)
fi
last_week=""
if [[ -n "$last_updated" ]]; then
  iso_week "$last_updated" 2>/dev/null
  last_week=$REPLY
fi
plural=""
[[ "$open_items" -ne 1 ]] && plural="s"
open_item_count_text="$open_items open item$plural"

msg="This repo has unresolved work recorded in \`$rel\` ($open_items open item$plural). Mention this to the user and offer to run \`/pick-up\` to resume it -- don't read the file or act on its items yourself; /pick-up owns that flow (plan-mode read, verification against current state, then user approval)."

if [[ -z "$last_week" || "$last_week" != "$this_week" ]]; then
  msg="\`$rel\` has $open_item_count_text and hasn't been reconciled this week (last: ${last_updated:-never}). Run the weekly sweep without blocking the user: launch one background Agent (subagent_type \"fork\") whose prompt is to invoke the \`pick-up\` skill with args \`sweep\` for $repo_root, then carry on with the user's request. When it reports back, relay its result (each removed item with its evidence, and how many are still open) and offer \`/pick-up\` for what's left. Don't read the file or act on its items yourself."
fi

json_escape "$msg"
printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$JSON_STRING"

exit 0
