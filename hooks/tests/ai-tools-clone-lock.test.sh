#!/usr/bin/env bash
# Behaviour matrix for the working-tree lock. All repositories and chmods are
# temporary; snapshot the dev working tree so uncommitted changes are tested.
# Run: bash hooks/tests/ai-tools-clone-lock.test.sh (also /bin/bash on macOS).
SOURCE="$(cd "$(dirname "$0")/../.." && pwd -P)"
pass=0; fail=0
for dependency in git rsync python3; do
  command -v "$dependency" >/dev/null 2>&1 || { echo "FAIL: tests require $dependency"; exit 1; }
done
TMP_DIR=$(mktemp -d) || exit 1
TMP_DIR=$(cd "$TMP_DIR" && pwd -P)
trap 'chmod -R u+w "$TMP_DIR"; rm -rf "$TMP_DIR"' EXIT
# Prevent user Git hooks/signing settings from touching anything outside here.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME='Lock test' GIT_AUTHOR_EMAIL='lock-test@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
unset AI_TOOLS_CLONE_LOCK_HELD
export AI_TOOLS_CLONE_LOCK_QUIET=1 LC_ALL=C

expect() { # expect <exit code> <label> <command...>
  local wanted="$1" label="$2" got
  shift 2
  "$@" > "$TMP_DIR/stdout" 2> "$TMP_DIR/stderr"
  got=$?
  if [ "$got" = "$wanted" ]; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    echo "FAIL: $label — want exit $wanted, got $got"
    cat "$TMP_DIR/stdout" "$TMP_DIR/stderr"
  fi
}
expect_status() {
  local wanted="$1" code="$2" got output
  output=$("$BASH" "$HELPER" status 2> "$TMP_DIR/stderr"); got=$?
  if [ "$output" = "$wanted" ] && [ "$got" = "$code" ]; then
    pass=$((pass+1))
  else
    fail=$((fail+1)); echo "FAIL: status — want $wanted/$code, got $output/$got"
  fi
}
denied_write() {
  python3 - "$1" <<'PY'
import errno, sys
try:
    with open(sys.argv[1], "a") as f:
        f.write("unexpected write\n")
except OSError as e:
    sys.exit(0 if e.errno == errno.EACCES else 1)
sys.exit(1)
PY
}
append_file() { printf 'runtime\n' >> "$1"; }
clean_clone() { [ -z "$(git -C "$CLONE" status --porcelain)" ]; }
mutex_gone() { [ ! -e "$CLONE/.git/ai-tools-clone-lock.d" ]; }
contains() { [[ "$1" == *"$2"* ]]; }
stdin_roundtrip() {
  printf 'input\n' | "$BASH" "$HELPER" run -- "$BASH" -c 'IFS= read -r line && [ "$line" = input ]'
}

mkdir -p "$TMP_DIR/source"
rsync -a --exclude=.git "$SOURCE/" "$TMP_DIR/source/" || exit 1
git init -q "$TMP_DIR/source" || exit 1
git -C "$TMP_DIR/source" symbolic-ref HEAD refs/heads/main || exit 1
printf 'baseline\n' > "$TMP_DIR/source/lock-test.txt"
printf 'odd path\n' > "$TMP_DIR/source/--lock test"$'\n''file'
mkdir -p "$TMP_DIR/source/lock-test-subdir" "$TMP_DIR/outside"
printf 'baseline\n' > "$TMP_DIR/source/lock-test-subdir/tracked.txt"
printf 'outside\n' > "$TMP_DIR/outside/sentinel"
ln -s "$TMP_DIR/outside" "$TMP_DIR/source/lock-test-link"
mkdir -p "$TMP_DIR/source/skills/tracked-memory"
printf 'tracked runtime name\n' > "$TMP_DIR/source/skills/tracked-memory/memory.md"
git -C "$TMP_DIR/source" add . || exit 1
git -C "$TMP_DIR/source" add -f skills/tracked-memory/memory.md || exit 1
git -C "$TMP_DIR/source" commit -qm baseline || exit 1
git init --bare -q "$TMP_DIR/origin.git" || exit 1
git -C "$TMP_DIR/origin.git" symbolic-ref HEAD refs/heads/main || exit 1
git -C "$TMP_DIR/source" remote add origin "$TMP_DIR/origin.git" || exit 1
git -C "$TMP_DIR/source" push -q origin main || exit 1
git clone --no-hardlinks -q "$TMP_DIR/origin.git" "$TMP_DIR/writer" || exit 1

