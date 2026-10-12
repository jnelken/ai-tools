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
2|curl binary data|curl --data-binary @f https://x
2|curl urlencode data|curl --data-urlencode q=1 https://x
2|curl form|curl -F text=incident https://x
2|curl attached form|curl -Ftext=incident https://x
2|curl long form|curl --form text=incident https://x
2|curl equals form|curl --form=text=incident https://x
2|curl form-string|curl --form-string text=incident https://x
2|curl json|curl --json '{"a":1}' https://x
2|curl equals json|curl --json={} https://x
2|curl upload short|curl -T file https://x
2|curl upload attached|curl -Tfile https://x
2|curl upload long|curl --upload-file file https://x
2|curl get with form still writes|curl -G -F a=b https://x
2|curl get with json still writes|curl -G --json '{}' https://x
2|curl get with upload still writes|curl --get -T file https://x
0|curl silent|curl -s https://x
0|curl get with data|curl -G -d q=1 https://x
0|curl long get with urlencode|curl --get --data-urlencode q=1 https://x
0|curl bundled get with data|curl -sG -d q=1 https://x
0|curl data then get|curl -d q=1 -G https://x
0|curl get with long data|curl -G --data q=1 https://x
0|GitHub API plain path|gh api repos/x/y/pulls/1
0|GitHub API paginate with jq|gh api repos/x/y/issues --paginate -q '.[].title'
0|GitHub API explicit GET with field|gh api -X GET repos/x/y/issues -f state=open
0|GitHub API long GET with typed field|gh api --method GET repos/x/y/issues -F per_page=5
0|GitHub API GraphQL query|gh api graphql -f query='query { viewer { login } }'
0|GitHub API GraphQL typed field query|gh api graphql -F query='query { viewer { login } }'
2|GitHub API implicit POST field|gh api repos/x/y/issues -f title=incident
2|GitHub API raw field|gh api repos/x/y/issues --raw-field title=incident
2|GitHub API typed field|gh api repos/x/y/issues -F title=incident
2|GitHub API long field|gh api repos/x/y/issues --field title=incident
2|GitHub API attached field|gh api repos/x/y/issues -ftitle=incident
2|GitHub API equals field|gh api repos/x/y/issues --field=title=incident
2|GitHub API field before path|gh api -f title=incident repos/x/y/issues
2|GitHub API input file|gh api repos/x/y/issues --input body.json
2|GitHub API equals input|gh api repos/x/y/issues --input=body.json
2|GitHub API piped input|echo '{}' | gh api repos/x/y/issues --input -
2|GitHub API method POST with field|gh api -X POST repos/x/y/issues -f title=incident
2|GitHub API GraphQL mutation|gh api graphql -f query='mutation { addStar(input:{starrableId:"x"}) { clientMutationId } }'
2|GitHub API GraphQL mutation with explicit GET|gh api -X GET graphql -f query='mutation { a }'
2|GitHub API GraphQL input file|gh api graphql --input q.json
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
expect 2 'GraphQL mutation on a later line' $'gh api graphql -f query=\'\nmutation {\n  addStar(input: {starrableId: "x"}) { clientMutationId }\n}\''
expect 0 'multi-line GraphQL query' $'gh api graphql -f query=\'\nquery {\n  viewer { login }\n}\''

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
