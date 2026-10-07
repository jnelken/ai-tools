#!/usr/bin/env bash
# Behaviour matrix for zsh-word-split-guard.sh. Run: bash hooks/tests/zsh-word-split-guard.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/zsh-word-split-guard.sh}"
pass=0; fail=0

run() { # run <command> → BLOCK | PASS | ERROR(status)
  jq -n --arg c "$1" '{tool_input:{command:$c}}' | ZSH_GUARD_FORCE=1 bash "$HOOK" >/dev/null 2>&1
  case $? in 0) echo PASS;; 2) echo BLOCK;; *) echo "ERROR($?)";; esac
}
expect() { # expect <want> <label> <command>
  local got; got=$(run "$3")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want $1, got $got"; fi
}

# Traps (block)
expect BLOCK "set -- \$m"              'for m in "a b"; do set -- $m; echo $1; done'
expect BLOCK "for x in \$list"         'for f in $files; do echo $f; done'
expect BLOCK "bare === separator"      'git log -1; echo ===; git status'
expect BLOCK "bare ---- after &&"      'ls && ===='
expect BLOCK "=== at start"            '=== && ls'

# Safe forms (pass)
expect PASS  "set -- \${=m}"           'set -- ${=m}; echo $1'
expect PASS  "quoted ==="              "echo '==='; echo \"===\""
expect PASS  "=== inside commit msg"   'git commit -m "notes === here"'
expect PASS  "for over literal words"  'for f in a b c; do echo $f; done'
expect PASS  "for over \${=var}"       'for f in ${=files}; do echo $f; done'
expect PASS  "for over \$(cmd)"        'for f in $(ls); do echo $f; done'
expect PASS  "==/=~ in test"           '[[ $a == b ]] && echo ok'
expect PASS  "= in [ ]"                '[ "$a" = b ] && echo ok'
expect PASS  "var=value"               'FOO=bar baz'
expect PASS  "flag --x=y"              'pup logs --from=15m'
expect PASS  "set -- literal"          'set -- a b c'
expect PASS  "bypass comment"          'echo === # zsh-ok'
expect PASS  "empty"                   ''

# Non-zsh shells are left alone
out=$(jq -n '{tool_input:{command:"echo ==="}}' | SHELL=/bin/bash ZSH_GUARD_FORCE= bash "$HOOK" >/dev/null 2>&1; echo $?)
if [[ "$out" == 0 ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: bash shell — want exit 0, got $out"; fi

echo "$pass passed, $fail failed"
((fail == 0))