new_clone() {
  CLONE="$TMP_DIR/$1"
  git clone --no-hardlinks -q "$TMP_DIR/origin.git" "$CLONE" || exit 1
  export AI_TOOLS_HOME="$CLONE"
  HELPER="$CLONE/bin/ai-tools-clone-lock"
}

new_clone 'deploy clone'
# Runtime entries already present before locking get owner-write back.
mkdir -p "$CLONE/lib/__pycache__" "$CLONE/lock-test-subdir/__pycache__"
printf 'cache\n' > "$CLONE/lib/__pycache__/module.pyc"
ln -s "$TMP_DIR/outside" "$CLONE/lib/__pycache__/outside"
ln -s "$TMP_DIR/outside/sentinel" "$CLONE/.env"
rm -f "$CLONE/skills/session-hardening/memory.md"
expect_status unlocked 1
expect 0 'lock' "$BASH" "$HELPER" lock -q
expect 0 'lock twice' "$BASH" "$HELPER" lock -q
expect_status locked 0
expect 0 'tracked write returns EACCES' denied_write "$CLONE/lock-test.txt"
expect 0 'root creation returns EACCES' denied_write "$CLONE/new.txt"
expect 0 'subdir creation returns EACCES' denied_write "$CLONE/lock-test-subdir/new.txt"
expect 0 'forced tracked ignored file stays locked' denied_write "$CLONE/skills/tracked-memory/memory.md"
expect 0 'memory was created' test -f "$CLONE/skills/session-hardening/memory.md"
expect 0 'memory is appendable' append_file "$CLONE/skills/session-hardening/memory.md"
expect 0 'memory parent stays locked' denied_write "$CLONE/skills/session-hardening/new.txt"
expect 0 'existing cache is refreshable' append_file "$CLONE/lib/__pycache__/module.pyc"
expect 0 'cache directory can create entries' append_file "$CLONE/lib/__pycache__/new.pyc"
expect 0 'other existing cache directory can create entries' append_file "$CLONE/lock-test-subdir/__pycache__/new.pyc"
expect 0 'outside symlink targets were not locked' test -w "$TMP_DIR/outside/sentinel"
expect 0 'fetch while locked' git -C "$CLONE" fetch -q origin
expect 0 'update-ref while locked' git -C "$CLONE" update-ref refs/lock-test HEAD
expect 0 'locked clone remains clean' clean_clone
expect 0 'mutex released' mutex_gone

# Both partial states, NUL-delimited unusual names, and a symlinked root.
chmod u+w "$CLONE"
expect_status partial 1
chmod a-w "$CLONE"
chmod u+w "$CLONE/--lock test"$'\n''file'
expect_status partial 1
expect 0 'restore full lock' "$BASH" "$HELPER" lock -q
ln -s "$CLONE" "$TMP_DIR/clone-alias"
expect 0 'alias and trailing slashes' env AI_TOOLS_HOME="$TMP_DIR/clone-alias///" "$BASH" "$HELPER" lock -q

# Fast-forward changes the helper itself, exercising the final-line exit.
printf 'upstream\n' > "$TMP_DIR/writer/lock-test.txt"
printf 'added\n' > "$TMP_DIR/writer/lock-test-subdir/added.txt"
python3 - "$TMP_DIR/writer/bin/ai-tools-clone-lock" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace("# Keep the deploy working tree", "# Updated: keep the deploy working tree"))
PY
git -C "$TMP_DIR/writer" add lock-test.txt lock-test-subdir/added.txt bin/ai-tools-clone-lock || exit 1
git -C "$TMP_DIR/writer" commit -qm update || exit 1
git -C "$TMP_DIR/writer" push -q origin main || exit 1
expect 0 'fetch next update while locked' git -C "$CLONE" fetch -q origin
expect 0 'run fast-forward with helper rewrite' "$BASH" "$HELPER" run -- git -C "$CLONE" merge --ff-only origin/main
expect 0 'new upstream file exists' test -f "$CLONE/lock-test-subdir/added.txt"
expect_status locked 0
expect 0 'fast-forward stays clean' clean_clone
expect 0 'run preserves command stdin' stdin_roundtrip
expect_status locked 0
expect 1 'false exit propagates' "$BASH" "$HELPER" run -- false
expect_status locked 0
expect 37 'arbitrary command exit propagates' "$BASH" "$HELPER" run -- "$BASH" -c 'exit 37'
expect_status locked 0
expect 0 'nested run and held export' "$BASH" "$HELPER" run -- "$BASH" "$HELPER" run -- "$BASH" -c \
  '[ "$AI_TOOLS_CLONE_LOCK_HELD" = 1 ] && printf nested > "$AI_TOOLS_HOME/nested.txt"'
