#!/usr/bin/env bash
# Claude Code PreToolUse guardrail for Bash in ops-investigator's frontmatter.
# Deliberately text-based, not a sandbox: split command text on ;, &, |, and
# newline, strip quotes, then look for known mutating CLI words. Quoted echo
# examples can be blocked; quoted separators can split data. Shell aliases,
# variables, escapes, scripts, and indirect execution are not resolved.
# Request bodies imply writes: gh api with -f/-F/--raw-field/--field/--input and
# no explicit -X/--method (gh then POSTs), and curl with -d/--data*/--json/-F/
# --form/-T/--upload-file (curl -G/--get turns -d/--data* into a query string).
# gh api graphql is allowed for queries but blocked if "mutation" appears
# anywhere in the unsplit command; query text read from a file (-F query=@f,
# --input) is never inspected, so --input stays blocked.
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
    if [[ "$args" =~ (^|[[:space:]])api([[:space:]]|$) ]]; then
      if [[ "$args" =~ (^|[[:space:]])(-X[[:space:]]*|--method([[:space:]]+|=))(POST|PATCH|PUT|DELETE)([[:space:]]|$) ]]; then
        deny 'GitHub API write methods are prohibited'
      fi
      # Without an explicit method, gh api POSTs whenever a body is supplied.
      # An explicit read method (e.g. -X GET) makes fields a query string.
      if ! [[ "$args" =~ (^|[[:space:]])(-X|--method([[:space:]=]|$)) ]]; then
        if [[ "$args" =~ (^|[[:space:]])--input([[:space:]=]|$) ]]; then
          deny 'gh api --input implies a POST request body'
        fi
        if [[ "$args" =~ (^|[[:space:]])(-[fF]|--(raw-)?field([[:space:]=]|$)) ]] &&
           ! [[ "$args" =~ (^|[[:space:]])graphql([[:space:]]|$) ]]; then
          deny 'gh api request fields imply a POST; pass -X GET for a query'
        fi
      fi
      # GraphQL queries are reads; check the whole command so a mutation on a
      # later line of a multi-line query is not hidden by newline splitting.
      if [[ "$args" =~ (^|[[:space:]])graphql([[:space:]]|$) ]] &&
         [[ "$cmd" =~ (^|[^[:alnum:]_])mutation([^[:alnum:]_]|$) ]]; then
        deny 'GraphQL mutations are prohibited'
      fi
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
    # -G/--get (also bundled, e.g. -sG) turns -d/--data* into a query string.
    curl_get=0
    if [[ "$args" =~ (^|[[:space:]])(--get|-[sSLkvfig]*G[sSLkvfig]*)([[:space:]]|$) ]]; then
      curl_get=1
    fi
    if [[ "$args" =~ (^|[[:space:]])(-X[[:space:]]*|--request([[:space:]]+|=))(POST|PATCH|PUT|DELETE)([[:space:]]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])(--json|--form(-string)?|--upload-file)([[:space:]=]|$) ]] ||
       [[ "$args" =~ (^|[[:space:]])-[FT] ]] ||
       { ((curl_get == 0)) && [[ "$args" =~ (^|[[:space:]])(--data([[:space:]=]|$|-)|-d) ]]; }; then
      deny 'curl write methods and data payloads are prohibited'
    fi
  fi
done <<< "$segments"

exit 0
