#!/usr/bin/env bash
# SessionStart hook: keeps the deploy clone ($AI_TOOLS_HOME, default
# ~/.ai-tools) in sync with origin, nudges if it's been edited in place, and
# nudges if ~/.claude/* links are still pointed at a dev checkout (--dev
# left on) instead of the deploy clone.
#
# Fast, best-effort, NEVER blocks a session: every exit path is 0, guaranteed
# by the EXIT trap below regardless of what fails partway through.
#
# Bash 3.2 / Linux compatible: no mapfile, no associative arrays, no
# ${var,,}, no GNU-only realpath/readlink -f.

set -e
trap 'exit 0' EXIT

AI_TOOLS_HOME="${AI_TOOLS_HOME:-$HOME/.ai-tools}"
AI_TOOLS_REF="${AI_TOOLS_REF:-main}"
CLAUDE_DIR="$HOME/.claude"
STATE_DIR="$HOME/.claude/state"

command -v jq >/dev/null 2>&1 || exit 0

# Nothing to do if there's no deploy clone (or it's not a git repo) yet —
# that's install.sh's job, not this hook's.
git -C "$AI_TOOLS_HOME" rev-parse --git-dir >/dev/null 2>&1 || exit 0

mkdir -p "$STATE_DIR" 2>/dev/null || true

# timeout-guard network calls: prefer GNU `timeout`, fall back to `gtimeout`
# (Homebrew coreutils on macOS). Stock macOS has neither, so the fetch ALSO
# carries git's own low-speed abort (under 1 KB/s for 10s → give up) — a
# stalled network can't hang session start either way.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout 10"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout 10"
fi
GIT_NET_OPTS="-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10"

# resolve_path <path> -> best-effort absolute, symlink-resolved path.
# Portable equivalent of `readlink -f` / `realpath` for bash 3.2 + Linux.
resolve_path() {
  local p="$1" dir base
  if [ -d "$p" ]; then
    (cd -P "$p" 2>/dev/null && pwd -P) || echo "$p"
  else
    dir="$(dirname "$p")"
    base="$(basename "$p")"
    if [ -d "$dir" ]; then
      echo "$(cd -P "$dir" 2>/dev/null && pwd -P)/$base"
    else
      echo "$p"
    fi
  fi
}

MSG=""
append_msg() {
  if [ -n "$MSG" ]; then
    MSG="$MSG

$1"
  else
    MSG="$1"
  fi
}

# cooldown_ok <state_file> <seconds> <hash>
# 24h-style cooldown, keyed by a content hash so a CHANGED situation
# re-nudges immediately even mid-cooldown (same pattern as
# claude-symlink-hygiene.sh).
cooldown_ok() {
  local file="$1" secs="$2" hash="$3" last_hash="" last_time=0 now
  if [ -f "$file" ]; then
    read -r last_hash last_time < "$file" 2>/dev/null || true
  fi
  case "$last_time" in ''|*[!0-9]*) last_time=0 ;; esac
  now=$(date +%s)
  if [ "$hash" = "$last_hash" ] && [ $((now - last_time)) -lt "$secs" ]; then
    return 1
  fi
  return 0
}
record_cooldown() {
  local file="$1" hash="$2"
  printf '%s %s\n' "$hash" "$(date +%s)" > "$file" 2>/dev/null || true
}

# ── 1. dirty deploy clone vs. clean-clone sync (mutually exclusive) ──
DIRTY_STATUS="$(git -C "$AI_TOOLS_HOME" status --porcelain 2>/dev/null || true)"

if [ -n "$DIRTY_STATUS" ]; then
  dirty_state_file="$STATE_DIR/ai-tools-dirty-nudge.txt"
  dirty_hash="$(printf '%s' "$DIRTY_STATUS" | shasum 2>/dev/null | awk '{print $1}')"
  if [ -n "$dirty_hash" ] && cooldown_ok "$dirty_state_file" 86400 "$dirty_hash"; then
    record_cooldown "$dirty_state_file" "$dirty_hash"
    append_msg "⚠️ The ai-tools DEPLOYED copy ($AI_TOOLS_HOME) has uncommitted edits.