expect_status locked 0
expect 0 'nested run wrote successfully' test -f "$CLONE/nested.txt"
expect 0 'nested run mutex released' mutex_gone
expect 0 'unlock' "$BASH" "$HELPER" unlock -q
expect 0 'unlock twice' "$BASH" "$HELPER" unlock -q
expect_status unlocked 1
expect 0 'tracked writes restored' append_file "$CLONE/lock-test.txt"
expect 0 'new root files restored' append_file "$CLONE/unlocked.txt"
expect 0 'new subdir files restored' append_file "$CLONE/lock-test-subdir/unlocked.txt"

new_clone diverged
printf 'local\n' > "$CLONE/local.txt"
git -C "$CLONE" add local.txt || exit 1
git -C "$CLONE" commit -qm local || exit 1
expect 0 'lock diverged clone' "$BASH" "$HELPER" lock -q
printf 'next upstream\n' > "$TMP_DIR/writer/next.txt"
git -C "$TMP_DIR/writer" add next.txt || exit 1
git -C "$TMP_DIR/writer" commit -qm next || exit 1
git -C "$TMP_DIR/writer" push -q origin main || exit 1
git -C "$CLONE" fetch -q origin || exit 1
expect 128 'non-ff merge propagates failure' "$BASH" "$HELPER" run -- git -C "$CLONE" merge --ff-only origin/main
expect_status locked 0
expect 0 'failed merge stays clean' clean_clone

# Reclaim a crashed owner's mutex, including its ignored-entry scratch file.
mkdir "$CLONE/.git/ai-tools-clone-lock.d"
printf '99999999\n' > "$CLONE/.git/ai-tools-clone-lock.d/pid"
touch "$CLONE/.git/ai-tools-clone-lock.d/ignored"
expect 0 'stale mutex recovered' "$BASH" "$HELPER" lock -q
expect 0 'stale mutex released' mutex_gone
expect 0 'mutex serializes independent commands' python3 - "$BASH" "$HELPER" "$TMP_DIR/mutex-ready" <<'PY'
import os, subprocess, sys, time
bash, helper, ready = sys.argv[1:]
first = subprocess.Popen([bash, helper, "run", "--", bash, "-c",
                          'touch "$1"; sleep 2', "bash", ready])
try:
    deadline = time.monotonic() + 5
    while not os.path.exists(ready) and time.monotonic() < deadline:
        time.sleep(.02)
    assert os.path.exists(ready), "first command did not start"
    second = subprocess.Popen([bash, helper, "lock", "-q"])
    try:
        time.sleep(.2)
        assert second.poll() is None, "second command did not wait for mutex"
        assert first.wait(timeout=8) == 0
        assert second.wait(timeout=8) == 0
    finally:
        if second.poll() is None:
            second.terminate(); second.wait(timeout=8)
finally:
    if first.poll() is None:
        first.terminate(); first.wait(timeout=8)
PY
expect_status locked 0

for signal in INT TERM HUP; do
  expect 0 "$signal re-locks and releases mutex" python3 - "$BASH" "$HELPER" "$TMP_DIR/signal-ready-$signal" "$signal" <<'PY'
import os, signal, subprocess, sys, time
bash, helper, ready, name = sys.argv[1:]
p = subprocess.Popen([bash, helper, "run", "--", bash, "-c",
                      'touch "$1"; while :; do sleep 1; done', "bash", ready])
try:
    deadline = time.monotonic() + 5
    while not os.path.exists(ready) and time.monotonic() < deadline:
        time.sleep(.02)
    assert os.path.exists(ready), "command did not start"
    sig = getattr(signal, "SIG" + name)
    p.send_signal(sig)
    assert p.wait(timeout=8) == 128 + sig
