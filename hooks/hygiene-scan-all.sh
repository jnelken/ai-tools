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

CODE_DIR="${HYGIENE_CODE_DIR:-$HOME/Dropbox/code}"
REPORT="${HYGIENE_REPORT:-$HOME/.claude/HYGIENE.md}"
TTL_HOURS="${HYGIENE_REPORT_TTL_HOURS:-24}"

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

[ -d "$CODE_DIR" ] || exit 0
mkdir -p "$(dirname "$REPORT")" 2>/dev/null || exit 0

# Fresh enough? Then this run is a no-op — that's the whole point of the cache.
if [ "${HYGIENE_FORCE:-0}" != "1" ] && [ -f "$REPORT" ]; then
  now=$(date +%s)
  mtime=$(stat -f %m "$REPORT" 2>/dev/null || stat -c %Y "$REPORT" 2>/dev/null || echo 0)
  case "$mtime" in ''|*[!0-9]*) mtime=0 ;; esac
  [ "$now" -lt "$(( mtime + TTL_HOURS * 3600 ))" ] && exit 0
fi

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

    name=$(basename "$repo")
    branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
    dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    case "$dirty" in ''|*[!0-9]*) dirty=0 ;; esac

    out=$(hygiene_scan_repo "$repo" 2>/dev/null)
    present=$(printf '%s\n' "$out" | sed -n 's/^PRESENT://p' | paste -sd, - | sed 's/,/, /g')
    missing=$(printf '%s\n' "$out" | sed -n 's/^MISSING://p' | paste -sd, - | sed 's/,/, /g')
    problems=$(printf '%s\n' "$out" | sed -n 's/^PROBLEM://p')

    echo "## $name"
    echo
    echo "\`$repo\` · branch \`$branch\` · $( [ "$dirty" -gt 0 ] && echo "$dirty uncommitted" || echo "clean" )"
    echo
    [ -n "$present" ] && echo "- **Present:** $present"
    [ -n "$missing" ] && echo "- **Missing:** $missing"
    if [ -n "$problems" ]; then
      problem_repos=$(( problem_repos + 1 ))
      echo "- **Problems:**"
      printf '%s\n' "$problems" | while IFS= read -r p; do
        [ -n "$p" ] && echo "  - $p"
      done
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
