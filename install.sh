#!/usr/bin/env bash
# Bootstrap ai-tools into ~/.claude/.
#
# Deploy model: the dev repo (~/code/ai-tools) is for development only. What's
# on origin/main is what actually gets deployed — identically on every
# machine and every cloud environment. This script maintains a DEPLOY CLONE
# at $AI_TOOLS_HOME (default ~/.ai-tools) that tracks origin/main, and
# symlinks ~/.claude/* into THAT clone, never into ~/code/ai-tools.
#
# Works three ways:
#   1. Piped, no checkout at all:
#        curl -fsSL https://raw.githubusercontent.com/jnelken/ai-tools/main/install.sh | bash
#   2. Run from the deploy clone itself (~/.ai-tools/install.sh).
#   3. Run from the dev checkout (~/code/ai-tools/install.sh) — default
#      behavior is STILL the hosted install (update+link from AI_TOOLS_HOME).
#      Pass --dev to link from the checkout instead, for testing an unpushed
#      change; a plain run afterward flips the links back.
#
# GOTCHA: the symlink is on the SKILL/HOOK DIRECTORY or FILE itself, not
# individual files inside it (e.g. ~/.claude/skills/<name> -> the deploy
# clone's skills/<name>). Any rm/ln/cp against a path *inside* an installed
# skill (~/.claude/skills/<name>/foo) resolves straight through that symlink
# to the real file. Never `rm` + `ln -s` a file at such a path expecting to
# "add" a symlink — it deletes the real file and replaces it with a symlink
# pointing at its own path. If a per-file symlink is ever truly wanted,
# target the resolved source path directly, never the ~/.claude/... path.
#
# Bash 3.2 compatible (macOS default bash) and Linux compatible: no mapfile,
# no associative arrays, no ${var,,}, no GNU-only `realpath`/`readlink -f`.
set -euo pipefail

# ── defaults (all overridable from the environment) ──
AI_TOOLS_HOME="${AI_TOOLS_HOME:-$HOME/.ai-tools}"
AI_TOOLS_REPO="${AI_TOOLS_REPO:-https://github.com/jnelken/ai-tools.git}"
AI_TOOLS_REF="${AI_TOOLS_REF:-main}"
# Strip trailing slashes so link targets compare byte-for-byte on re-runs
# (link_one compares `readlink` output to $src as plain strings).
while [ "${AI_TOOLS_HOME%/}" != "$AI_TOOLS_HOME" ] && [ "$AI_TOOLS_HOME" != "/" ]; do
  AI_TOOLS_HOME="${AI_TOOLS_HOME%/}"
done

CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"
SETTINGS_BACKED_UP=0

DEV_MODE=0
NO_UPDATE=0
QUIET=0

