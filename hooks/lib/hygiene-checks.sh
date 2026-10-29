#!/usr/bin/env bash
# Shared repo-hygiene checks. Single source of truth for two callers:
#   - personal-repo-hygiene-check.sh  (SessionStart, sync, cwd repo only)
#   - hygiene-scan-all.sh             (SessionStart, async, every personal repo)
#
# Keeping these in one place is deliberate: the autocommit guard drifted between
# an inlined copy and the skill once already, and the divergence went unnoticed
# for two weeks.
#
# Contract: hygiene_scan_repo <repo_root> prints structured lines to stdout.
#   PRESENT:<label>    a convention this repo satisfies
#   MISSING:<label>    a convention absent (advisory — Tier 2)
#   PROBLEM:<text>     something actually wrong (Tier 1 — always surfaced)
# Never exits non-zero on a check failure; every probe is best-effort.
#
# Process budget: every fork+exec costs ~16 ms on the work Mac (an EDR gates
# exec), so these helpers read .git metadata, configs and small files with bash
# builtins and keep `git` for what only git can answer (fsck, upstream track,
# ls-files, status). Callers that want the result without a `$(...)` subshell
# use hygiene_scan_repo_collect, which fills HYGIENE_SCAN_OUT instead of
# printing. Bash 3.2 safe (no ${x,,}, no mapfile, no assoc arrays).

# Epoch seconds, memoised: at most one `date` per run, and only when a caller
# actually needs the current time. Sets HYGIENE_NOW.
hygiene_now() {
  [ -n "${HYGIENE_NOW:-}" ] || HYGIENE_NOW=$(date +%s)
}

# Nearest ancestor (inclusive) of <dir> that holds a .git entry — a directory,
# or a FILE in a worktree/submodule, hence -e. Sets HYGIENE_ROOT, empty if none.
# Pass a physical path (callers `cd -P` first) so the result matches what
# `git rev-parse --show-toplevel` prints.
hygiene_repo_root() {
  local d="$1"
  HYGIENE_ROOT=""
  while [ -n "$d" ]; do
    if [ -e "$d/.git" ]; then HYGIENE_ROOT="$d"; return 0; fi
    d="${d%/*}"
  done
  return 1
}

