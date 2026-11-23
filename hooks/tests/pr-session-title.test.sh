#!/usr/bin/env bash
# Behaviour matrix for pr-session-title.py. Run: bash tests/pr-session-title.test.sh
# Sessions dir and pending-title dir are temp dirs; nothing touches ~/.claude.
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/pr-session-title.py}"
pass=0; fail=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/pr-session-title-test-$$-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

export PR_SESSION_TITLE_SESSIONS_DIR="$TMP/sessions"
export PR_SESSION_TITLE_STATE_DIR="$TMP/state"
mkdir -p "$PR_SESSION_TITLE_SESSIONS_DIR"

expect() { # expect <want> <label> <got>
  if [[ "$3" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want [$1], got [$3]"; fi
}
named() { # named <name> <source>  -> session s1 has this name
  printf '{"pid":1,"sessionId":"s1","name":"%s","nameSource":"%s"}' "$1" "$2" > "$PR_SESSION_TITLE_SESSIONS_DIR/1.json"
}
reset() { rm -rf "$PR_SESSION_TITLE_STATE_DIR"; }
bash_json() { # bash_json <command> <stdout> [stderr]
  python3 -c 'import json,sys; print(json.dumps({"session_id":"s1","tool_name":"Bash","tool_input":{"command":sys.argv[1]},"tool_response":{"stdout":sys.argv[2],"stderr":sys.argv[3],"interrupted":False}}))' "$1" "$2" "${3:-}"
}
post() { python3 "$HOOK" --event posttool <<<"$(bash_json "$@")"; }
prompt() { python3 "$HOOK" --event prompt <<<"{\"session_id\":\"s1\",\"prompt\":\"${1:-next}\"}"; }
want_title() { printf '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "%s"}}' "$1"; }
cycle() { # cycle <command> <stdout> [stderr] -> what the next prompt emits
  reset; post "$@"; prompt
}
URL='https://github.com/Concentro-Inc/woodrow/pull/1345'

# ── which sessions get renamed ──
named "shimmer-promise-d4583f6f-92" derived
expect "$(want_title woodrow#1345)" "derived name is replaced" "$(cycle 'gh pr create --draft --title x' "$URL")"
named "Fix the thing" auto
expect "$(want_title woodrow#1345)" "auto name is replaced" "$(cycle 'gh pr create --draft' "$URL")"
named "api#5821" user
expect "$(want_title woodrow#1345)" "older PR name is replaced, even a typed one" "$(cycle 'gh pr create' "$URL")"
named "folio-platform#4567" hook
expect "$(want_title woodrow#1345)" "older hook-set PR name is replaced" "$(cycle 'gh pr create' "$URL")"
named "my pricing investigation" user
expect "" "manual /rename name is kept" "$(cycle 'gh pr create' "$URL")"
named "woodrow#1345" hook
expect "" "title already equal -> nothing" "$(cycle 'gh pr create' "$URL")"

# ── what counts as a PR creation ──
named "x" derived
expect "$(want_title api#5821)" "repo comes from the URL" "$(cycle 'gh pr create' 'https://github.com/Concentro-Inc/api/pull/5821')"
expect "$(want_title internal-tools#246)" "hyphenated repo" "$(cycle 'gh pr create' 'https://github.com/Concentro-Inc/internal-tools/pull/246')"
expect "$(want_title woodrow#1345)" "pr:create wrapper" "$(cycle 'pnpm run pr:create -- --preview "x: /y"' "$URL")"
expect "$(want_title woodrow#1346)" "last URL wins (parent PR printed first)" "$(cycle 'pnpm run pr:create' "$URL"$'\n''https://github.com/Concentro-Inc/woodrow/pull/1346')"
expect "$(want_title woodrow#1345)" "URL on stderr counts" "$(cycle 'gh pr create' '' "$URL")"
expect "" "gh pr view does not trigger" "$(cycle 'gh pr view --json url' "$URL")"
expect "" "gh pr edit does not trigger" "$(cycle 'gh pr edit 1 --title x' "$URL")"
expect "" "create command without a PR URL" "$(cycle 'gh pr create' 'done')"
expect "" "non-GitHub URL" "$(cycle 'gh pr create' 'https://example.com/a/b/pull/3')"

# ── phase timing and edge cases ──
reset; named "x" derived
expect "" "posttool prints nothing" "$(post 'gh pr create' "$URL")"
expect "$(want_title woodrow#1345)" "next prompt emits it" "$(prompt)"
expect "" "and only once" "$(prompt)"

reset; post 'gh pr create' "$URL"
expect "" "/rename that turn drops the pending title" "$(prompt '/rename my-name')"
expect "" "…and it stays dropped" "$(prompt)"

reset; post 'gh pr create' "$URL"; named "chosen by hand" user
expect "" "user renamed between PR and prompt -> kept" "$(prompt)"

reset; named "x" derived; post 'gh pr create' 'https://github.com/o/api/pull/1'; post 'gh pr create' "$URL"
expect "$(want_title woodrow#1345)" "second PR overwrites the pending first" "$(prompt)"

reset; named "x" derived; post 'gh pr create' 'https://github.com/o/api/pull/1'; named "chosen by hand" user; post 'gh pr create' "$URL"
expect "" "later PR clears a stale pending title when the name is protected" "$(prompt)"

reset; rm -f "$PR_SESSION_TITLE_SESSIONS_DIR"/*.json
expect "" "session file missing -> leave alone" "$(cycle 'gh pr create' "$URL")"

expect "" "empty stdin is harmless" "$(python3 "$HOOK" --event prompt <<<'')"
expect "" "garbage stdin is harmless" "$(python3 "$HOOK" --event posttool <<<'not json')"

if [[ $fail -eq 0 ]]; then echo "pr-session-title: all $pass checks passed"; else echo "pr-session-title: $fail failed, $pass passed"; exit 1; fi
