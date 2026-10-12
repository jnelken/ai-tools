#!/usr/bin/env bash
# Claude Code PreToolUse guardrail for Bash in ops-investigator's frontmatter.
# Deliberately text-based, not a sandbox: split command text on ;, &, |, and
# newline, strip quotes, then look for known mutating CLI words. Quoted echo
# examples can be blocked; quoted separators can split data. Shell aliases,
# variables, escapes, scripts, and indirect execution are not resolved.
# Fail open (exit 0) if jq is missing or the hook JSON cannot be parsed.
set -u

command -v jq >/dev/null 2>&1 || exit 0
cmd=$(jq -er '.tool_input.command | select(type == "string")' 2>/dev/null) || exit 0

deny() {
  printf 'readonly-ops-guard: %s\n' "$1" >&2
  exit 2
}

# A simple split also covers && and ||, leaving harmless empty segments.
segments="${cmd//;/$'\n'}"
segments="${segments//&/$'\n'}"
segments="${segments//|/$'\n'}"
while IFS= read -r segment; do
  segment="${segment//\"/}"
  segment="${segment//\'/}"

  if [[ "$segment" =~ (^|[[:space:]/])koyeb[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(update|redeploy|delete|pause|resume|create)([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])secrets?[[:space:]]+reveal([[:space:]]|$) ]]; then
      deny 'Koyeb mutations and secret reveals are prohibited'
    fi
  fi

  if [[ "$segment" =~ (^|[[:space:]/])pup[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(create|update|delete|mute|unmute|patch|cancel|trigger)([[:space:]]|$) ]]; then
      deny 'Datadog mutations are prohibited'
    fi
  fi

  if [[ "$segment" =~ (^|[[:space:]/])gh[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])run[[:space:]]+(rerun|cancel)([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])workflow[[:space:]]+run([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])pr[[:space:]]+(merge|comment|edit|close|review|ready)([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])issue[[:space:]]+(create|comment|edit|close)([[:space:]]|$) ]]; then
      deny 'GitHub mutations are prohibited'
    fi
    if [[ "$args" =~ (^|[[:space:]])api([[:space:]]|$) ]] &&
       [[ "$args" =~ (^|[[:space:]])(-X[[:space:]]*|--method([[:space:]]+|=))(POST|PATCH|PUT|DELETE)([[:space:]]|$) ]]; then
      deny 'GitHub API write methods are prohibited'
    fi
  fi

  if [[ "$segment" =~ (^|[[:space:]/])git[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(push|commit|checkout|switch|stash)([[:space:]]|$) ]] ||
       { [[ "$args" =~ (^|[[:space:]])reset([[:space:]]|$) ]] &&
         [[ "$args" =~ (^|[[:space:]])--hard([[:space:]]|$) ]]; }; then
      deny 'Git writes and destructive worktree commands are prohibited'
    fi
  fi

  if [[ "$segment" =~ (^|[[:space:]/])curl[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(-X[[:space:]]*|--request([[:space:]]+|=))(POST|PATCH|PUT|DELETE)([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])(--data([[:space:]=]|$|-)|-d) ]]; then
      deny 'curl write methods and data payloads are prohibited'
    fi
  fi
done <<< "$segments"

exit 0
