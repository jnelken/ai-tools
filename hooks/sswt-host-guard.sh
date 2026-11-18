#!/usr/bin/env bash
# Claude Code PreToolUse hook on Bash: on jake1 (the human-led work Mac), a
# `superset workspaces create` must name its host with --host.
#
# Why: agent work belongs on jake2 (the agent host); jake1 freezes under memory
# pressure during parallel agent work. The rule is in instructions/machine/
# (work-mac.md, superset.md), but advice alone was skipped: the Superset CLI
# defaults to this machine (or the cloud), so forgetting the flag silently put
# the workspace on jake1. Requiring --host turns the placement into a deliberate
# choice. `--host <jake2 id>` is the default; `--host <jake1 id>` is fine when
# the agent decides jake1 is right (jake2 offline, light work, Jake said so).
#
# Blocks (exit 2, reason on stderr) a `superset ws|workspaces create` with no
# --host, including one that passes --local. Does nothing on any other machine:
# it acts only when the machine profile is work-mac and `hostname -s` is jake1.
# Append `# sswt-host-ok` to the command to bypass it.
#
# Tests: bash hooks/tests/sswt-host-guard.test.sh

set -uo pipefail

JAKE1_ID=896a8c9f35f5ec59973774b4262feb71
JAKE2_ID=5f5aff3dec6113da62545abf567f8d10

# Only the human-led work Mac. SSWT_GUARD_FORCE bypasses the machine check for tests.
if [[ -z "${SSWT_GUARD_FORCE:-}" ]]; then
  [[ "$(hostname -s 2>/dev/null)" == "jake1" ]] || exit 0
  machine="${AGENT_MACHINE:-}"
  if [[ -z "$machine" ]]; then
    machine=$(jq -r '.machine // empty' "${AGENT_PROFILES_CONFIG:-$HOME/.config/agent-profiles/config.json}" 2>/dev/null) || machine=""
  fi
  [[ "$machine" == "work-mac" ]] || exit 0
fi

cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[[ -n "$cmd" ]] || exit 0
[[ "$cmd" == *"# sswt-host-ok"* ]] && exit 0
[[ "$cmd" == *superset* ]] || exit 0

# Drop quoted spans so prose (commit messages, --prompt text) cannot match,
# then split the command into simple commands on ; & | and newlines.
bare=$(printf '%s\n' "$cmd" | perl -0777 -pe 's/(\\.)|\x27[^\x27]*\x27|"(?:[^"\\]|\\.)*"/defined $1 ? $1 : ""/gse')

blocked=0
while IFS= read -r seg; do
  # `superset [global flags] (ws|workspaces) create ...`, optionally after env assignments.
  printf '%s' "$seg" | grep -Eq '(^|[[:space:]])superset([[:space:]]+-[^[:space:]]+)*[[:space:]]+(ws|workspaces)[[:space:]]+create([[:space:]]|$)' || continue
  # --host <id> or --host=<id> anywhere in the same simple command is a deliberate choice.
  printf '%s' "$seg" | grep -Eq '(^|[[:space:]])--host([[:space:]=])' && continue
  blocked=1
done < <(printf '%s\n' "$bare" | tr ';&|' '\n\n\n')

((blocked == 0)) && exit 0

{
  echo "Blocked by sswt-host-guard: 'superset workspaces create' on jake1 must pass --host."
  echo "  Agent work belongs on jake2 (the agent host): --host $JAKE2_ID"
  echo "  If jake1 is the right place, say so by passing it: --host $JAKE1_ID"
  echo "  (--local is not accepted here; use --host with jake1's id.)"
  echo "  jake2 first: 'superset hosts list' shows it online, and"
  echo "  'superset projects list --host $JAKE2_ID' includes the repo. Details:"
  echo "  ~/.claude/instructions/superset-workspaces.md (\"Remote Host (jake2)\")."
  echo "Append '# sswt-host-ok' to the command to bypass this check."
} >&2
exit 2
