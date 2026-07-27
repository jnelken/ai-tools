#!/usr/bin/env bash
# Symlink skills, slash commands, and hooks from this repo into ~/.claude/.
# Refuses to clobber existing non-symlink files/dirs — manually merge if needed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

# link_one <src> <dest>
# Creates a symlink dest → src, with safety:
#   - if dest is already the right symlink, no-op
#   - if dest is a different symlink, replace it
#   - if dest is a real file/dir, refuse + print manual override instructions
link_one() {
  local src=$1 dest=$2 kind=$3

  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$src" ]; then
      echo "  ✓ $(basename "$dest") (already linked)"
      return
    fi
    echo "  ↻ $(basename "$dest") (replacing symlink)"
    rm "$dest"
  elif [ -e "$dest" ]; then
    echo "  ⚠ $(basename "$dest") — existing $kind, NOT symlinked."
    echo "     To replace with this repo's version:"
    echo "       rm -rf \"$dest\" && ln -s \"$src\" \"$dest\""
    return
  fi

  ln -s "$src" "$dest"
  echo "  → $(basename "$dest") linked"
}

# ── skills/ ──
if [ -d "$REPO_ROOT/skills" ]; then
  mkdir -p "$CLAUDE_DIR/skills"
  echo "── skills ──"
  for skill in "$REPO_ROOT/skills"/*/; do
    name="$(basename "${skill%/}")"
    link_one "$REPO_ROOT/skills/$name" "$CLAUDE_DIR/skills/$name" "skill directory"
  done
fi

# ── commands/ (slash commands) ──
if [ -d "$REPO_ROOT/commands" ]; then
  mkdir -p "$CLAUDE_DIR/commands"
  echo ""
  echo "── commands ──"
  for cmd in "$REPO_ROOT/commands"/*.md; do
    [ -e "$cmd" ] || continue
    name="$(basename "$cmd")"
    link_one "$REPO_ROOT/commands/$name" "$CLAUDE_DIR/commands/$name" "command file"
  done
fi

# ── hooks/ ──
if [ -d "$REPO_ROOT/hooks" ]; then
  mkdir -p "$CLAUDE_DIR/hooks"
  echo ""
  echo "── hooks ──"
  for hook in "$REPO_ROOT/hooks"/*; do
    [ -e "$hook" ] || continue
    name="$(basename "$hook")"
    # Skip documentation files — only symlink executables/scripts.
    case "$name" in
      README*|*.md) continue ;;
    esac
    link_one "$REPO_ROOT/hooks/$name" "$CLAUDE_DIR/hooks/$name" "hook file"
  done
  echo ""
  echo "  Note: hooks are wired up via ~/.claude/settings.json — symlinking the script"
  echo "        alone doesn't enable execution. See hooks/README.md for required config."
fi

# ── automations/ ──
if [ -d "$REPO_ROOT/automations" ]; then
  echo ""
  echo "── automations ──"
  for auto in "$REPO_ROOT/automations"/*/; do
    [ -d "$auto" ] || continue
    name="$(basename "${auto%/}")"
    mkdir -p "$CLAUDE_DIR/automations/$name"
    for script in "$auto"*.sh "$auto"*.py; do
      [ -e "$script" ] || continue
      link_one "$script" "$CLAUDE_DIR/automations/$name/$(basename "$script")" "automation script"
    done
  done
  echo ""
  echo "  Note: scheduling (launchd plists) isn't symlinked and doesn't auto-install —"
  echo "        see automations/README.md to wire each automation up on a new machine."
fi

# ── statusline/ ──
if [ -d "$REPO_ROOT/statusline" ]; then
  echo ""
  echo "── statusline ──"

  # jq is required to parse the statusline JSON on stdin.
  if ! command -v jq >/dev/null 2>&1; then
    echo "  jq not found — attempting automatic install…"
    if   command -v brew    >/dev/null 2>&1; then brew install jq
    elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y jq
    elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y jq
    elif command -v yum     >/dev/null 2>&1; then sudo yum install -y jq
    elif command -v pacman  >/dev/null 2>&1; then sudo pacman -S --noconfirm jq
    elif command -v zypper  >/dev/null 2>&1; then sudo zypper install -y jq
    elif command -v apk     >/dev/null 2>&1; then sudo apk add jq
    else
      echo "  ⚠ No supported package manager found — install jq manually: https://jqlang.github.io/jq/download/"
    fi
  fi

  link_one "$REPO_ROOT/statusline/awesome-statusline.sh" "$CLAUDE_DIR/awesome-statusline.sh" "statusline script"

  if command -v jq >/dev/null 2>&1; then
    SETTINGS="$CLAUDE_DIR/settings.json"
    STATUSLINE_JSON='{"type":"command","command":"bash ~/.claude/awesome-statusline.sh"}'
    mkdir -p "$CLAUDE_DIR"
    if [ -f "$SETTINGS" ]; then
      BACKUP="$SETTINGS.backup-$(date +%Y%m%d-%H%M%S)"
      cp "$SETTINGS" "$BACKUP"
      jq --argjson sl "$STATUSLINE_JSON" '.statusLine = $sl' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
      echo "  settings.json: statusLine set (backup: $(basename "$BACKUP"))"
    else
      jq -n --argjson sl "$STATUSLINE_JSON" '{statusLine: $sl}' > "$SETTINGS"
      echo "  settings.json: created with statusLine set"
    fi
  else
    echo "  ⚠ jq unavailable — skipped settings.json statusLine wiring. Set it manually:"
    echo "     statusLine.command = \"bash ~/.claude/awesome-statusline.sh\""
  fi
fi

echo ""
echo "Done."
