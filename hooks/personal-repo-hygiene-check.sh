#!/usr/bin/env bash
# SessionStart hook: nudge repo hygiene conventions when a session opens
# inside a git repo. Four independent, best-effort checks:
#   1. plan docs live under docs/plans/ (or docs/plans/archive/)
#   2. if an ADR dir already exists, its entries follow the convention
#   3. PRODUCT.md + DESIGN.md both missing -> suggest /impeccable
#   4. .superset/config.json missing -> suggest /superset-config
#
# Never blocks a session: every failure mode is a silent no-op.
#
# State: ~/.claude/state/hygiene-nudges/<repo-hash>.json holds
# last_nudged_at_epoch, so a repo you open ten times a day only gets
# nudged once per HYGIENE_NUDGE_COOLDOWN_HOURS (default 24).

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat 2>/dev/null || true)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
cwd="${cwd:-$PWD}"

repo_root=$(cd "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)
[ -z "$repo_root" ] && exit 0

# Personal-repo gate: only run inside repos owned by an allowed GitHub
# owner (default: jnelken) — never Concentro-Inc or any other org. A repo
# with no configured origin remote is treated as personal (local-only
# scratch work, not a cloned company repo).
personal_owners="${PERSONAL_REPO_OWNERS:-jnelken}"
origin_url=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
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

command -v shasum >/dev/null 2>&1 || exit 0
repo_hash=$(printf '%s' "$repo_root" | shasum -a 256 | cut -c1-16)

state_dir="$HOME/.claude/state/hygiene-nudges"
mkdir -p "$state_dir" 2>/dev/null || exit 0
state_file="$state_dir/$repo_hash.json"

cooldown_hours="${HYGIENE_NUDGE_COOLDOWN_HOURS:-24}"
cooldown_seconds=$(( cooldown_hours * 3600 ))
now_epoch=$(date +%s)

if [ -f "$state_file" ]; then
  last_epoch=$(jq -r '.last_nudged_at_epoch // 0' "$state_file" 2>/dev/null || echo 0)
  case "$last_epoch" in ''|*[!0-9]*) last_epoch=0 ;; esac
  if [ "$now_epoch" -lt "$(( last_epoch + cooldown_seconds ))" ]; then
    exit 0
  fi
fi

notes=()

# --- 1. plan docs location -------------------------------------------------
plan_hits=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  base=$(basename "$f")
  if printf '%s' "$base" | grep -qiE 'plan'; then
    plan_hits+=("${f#"$repo_root"/}")
  elif head -5 "$f" 2>/dev/null | grep -q '\*\*Suggested execution:\*\*'; then
    plan_hits+=("${f#"$repo_root"/}")
  fi
done < <(find "$repo_root" "$repo_root/docs" -maxdepth 1 -iname '*.md' 2>/dev/null)

if [ "${#plan_hits[@]}" -gt 0 ]; then
  joined=$(IFS=', '; echo "${plan_hits[*]}")
  notes+=("Plan-looking doc(s) outside docs/plans/: $joined. Consider moving them to docs/plans/ (or docs/plans/archive/ if superseded).")
fi

# --- 2. ADR practice (only if already adopted) ------------------------------
adr_dir=""
for d in docs/adr docs/decisions adr; do
  if [ -d "$repo_root/$d" ]; then
    adr_dir="$repo_root/$d"
    break
  fi
done

if [ -n "$adr_dir" ]; then
  adr_issues=()
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    base=$(basename "$f")
    ok=1
    printf '%s' "$base" | grep -qE '^[0-9]{4}-.+\.md$' || ok=0
    grep -qiE '^status:' "$f" 2>/dev/null || ok=0
    [ "$ok" -eq 0 ] && adr_issues+=("$base")
  done < <(find "$adr_dir" -maxdepth 1 -iname '*.md' 2>/dev/null)

  if [ "${#adr_issues[@]}" -gt 0 ]; then
    joined=$(IFS=', '; echo "${adr_issues[*]}")
    notes+=("ADR dir ${adr_dir#"$repo_root"/} has entries not following NNNN-title.md + a Status: field: $joined.")
  fi
fi

# --- 3. PRODUCT.md + DESIGN.md ---------------------------------------------
if [ ! -f "$repo_root/PRODUCT.md" ] && [ ! -f "$repo_root/DESIGN.md" ]; then
  notes+=("No PRODUCT.md or DESIGN.md found. Consider running /impeccable to establish product/design docs.")
fi

# --- 4. superset-config -------------------------------------------------
if [ ! -f "$repo_root/.superset/config.json" ]; then
  notes+=("No .superset/config.json found. Consider running /superset-config to set up setup/run/teardown scripts.")
fi

jq -n --arg t "$now_epoch" '{"last_nudged_at_epoch": ($t | tonumber)}' > "$state_file" 2>/dev/null || true

[ "${#notes[@]}" -eq 0 ] && exit 0

message="Repo hygiene check ($repo_root):"
for n in "${notes[@]}"; do
  message="$message
- $n"
done

jq -n --arg ctx "$message" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$ctx}}'
