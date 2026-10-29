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
# State: ~/.claude/state/hygiene-nudges/<repo-path-slug>.json holds
# last_nudged_at_epoch, so a repo opened ten times a day nudges once per
# HYGIENE_NUDGE_COOLDOWN_HOURS (default 24). Tier-1 problems ignore the
# cooldown — a corrupt .git or a 300-day-old unpushed commit is not a nudge.
#
# Process budget: a Concentro repo exits having spawned nothing (stdin, cwd,
# repo root and origin owner are all read with builtins); a personal repo costs
# fsck + for-each-ref + ls-files + date, plus jq only when there is something
# to say. See lib/hygiene-checks.sh.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

# Locate hooks/ (and so lib/). settings.json invokes this through a
# ~/.claude/hooks/ symlink into the deploy clone, so the script's own directory
# is NOT where lib/ lives. Cheapest first: not a symlink -> its own dir; a
# symlink that is the same file as $AI_TOOLS_HOME/hooks/<name> -> that dir
# (builtin -ef, no readlink); otherwise follow the link chain.
_src="${BASH_SOURCE[0]}"
_hooks=""
if [ -L "$_src" ]; then
  _aih="${AI_TOOLS_HOME:-$HOME/.ai-tools}"
  if [ "$_aih/hooks/${_src##*/}" -ef "$_src" ]; then
    _hooks="$_aih/hooks"
  else
    while [ -L "$_src" ]; do
      _dir=$(cd -P "$(dirname "$_src")" 2>/dev/null && pwd)
      _src=$(readlink "$_src")
      case "$_src" in /*) ;; *) _src="$_dir/$_src" ;; esac
    done
  fi
fi
if [ -z "$_hooks" ]; then
  case "$_src" in */*) _hooks="${_src%/*}" ;; *) _hooks=. ;; esac
  case "$_hooks" in /*) ;; *) _hooks="$PWD/$_hooks" ;; esac
fi
LIB="$_hooks/lib/hygiene-checks.sh"
[ -r "$LIB" ] || exit 0
# shellcheck source=lib/hygiene-checks.sh
. "$LIB" || exit 0

REPORT="${HYGIENE_REPORT:-$HOME/.claude/HYGIENE.md}"

# stdin is one small JSON object. Read it with the builtin and pull cwd out with
# a regex; only a payload the regex can't take (escaped characters, a null cwd)
# pays for a jq.
input=""
while IFS= read -r line || [ -n "$line" ]; do input="$input$line"$'\n'; done
cwd=""
_cwd_re='"cwd"[[:space:]]*:[[:space:]]*"([^"\\]*)"'
if [[ $input =~ $_cwd_re ]]; then
  cwd="${BASH_REMATCH[1]}"
elif [[ $input == *'"cwd"'* ]]; then
  { cwd=$(jq -r '.cwd // empty' <<< "$input"); } 2>/dev/null || cwd=""
fi
cwd="${cwd:-$PWD}"

# Repo root: walk up for a .git entry (a FILE in a worktree, so -e) from the
# physical cwd, which is what `git rev-parse --show-toplevel` reports. git is
# only the fallback for a cwd the walk can't place.
cd -P -- "$cwd" 2>/dev/null || exit 0
hygiene_repo_root "$PWD"
repo_root="$HYGIENE_ROOT"
[ -z "$repo_root" ] && { repo_root=$(git rev-parse --show-toplevel); } 2>/dev/null
[ -z "$repo_root" ] && exit 0

hygiene_is_personal_repo "$repo_root" || exit 0

# --- live check of THIS repo ------------------------------------------------
hygiene_scan_repo_collect "$repo_root"
problems="" missing="" no_impeccable=0
while IFS= read -r line; do
  case "$line" in
    PROBLEM:*) problems="${problems:+$problems$'\n'}${line#PROBLEM:}" ;;
    'MISSING:impeccable document') no_impeccable=$(( no_impeccable + 1 )) ;;
    MISSING:*) missing="${missing:+$missing, }${line#MISSING:}" ;;
  esac
done <<< "$HYGIENE_SCAN_OUT"

# --- cooldown applies to advisory notes only, never to real problems --------
# The state file is named after the repo path (no hashing process); it holds the
# epoch of the last advisory nudge.
show_advisory=1
state_dir="$HOME/.claude/state/hygiene-nudges"
repo_key="${repo_root//[![:alnum:]._-]/_}"
[ "${#repo_key}" -gt 180 ] && repo_key="${repo_key: -180}"
state_file="$state_dir/$repo_key.json"
cooldown_hours="${HYGIENE_NUDGE_COOLDOWN_HOURS:-24}"
case "$cooldown_hours" in ''|*[!0-9]*) cooldown_hours=24 ;; esac
hygiene_now
if [ -f "$state_file" ]; then
  state_json=""
  IFS= read -r -d '' state_json < "$state_file" 2>/dev/null || true
  last_epoch=0
  _epoch_re='"last_nudged_at_epoch"[[:space:]]*:[[:space:]]*([0-9]+)'
  [[ $state_json =~ $_epoch_re ]] && last_epoch="${BASH_REMATCH[1]}"
  [ "$HYGIENE_NOW" -lt "$(( last_epoch + cooldown_hours * 3600 ))" ] && show_advisory=0
fi
if [ "$show_advisory" -eq 1 ]; then
  [ -d "$state_dir" ] || mkdir -p "$state_dir" 2>/dev/null
  printf '{\n  "last_nudged_at_epoch": %s\n}\n' "$HYGIENE_NOW" > "$state_file" 2>/dev/null
fi

notes=""
if [ -n "$problems" ]; then
  while IFS= read -r p; do
    [ -n "$p" ] && notes="$notes
- $p"
  done <<< "$problems"
fi
if [ "$show_advisory" -eq 1 ] && [ "${no_impeccable:-0}" -gt 0 ]; then
  notes="$notes
- \`\$impeccable document\` has not been run in this repo (needs both DESIGN.md and .impeccable/design.json). Tell the user in your first reply; don't run it unprompted."
fi
if [ "$show_advisory" -eq 1 ] && [ -n "$missing" ]; then
  notes="$notes
- Missing here: $missing. Offer these only if this session gives a real reason to (see the ROADMAP.md / docs conventions in CLAUDE.md); don't create them unprompted."
fi

# --- one summary line for every OTHER repo, read from the cached report ------
# Every "## <repo>" heading followed by a problem bullet ("  - ...") names a repo
# with open problems; the repo this session is in is skipped. Sorted, unique.
other="" n=0
if [ -f "$REPORT" ]; then
  this_name="${repo_root##*/}"
  _rep=""
  _names=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      '## '*) _rep="${line#\#\# }" ;;
      '  - '*)
        if [ -n "$_rep" ] && [ "$_rep" != "$this_name" ]; then
          _dup=0
          for _x in ${_names[@]+"${_names[@]}"}; do [ "$_x" = "$_rep" ] && { _dup=1; break; }; done
          [ "$_dup" -eq 0 ] && _names=(${_names[@]+"${_names[@]}"} "$_rep")
          _rep=""
        fi
        ;;
    esac
  done < "$REPORT"
  # insertion sort (no `sort` process; bash 3.2 has no mapfile)
  _i=1
  while [ "$_i" -lt "${#_names[@]}" ]; do
    _v="${_names[$_i]}"; _j=$(( _i - 1 ))
    while [ "$_j" -ge 0 ] && [[ "${_names[$_j]}" > "$_v" ]]; do
      _names[$(( _j + 1 ))]="${_names[$_j]}"; _j=$(( _j - 1 ))
    done
    _names[$(( _j + 1 ))]="$_v"
    _i=$(( _i + 1 ))
  done
  n=${#_names[@]}
  for _x in ${_names[@]+"${_names[@]}"}; do other="${other:+$other, }$_x"; done
fi
if [ -n "$other" ]; then
  notes="$notes
- $n other repo(s) have open problems: $other — details in \`$REPORT\` (don't act on these unless asked)."
fi

[ -z "$notes" ] && exit 0

message="Repo hygiene ($repo_root):$notes"
jq -n --arg ctx "$message" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$ctx}}'