# Resolve <repo>/.git to the real git dir without git. Sets HYGIENE_GITDIR.
# Fails (return 1) on anything unusual so the caller can ask git instead.
hygiene_git_dir() {
  local repo="$1" g="$1/.git" line=""
  HYGIENE_GITDIR=""
  if [ -d "$g" ]; then
    HYGIENE_GITDIR="$g"
  elif [ -f "$g" ]; then
    IFS= read -r line < "$g" 2>/dev/null || [ -n "$line" ] || return 1
    line="${line%$'\r'}"
    case "$line" in 'gitdir: '*) line="${line#gitdir: }" ;; *) return 1 ;; esac
    case "$line" in /*|[A-Za-z]:*) ;; *) line="$repo/$line" ;; esac
    [ -d "$line" ] || return 1
    HYGIENE_GITDIR="$line"
  else
    return 1
  fi
  [ -r "$HYGIENE_GITDIR/HEAD" ]
}

# Read origin's URL straight from the repo config (the common dir's config for a
# linked worktree). Sets HYGIENE_ORIGIN_URL. Returns 1 — "ask git instead" —
# unless it found a plain URL and nothing that could rewrite or redirect it:
# no GIT_CONFIG_* env, no include/includeIf, no url.<base>.insteadOf in the repo
# config, ~/.gitconfig or the XDG config, and no quoting/escapes/comments in the
# value. A repo with no origin also returns 1, so a config this parser misreads
# can never silently become "no remote = personal".
hygiene_origin_url_native() {
  local repo="$1" common="" c cfg line section="" v xdg
  HYGIENE_ORIGIN_URL=""
  [ -z "${GIT_CONFIG_GLOBAL-}${GIT_CONFIG_SYSTEM-}${GIT_CONFIG_COUNT-}${GIT_CONFIG-}${GIT_CONFIG_PARAMETERS-}" ] || return 1
  hygiene_git_dir "$repo" || return 1
  common="$HYGIENE_GITDIR"
  if [ -f "$HYGIENE_GITDIR/commondir" ]; then
    IFS= read -r c < "$HYGIENE_GITDIR/commondir" || [ -n "$c" ] || return 1
    c="${c%$'\r'}"
    case "$c" in /*|[A-Za-z]:*) ;; *) c="$HYGIENE_GITDIR/$c" ;; esac
    common="$c"
  fi
  [ -r "$common/config" ] || return 1
  xdg="${XDG_CONFIG_HOME:-$HOME/.config}/git/config"
  local sec_re='^[[:space:]]*\[([^]]*)\]'
  local url_re='^[[:space:]]*[uU][rR][lL][[:space:]]*=[[:space:]]*(.*)$'
  for cfg in "$common/config" "$HOME/.gitconfig" "$xdg"; do
    [ -r "$cfg" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      case "$line" in
        *[iI][nN][sS][tT][eE][aA][dD][oO][fF]*|*[iI][nN][cC][lL][uU][dD][eE]*) return 1 ;;
      esac
      [ "$cfg" = "$common/config" ] || continue
      if [[ $line =~ $sec_re ]]; then
        section="${BASH_REMATCH[1]}"
      elif [ -z "$HYGIENE_ORIGIN_URL" ] && [ "$section" = 'remote "origin"' ] && [[ $line =~ $url_re ]]; then
        v="${BASH_REMATCH[1]}"
        v="${v%"${v##*[![:space:]]}"}"
        case "$v" in *[\"\\\#\;]*) return 1 ;; esac
        HYGIENE_ORIGIN_URL="$v"
      fi
    done < "$cfg"
  done
  [ -n "$HYGIENE_ORIGIN_URL" ]
}

# Branch name HEAD points at (or "HEAD" when detached) without git. Sets
# HYGIENE_BRANCH; returns 1 when the repo can't be read natively.
hygiene_branch() {
  local line=""
  HYGIENE_BRANCH=""
  hygiene_git_dir "$1" || return 1
  IFS= read -r line < "$HYGIENE_GITDIR/HEAD" || [ -n "$line" ] || return 1
  case "$line" in
    'ref: refs/heads/'*) HYGIENE_BRANCH="${line#ref: refs/heads/}" ;;
    *) HYGIENE_BRANCH="HEAD" ;;
  esac
}

# Is this a repo the hygiene system governs? Personal (jnelken) or no remote
# at all (local-only scratch). Never Concentro-Inc or another org.
hygiene_is_personal_repo() {
  local repo_root="$1"
  local personal_owners="${PERSONAL_REPO_OWNERS:-jnelken}"
  local origin_url
  if hygiene_origin_url_native "$repo_root"; then
    origin_url="$HYGIENE_ORIGIN_URL"
  else
    { origin_url=$(git -C "$repo_root" remote get-url origin); } 2>/dev/null || origin_url=""
  fi
  [ -z "$origin_url" ] && return 0   # no remote = personal scratch

  local old_ifs="$IFS" owner
  IFS=','
  for owner in $personal_owners; do
    case "$origin_url" in
      *"github.com:$owner/"*|*"github.com/$owner/"*) IFS="$old_ifs"; return 0 ;;
    esac
  done
  IFS="$old_ifs"
  return 1
}

_hygiene_emit() { HYGIENE_SCAN_OUT="${HYGIENE_SCAN_OUT}$1"$'\n'; }

# Prints the structured lines (the original contract).
hygiene_scan_repo() {
  hygiene_scan_repo_collect "$1"
  printf '%s' "$HYGIENE_SCAN_OUT"
}

# Same checks, no output and no subshell: the lines land in HYGIENE_SCAN_OUT
# (newline-terminated). Prefer this over `out=$(hygiene_scan_repo ...)`, which
# costs a fork.
hygiene_scan_repo_collect() {
  local repo_root="$1"
  local gitdir f d line base i hit
  HYGIENE_SCAN_OUT=""

  if hygiene_git_dir "$repo_root"; then
    gitdir="$HYGIENE_GITDIR"
  else
    { gitdir=$(git -C "$repo_root" rev-parse --git-dir); } 2>/dev/null || {
      _hygiene_emit "PROBLEM:not a git repo (or .git unreadable)"
      return 0
    }
    case "$gitdir" in /*) ;; *) gitdir="$repo_root/$gitdir" ;; esac
  fi

  # --- Tier 1: git state ---------------------------------------------------
  # Mirrors dotfiles/bin/git-safe-to-autocommit (mid-rebase/merge/cherry-pick/
  # revert/bisect, detached HEAD) with file tests instead of three processes
  # (zsh + two git). Keep the two in step. Like before, skipped on machines
  # without the dotfiles guard.
  local guard="$HOME/dotfiles/bin/git-safe-to-autocommit" why=""
  if [ -x "$guard" ]; then
    if [ -e "$gitdir/MERGE_HEAD" ] || [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] \
        || [ -e "$gitdir/CHERRY_PICK_HEAD" ] || [ -e "$gitdir/REVERT_HEAD" ] || [ -e "$gitdir/BISECT_LOG" ]; then
      why="repo mid-rebase/merge/cherry-pick/revert/bisect, resolve manually"
    else
      line=""
      IFS= read -r line < "$gitdir/HEAD" 2>/dev/null || true
      case "$line" in ref:*) ;; *) why="HEAD is detached" ;; esac
    fi
    [ -n "$why" ] && _hygiene_emit "PROBLEM:git state — $why"
  fi

  # --- Tier 1: .git integrity ----------------------------------------------
  # Dropbox-synced .git dirs do get corrupted (snap-dash lost trees and blobs
  # this way). Must be a real fsck: on snap-dash, HEAD still resolves and
  # `git status` still succeeds while trees are demonstrably broken, so the
  # cheap probes report a clean bill of health on a repo that is not fine.
  # --connectivity-only keeps it at ~0.05s, so it's affordable per repo.
  # Process budget: a `$(...)` that holds a plain command execs it directly (one
  # process), but any redirect INSIDE the substitution forces an extra bash
  # fork, so stderr is silenced by wrapping the assignment in { ...; } 2>/dev/null
  # and the clean case runs fsck with no capture at all (>/dev/null 2>&1); only a
  # failing fsck is re-run to read its messages. -c core.commitGraph=false and
  # core.multiPackIndex=false stop git >= 2.4x spawning `commit-graph verify` and
  # `multi-pack-index verify` children: those check derived caches, not the
  # trees/blobs this probe exists for.
  local fsck_out="" fsck_first="" fsck_n=0
  if ! git -c core.commitGraph=false -c core.multiPackIndex=false -C "$repo_root" \
        fsck --no-progress --connectivity-only >/dev/null 2>&1; then
    fsck_out=$(git -c core.commitGraph=false -c core.multiPackIndex=false -C "$repo_root" \
      fsck --no-progress --connectivity-only 2>&1)
  fi
  while IFS= read -r line; do
    case "$line" in
      [eE][rR][rR][oO][rR]*|[fF][aA][tT][aA][lL]*|[mM][iI][sS][sS][iI][nN][gG]*|[bB][rR][oO][kK][eE][nN]\ [lL][iI][nN][kK]*)
        fsck_n=$(( fsck_n + 1 ))
        [ -z "$fsck_first" ] && fsck_first="$line"
        ;;
    esac
  done <<< "$fsck_out"
  if [ "$fsck_n" -gt 0 ]; then
    _hygiene_emit "PROBLEM:.git integrity — $fsck_n fsck error(s), e.g. $fsck_first. Do NOT run git gc / reflog expire here; decide repair-vs-reclone first"
  fi

  # --- Tier 1: unpushed commits, with age ----------------------------------
  # The state that silently precedes a stuck rebase: committed but never pushed.
  # One for-each-ref gives the branch's upstream track ("[ahead 2, behind 1]");
  # empty means no upstream or nothing ahead, so the usual case is one process.
  local branch="" track ahead oldest log_out days
  line=""
  IFS= read -r line < "$gitdir/HEAD" 2>/dev/null || true
  case "$line" in 'ref: refs/heads/'*) branch="${line#ref: refs/heads/}" ;; esac
  if [ -n "$branch" ]; then
    { track=$(git -C "$repo_root" for-each-ref --format='%(upstream:track)' "refs/heads/$branch"); } 2>/dev/null
    case "$track" in
      *'ahead '*)
        ahead="${track#*ahead }"; ahead="${ahead%%[!0-9]*}"
        case "$ahead" in ''|*[!0-9]*) ahead=0 ;; esac
        if [ "$ahead" -gt 0 ]; then
          { log_out=$(git -C "$repo_root" log --format=%ct '@{upstream}..HEAD'); } 2>/dev/null
          oldest="${log_out##*$'\n'}"
          case "$oldest" in ''|*[!0-9]*) oldest=0 ;; esac
          if [ "$oldest" -gt 0 ]; then
            hygiene_now
            days=$(( (HYGIENE_NOW - oldest) / 86400 ))
            if [ "$days" -ge 3 ]; then
              _hygiene_emit "PROBLEM:$ahead commit(s) unpushed, oldest ${days}d old — push or reconcile before it turns into a rebase conflict"
            fi
          fi
        fi
        ;;
    esac
  fi

  # --- Tier 1: tracked secrets ---------------------------------------------
  # git narrows the listing with (case-insensitive) pathspecs; the exact
  # patterns below then pick the real hits, so a big repo isn't piped through
  # grep. Same patterns as before: .env / .env.<x>, *.pem|p12|pfx, id_rsa & co,
  # minus *.example|sample|template|dist.
  local secrets="" secrets_n=0 cand prev_nocase=0
  { cand=$(git -C "$repo_root" ls-files -- \
    ':(icase)*.env' ':(icase)*.env.*' ':(icase)*.pem' ':(icase)*.p12' ':(icase)*.pfx' \
    ':(icase)*id_rsa' ':(icase)*id_dsa' ':(icase)*id_ecdsa' ':(icase)*id_ed25519'); } 2>/dev/null
  if [ -n "$cand" ]; then
    shopt -q nocasematch && prev_nocase=1
    shopt -s nocasematch
    local re_secret='(^|/)\.env(\.[a-z0-9_-]+)?$|\.(pem|p12|pfx)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$'
    local re_skip='\.(example|sample|template|dist)$|\.env\.example'
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if [[ $f =~ $re_secret ]] && ! [[ $f =~ $re_skip ]]; then
        secrets="$secrets$f "
        secrets_n=$(( secrets_n + 1 ))
        [ "$secrets_n" -ge 5 ] && break
      fi
    done <<< "$cand"
    [ "$prev_nocase" -eq 1 ] || shopt -u nocasematch
  fi
  [ "$secrets_n" -gt 0 ] && _hygiene_emit "PROBLEM:possible secrets tracked in git: $secrets"

  # --- Tier 2: plan docs location ------------------------------------------
  # *.md directly in the repo root and docs/ (dotfiles included).
  local plan_hits=""
  for f in "$repo_root"/*.[mM][dD] "$repo_root"/.[!.]*.[mM][dD] \
           "$repo_root"/docs/*.[mM][dD] "$repo_root"/docs/.[!.]*.[mM][dD]; do
    [ -e "$f" ] || continue
    base="${f##*/}"
    hit=0
    case "$base" in
      *[pP][lL][aA][nN]*) hit=1 ;;
      *)
        if [ -f "$f" ]; then
          i=0
          while [ "$i" -lt 5 ] && { IFS= read -r line || [ -n "$line" ]; }; do
            i=$(( i + 1 ))
            case "$line" in *'**Suggested execution:**'*) hit=1; break ;; esac
          done < "$f"
        fi
        ;;
    esac
    [ "$hit" -eq 1 ] && plan_hits="$plan_hits ${f#"$repo_root"/}"
  done
  [ -n "$plan_hits" ] && _hygiene_emit "PROBLEM:plan-looking doc(s) outside docs/plans/:$plan_hits"

  # --- Tier 2: ADR convention (only if already adopted) --------------------
  local adr_dir=""
  for d in docs/adr docs/decisions adr; do
    [ -d "$repo_root/$d" ] && { adr_dir="$repo_root/$d"; break; }
  done
  if [ -n "$adr_dir" ]; then
    local adr_issues="" ok
    for f in "$adr_dir"/*.[mM][dD] "$adr_dir"/.[!.]*.[mM][dD]; do
      [ -e "$f" ] || continue
      base="${f##*/}"; ok=1
      [[ $base =~ ^[0-9]{4}-.+\.md$ ]] || ok=0
      if [ "$ok" -eq 1 ]; then
        hit=0
        while IFS= read -r line || [ -n "$line" ]; do
          case "$line" in [sS][tT][aA][tT][uU][sS]:*) hit=1; break ;; esac
        done < "$f" 2>/dev/null
        [ "$hit" -eq 1 ] || ok=0
      fi
      [ "$ok" -eq 0 ] && adr_issues="$adr_issues $base"
    done
    [ -n "$adr_issues" ] && _hygiene_emit "PROBLEM:ADR entries not following NNNN-title.md + Status::$adr_issues"
  fi

  # --- Tier 2: expected docs / config --------------------------------------
  for f in PRODUCT.md DESIGN.md CLAUDE.md README.md; do
    if [ -f "$repo_root/$f" ]; then _hygiene_emit "PRESENT:$f"; else _hygiene_emit "MISSING:$f"; fi
  done
  # --- Tier 2: `$impeccable document` has been run (frontend repos only) ------
  # `document` (scan mode) writes DESIGN.md plus the .impeccable/design.json
  # sidecar; seed mode and hand-written files leave no sidecar, so both files
  # together are the evidence it ran. Gated on a UI signal so backend/CLI repos
  # never nag. The label prefix "impeccable document" is matched by the sync hook.
  local ui=0 pkg=""
  if [ -f "$repo_root/index.html" ]; then
    ui=1
  elif [ -f "$repo_root/package.json" ]; then
    IFS= read -r -d '' pkg < "$repo_root/package.json" 2>/dev/null || true
    local re_ui='"(react|vue|next|svelte|solid-js|astro|@angular/core)"'
    [[ $pkg =~ $re_ui ]] && ui=1
  fi
  if [ "$ui" -eq 1 ]; then
    if [ -f "$repo_root/DESIGN.md" ] && [ -f "$repo_root/.impeccable/design.json" ]; then
      _hygiene_emit "PRESENT:impeccable document"
    else
      _hygiene_emit "MISSING:impeccable document"
    fi
  fi
  if [ -f "$repo_root/.superset/config.json" ]; then
    _hygiene_emit "PRESENT:.superset/config.json"
  else
    _hygiene_emit "MISSING:.superset/config.json"
  fi

  # ROADMAP.md, honoring the .noroadmap per-repo opt-out.
  if [ -f "$repo_root/.noroadmap" ]; then
    _hygiene_emit "PRESENT:ROADMAP.md (opted out via .noroadmap)"
  else
    local roadmap=""
    for f in ROADMAP.md docs/ROADMAP.md; do
      [ -f "$repo_root/$f" ] && { roadmap="$repo_root/$f"; break; }
    done
    if [ -z "$roadmap" ]; then
      _hygiene_emit "MISSING:ROADMAP.md"
    else
      # Substantive = not blank, not a heading/comment (#), not a rule
      # (---, ===, ___, ***), not a table row (|), not a bare TBD/TODO/WIP/
      # "Coming soon". Only "fewer than 3" matters, so stop counting at 3.
      local substantive=0
      local re_blank='^[[:space:]]*$|^[[:space:]]*#|^[[:space:]]*[-=_*]{3,}[[:space:]]*$|^[[:space:]]*\|'
      local re_todo='^[[:space:]]*(TBD|TODO|WIP|Coming soon)\.?[[:space:]]*$'
      while IFS= read -r line || [ -n "$line" ]; do
        [[ $line =~ $re_blank ]] && continue
        [[ $line =~ $re_todo ]] && continue
        substantive=$(( substantive + 1 ))
        [ "$substantive" -ge 3 ] && break
      done < "$roadmap" 2>/dev/null
      if [ "$substantive" -lt 3 ]; then
        _hygiene_emit "MISSING:ROADMAP.md (exists but a stub — $substantive substantive lines)"
      else
        _hygiene_emit "PRESENT:ROADMAP.md"
      fi
    fi
  fi
  return 0
}
