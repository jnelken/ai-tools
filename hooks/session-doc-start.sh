#!/usr/bin/env bash
# SessionStart hook: create/refresh this session's handoff doc at
# .claude/.sessions/<session_id>.md (under the worktree's .claude/ dir,
# globally gitignored — see ~/.config/git/ignore, tracked in dotfiles as
# git/ignore), and sweep stale docs from prior sessions.
#
# Staleness, not liveness: a doc is swept purely by updated_at age
# (default SESSION_DOC_STALE_HOURS=6), never by checking whether the
# session that wrote it is still running. This makes crashed / kill -9
# sessions self-heal without needing any liveness check.
#
# Also mirrors the same doc to a durable, worktree-independent location
# keyed by repo identity (~/.claude/state/repo-sessions/<repo_key>/), so a
# worktree torn down mid-session (sswt teardown, `git worktree remove`)
# doesn't take the only record of that in-progress work with it. See
# session-doc-end.sh for how that mirror's lifecycle differs from the
# local doc's.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

input=""
while IFS= read -r input_line || [[ -n "$input_line" ]]; do
  input="$input$input_line"$'\n'
done
session_id="" cwd=""
session_re='"session_id"[[:space:]]*:[[:space:]]*"([^"\\]*)"'
cwd_re='"cwd"[[:space:]]*:[[:space:]]*"([^"\\]*)"'
if [[ "$input" =~ $session_re ]]; then session_id=${BASH_REMATCH[1]}; fi
if [[ "$input" =~ $cwd_re ]]; then cwd=${BASH_REMATCH[1]}; fi
# Decode both fields in one jq when either fast capture is unsafe/missing.
if [[ ! "$input" =~ $session_re || ! "$input" =~ $cwd_re ]]; then
  fields=$(jq -r '
    "session_id=" + (.session_id // "" | @sh),
    "cwd=" + (.cwd // "" | @sh)' 2>/dev/null <<< "$input")
  # @sh quotes values, including embedded newlines and single quotes.
  session_id="" cwd=""
  eval "$fields"
fi
# Match the trailing-newline stripping of the original jq substitutions.
while [[ "$session_id" == *$'\n' ]]; do session_id=${session_id%$'\n'}; done
while [[ "$cwd" == *$'\n' ]]; do cwd=${cwd%$'\n'}; done
cwd="${cwd:-$PWD}"
[ -z "$session_id" ] && exit 0

cd -P "$cwd" 2>/dev/null || exit 0
repo_root=$PWD
while [[ ! -e "$repo_root/.git" ]]; do
  if [[ "$repo_root" == / || -z "$repo_root" ]]; then
    repo_root=$(cd "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)
    break
  fi
  repo_root=${repo_root%/*}
  [[ -n "$repo_root" ]] || repo_root=/
done
[ -z "$repo_root" ] && exit 0

# A linked worktree keeps HEAD in its own gitdir, and config in commondir.
gitdir="$repo_root/.git"
if [[ -f "$gitdir" ]]; then
  IFS= read -r gitdir_line < "$gitdir" || true
  gitdir=${gitdir_line#gitdir: }
  case "$gitdir" in /*|[A-Za-z]:/*) ;; *) gitdir="$repo_root/$gitdir" ;; esac
fi
if [[ -n "$gitdir" ]] && cd -P "$gitdir" 2>/dev/null; then gitdir=$PWD; else gitdir=""; fi
common_dir="$gitdir"
if [[ -f "$gitdir/commondir" ]]; then
  IFS= read -r common_dir < "$gitdir/commondir" || true
  case "$common_dir" in /*|[A-Za-z]:/*) ;; *) common_dir="$gitdir/$common_dir" ;; esac
  if [[ -n "$common_dir" ]] && cd -P "$common_dir" 2>/dev/null; then common_dir=$PWD; else common_dir=""; fi
fi

# Standard origin config needs no git process. Unusual quoting, includes or
# extensions use git's parser. url.<base>.insteadOf rewrites are deliberately
# not applied on the fast path; the ownership gate uses the literal URL.
origin_url="" origin_parsed=0 in_origin=0 in_ext=0 worktree_config=0 config_complex=0
origin_section_re='^\[remote[[:space:]]+"origin"\][[:space:]]*([#;].*)?$'
url_re='^url[[:space:]]*=[[:space:]]*(.*)$'
if [[ -n "$common_dir" && -r "$common_dir/config" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    case "$line" in
      \[*)
        in_origin=0 in_ext=0
        if [[ "$line" =~ $origin_section_re ]]; then in_origin=1; fi
        case "$line" in
          \[include*) config_complex=1 ;;
          \[extensions*) in_ext=1 ;;
        esac ;;
      *)
        # worktreeConfig only adds $gitdir/config.worktree (checked below);
        # any other extension goes to git's parser.
        if [[ "$in_ext" -eq 1 && -n "$line" ]]; then
          if [[ "$line" =~ ^worktreeConfig[[:space:]]*=[[:space:]]*true$ ]]; then
            worktree_config=1
          else
            config_complex=1
          fi
        fi
        if [[ "$in_origin" -eq 1 && "$line" =~ $url_re ]]; then
          value=${BASH_REMATCH[1]}
          case "$value" in *\\*|*\"*|*\#*|*\;*) config_complex=1 ;; esac
          if [[ "$origin_parsed" -eq 0 ]]; then origin_url=$value; origin_parsed=1; fi
        fi ;;
    esac
  done < "$common_dir/config"
fi
# A per-worktree config could override the remote; only an absent or remote-free
# config.worktree keeps the fast path valid.
if [[ "$worktree_config" -eq 1 && -n "$gitdir" && -e "$gitdir/config.worktree" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in \[remote*|\[include*|\[url*) config_complex=1 ;; esac
  done < "$gitdir/config.worktree"
fi
if [[ "$origin_parsed" -eq 0 || "$config_complex" -eq 1 ]]; then
  origin_url=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
fi

# Personal-repo gate: only run inside repos owned by an allowed GitHub
# owner (default: jnelken) — never Concentro-Inc or any other org. A repo
# with no configured origin remote is treated as personal (local-only
# scratch work, not a cloned company repo).
personal_owners="${PERSONAL_REPO_OWNERS:-jnelken}"
if [ -n "$origin_url" ]; then
  is_personal=0
  old_ifs="$IFS"; IFS=','
  for owner in $personal_owners; do
    case "$origin_url" in
      *"github.com:$owner/"*|*"github.com/$owner/"*) is_personal=1 ;;
    esac
  done
  IFS="$old_ifs"
  [ "$is_personal" -eq 1 ] || exit 0
fi

dir="$repo_root/.claude/.sessions"
[[ -d "$dir" ]] || mkdir -p "$dir" 2>/dev/null || exit 0

# One-time migration from the pre-2026-10-26 location at the worktree root
# (.claude-sessions/): carry any docs over so the staleness sweep below
# still sees them, then drop the old dir once it's empty.
legacy_dir="$repo_root/.claude-sessions"
if [ -d "$legacy_dir" ]; then
  for f in "$legacy_dir"/*.md; do
    [ -e "$f" ] || continue
    [ -e "$dir/${f##*/}" ] || mv "$f" "$dir/" 2>/dev/null || true
  done
  rmdir "$legacy_dir" 2>/dev/null || true
fi

this_file="$dir/$session_id.md"
stale_hours="${SESSION_DOC_STALE_HOURS:-6}"
stale_seconds=$(( stale_hours * 3600 ))
now_epoch=$(date +%s)

# Small session docs: collect first fields and the awk-compatible body in one
# read pass. Strip only leading spaces after ':' (the old sed behavior).
read_doc() {
  doc_updated="" doc_ended="" doc_wt="" doc_started="" doc_started_epoch="" doc_body=""
  local line key value seen='|' separators=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == --- ]]; then
      separators=$((separators + 1))
    elif [[ "$separators" -ge 2 ]]; then
      doc_body="$doc_body$line"$'\n'
    fi
    key=${line%%:*}
    case "$key" in
      updated_at_epoch|ended_cleanly|worktree_path|started_at|started_at_epoch)
        [[ "$line" == *:* && "$seen" != *"|$key|"* ]] || continue
        seen="$seen$key|"
        value=${line#*:}
        value=${value#"${value%%[! ]*}"}
        case "$key" in
          updated_at_epoch) doc_updated=$value ;;
          ended_cleanly) doc_ended=$value ;;
          worktree_path) doc_wt=$value ;;
          started_at) doc_started=$value ;;
          started_at_epoch) doc_started_epoch=$value ;;
        esac ;;
    esac
  done < "$1"
  # Command substitution used to strip all trailing newlines from awk output.
  while [[ "$doc_body" == *$'\n' ]]; do doc_body=${doc_body%$'\n'}; done
}