usage() {
  cat <<EOF
Usage: install.sh [--dev] [--no-update] [--quiet] [-h|--help]

Symlinks ai-tools content (skills, commands, agents, hooks, automations,
statusline) into ~/.claude/, sourced from a deploy clone that tracks
origin/main — never from an uncommitted dev checkout, unless --dev is given.

Invocation modes:
  curl -fsSL https://raw.githubusercontent.com/jnelken/ai-tools/main/install.sh | bash
      Piped from GitHub, no local checkout needed. Clones/updates
      \$AI_TOOLS_HOME, then links from it.

  ~/.ai-tools/install.sh
      Run from the deploy clone itself. Same behavior as piped.

  ~/code/ai-tools/install.sh
      Run from the dev checkout. Default behavior is STILL the hosted
      install (update + link from \$AI_TOOLS_HOME) — the dev checkout is
      NOT linked unless --dev is passed.

Flags:
  --dev         Link from THIS checkout instead of \$AI_TOOLS_HOME. Prints a
                loud warning — this is a temporary test mode. Run
                ./install.sh (no flags) afterward to flip back to the
                deployed copy. Errors out if used when piped (no checkout to
                link from).
  --no-update   Skip the git fetch/merge of \$AI_TOOLS_HOME; just (re)link
                from whatever is already there. Implied by --dev.
  --quiet       Only print changes and warnings — suppress "already linked"
                lines and section headers that have nothing to report.
  -h, --help    Show this help and exit.

Environment variables:
  AI_TOOLS_HOME   Deploy clone location (default: \$HOME/.ai-tools)
  AI_TOOLS_REPO   Git remote to clone/update from
                  (default: https://github.com/jnelken/ai-tools.git)
  AI_TOOLS_REF    Branch/ref to track (default: main)

Dev workflow: edit in ~/code/ai-tools, commit, push — that IS the deploy
step. Then run ~/.ai-tools/install.sh (or wait for the next session's
ai-tools-sync SessionStart hook) to pick it up on a given machine.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV_MODE=1 ;;
    --no-update) NO_UPDATE=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

# ── resolve SCRIPT_DIR only if BASH_SOURCE[0] is a readable file ──
# (empty/unset when bash is reading a piped script from stdin)
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && [ -r "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
fi

if [ "$DEV_MODE" -eq 1 ]; then
  if [ -z "$SCRIPT_DIR" ]; then
    echo "Error: --dev requires running install.sh from a checkout (e.g. ~/code/ai-tools/install.sh)." >&2
    echo "There is no checkout to link from when the script is piped via stdin." >&2
    exit 1
  fi
  SRC_ROOT="$SCRIPT_DIR"
  NO_UPDATE=1
  echo "⚠ --dev: linking ~/.claude/* from $SRC_ROOT (an uncommitted checkout may be live)."
  echo "  This is a temporary test mode. Run ./install.sh (no flags) to flip back to the deployed copy."
else
  SRC_ROOT="$AI_TOOLS_HOME"
fi

# link_one <src> <dest> <kind>
# Creates a symlink dest -> src, with safety:
#   - if dest is already the right symlink, no-op (quiet-suppressible)
#   - if dest is a different symlink, replace it
#   - if dest is a real file/dir, refuse + print manual override instructions
link_one() {
  local src=$1 dest=$2 kind=$3

  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$src" ]; then
      if [ "$QUIET" -eq 0 ]; then echo "  ✓ $(basename "$dest") (already linked)"; fi
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

# backup_settings_once: back up settings.json exactly once per run, before
# the first write to it — regardless of how many sections end up writing.
backup_settings_once() {
  if [ "$SETTINGS_BACKED_UP" -eq 0 ] && [ -f "$SETTINGS" ]; then
    local backup
    backup="$SETTINGS.backup-$(date +%Y%m%d-%H%M%S)"
    cp "$SETTINGS" "$backup"
    echo "  settings.json backup: $(basename "$backup")"
  fi
  SETTINGS_BACKED_UP=1
}

# run_section <header> <fn>
# Runs section function $fn. Under --quiet, buffers its output and only
# prints the header + content if the function actually produced output
# (link_one and section functions suppress purely-informational/no-op lines
# under QUIET, so a section with nothing to report stays fully silent).
run_section() {
  local header="$1" fn="$2"
  if [ "$QUIET" -eq 0 ]; then
    echo ""
    echo "$header"
    "$fn"
  else
    local tmp
    tmp="$(mktemp)"
    "$fn" > "$tmp" 2>&1
    if [ -s "$tmp" ]; then
      echo ""
      echo "$header"
      cat "$tmp"
    fi
    rm -f "$tmp"
  fi
}

# ensure_jq: jq is needed for every settings.json write (statusLine, the
# ai-tools-sync wiring) and for the statusline itself. Try to install it, but
# NEVER let that abort the installer: package managers may need a sudo
# password nobody can type (cloud containers, `curl | bash` where stdin IS the
# script), so every attempt is non-interactive (sudo -n, stdin from /dev/null)
# and failure just falls through to a loud warning.
ensure_jq() {
  command -v jq >/dev/null 2>&1 && return 0
  echo "jq not found — attempting automatic install..."
  if   command -v brew    >/dev/null 2>&1; then brew install jq </dev/null || true
  elif command -v apt-get >/dev/null 2>&1; then { sudo -n apt-get update && sudo -n apt-get install -y jq; } </dev/null || true
  elif command -v dnf     >/dev/null 2>&1; then sudo -n dnf install -y jq </dev/null || true
  elif command -v yum     >/dev/null 2>&1; then sudo -n yum install -y jq </dev/null || true
  elif command -v pacman  >/dev/null 2>&1; then sudo -n pacman -S --noconfirm jq </dev/null || true
  elif command -v zypper  >/dev/null 2>&1; then sudo -n zypper install -y jq </dev/null || true
  elif command -v apk     >/dev/null 2>&1; then sudo -n apk add jq </dev/null || true
  fi
  if command -v jq >/dev/null 2>&1; then
    echo "  jq installed."
    return 0
  fi
  echo "⚠ jq is NOT available. Symlinks will still be created, but every settings.json"
  echo "  write is skipped — including the ai-tools-sync SessionStart hook, so this"
  echo "  machine will NOT auto-update from origin until jq is installed and install.sh"
  echo "  is re-run. Install: https://jqlang.github.io/jq/download/"
  return 0
}

# ── update step: sync $AI_TOOLS_HOME from origin (skipped w/ --no-update or --dev) ──
update_ai_tools_home() {
  if [ ! -e "$AI_TOOLS_HOME" ] || { [ -d "$AI_TOOLS_HOME" ] && [ -z "$(ls -A "$AI_TOOLS_HOME" 2>/dev/null)" ]; }; then
    echo "Cloning $AI_TOOLS_REPO ($AI_TOOLS_REF) into ${AI_TOOLS_HOME}..."
    git clone --branch "$AI_TOOLS_REF" "$AI_TOOLS_REPO" "$AI_TOOLS_HOME"
    return
  fi

  if [ -d "$AI_TOOLS_HOME/.git" ]; then
    if [ -n "$(git -C "$AI_TOOLS_HOME" status --porcelain 2>/dev/null)" ]; then
      echo "⚠ $AI_TOOLS_HOME (the deployed copy) has uncommitted edits — skipping update."
      echo "  Edits belong in ~/code/ai-tools (commit + push there); this deploy clone is"
      echo "  managed by install.sh and gets overwritten on the next clean update."
      return
    fi
    local before_rev after_rev
    before_rev="$(git -C "$AI_TOOLS_HOME" rev-parse HEAD 2>/dev/null || true)"
    if git -C "$AI_TOOLS_HOME" fetch -q origin "$AI_TOOLS_REF" \
      && git -C "$AI_TOOLS_HOME" merge --ff-only -q "origin/$AI_TOOLS_REF"; then
      after_rev="$(git -C "$AI_TOOLS_HOME" rev-parse HEAD 2>/dev/null || true)"
      if [ "$before_rev" != "$after_rev" ]; then
        echo "Updated $AI_TOOLS_HOME to latest $AI_TOOLS_REF."
      elif [ "$QUIET" -eq 0 ]; then
        echo "$AI_TOOLS_HOME already up to date with $AI_TOOLS_REF."
      fi
    else
      echo "⚠ Could not fast-forward $AI_TOOLS_HOME (fetch/merge failed) — continuing with what's there."
    fi
    return
  fi

  echo "Error: $AI_TOOLS_HOME exists, is non-empty, and is not a git repo." >&2
  echo "Refusing to touch it automatically. Move it aside and re-run, e.g.:" >&2
  echo "  mv \"$AI_TOOLS_HOME\" \"$AI_TOOLS_HOME.bak\"" >&2
  exit 1
}

if [ "$DEV_MODE" -eq 0 ] && [ "$NO_UPDATE" -eq 0 ]; then
  update_ai_tools_home
fi

if [ ! -d "$SRC_ROOT" ]; then
  echo "Error: source directory $SRC_ROOT does not exist — nothing to link." >&2
  exit 1
fi
# Physical path, so links are stable across re-runs regardless of how
# AI_TOOLS_HOME was spelled (symlinked parent dirs, ./, trailing slashes).
SRC_ROOT="$(cd -P "$SRC_ROOT" && pwd -P)"

ensure_jq

if [ "$QUIET" -eq 0 ]; then
  echo ""
  echo "Linking ~/.claude/* from: $SRC_ROOT"
fi

# ── skills/ ──
section_skills() {
  [ -d "$SRC_ROOT/skills" ] || return 0
  mkdir -p "$CLAUDE_DIR/skills"
  local skill name
  for skill in "$SRC_ROOT/skills"/*/; do
    [ -d "$skill" ] || continue
    name="$(basename "${skill%/}")"
    link_one "$SRC_ROOT/skills/$name" "$CLAUDE_DIR/skills/$name" "skill directory"
  done
}

# ── commands/ (slash commands) ──
section_commands() {
  [ -d "$SRC_ROOT/commands" ] || return 0
  mkdir -p "$CLAUDE_DIR/commands"
  local cmd name
  for cmd in "$SRC_ROOT/commands"/*.md; do
    [ -e "$cmd" ] || continue
    name="$(basename "$cmd")"
    link_one "$SRC_ROOT/commands/$name" "$CLAUDE_DIR/commands/$name" "command file"
  done
}

# ── agents/ ──
section_agents() {
  [ -d "$SRC_ROOT/agents" ] || return 0
  mkdir -p "$CLAUDE_DIR/agents"
  local agent name
  for agent in "$SRC_ROOT/agents"/*.md; do
    [ -e "$agent" ] || continue
    name="$(basename "$agent")"
    link_one "$SRC_ROOT/agents/$name" "$CLAUDE_DIR/agents/$name" "agent file"
  done
}

# wire_ai_tools_sync_hook: idempotently add the ai-tools-sync.sh SessionStart
# entry to settings.json, unless some SessionStart command already mentions
# it. Runs BEFORE the "not wired" diagnostic below so that diagnostic sees
# ai-tools-sync.sh as already wired on a fresh install.
wire_ai_tools_sync_hook() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "  ⚠ jq unavailable — skipped settings.json SessionStart wiring for ai-tools-sync.sh."
    echo "     Add manually — see hooks/README.md."
    return
  fi

  mkdir -p "$CLAUDE_DIR"
  # shellcheck disable=SC2016  # literal $HOME — meant for settings.json, not expanded here
  local cmd_str='bash "$HOME/.claude/hooks/ai-tools-sync.sh"'

  if [ -f "$SETTINGS" ]; then
    if jq -e '(.hooks.SessionStart // [])[]? | .hooks[]? | select(.command? and (.command | contains("ai-tools-sync.sh")))' \
      "$SETTINGS" >/dev/null 2>&1; then
      if [ "$QUIET" -eq 0 ]; then echo "  ✓ settings.json SessionStart already wires ai-tools-sync.sh"; fi
      return
    fi
    backup_settings_once
    jq --arg cmd "$cmd_str" '
      .hooks = (.hooks // {}) |
      .hooks.SessionStart = ((.hooks.SessionStart // []) + [{"hooks":[{"type":"command","command":$cmd,"timeout":30}]}])
    ' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
    echo "  settings.json: wired ai-tools-sync.sh into hooks.SessionStart"
  else
    jq -n --arg cmd "$cmd_str" \
      '{hooks:{SessionStart:[{"hooks":[{"type":"command","command":$cmd,"timeout":30}]}]}}' > "$SETTINGS"
    echo "  settings.json: created, with ai-tools-sync.sh wired into hooks.SessionStart"
  fi
}

# ── hooks/ ──
section_hooks() {
  [ -d "$SRC_ROOT/hooks" ] || return 0
  mkdir -p "$CLAUDE_DIR/hooks"
  local hook name
  for hook in "$SRC_ROOT/hooks"/*; do
    [ -e "$hook" ] || continue
    name="$(basename "$hook")"
    # Skip documentation files — only symlink executables/scripts (and the
    # shared lib/ dir, which link_one symlinks as a single directory).
    case "$name" in
      README*|*.md) continue ;;
    esac
    link_one "$SRC_ROOT/hooks/$name" "$CLAUDE_DIR/hooks/$name" "hook file"
  done

  if [ "$QUIET" -eq 0 ]; then
    echo ""
    echo "  Note: hooks are wired up via ~/.claude/settings.json — symlinking the script"
    echo "        alone doesn't enable execution. See hooks/README.md for required config."
  fi

  wire_ai_tools_sync_hook

  # Diff symlinked hook scripts against settings.json to flag ones that are
  # present on disk but not actually wired into any hooks.<EVENT> entry.
  # Runs AFTER wire_ai_tools_sync_hook so a fresh ai-tools-sync.sh isn't
  # flagged as unwired the moment it's added above.
  if command -v jq >/dev/null 2>&1 && [ -f "$SETTINGS" ]; then
    local NOT_WIRED=()
    for hook in "$SRC_ROOT/hooks"/*; do
      [ -e "$hook" ] || continue
      name="$(basename "$hook")"
      case "$name" in
        README*|*.md) continue ;;
        # Wired outside settings.json's hooks tree — not applicable here.
        enforce-claude-symlinks.sh) continue ;;  # git pre-commit hook (.git/hooks/pre-commit)
        set-process-title.cjs) continue ;;       # loaded via env.NODE_OPTIONS, not hooks.*
        lib) continue ;;                         # shared helper dir, not an individual hook
      esac
      if ! jq -e --arg name "$name" \
        '.hooks // {} | to_entries[] | .value[]? | .hooks[]? | select(.command? and (.command | contains($name)))' \
        "$SETTINGS" >/dev/null 2>&1; then
        NOT_WIRED+=("$name")
      fi
    done
    # Steady-state on a fresh machine (settings.json is machine-local and not
    # deployed), so it repeats identically every run — informational, hidden
    # under --quiet (which the ai-tools-sync hook uses).
    if [ ${#NOT_WIRED[@]} -gt 0 ] && [ "$QUIET" -eq 0 ]; then
      echo ""
      echo "  ⚠ Symlinked but NOT wired in settings.json (inactive until added):"
      for n in "${NOT_WIRED[@]}"; do
        echo "     - $n"
      done
      echo "     See hooks/README.md for the exact settings.json entry for each."
    else
      if [ "$QUIET" -eq 0 ]; then
        echo ""
        echo "  ✓ All hooks are wired in settings.json."
      fi
    fi
  fi
}

# ── automations/ ──
section_automations() {
  [ -d "$SRC_ROOT/automations" ] || return 0
  local auto name script
  for auto in "$SRC_ROOT/automations"/*/; do
    [ -d "$auto" ] || continue
    name="$(basename "${auto%/}")"
    mkdir -p "$CLAUDE_DIR/automations/$name"
    for script in "$auto"*.sh "$auto"*.py; do
      [ -e "$script" ] || continue
      link_one "$script" "$CLAUDE_DIR/automations/$name/$(basename "$script")" "automation script"
    done
  done
  if [ "$QUIET" -eq 0 ]; then
    echo ""
    echo "  Note: scheduling (launchd plists) isn't symlinked and doesn't auto-install —"
    echo "        see automations/README.md to wire each automation up on a new machine."
  fi
}

# ── statusline/ ──
section_statusline() {
  [ -d "$SRC_ROOT/statusline" ] || return 0

  # jq (needed to parse the statusline JSON on stdin) is bootstrapped by
  # ensure_jq before any section runs.
  link_one "$SRC_ROOT/statusline/awesome-statusline.sh" "$CLAUDE_DIR/awesome-statusline.sh" "statusline script"

  if command -v jq >/dev/null 2>&1; then
    local target_cmd='bash ~/.claude/awesome-statusline.sh'
    mkdir -p "$CLAUDE_DIR"
    local current_cmd=""
    if [ -f "$SETTINGS" ]; then
      current_cmd="$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null || true)"
    fi
    if [ "$current_cmd" = "$target_cmd" ]; then
      if [ "$QUIET" -eq 0 ]; then echo "  ✓ settings.json statusLine already set"; fi
    else
      local statusline_json='{"type":"command","command":"bash ~/.claude/awesome-statusline.sh"}'
      if [ -f "$SETTINGS" ]; then
        backup_settings_once
        jq --argjson sl "$statusline_json" '.statusLine = $sl' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
        echo "  settings.json: statusLine set"
      else
        jq -n --argjson sl "$statusline_json" '{statusLine: $sl}' > "$SETTINGS"
        echo "  settings.json: created with statusLine set"
      fi
    fi
  else
    echo "  ⚠ jq unavailable — skipped settings.json statusLine wiring. Set it manually:"
    echo "     statusLine.command = \"bash ~/.claude/awesome-statusline.sh\""
  fi
}

# ── prune: dangling ~/.claude/* symlinks left by renamed/removed ai-tools content ──
# install.sh only ever links names that exist in the source tree, so a skill or
# automation that was renamed upstream leaves an orphan behind (and would trip
# ai-tools-sync's stale-link check forever). Only links whose target was inside
# an ai-tools checkout are touched: under $AI_TOOLS_HOME, under $SRC_ROOT, or a
# path containing "ai-tools". Anything else in ~/.claude is left alone.
is_ai_tools_target() {
  case "$1" in
    "$AI_TOOLS_HOME"/*|"$SRC_ROOT"/*|*ai-tools*) return 0 ;;
    *) return 1 ;;
  esac
}
prune_one() {
  local link=$1 target
  [ -L "$link" ] || return 0
  [ -e "$link" ] && return 0          # resolves fine — not dangling
  target="$(readlink "$link")"
  is_ai_tools_target "$target" || return 0
  rm "$link"
  echo "  ✂ ${link#"$HOME"/} (dangling → $target) pruned"
}
section_prune() {
  local sub link autodir
  for sub in skills commands hooks agents; do
    [ -d "$CLAUDE_DIR/$sub" ] || continue
    for link in "$CLAUDE_DIR/$sub"/* "$CLAUDE_DIR/$sub"/.[!.]*; do
      prune_one "$link"
    done
  done
  if [ -d "$CLAUDE_DIR/automations" ]; then
    for autodir in "$CLAUDE_DIR/automations"/*/; do
      [ -d "$autodir" ] || continue
      for link in "$autodir"*; do
        prune_one "$link"
      done
      # An automation dir with nothing left in it was a renamed/removed automation.
      if [ -z "$(ls -A "$autodir" 2>/dev/null)" ]; then
        rmdir "$autodir" && echo "  ✂ ${autodir#"$HOME"/} (empty) removed"
      fi
    done
  fi
  prune_one "$CLAUDE_DIR/awesome-statusline.sh"
}

run_section "── prune stale links ──" section_prune
run_section "── skills ──" section_skills
run_section "── commands ──" section_commands
run_section "── agents ──" section_agents
run_section "── hooks ──" section_hooks
run_section "── automations ──" section_automations
run_section "── statusline ──" section_statusline

echo ""
echo "Done."
