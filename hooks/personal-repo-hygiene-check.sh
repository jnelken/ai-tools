#!/usr/bin/env bash
# SessionStart hook (SYNC): surface repo hygiene for the repo this session is
# actually in, plus a one-line pointer to problems elsewhere.
#
# Split of labor with hygiene-scan-all.sh (the async sibling):
#   - This hook checks the CWD repo LIVE every session (~0.2s). The repo you're
#     working in is the one most likely to have changed, so it is never served
#     from cache.
#   - ~/.claude/HYGIENE.md holds the other repos, refreshed in the background at
#     most daily. We read at most ONE summary line from it — never inject the
#     whole report into context.
#
# All checks live in lib/hygiene-checks.sh, shared with the scanner so the two
# can't drift.
#
# Never blocks a session: every failure mode is a silent no-op.
# State: ~/.claude/state/hygiene-nudges/<repo-hash>.json holds
# last_nudged_at_epoch, so a repo opened ten times a day nudges once per
# HYGIENE_NUDGE_COOLDOWN_HOURS (default 24). Tier-1 problems ignore the
# cooldown — a corrupt .git or a 300-day-old unpushed commit is not a nudge.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

# Resolve through the ~/.claude/hooks/ symlink — that's how settings.json
# invokes this, and dirname of the symlink is NOT where lib/ lives.
_src="${BASH_SOURCE[0]}"
while [ -L "$_src" ]; do
  _dir=$(cd -P "$(dirname "$_src")" 2>/dev/null && pwd)
  _src=$(readlink "$_src")
  case "$_src" in /*) ;; *) _src="$_dir/$_src" ;; esac
done
LIB="$(cd -P "$(dirname "$_src")" 2>/dev/null && pwd)/lib/hygiene-checks.sh"
[ -r "$LIB" ] || exit 0
# shellcheck source=lib/hygiene-checks.sh
. "$LIB" || exit 0

REPORT="${HYGIENE_REPORT:-$HOME/.claude/HYGIENE.md}"

input=$(cat 2>/dev/null || true)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
cwd="${cwd:-$PWD}"

repo_root=$(cd "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)
[ -z "$repo_root" ] && exit 0

hygiene_is_personal_repo "$repo_root" || exit 0

# --- live check of THIS repo ------------------------------------------------
out=$(hygiene_scan_repo "$repo_root" 2>/dev/null)
problems=$(printf '%s\n' "$out" | sed -n 's/^PROBLEM://p')
missing=$(printf '%s\n' "$out" | sed -n 's/^MISSING://p' | paste -sd, - | sed 's/,/, /g')

# --- cooldown applies to advisory notes only, never to real problems --------
show_advisory=1
if command -v shasum >/dev/null 2>&1; then
  repo_hash=$(printf '%s' "$repo_root" | shasum -a 256 | cut -c1-16)
  state_dir="$HOME/.claude/state/hygiene-nudges"
  mkdir -p "$state_dir" 2>/dev/null
  state_file="$state_dir/$repo_hash.json"
  cooldown_hours="${HYGIENE_NUDGE_COOLDOWN_HOURS:-24}"
  now_epoch=$(date +%s)
  if [ -f "$state_file" ]; then
    last_epoch=$(jq -r '.last_nudged_at_epoch // 0' "$state_file" 2>/dev/null || echo 0)
    case "$last_epoch" in ''|*[!0-9]*) last_epoch=0 ;; esac
    [ "$now_epoch" -lt "$(( last_epoch + cooldown_hours * 3600 ))" ] && show_advisory=0
  fi
  [ "$show_advisory" -eq 1 ] && \
    jq -n --arg t "$now_epoch" '{"last_nudged_at_epoch": ($t | tonumber)}' > "$state_file" 2>/dev/null
fi

notes=""
if [ -n "$problems" ]; then
  while IFS= read -r p; do
    [ -n "$p" ] && notes="$notes
- $p"
  done <<< "$problems"
fi
if [ "$show_advisory" -eq 1 ] && [ -n "$missing" ]; then
  notes="$notes
- Missing here: $missing. Offer these only if this session gives a real reason to (see the ROADMAP.md / docs conventions in CLAUDE.md); don't create them unprompted."
fi

# --- one summary line for every OTHER repo, read from the cached report ------
other=""
if [ -f "$REPORT" ]; then
  this_name=$(basename "$repo_root")
  other=$(awk -v skip="$this_name" '
    /^## /      { repo = substr($0, 4); next }
    /^  - /     { if (repo != skip && repo != "") { print repo; repo = "" } }
  ' "$REPORT" 2>/dev/null | sort -u | paste -sd, - | sed 's/,/, /g')
fi
if [ -n "$other" ]; then
  n=$(printf '%s' "$other" | awk -F', ' '{print NF}')
  notes="$notes
- $n other repo(s) have open problems: $other — details in \`$REPORT\` (don't act on these unless asked)."
fi

[ -z "$notes" ] && exit 0

message="Repo hygiene ($repo_root):$notes"
jq -n --arg ctx "$message" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$ctx}}'