That's the deploy clone install.sh manages — it isn't the source of truth and
gets overwritten on the next clean update. Move the edit to ~/code/ai-tools,
commit, and push there instead. Do NOT pull/sync this deploy clone while
it's dirty — that would clobber the local edit."
  fi
else
  sync_state_file="$STATE_DIR/ai-tools-sync.txt"
  last_sync=0
  [ -f "$sync_state_file" ] && last_sync="$(cat "$sync_state_file" 2>/dev/null || echo 0)"
  case "$last_sync" in ''|*[!0-9]*) last_sync=0 ;; esac
  now_epoch=$(date +%s)

  if [ $((now_epoch - last_sync)) -ge 3600 ]; then
    record_cooldown_simple() { printf '%s\n' "$now_epoch" > "$sync_state_file" 2>/dev/null || true; }
    record_cooldown_simple

    old_head="$(git -C "$AI_TOOLS_HOME" rev-parse --short HEAD 2>/dev/null || true)"

    fetch_ok=1
    # shellcheck disable=SC2086  # $TIMEOUT_BIN is intentionally word-split (or empty)
    $TIMEOUT_BIN git $GIT_NET_OPTS -C "$AI_TOOLS_HOME" fetch -q origin "$AI_TOOLS_REF" >/dev/null 2>&1 || fetch_ok=0

    if [ "$fetch_ok" -eq 1 ]; then
      merge_ok=1
      # shellcheck disable=SC2086
      $TIMEOUT_BIN git -C "$AI_TOOLS_HOME" merge --ff-only -q "origin/$AI_TOOLS_REF" >/dev/null 2>&1 || merge_ok=0

      if [ "$merge_ok" -eq 1 ]; then
        new_head="$(git -C "$AI_TOOLS_HOME" rev-parse --short HEAD 2>/dev/null || true)"
        if [ -n "$old_head" ] && [ -n "$new_head" ] && [ "$old_head" != "$new_head" ]; then
          n_files="$(git -C "$AI_TOOLS_HOME" diff --name-only "$old_head" "$new_head" 2>/dev/null | wc -l | tr -d ' ')"
          if [ -f "$AI_TOOLS_HOME/install.sh" ]; then
            bash "$AI_TOOLS_HOME/install.sh" --no-update --quiet >/dev/null 2>&1 || true
          fi
          append_msg "ai-tools deployed: ${old_head}..${new_head}, ${n_files} file(s) changed. Re-linked ~/.claude/* from the updated deploy clone."
        fi
      else
        append_msg "⚠️ ai-tools deploy clone ($AI_TOOLS_HOME) could not fast-forward to origin/$AI_TOOLS_REF (local history diverged?). Left as-is — investigate manually."
      fi
    fi
    # fetch failure (offline, etc.) is silent — never worth nudging about.
  fi
fi

# ── 2. links that don't point into the deploy clone ──
# Every ~/.claude/* link install.sh manages should resolve inside
# $AI_TOOLS_HOME. Anything else falls in one of three buckets:
#   dev    — resolves into some OTHER ai-tools checkout (a prior
#            `install.sh --dev` left on). Detected by the checkout's SHAPE —
#            a parent dir holding install.sh + skills/ — not by path, because
#            a Superset worktree of the repo lives under ~/code/worktrees/…
#            and never has "ai-tools" in its path.
#   stale  — dangling, and the target was ai-tools content (under
#            $AI_TOOLS_HOME or a path containing "ai-tools"). Left behind by a
#            rename/removal upstream; install.sh prunes these.
#   other  — third-party (e.g. ~/.agents/skills/*) or unrelated. Ignored.
resolved_home="$(resolve_path "$AI_TOOLS_HOME")"