for f in "$dir"/*.md; do
  [ -e "$f" ] || continue
  [ "$f" = "$this_file" ] && continue
  read_doc "$f"
  updated_epoch=$doc_updated
  case "$updated_epoch" in ''|*[!0-9]*) updated_epoch=0 ;; esac
  if [ "$now_epoch" -ge "$(( updated_epoch + stale_seconds ))" ]; then
    rm -f "$f" 2>/dev/null || true
  fi
done

branch="" head=""
if [[ -r "$gitdir/HEAD" ]]; then IFS= read -r head < "$gitdir/HEAD" || true; fi
case "$head" in
  'ref: refs/heads/'*) branch=${head#ref: refs/heads/} ;;
  *) branch=$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown') ;;
esac
host=${HOSTNAME:-}
host=${host%%.*}
[[ -n "$host" ]] || host=$(hostname -s 2>/dev/null || echo 'unknown')
# UTC civil-from-days (Gregorian), using the same epoch as the age checks.
# Bash 3.2 has no epoch-to-calendar printf conversion.
days=$((now_epoch / 86400)) seconds=$((now_epoch % 86400))
z=$((days + 719468)) era=$((z / 146097)) doe=$((z - era * 146097))
yoe=$(((doe - doe / 1460 + doe / 36524 - doe / 146096) / 365))
y=$((yoe + era * 400)) doy=$((doe - (365 * yoe + yoe / 4 - yoe / 100)))
mp=$(((5 * doy + 2) / 153)) d=$((doy - (153 * mp + 2) / 5 + 1))
m=$((mp + (mp < 10 ? 3 : -9))) y=$((y + (m <= 2)))
printf -v now_iso '%04d-%02d-%02dT%02d:%02d:%02dZ' "$y" "$m" "$d" \
  "$((seconds / 3600))" "$((seconds / 60 % 60))" "$((seconds % 60))"

if [ -f "$this_file" ]; then
  # resuming the same session_id — preserve started_at, keep the body
  read_doc "$this_file"
  started_at=$doc_started
  started_epoch=$doc_started_epoch
  body=$doc_body
else
  started_at=""
  started_epoch=""
  body=""
fi
[ -z "$started_at" ] && started_at="$now_iso"
[ -z "$started_epoch" ] && started_epoch="$now_epoch"

{
  echo "---"
  echo "session_id: $session_id"
  echo "started_at: $started_at"
  echo "started_at_epoch: $started_epoch"
  echo "updated_at: $now_iso"
  echo "updated_at_epoch: $now_epoch"
  echo "branch: $branch"
  echo "host: $host"
  echo "cwd: $repo_root"
  echo "---"
  if [ -n "$body" ]; then
    printf '%s\n' "$body"
  else
    echo ""
    echo "_No active-work summary yet._"
  fi
} > "$this_file"

rel="${this_file#"$repo_root"/}"
msg="Session handoff doc: $rel — keep its body updated with 2-4 sentences on current work and next steps, especially before pausing or ending the session. It is removed automatically on clean exit, and swept automatically if it goes stale (crash/force-quit case)."

# --- Durable, worktree-independent mirror -----------------------------
# repo_key is derived from the repo's git-common-dir, which resolves to
# the same path from any linked worktree of this repo (and the main
# checkout) — so the mirror lands in the same place no matter which
# worktree wrote it.
main_repo_root="$common_dir"
# Fallback to git when the native .git/commondir walk could not place it: the
# mirror is the crash-recovery feature, so it must not be skipped silently.
[ -n "$main_repo_root" ] || main_repo_root=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
main_repo_root="${main_repo_root%/.git}"

if [ -n "$main_repo_root" ]; then
  repo_hash=$(printf '%s' "$main_repo_root" | shasum -a 256)
  repo_key="${main_repo_root##*/}-${repo_hash:0:8}"
  global_dir="$HOME/.claude/state/repo-sessions/$repo_key"
  [[ -d "$global_dir" ]] || mkdir -p "$global_dir" 2>/dev/null

  if [ -d "$global_dir" ]; then
    global_this_file="$global_dir/$session_id.md"

    # Sweep stale global docs — but only ones already marked ended_cleanly:
    # true, and only after a much longer threshold than the local sweep.
    # An unresolved (crashed/killed) entry is never swept by age alone —
    # that's the whole point of the durable mirror.
    global_stale_days="${SESSION_DOC_GLOBAL_STALE_DAYS:-30}"
    global_stale_seconds=$(( global_stale_days * 86400 ))
    for f in "$global_dir"/*.md; do
      [ -e "$f" ] || continue
      [ "$f" = "$global_this_file" ] && continue
      read_doc "$f"
      ended_cleanly=$doc_ended
      [ "$ended_cleanly" = "true" ] || continue
      updated_epoch=$doc_updated
      case "$updated_epoch" in ''|*[!0-9]*) updated_epoch=0 ;; esac
      if [ "$now_epoch" -ge "$(( updated_epoch + global_stale_seconds ))" ]; then
        rm -f "$f" 2>/dev/null || true
      fi
    done

    # Discovery nudge: other sessions in this repo whose global doc isn't
    # marked ended_cleanly, and which look orphaned — either their
    # worktree is gone from disk, or they've gone quiet for a while (same
    # 15-minute liveness threshold wrapup-repos uses), so a live sibling
    # session in another worktree isn't falsely flagged.
    nudge_count=0
    for f in "$global_dir"/*.md; do
      [ -e "$f" ] || continue
      [ "$f" = "$global_this_file" ] && continue
      read_doc "$f"
      ended_cleanly=$doc_ended
      [ "$ended_cleanly" = "true" ] && continue
      wt=$doc_wt
      upd=$doc_updated
      case "$upd" in ''|*[!0-9]*) upd=0 ;; esac
      orphaned=0
      if [ -z "$wt" ] || [ ! -d "$wt" ]; then
        orphaned=1
      elif [ "$now_epoch" -ge "$(( upd + 900 ))" ]; then
        orphaned=1
      fi
      [ "$orphaned" -eq 1 ] && nudge_count=$(( nudge_count + 1 ))
    done
    if [ "$nudge_count" -gt 0 ]; then
      msg="$msg $nudge_count unresolved session doc(s) for this repo may reflect interrupted work — see ~/.claude/state/repo-sessions/$repo_key/."
    fi

    {
      echo "---"
      echo "session_id: $session_id"
      echo "started_at: $started_at"
      echo "started_at_epoch: $started_epoch"
      echo "updated_at: $now_iso"
      echo "updated_at_epoch: $now_epoch"
      echo "branch: $branch"
      echo "host: $host"
      echo "cwd: $repo_root"
      echo "main_repo_root: $main_repo_root"
      echo "worktree_path: $repo_root"
      echo "---"
      if [ -n "$body" ]; then
        printf '%s\n' "$body"
      else
        echo ""
        echo "_No active-work summary yet._"
      fi
    } > "$global_this_file"
  fi
fi

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

json_escape "$msg"
printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$JSON_STRING"
