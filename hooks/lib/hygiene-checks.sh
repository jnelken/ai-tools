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

# Is this a repo the hygiene system governs? Personal (jnelken) or no remote
# at all (local-only scratch). Never Concentro-Inc or another org.
hygiene_is_personal_repo() {
  local repo_root="$1"
  local personal_owners="${PERSONAL_REPO_OWNERS:-jnelken}"
  local origin_url
  origin_url=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
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

hygiene_scan_repo() {
  local repo_root="$1"
  local gitdir f d

  gitdir=$(git -C "$repo_root" rev-parse --git-dir 2>/dev/null) || {
    echo "PROBLEM:not a git repo (or .git unreadable)"
    return 0
  }
  case "$gitdir" in /*) ;; *) gitdir="$repo_root/$gitdir" ;; esac

  # --- Tier 1: git state ---------------------------------------------------
  # Reuses the guard shared with the dotfiles auto-sync hook.
  local guard="$HOME/dotfiles/bin/git-safe-to-autocommit" why
  if [ -x "$guard" ]; then
    why=$("$guard" "$repo_root" 2>&1) || echo "PROBLEM:git state — $why"
  fi

  # --- Tier 1: .git integrity ----------------------------------------------
  # Dropbox-synced .git dirs do get corrupted (snap-dash lost trees and blobs
  # this way). Must be a real fsck: on snap-dash, HEAD still resolves and
  # `git status` still succeeds while trees are demonstrably broken, so the
  # cheap probes report a clean bill of health on a repo that is not fine.
  # --connectivity-only keeps it at ~0.05s, so it's affordable per repo.
  local fsck_err
  fsck_err=$(git -C "$repo_root" fsck --no-progress --connectivity-only 2>&1 \
    | grep -iE '^(error|fatal|missing|broken link)' | head -3)
  if [ -n "$fsck_err" ]; then
    local n
    n=$(git -C "$repo_root" fsck --no-progress --connectivity-only 2>&1 \
      | grep -ciE '^(error|fatal|missing|broken link)')
    echo "PROBLEM:.git integrity — $n fsck error(s), e.g. $(printf '%s' "$fsck_err" | head -1). Do NOT run git gc / reflog expire here; decide repair-vs-reclone first"
  fi

  # --- Tier 1: unpushed commits, with age ----------------------------------
  # The state that silently precedes a stuck rebase: committed but never pushed.
  if git -C "$repo_root" rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
    local ahead oldest now days
    ahead=$(git -C "$repo_root" rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)
    case "$ahead" in ''|*[!0-9]*) ahead=0 ;; esac
    if [ "$ahead" -gt 0 ]; then
      oldest=$(git -C "$repo_root" log --format=%ct '@{upstream}..HEAD' 2>/dev/null | tail -1)
      case "$oldest" in ''|*[!0-9]*) oldest=0 ;; esac
      if [ "$oldest" -gt 0 ]; then
        now=$(date +%s); days=$(( (now - oldest) / 86400 ))
        if [ "$days" -ge 3 ]; then
          echo "PROBLEM:$ahead commit(s) unpushed, oldest ${days}d old — push or reconcile before it turns into a rebase conflict"
        fi
      fi
    fi
  fi

  # --- Tier 1: tracked secrets ---------------------------------------------
  local secrets
  secrets=$(git -C "$repo_root" ls-files 2>/dev/null \
    | grep -iE '(^|/)\.env(\.[a-z0-9_-]+)?$|\.(pem|p12|pfx)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$' \
    | grep -viE '\.(example|sample|template|dist)$|\.env\.example' | head -5)
  if [ -n "$secrets" ]; then
    echo "PROBLEM:possible secrets tracked in git: $(echo "$secrets" | tr '\n' ' ')"
  fi

  # --- Tier 2: plan docs location ------------------------------------------
  local plan_hits=""
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    local base; base=$(basename "$f")
    if printf '%s' "$base" | grep -qiE 'plan'; then
      plan_hits="$plan_hits ${f#"$repo_root"/}"
    elif head -5 "$f" 2>/dev/null | grep -q '\*\*Suggested execution:\*\*'; then
      plan_hits="$plan_hits ${f#"$repo_root"/}"
    fi
  done < <(find "$repo_root" "$repo_root/docs" -maxdepth 1 -iname '*.md' 2>/dev/null)
  [ -n "$plan_hits" ] && echo "PROBLEM:plan-looking doc(s) outside docs/plans/:$plan_hits"

  # --- Tier 2: ADR convention (only if already adopted) --------------------
  local adr_dir=""
  for d in docs/adr docs/decisions adr; do
    [ -d "$repo_root/$d" ] && { adr_dir="$repo_root/$d"; break; }
  done
  if [ -n "$adr_dir" ]; then
    local adr_issues=""
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      local base ok; base=$(basename "$f"); ok=1
      printf '%s' "$base" | grep -qE '^[0-9]{4}-.+\.md$' || ok=0
      grep -qiE '^status:' "$f" 2>/dev/null || ok=0
      [ "$ok" -eq 0 ] && adr_issues="$adr_issues $base"
    done < <(find "$adr_dir" -maxdepth 1 -iname '*.md' 2>/dev/null)
    [ -n "$adr_issues" ] && echo "PROBLEM:ADR entries not following NNNN-title.md + Status::$adr_issues"
  fi

  # --- Tier 2: expected docs / config --------------------------------------
  for f in PRODUCT.md DESIGN.md CLAUDE.md README.md; do
    if [ -f "$repo_root/$f" ]; then echo "PRESENT:$f"; else echo "MISSING:$f"; fi
  done
  if [ -f "$repo_root/.superset/config.json" ]; then
    echo "PRESENT:.superset/config.json"
  else
    echo "MISSING:.superset/config.json"
  fi

  # ROADMAP.md, honoring the .noroadmap per-repo opt-out.
  if [ -f "$repo_root/.noroadmap" ]; then
    echo "PRESENT:ROADMAP.md (opted out via .noroadmap)"
  else
    local roadmap=""
    for f in ROADMAP.md docs/ROADMAP.md; do
      [ -f "$repo_root/$f" ] && { roadmap="$repo_root/$f"; break; }
    done
    if [ -z "$roadmap" ]; then
      echo "MISSING:ROADMAP.md"
    else
      local substantive
      substantive=$(grep -vE '^\s*$|^\s*#|^\s*[-=_*]{3,}\s*$|^\s*\|' "$roadmap" 2>/dev/null \
        | grep -vE '^\s*(TBD|TODO|WIP|Coming soon)\.?\s*$' | wc -l | tr -d ' ')
      case "$substantive" in ''|*[!0-9]*) substantive=0 ;; esac
      if [ "$substantive" -lt 3 ]; then
        echo "MISSING:ROADMAP.md (exists but a stub — $substantive substantive lines)"
      else
        echo "PRESENT:ROADMAP.md"
      fi
    fi
  fi
}
