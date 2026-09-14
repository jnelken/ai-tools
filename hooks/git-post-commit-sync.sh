#!/usr/bin/env bash
# git post-commit hook for THIS checkout (the dev checkout / source of
# truth, e.g. ~/code/ai-tools) — installed as .git/hooks/post-commit via a
# relative symlink, same pattern as pre-commit -> enforce-claude-symlinks.sh.
#
# Pushes the commit on main, then fast-forwards the deploy clone
# ($AI_TOOLS_HOME, default ~/.ai-tools) from origin and re-links ~/.claude/*
# — the same sync ai-tools-sync.sh does at SessionStart, just triggered
# immediately by the commit instead of waiting for the next session /
# the hourly cooldown. Deploy clone only ever tracks origin (never this
# checkout directly) — that's the deliberate "hosted source" design — so
# this hook pushes first; a commit that isn't on main, or doesn't push
# cleanly, never touches the deploy clone at all.
#
# Never blocks the commit: every exit path is 0.
set -e
trap 'exit 0' EXIT

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit 0
[ "$BRANCH" = "main" ] || exit 0

AI_TOOLS_HOME="${AI_TOOLS_HOME:-$HOME/.ai-tools}"

TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout 10"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout 10"
fi
GIT_NET_OPTS="-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10"

# Push — best effort. Nothing to push, no upstream, diverged history, or
# offline all look the same here: skip the rest silently, don't force
# anything.
# shellcheck disable=SC2086
if ! $TIMEOUT_BIN git $GIT_NET_OPTS -C "$REPO_ROOT" push origin "$BRANCH" -q >/dev/null 2>&1; then
  echo "[ai-tools post-commit] push skipped (nothing to push, offline, or diverged) — deploy clone left as-is" >&2
  exit 0
fi

git -C "$AI_TOOLS_HOME" rev-parse --git-dir >/dev/null 2>&1 || exit 0

if [ -n "$(git -C "$AI_TOOLS_HOME" status --porcelain 2>/dev/null)" ]; then
  echo "[ai-tools post-commit] deploy clone ($AI_TOOLS_HOME) has uncommitted edits — not touching it" >&2
  exit 0
fi

old_head="$(git -C "$AI_TOOLS_HOME" rev-parse --short HEAD 2>/dev/null || true)"
# shellcheck disable=SC2086
$TIMEOUT_BIN git $GIT_NET_OPTS -C "$AI_TOOLS_HOME" fetch -q origin main >/dev/null 2>&1 || exit 0
git -C "$AI_TOOLS_HOME" merge --ff-only -q origin/main >/dev/null 2>&1 || exit 0
new_head="$(git -C "$AI_TOOLS_HOME" rev-parse --short HEAD 2>/dev/null || true)"

if [ -n "$old_head" ] && [ -n "$new_head" ] && [ "$old_head" != "$new_head" ]; then
  if [ -f "$AI_TOOLS_HOME/install.sh" ]; then
    bash "$AI_TOOLS_HOME/install.sh" --no-update --quiet >/dev/null 2>&1 || true
  fi
  echo "[ai-tools post-commit] pushed + deployed ${old_head}..${new_head} — ~/.claude relinked"
fi

exit 0