finally:
    if p.poll() is None:
        p.terminate(); p.wait(timeout=8)
PY
  expect_status locked 0
  expect 0 "$signal mutex released" mutex_gone
done

mkdir "$TMP_DIR/not-a-clone"
expect 2 'refuse missing .git' env AI_TOOLS_HOME="$TMP_DIR/not-a-clone" "$BASH" "$HELPER" lock
refusal=$(cat "$TMP_DIR/stderr")
expect 0 'refusal explains .git requirement' contains "$refusal" '.git is not a directory'
expect 2 'missing subcommand' "$BASH" "$HELPER"
expect 2 'unknown subcommand' "$BASH" "$HELPER" surprise
expect 2 'run requires command' "$BASH" "$HELPER" run --

# Extract real installer functions instead of linking into the real ~/.claude.
for function in install_deploy_guard clone_lock update_ai_tools_home; do
  awk -v name="$function" '
    $0 == name "() {" { printing=1 }
    printing { print }
    printing && /^}$/ { exit }
  ' "$SOURCE/install.sh" >> "$TMP_DIR/install-functions"
done
. "$TMP_DIR/install-functions"
# These are used by the extracted installer functions.
# shellcheck disable=SC2034
AI_TOOLS_DEV_HINT="$TMP_DIR/source"
# shellcheck disable=SC2034
AI_TOOLS_REF=main
# shellcheck disable=SC2034
AI_TOOLS_REPO="$TMP_DIR/origin.git"
# shellcheck disable=SC2034
QUIET=1
expect 0 'install deploy guard while locked' install_deploy_guard
expect 1 'generated guard refuses' "$CLONE/.git/hooks/pre-commit"
guard_text=$(cat "$TMP_DIR/stderr")
expect 0 'generated guard names wrapped recovery command' contains "$guard_text" \
  "$CLONE/bin/ai-tools-clone-lock run -- git -C $CLONE checkout -- ."
expect 0 'read-only explanation present' contains "$guard_text" 'read-only on purpose'
clone_lock_fallback_test() (
  AI_TOOLS_HOME="$TMP_DIR/not-a-clone"
  clone_lock run -- "$BASH" -c 'exit 23'
  [ $? -eq 23 ] && clone_lock lock -q
)
expect 0 'old clone fallback executes command' clone_lock_fallback_test

new_clone installer-update
expect 0 'installer clone starts locked' "$BASH" "$HELPER" lock -q
printf 'installer update\n' > "$TMP_DIR/writer/installer-update.txt"
git -C "$TMP_DIR/writer" add installer-update.txt || exit 1
git -C "$TMP_DIR/writer" commit -qm installer-update || exit 1
git -C "$TMP_DIR/writer" push -q origin main || exit 1
expect 0 'installer fast-forward uses helper' update_ai_tools_home
expect 0 'installer picked up upstream file' test -f "$CLONE/installer-update.txt"
expect_status locked 0
expect 0 'installer clone stays clean' clean_clone

CLONE="$TMP_DIR/bootstrap"
export AI_TOOLS_HOME="$CLONE"
HELPER="$CLONE/bin/ai-tools-clone-lock"
expect 0 'initial clone gets locked' update_ai_tools_home
expect_status locked 0
expect 0 'bootstrap clone stays clean' clean_clone

# Exercise the actual last installer step for both deploy and dev modes.
awk '
  /^if \[ "\$DEV_MODE" -eq 0 \] && \[ -d "\$AI_TOOLS_HOME\/.git" \]; then/ { printing=1 }
  printing { print }
  printing && /^fi$/ { exit }
' "$SOURCE/install.sh" > "$TMP_DIR/install-tail"
expect 0 'installer final lock block found' test -s "$TMP_DIR/install-tail"
installer_tail_test() (
  # shellcheck disable=SC2034
  DEV_MODE="$1"
  . "$TMP_DIR/install-tail"
)
expect 0 'unlock before dev install' "$BASH" "$HELPER" unlock -q
expect 0 'dev mode skips final lock' installer_tail_test 1
expect_status unlocked 1
expect 0 'deploy mode locks as last step' installer_tail_test 0
expect_status locked 0

echo "ai-tools-clone-lock: $pass passed, $fail failed"
((fail == 0))
