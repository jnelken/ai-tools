#!/bin/zsh
# Compatibility shim. wrapup-repos is now a skill (skills/wrapup-repos) run by bin/skill-automation, and
# the plist calls that directly. This file only keeps an already-loaded OLD plist (one that still runs
# ~/.claude/automations/wrapup-repos/run.sh) working until it is reloaded. Delete it once every
# personal machine has re-bootstrapped com.jake.wrapup-repos.
exec "$HOME/.ai-tools/bin/skill-automation" wrapup-repos --once-per week \
  --add-dir "$("$HOME/.ai-tools/bin/personal-code-dir" 2>/dev/null)"