# is_ai_tools_checkout <dir>: does <dir> look like a checkout of this repo?
is_ai_tools_checkout() {
  [ -f "$1/install.sh" ] && [ -d "$1/skills" ] && [ -d "$1/hooks" ]
}
# checkout_root_of <path>: walk up at most 4 parents looking for a checkout.
checkout_root_of() {
  local d="$1" i=0
  [ -d "$d" ] || d="$(dirname "$d")"
  while [ "$i" -lt 4 ] && [ -n "$d" ] && [ "$d" != "/" ]; do
    if is_ai_tools_checkout "$d"; then echo "$d"; return 0; fi
    d="$(dirname "$d")"; i=$((i + 1))
  done
  return 1
}

dev_offenders=""
stale_offenders=""
# classify_link <link>
classify_link() {
  local link="$1" target abs_target resolved_target root
  [ -L "$link" ] || return 0
  target="$(readlink "$link")"
  case "$target" in
    /*) abs_target="$target" ;;
    *) abs_target="$(dirname "$link")/$target" ;;   # relative targets resolve from the link's dir
  esac
  # Dangling first: a broken link INTO the deploy clone is stale, not fine.
  if [ ! -e "$abs_target" ]; then
    case "$target" in
      "$AI_TOOLS_HOME"/*|*ai-tools*)
        stale_offenders="${stale_offenders}${stale_offenders:+$'\n'}$link -> $target" ;;
    esac
    return 0
  fi
  resolved_target="$(resolve_path "$abs_target")"
  case "$resolved_target" in
    "$resolved_home"|"$resolved_home"/*) return 0 ;;  # points into the deploy clone — fine
  esac
  if root="$(checkout_root_of "$resolved_target")"; then
    dev_offenders="${dev_offenders}${dev_offenders:+$'\n'}$link -> $root"
  fi
}

for sub in skills commands hooks agents automations; do
  d="$CLAUDE_DIR/$sub"
  [ -d "$d" ] || continue
  while IFS= read -r link; do
    classify_link "$link"
  done < <(find "$d" -maxdepth 2 -type l 2>/dev/null)
done
classify_link "$CLAUDE_DIR/awesome-statusline.sh"

if [ -n "$dev_offenders" ]; then
  dev_state_file="$STATE_DIR/ai-tools-devmode-nudge.txt"
  dev_hash="$(printf '%s' "$dev_offenders" | shasum 2>/dev/null | awk '{print $1}')"
  if [ -n "$dev_hash" ] && cooldown_ok "$dev_state_file" 86400 "$dev_hash"; then
    record_cooldown "$dev_state_file" "$dev_hash"
    dev_count="$(printf '%s\n' "$dev_offenders" | wc -l | tr -d ' ')"
    dev_roots="$(printf '%s\n' "$dev_offenders" | awk -F' -> ' '{print $2}' | sort -u)"
    append_msg "⚠️ $dev_count ~/.claude/* link(s) point at a DEV checkout of ai-tools, not the deployed copy ($AI_TOOLS_HOME) — \`install.sh --dev\` was left on. Checkout(s):

$dev_roots

Run \`~/.ai-tools/install.sh\` (no flags) to flip them back to the deploy clone."
  fi
fi

if [ -n "$stale_offenders" ]; then
  stale_state_file="$STATE_DIR/ai-tools-stale-nudge.txt"
  stale_hash="$(printf '%s' "$stale_offenders" | shasum 2>/dev/null | awk '{print $1}')"
  if [ -n "$stale_hash" ] && cooldown_ok "$stale_state_file" 86400 "$stale_hash"; then
    record_cooldown "$stale_state_file" "$stale_hash"
    append_msg "⚠️ Some ~/.claude/* links are dangling — their ai-tools target no longer exists (renamed or removed upstream):

$stale_offenders

Run \`~/.ai-tools/install.sh\` to prune them."
  fi
fi

if [ -n "$MSG" ]; then
  jq -n --arg msg "$MSG" '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: $msg
    }
  }'
fi

exit 0
