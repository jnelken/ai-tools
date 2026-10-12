#!/usr/bin/env bash
# Claude Code PreToolUse guardrail for Bash in ops-investigator's frontmatter.
# Deliberately text-based, not a sandbox, and fail-open: split command text on
# ;, &, |, and newline, strip quotes, parentheses, and backticks (so commands in
# subshells, $(...), and `...` are seen), then look for known mutating CLI
# words. A verb right after its command group may be preceded by options
# (gh run -R x/y rerun 9, koyeb secrets -o json reveal K), but a mutating word
# that is merely an argument (gh pr list --search merge) is not blocked. Other
# verb lists (koyeb, pup) match the word anywhere after the tool name, so a
# service or query literally named e.g. "create" can be blocked. Quoted echo
# examples can be blocked; quoted separators can split data. Shell aliases,
# variables, escapes, abbreviated long options, scripts, config files, and
# indirect execution are not resolved. Only the CLIs and verbs listed below are
# covered.
# Request bodies imply writes: gh api with -f/-F/--raw-field/--field/--input and
# no explicit -X/--method (gh then POSTs), and curl with -d/--data*/--json/-F/
# --form/-T/--upload-file (curl -G/--get turns -d/--data* into a query string),
# including bundled short flags like -sX POST or -sSd x. pup api defaults to
# GET (fields become query parameters), so only an explicit write method blocks.
# gh api graphql is allowed for queries but blocked if "mutation" appears
# anywhere in the unsplit command; query text read from a file (-F query=@f,
# --input) is never inspected, so both stay blocked.
# Fail open (exit 0) if jq is missing or the hook JSON cannot be parsed.
set -u

command -v jq >/dev/null 2>&1 || exit 0
cmd=$(jq -er '.tool_input.command | select(type == "string")' 2>/dev/null) || exit 0

deny() {
  printf 'readonly-ops-guard: %s\n' "$1" >&2
  exit 2
}

# HTTP methods that write, matched case-insensitively without nocasematch
# (which would also fold curl's read-only -D into the write flag -d).
meth='([Pp][Oo][Ss][Tt]|[Pp][Aa][Tt][Cc][Hh]|[Pp][Uu][Tt]|[Dd][Ee][Ll][Ee][Tt][Ee])'

# Zero or more options (each with at most one value token) between a command
# group and its verb. A bare subcommand such as "list" ends the match, so
# "gh pr list --search merge" is not mistaken for "gh pr merge".
opts='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'

has_verb() { # has_verb <args> <group regex> <verb regex>
  [[ "$1" =~ (^|[[:space:]])($2)${opts}[[:space:]]+($3)([[:space:]]|$) ]]
}

api_write_re='(^|[[:space:]])(-X[[:space:]]*|--method([[:space:]]+|=))'$meth'([[:space:]]|$)'
api_method_re='(^|[[:space:]])(-X|--method([[:space:]=]|$))'

# check_api <args after the executable> <deny message> <1 if a body implies POST>
check_api() {
  local a="$1" msg="$2" implicit_post="$3"
  [[ "$a" =~ (^|[[:space:]])api([[:space:]]|$) ]] || return 0
  if [[ "$a" =~ $api_write_re ]]; then
    deny "$msg"
  fi
  # Without an explicit method, gh api POSTs whenever a body is supplied.
  # An explicit read method (e.g. -X GET) makes fields a query string.
  if ((implicit_post)) && ! [[ "$a" =~ $api_method_re ]]; then
    if [[ "$a" =~ (^|[[:space:]])--input([[:space:]=]|$) ]]; then
      deny 'gh api --input implies a POST request body'
    fi
    if [[ "$a" =~ (^|[[:space:]])(-[fF]|--(raw-)?field([[:space:]=]|$)) ]] &&
       ! [[ "$a" =~ (^|[[:space:]])graphql([[:space:]]|$) ]]; then
      deny 'gh api request fields imply a POST; pass -X GET for a query'
    fi
  fi
  if [[ "$a" =~ (^|[[:space:]])graphql([[:space:]]|$) ]]; then
    # GraphQL queries are reads; check the whole command so a mutation on a
    # later line of a multi-line query is not hidden by newline splitting.
    if [[ "$cmd" =~ (^|[^[:alnum:]_])mutation([^[:alnum:]_]|$) ]]; then
      deny 'GraphQL mutations are prohibited'
    fi
    # A query read from a file cannot be inspected.
    if [[ "$a" =~ (query=@|(^|[[:space:]])--input([[:space:]=]|$)) ]]; then
      deny 'GraphQL queries read from a file cannot be inspected'
    fi
  fi
}

# curl short flags that take no argument, so they can be bundled before -X/-d/-F/-T.
cfl='[sSLfivkgGIOJN#0-9]'
curl_get_re='(^|[[:space:]])(--get|-'$cfl'*G'$cfl'*)([[:space:]]|$)'
curl_method_re='(^|[[:space:]])(-'$cfl'*X[[:space:]]*|--request([[:space:]]+|=))'$meth'([[:space:]]|$)'
curl_body_re='(^|[[:space:]])((--json|--form(-string)?|--upload-file)([[:space:]=]|$)|-'$cfl'*[FT])'
curl_data_re='(^|[[:space:]])(--data([[:space:]=]|$|-)|-'$cfl'*d)'

# A simple split also covers && and ||, leaving harmless empty segments.
segments="${cmd//;/$'\n'}"
segments="${segments//&/$'\n'}"
segments="${segments//|/$'\n'}"
while IFS= read -r segment; do
  segment="${segment//\"/}"
  segment="${segment//\'/}"
  segment="${segment//[()]/ }"
  segment="${segment//\`/ }"

  if [[ "$segment" =~ (^|[[:space:]/])koyeb[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(update|redeploy|delete|pause|resume|create|scale|deploy)([[:space:]]|$) ]] ||
       has_verb "$args" 'secrets?|sec' reveal; then
      deny 'Koyeb mutations and secret reveals are prohibited'
    fi
  fi

  if [[ "$segment" =~ (^|[[:space:]/])pup[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if [[ "$args" =~ (^|[[:space:]])(create|update|delete|mute|unmute|patch|cancel|trigger)([[:space:]]|$) ]] ||
       has_verb "$args" tags 'add|remove'; then
      deny 'Datadog mutations are prohibited'
    fi
    check_api "$args" 'Datadog API write requests are prohibited' 0
  fi

  if [[ "$segment" =~ (^|[[:space:]/])gh[[:space:]](.*) ]]; then
    args="${BASH_REMATCH[2]}"
    if has_verb "$args" run 'rerun|cancel|delete' ||
       has_verb "$args" workflow 'run|enable|disable' ||
       has_verb "$args" pr 'merge|comment|edit|close|review|ready|create|reopen|lock|unlock|update-branch' ||
       has_verb "$args" issue 'create|comment|edit|close|reopen|delete|lock|unlock|transfer|pin|unpin'; then
      deny 'GitHub mutations are prohibited'
    fi
    check_api "$args" 'GitHub API write methods are prohibited' 1
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
    if [[ "$args" =~ $curl_get_re ]]; then
      curl_get=1
    fi
    if [[ "$args" =~ $curl_method_re ]] ||
       [[ "$args" =~ $curl_body_re ]] ||
       { ((curl_get == 0)) && [[ "$args" =~ $curl_data_re ]]; }; then
      deny 'curl write methods and data payloads are prohibited'
    fi
  fi
done <<< "$segments"

exit 0
