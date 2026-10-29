#!/usr/bin/env bash
# SessionStart hook (ASYNC): regenerate ~/.claude/HYGIENE.md across every repo
# the hygiene system governs. Runs in the background so a session never waits
# on it; a full scan is ~5-8s across ~24 repos.
#
# The report IS the cache: while it's fresh, sessions read it instead of
# re-scanning. The repo you're actually in is still checked live by the sync
# hook, so the thing most likely to have changed is never served stale.
#
# Force a rebuild: HYGIENE_FORCE=1 bash hygiene-scan-all.sh
# Never blocks a session: every failure mode is a silent no-op.

set -u  # NOT -e

REPORT="${HYGIENE_REPORT:-$HOME/.claude/HYGIENE.md}"
TTL_HOURS="${HYGIENE_REPORT_TTL_HOURS:-24}"

# Fresh enough? Then this run is a no-op — that's the whole point of the cache.
# Decided FIRST, before resolving the lib, the code dir or the machine guard,
# with a single `find -mmin` (no lib, no python, no stat/date): a warm start
# costs one process. Anything unexpected (bad TTL, odd mtime) falls through to
# the scan below.
if [ "${HYGIENE_FORCE:-0}" != "1" ] && [ -f "$REPORT" ]; then
  case "$TTL_HOURS" in ''|*[!0-9]*) TTL_HOURS=24 ;; esac
  { _fresh=$(find "$REPORT" -mmin "-$(( TTL_HOURS * 60 ))"); } 2>/dev/null
  [ -n "$_fresh" ] && exit 0
fi

# Locate hooks/ (and so lib/ and ../bin/). settings.json invokes this through a
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

# Personal machines only (OMEN, JXIV). The work laptop normally has no personal
# code dir at all, but PERSONAL_CODE_DIR can point one anywhere — without this
# the full ~6s scan would fire on work sessions for repos it doesn't govern.
# Shared predicate with dotfiles/install.sh; absent guard = assume personal.
# Checked before resolving the code dir so a work machine never starts python.
GUARD="$HOME/dotfiles/bin/is-personal-machine"
if [ -x "$GUARD" ] && ! "$GUARD"; then
  exit 0
fi

{ CODE_DIR=$("$_hooks/../bin/personal-code-dir" --legacy-env HYGIENE_CODE_DIR); } 2>/dev/null || CODE_DIR=""
[ -d "$CODE_DIR" ] || exit 0
_report_dir="${REPORT%/*}"
[ "$_report_dir" = "$REPORT" ] && _report_dir=.
[ -d "$_report_dir" ] || mkdir -p "$_report_dir" 2>/dev/null || exit 0

# macOS has no flock. Atomic mkdir is the portable mutex; the sync hook uses the
# same lock when it splices its section, so the two can't interleave writes.
LOCK="$REPORT.lock"
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

tmp="$REPORT.tmp.$$"
{
  echo "# Repo hygiene"
  echo
  echo "_Generated $(date '+%Y-%m-%d %H:%M:%S'). Refreshes in the background at most every ${TTL_HOURS}h;"
  echo "the repo a session is actually in is re-checked live, so it is never served from here._"
  echo
  echo "Scanned: \`$CODE_DIR\` · personal repos only (see PERSONAL_REPO_OWNERS)."
  echo

  problem_repos=0
  total=0

  for d in "$CODE_DIR"/*/; do
    repo="${d%/}"
    [ -d "$repo/.git" ] || continue
    hygiene_is_personal_repo "$repo" || continue
    total=$(( total + 1 ))

    name="${repo##*/}"
    hygiene_branch "$repo" || { HYGIENE_BRANCH=$(git -C "$repo" rev-parse --abbrev-ref HEAD); } 2>/dev/null
    branch="${HYGIENE_BRANCH:-?}"
    { st=$(git -C "$repo" status --porcelain); } 2>/dev/null
    dirty=0
    if [ -n "$st" ]; then
      _nl="${st//[!$'\n']/}"
      dirty=$(( ${#_nl} + 1 ))
    fi
    if [ "$dirty" -gt 0 ]; then state="$dirty uncommitted"; else state="clean"; fi

    hygiene_scan_repo_collect "$repo"
    present="" missing="" problems=""
    while IFS= read -r line; do
      case "$line" in
        PRESENT:*) present="${present:+$present, }${line#PRESENT:}" ;;
        MISSING:*) missing="${missing:+$missing, }${line#MISSING:}" ;;
        PROBLEM:*) problems="${problems:+$problems$'\n'}${line#PROBLEM:}" ;;
      esac
    done <<< "$HYGIENE_SCAN_OUT"

    echo "## $name"
    echo
    echo "\`$repo\` · branch \`$branch\` · $state"
    echo
    [ -n "$present" ] && echo "- **Present:** $present"
    [ -n "$missing" ] && echo "- **Missing:** $missing"
    if [ -n "$problems" ]; then
      problem_repos=$(( problem_repos + 1 ))
      echo "- **Problems:**"
      while IFS= read -r p; do
        [ -n "$p" ] && echo "  - $p"
      done <<< "$problems"
    fi
    echo
  done

  echo "---"
  echo
  echo "_$total repos scanned; $problem_repos with problems._"
} > "$tmp" 2>/dev/null

# Atomic swap so a reader never sees a half-written report.
if [ -s "$tmp" ]; then
  mv -f "$tmp" "$REPORT" 2>/dev/null || rm -f "$tmp" 2>/dev/null
else
  rm -f "$tmp" 2>/dev/null
fi
