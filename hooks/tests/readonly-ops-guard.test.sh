#!/usr/bin/env bash
# Behaviour matrix for readonly-ops-guard.sh.
# Run: bash hooks/tests/readonly-ops-guard.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/readonly-ops-guard.sh}"
pass=0; fail=0
command -v jq >/dev/null 2>&1 || { echo 'FAIL: tests require jq'; exit 1; }

expect() { # expect <exit code> <label> <command>
  local got reason
  reason=$(jq -n --arg c "$3" '{tool_input:{command:$c}}' | bash "$HOOK" 2>&1)
  got=$?
  if [[ "$got" == "$1" ]] && { [[ "$got" == 0 ]] || [[ "$reason" == readonly-ops-guard:* && "$reason" != *$'\n'* ]]; }; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    echo "FAIL: $2 — want exit $1, got $got ($reason)"
  fi
}

# Text matching deliberately blocks a quoted echo example. Commands in this
# table are data passed to the hook, never executed.
while IFS='|' read -r expected label command; do
  expect "$expected" "$label" "$command"
done <<'CASES'
0|Datadog logs|pup logs search --query 'service:x' --from 1h
0|live Koyeb service|koyeb services get claudio -o json
0|Koyeb deployment|koyeb deployment get abc
0|failed step logs|gh run view 123 --log-failed
0|PR checks|gh pr checks 5
0|git history|git log --oneline
0|unrelated update word|grep -r update src
0|git diff|git -C /tmp/repo diff HEAD
0|git soft reset outside block list|git reset --soft HEAD
0|GitHub API GET|gh api --method GET repos/x/y
0|curl GET|curl -X GET https://x
0|curl default GET|curl https://x
0|read pipeline|pup logs search --query 'service:x' | jq '.data'
0|empty command|
2|quoted echo is blocked|echo "koyeb service update"
2|Koyeb update|koyeb service update foo --env X=1
2|Koyeb secrets reveal|koyeb secrets reveal K
2|Koyeb secret reveal|koyeb secret reveal K
2|Datadog mute|pup monitors mute 1
2|rerun workflow|gh run rerun 9
2|cancel run|gh run cancel 9
2|trigger workflow|gh workflow run ci.yml
2|merge PR|gh pr merge 3
2|create issue|gh issue create --title x
2|GitHub POST|gh api -X POST repos/x/y/issues
2|GitHub attached method|gh api -XPOST repos/x/y/issues
2|GitHub long method|gh api --method PATCH repos/x/y/issues/1
2|GitHub equals method|gh api --method=PUT repos/x/y
2|git push|git push origin main
2|git commit|git commit -m x
2|git hard reset|git reset --hard HEAD
2|git hard reset reversed flags|git reset HEAD --hard
2|git checkout|git checkout feature
2|git switch|git switch feature
2|git stash|git stash
2|git global options|git -C /tmp/repo push origin main
2|chained deletion|ls && koyeb services delete x
2|semicolon|ls;koyeb services delete x
2|or chain|false || pup monitors mute 1
2|pipeline mutation|ls | gh run rerun 9
2|absolute executable|/usr/local/bin/koyeb service pause foo
2|wrapped executable|env FOO=bar pup monitors mute 1
2|curl DELETE|curl -X DELETE https://x
2|curl attached method|curl -XPOST https://x
2|curl long method|curl --request PATCH https://x
2|curl equals method|curl --request=PUT https://x
2|curl short data|curl -d 'x=1' https://x
2|curl attached data|curl -dx=1 https://x
2|curl long data|curl --data 'x=1' https://x
2|curl equals data|curl --data=x=1 https://x
2|curl raw data|curl --data-raw 'x=1' https://x
CASES

# Cover every remaining mutating verb rather than just one per CLI.
for verb in update redeploy delete pause resume create; do
  expect 2 "Koyeb $verb" "koyeb services $verb foo"
done
for verb in create update delete mute unmute patch cancel trigger; do
  expect 2 "Datadog $verb" "pup monitors $verb 1"
done
for verb in merge comment edit close review ready; do
  expect 2 "PR $verb" "gh pr $verb 3"
done
for verb in create comment edit close; do
  expect 2 "issue $verb" "gh issue $verb 3"
done
for method in POST PATCH PUT DELETE; do
  expect 2 "GitHub method $method" "gh api -X $method repos/x/y"
  expect 2 "curl method $method" "curl -X $method https://x"
done
expect 2 'newline separator' $'git log --oneline\nkoyeb services delete x'

# Malformed or missing command JSON must fail open.
for input in 'not json' '{' '{}' '{"tool_input":{"command":null}}' '{"tool_input":{"command":123}}'; do
  printf '%s' "$input" | bash "$HOOK" >/dev/null 2>&1
  got=$?
  if [[ "$got" == 0 ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: malformed input — want exit 0, got $got"; fi
done

# Empty PATH simulates missing jq. Invoke Bash by absolute path so only the
# hook's dependency lookup fails, without creating any test files.
BASH_PATH=$(command -v bash)
printf '%s' '{"tool_input":{"command":"git push origin main"}}' | PATH=/nonexistent "$BASH_PATH" "$HOOK" >/dev/null 2>&1
got=$?
if [[ "$got" == 0 ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: missing jq — want exit 0, got $got"; fi

echo "readonly-ops-guard: $pass passed, $fail failed"
((fail == 0))
