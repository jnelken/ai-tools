#!/usr/bin/env bash
# Behaviour matrix for pick-up-nudge.sh. Run: bash tests/pick-up-nudge.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/pick-up-nudge.sh}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/pick-up-nudge-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q
mkdir -p "$TMP/.claude"
F="$TMP/.claude/IN_PROGRESS.md"
TODAY=2026-10-07 # a Wednesday, ISO week 2026-41

# run <file contents or "-" for no file> — prints SILENT, OFFER, SWEEP or RESET
run() {
  if [[ "$1" == "-" ]]; then rm -f "$F"; else printf '%s' "$1" > "$F"; fi
  local out; out=$(cd "$TMP" && PICK_UP_NUDGE_TODAY=$TODAY bash "$HOOK")
  local ctx; ctx=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null)
  if [[ -z "$out" ]]; then echo SILENT
  elif [[ "$ctx" == *"pick-up\` skill with args \`sweep\`"* ]]; then echo SWEEP
  elif [[ "$ctx" == *"offer to run \`/pick-up\`"* ]]; then echo OFFER
  elif [[ "$ctx" == Reset* ]]; then echo RESET
  else echo "UNKNOWN: $out"; fi
}

expect() { # expect <want> <label> <got>
  if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want $1, got $3"; fi
}

EMPTY=$'# In Progress\n\n_Nothing outstanding._\n'
open_file() { printf '# In Progress\n\n_Last updated: %s (close-out)_\n\n## Blocked (untracked)\n- [ ] thing\n\n## Close-out sessions\n- %s — abc\n' "$1" "$1"; }

expect SILENT "no file"                       "$(run -)"
expect OFFER  "open, reconciled this week"    "$(run "$(open_file 2026-10-05)")"
expect SWEEP  "open, reconciled last week"    "$(run "$(open_file 2026-10-04)")"
expect SWEEP  "open, reconciled long ago"     "$(run "$(open_file 2026-09-24)")"
expect SWEEP  "open, no date"                 "$(run $'# In Progress\n\n- [ ] thing\n')"
expect RESET  "nothing open resets"           "$(run $'# In Progress\n\n_Last updated: 2026-09-24_\n\n## Close-out sessions\n- 2026-09-24 — abc\n')"
expect "$EMPTY" "reset writes the empty template" "$(cat "$F")"$'\n'
expect SILENT "already empty template"        "$(run "$EMPTY")"
URGENT=$'# In Progress\n\n_Last updated: 2026-09-24_\n\n## Urgent, tracked elsewhere\n- CON-1234 — prod writes failing\n\n## Close-out sessions\n- 2026-09-24 — abc\n'
expect SILENT "only urgent pointers"          "$(run "$URGENT")"
expect "$URGENT" "urgent-only file untouched" "$(cat "$F")"$'\n'

echo "pick-up-nudge: $pass passed, $fail failed"
(( fail == 0 ))
