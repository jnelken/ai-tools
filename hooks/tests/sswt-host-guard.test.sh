#!/usr/bin/env bash
# Behaviour matrix for sswt-host-guard.sh. Run: bash hooks/tests/sswt-host-guard.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/sswt-host-guard.sh}"
pass=0; fail=0

run() { # run <command> → BLOCK | PASS | ERROR(status); machine check bypassed
  jq -n --arg c "$1" '{tool_input:{command:$c}}' | SSWT_GUARD_FORCE=1 bash "$HOOK" >/dev/null 2>&1
  case $? in 0) echo PASS;; 2) echo BLOCK;; *) echo "ERROR($?)";; esac
}
expect() { # expect <want> <label> <command>
  local got; got=$(run "$3")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want $1, got $got"; fi
}

# No --host (block)
expect BLOCK "workspaces create, no host"  'superset workspaces create --project p --branch b --agent claude --prompt "x"'
expect BLOCK "ws create, no host"          'superset ws create --project p --branch b'
expect BLOCK "--local is not a host"       'superset ws create --local --project p --branch b'
expect BLOCK "global flag before ws"       'superset --json ws create --project p --branch b'
expect BLOCK "after cd &&"                 'cd ~/x && superset ws create --project p --branch b'
expect BLOCK "second command in a chain"   'superset hosts list; superset ws create --project p --branch b'
expect BLOCK "multi-line command"          $'echo hi\nsuperset workspaces create --project p --branch b'
expect BLOCK "host only in other command"  'superset hosts list --host abc && superset ws create --project p --branch b'

# Deliberate host (pass)
expect PASS  "--host jake2"                'superset workspaces create --host 5f5aff3dec6113da62545abf567f8d10 --project p --branch b'
expect PASS  "--host jake1"                'superset ws create --host 896a8c9f35f5ec59973774b4262feb71 --project p --branch b'
expect PASS  "--host=id form"              'superset ws create --host=abc --project p --branch b'
expect PASS  "--host after other flags"    'superset ws create --project p --branch b --agent claude --host abc'
expect PASS  "--host id from variable"     'superset ws create --host "$H" --project p --branch b'
expect PASS  "bypass comment"              'superset ws create --local --project p --branch b # sswt-host-ok'

# Not a create (pass)
expect PASS  "workspaces list"             'superset workspaces list --local --json'
expect PASS  "terminals create"            'superset terminals create --local --workspace w'
expect PASS  "workspaces delete"           'superset workspaces delete w'
expect PASS  "unrelated command"           'git status'
expect PASS  "prose mentions create"       'git commit -m "docs: superset ws create needs --host"'
expect PASS  "prompt text mentions it"     'superset ws create --host abc --project p --branch b --prompt "run superset ws create --local later"'
expect PASS  "echo of the command"         "echo 'superset ws create --project p'"

# Machine gating (no SSWT_GUARD_FORCE): a non-jake1 hostname or non-work-mac profile never blocks.
gate() { # gate <hostname> <machine> → exit status
  local cfg; cfg=$(mktemp)
  jq -n --arg m "$2" '{machine:$m}' > "$cfg"
  local bin; bin=$(mktemp -d)
  printf '#!/bin/sh\necho %s\n' "$1" > "$bin/hostname"; chmod +x "$bin/hostname"
  jq -n '{tool_input:{command:"superset ws create --project p --branch b"}}' \
    | env -u AGENT_MACHINE PATH="$bin:$PATH" AGENT_PROFILES_CONFIG="$cfg" bash "$HOOK" >/dev/null 2>&1
  local rc=$?; rm -rf "$cfg" "$bin"; echo $rc
}
check() { # check <want-rc> <label> <hostname> <machine>
  local got; got=$(gate "$3" "$4")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $2 — want rc $1, got $got"; fi
}
check 2 "jake1 + work-mac blocks"          jake1 work-mac
check 0 "jake2 + work-mac allows"          jake2 work-mac
check 0 "jake1 + personal-mac allows"      jake1 personal-mac
check 0 "other host allows"                JXIV  personal-mac

echo "sswt-host-guard: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
