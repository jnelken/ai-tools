#!/usr/bin/env bash
# Behaviour matrix for block-push-to-main.sh. Run: bash tests/block-push-to-main.test.sh
#
# Self-contained: it builds its own fixture repos and its own denylist, so it
# does not depend on ~/.claude/hooks/block-push-to-main.denylist and can run
# against a vendored copy in any repo.
#
# NOTE for anyone editing this file from a Claude session: the case strings
# below contain literal `push`/`main` text, which is exactly what the live hook
# blocks. Write this file with the Write tool, not a shell heredoc.
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/block-push-to-main.sh}"
pass=0; fail=0

# The hook finds its denylist as a sibling of itself. To exercise denylist mode
# without touching the real one, run the hook through a shim directory that has
# a copy of the script plus a test denylist. Written with no trailing newline on
# the last entry on purpose — a `read` loop that drops it would leave `api`
# unguarded, and the `api` cases below would fail.
SHIM=$(mktemp -d "${TMPDIR:-/tmp}/block-push-shim-$$-XXXXXX")
cp "$HOOK" "$SHIM/block-push-to-main.sh"
printf '# test denylist\nwoodrow\napi' > "$SHIM/block-push-to-main.denylist"
HOOK_DENYLIST="$SHIM/block-push-to-main.sh"

# Run the hook the way Claude Code does: from the directory it reports as cwd,
# so a relative `-C .` resolves against the fixture and not the checkout that
# happens to be running the tests.
#
# Classification is strict: DENY only on exit 0 with a `deny` decision in
# stdout, ALLOW only on exit 0 with empty stdout. Anything else (a non-zero
# exit — e.g. a crash like an unbound-variable error — or exit 0 with
# unexpected stdout) is ERROR(status=N) and never silently counted as ALLOW.
run() { # run <hook> <cwd> <cmd> -> prints "DENY", "ALLOW", or "ERROR(status=N)"
  local out status
  out=$( cd "$2" 2>/dev/null && jq -n --arg c "$3" --arg d "$2" \
           '{tool_input:{command:$c},cwd:$d}' | bash "$1" )
  status=$?
  if (( status == 0 )) && [[ -z "$out" ]]; then
    echo ALLOW
  elif (( status == 0 )) && printf '%s' "$out" | jq -e '.hookSpecificOutput.permissionDecision == "deny"' >/dev/null 2>&1; then
    echo DENY
  else
    echo "ERROR(status=$status)"
  fi
}

check() { # check <expected> <cwd> <cmd> <label>
  local got; got=$(run "$HOOK_DENYLIST" "$2" "$3")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); printf '  ok   %-6s %s\n' "$got" "$4"
  else fail=$((fail+1)); printf '  FAIL want=%s got=%s  %s\n' "$1" "$got" "$4"; fi
}

check_vendored() { # same, but through a copy with NO sibling denylist
  local got; got=$(run "$VENDORED" "$2" "$3")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); printf '  ok   %-6s %s\n' "$got" "$4"
  else fail=$((fail+1)); printf '  FAIL want=%s got=%s  %s\n' "$1" "$got" "$4"; fi
}

check_noremote() { # through a copy vendored inside the no-origin fixture
  local got; got=$(run "$NOREMOTE_HOOK" "$2" "$3")
  if [[ "$got" == "$1" ]]; then pass=$((pass+1)); printf '  ok   %-6s %s\n' "$got" "$4"
  else fail=$((fail+1)); printf '  FAIL want=%s got=%s  %s\n' "$1" "$got" "$4"; fi
}

# Fixture: three repos with an `origin` whose name drives the denylist match,
# plus a worktree on a feature branch (the case CLAUDE_PROJECT_DIR got wrong).
FIXTURE=$(mktemp -d "${TMPDIR:-/tmp}/block-push-fixture-$$-XXXXXX")
trap 'rm -rf "$FIXTURE" "$SHIM"' EXIT
for r in woodrow api personal-thing; do
  mkdir -p "$FIXTURE/$r"
  git -C "$FIXTURE/$r" init -q -b main
  git -C "$FIXTURE/$r" config user.name test
  git -C "$FIXTURE/$r" config user.email test@example.com
  git -C "$FIXTURE/$r" remote add origin "git@github.com:Concentro-Inc/$r.git"
  git -C "$FIXTURE/$r" commit -q --allow-empty -m init
  git -C "$FIXTURE/$r" branch feature/thing
done
git -C "$FIXTURE/woodrow" worktree add -q "$FIXTURE/wt-feature" feature/thing

W="$FIXTURE/woodrow"        # on main, denylisted
A="$FIXTURE/api"            # on main, denylisted (last denylist line, no newline)
F="$FIXTURE/wt-feature"     # worktree of woodrow, on a feature branch
P="$FIXTURE/personal-thing" # on main, NOT denylisted
OUT="$FIXTURE"              # a directory that is not a git repo at all

# A vendored copy: lives inside the woodrow fixture, has no sibling denylist.
mkdir -p "$W/.claude/hooks"
cp "$HOOK" "$W/.claude/hooks/block-push-to-main.sh"
VENDORED="$W/.claude/hooks/block-push-to-main.sh"

# A repo with NO origin remote, plus a worktree of it — identity must still
# match across both, or a vendored hook would guard only the main checkout.
NR="$FIXTURE/noremote"
mkdir -p "$NR/.claude/hooks"
git -C "$NR" init -q -b main
git -C "$NR" config user.name test
git -C "$NR" config user.email test@example.com
git -C "$NR" commit -q --allow-empty -m init
git -C "$NR" branch feature/thing
git -C "$NR" worktree add -q "$FIXTURE/nr-wt" feature/thing
cp "$HOOK" "$NR/.claude/hooks/block-push-to-main.sh"
NOREMOTE_HOOK="$NR/.claude/hooks/block-push-to-main.sh"
NRW="$FIXTURE/nr-wt"

# A bare clone of woodrow — has no work tree at all, for exercising
# --git-dir/GIT_DIR= against a repo shape --show-toplevel can't see.
BARE="$FIXTURE/woodrow.git"
git clone -q --bare "$W" "$BARE"
git -C "$BARE" config remote.origin.url git@github.com:Concentro-Inc/woodrow.git

# A guarded repo whose path itself has a space, for exercising the
# quote-aware tokenizer against `-C`/`--git-dir` values that need it.
mkdir -p "$FIXTURE/with space"
git clone -q "$W" "$FIXTURE/with space/woodrow"
git -C "$FIXTURE/with space/woodrow" config remote.origin.url git@github.com:Concentro-Inc/woodrow.git
SPACED="$FIXTURE/with space/woodrow"

# An alias that expands to push, in the woodrow fixture only.
git -C "$W" config alias.p push
git -C "$W" config alias.shippit '!f() { git push --force-with-lease origin HEAD; }; f'

# Aliases carrying their own push arguments, on the feature-branch worktree.
git -C "$F" config alias.publish 'push origin main'
git -C "$F" config alias.deploy 'push origin HEAD:main'
git -C "$F" config alias.pubfeat 'push origin HEAD'

echo "-- true positives (must DENY)"
check DENY  "$W" 'git push origin main' 'explicit main'
check DENY  "$W" 'git push origin HEAD:main' 'HEAD-colon-main'
check DENY  "$W" 'git push --force origin +main' 'force plus-main'
check DENY  "$W" 'git push origin refs/heads/main' 'refs-heads-main'
check DENY  "$W" 'git push origin :main' 'delete main'
check DENY  "$W" 'git push' 'no refspec while on main'
check DENY  "$W" 'git push -u origin HEAD' 'HEAD while on main'
check DENY  "$W" 'git push --all origin' 'all branches'
check DENY  "$W" 'git push --mirror origin' 'mirror'
check DENY  "$W" 'git -C . push origin main' 'git -C dot before verb'
check DENY  "$W" 'git status && git push origin main' 'second segment'
check DENY  "$W" 'git push -o ci.skip origin' 'flag value, no refspec, on main'
check DENY  "$W" 'git push origin feature/thing:refs/heads/main' 'feature source, main destination'
check DENY  "$A" 'git push origin main' 'last denylist entry, no trailing newline'

echo "-- quoted and escaped refs"
check DENY  "$W" 'git push origin "main"' 'double-quoted main'
check DENY  "$W" "git push origin 'main'" 'single-quoted main'
check DENY  "$W" 'git push "origin" "main"' 'quoted remote and ref'
check DENY  "$W" 'git push origin "HEAD:main"' 'quoted HEAD-colon-main'
check DENY  "$W" 'git push origin "refs/heads/main"' 'quoted refs-heads-main'
check DENY  "$W" 'git push origin feat:"main"' 'quoted destination half'
check DENY  "$W" 'git push origin ma\in' 'backslash-escaped main'

echo "-- wildcard and all-branch forms"
check DENY  "$W" 'git push origin "refs/heads/*:refs/heads/*"' 'wildcard refspec'
check DENY  "$F" 'git push origin "refs/heads/*:refs/heads/*"' 'wildcard from a feature branch'
check DENY  "$F" 'git push --branches origin' 'branches is an alias for all'

echo "-- nested shell syntax"
check DENY  "$W" '(git push origin main)' 'subshell'
check DENY  "$W" 'echo "$(git push origin main)"' 'command substitution'
check DENY  "$W" "sh -c 'git push origin main'" 'sh -c'
check DENY  "$W" '{ git push origin main; }' 'brace group'

echo "-- the target repo is not the shell's directory"
check DENY  "$OUT" "git -C $W push origin main" 'guarded repo via -C, cwd not a repo'
check DENY  "$P"   "git -C $W push origin main" 'guarded repo via -C, cwd an unguarded repo'
check DENY  "$OUT" "git -C $W push" 'guarded repo via -C, no refspec, on main'
check ALLOW "$W"   "git -C $P push origin main" 'unguarded repo via -C, cwd guarded'
check ALLOW "$W"   "git -C $F push origin feature/thing" 'guarded repo via -C, feature branch'
check DENY  "$OUT" "git -C $P -C $W push origin main" 'last -C wins'
check DENY  "$OUT" "cd $W && git push origin main" 'cd into a guarded repo first'
check ALLOW "$W"   "cd $P && git push origin main" 'cd into an unguarded repo first'
check DENY  "$OUT" "git --git-dir=$W/.git --work-tree=$W push origin main" 'git-dir and work-tree'
check DENY  "$OUT" "GIT_DIR=$W/.git git push origin main" 'GIT_DIR env assignment'

echo "-- aliases that expand to push"
check DENY  "$W" 'git p origin main' 'alias p = push'
check DENY  "$W" 'git shippit' 'shell alias containing a push, on main'
check ALLOW "$F" 'git shippit' 'same alias on a feature branch'
check ALLOW "$W" 'git status' 'common verb, no alias lookup'

echo "-- refspec-less pushes that do not write a branch (must ALLOW)"
check ALLOW "$W" 'git push --tags origin' 'tags only, on main'
check DENY  "$W" 'git push --tags origin main' 'tags plus an explicit main refspec'
check ALLOW "$W" 'git push --help' 'help is not a push'
check ALLOW "$W" 'git push --dry-run origin' 'dry run writes nothing'

echo "-- false positives the old rule hit (must ALLOW)"
check ALLOW "$F" 'git push origin feature/thing' 'feature branch by name'
check ALLOW "$F" 'git push' 'no refspec, worktree on feature'
check ALLOW "$F" 'git commit -m "sync with main" && git push origin HEAD' 'main in commit message'
check ALLOW "$F" 'git push origin HEAD  # rebased onto main' 'main in trailing comment'
check ALLOW "$F" 'git log main..HEAD && git push origin feature/thing' 'main in sibling command'
check ALLOW "$F" 'git diff main...HEAD | head; git push origin HEAD' 'main in piped command'
check ALLOW "$F" 'git push origin main:release-candidate' 'main as SOURCE only'
check ALLOW "$F" 'git push origin main-ish' 'branch prefixed main'
check ALLOW "$F" 'git push --force-with-lease origin feature/thing' 'force-with-lease feature'
check ALLOW "$F" 'git push --push-option="deploy main" origin feature/thing' 'main inside a flag value'

echo "-- repo not on the denylist (must ALLOW)"
check ALLOW "$P" 'git push origin main' 'personal repo'
check ALLOW "$OUT" 'git push origin main' 'not a git repo at all'

echo "-- vendored copy: no denylist, guards only the repo it lives in"
check_vendored DENY  "$W" 'git push origin main' 'vendored guards its own repo'
check_vendored DENY  "$F" 'git push origin main' 'vendored guards its own worktree'
check_vendored ALLOW "$P" 'git push origin main' 'vendored ignores a different repo'
check_vendored ALLOW "$F" 'git push origin feature/thing' 'vendored allows feature branch'

echo "-- repo identity survives having no origin remote"
check_noremote DENY  "$NR"  'git push origin main' 'no-origin repo, main checkout'
check_noremote DENY  "$NRW" 'git push origin main' 'no-origin repo, worktree at another path'
check_noremote ALLOW "$NRW" 'git push origin feature/thing' 'no-origin worktree, feature branch'
check_noremote ALLOW "$P"   'git push origin main' 'no-origin vendored copy ignores other repos'

echo "-- matching refspec (bare colon forms push/delete everything)"
check DENY  "$W" 'git push origin :' 'bare colon refspec pushes every matching branch'
check DENY  "$W" 'git push origin +:' 'force bare colon refspec'
check ALLOW "$W" 'git push origin :feature-x' 'delete a non-main branch'

echo "-- wildcards that cannot reach main (must ALLOW)"
check DENY  "$W" "git push origin 'ma*'" 'wildcard destination that can match main'
check ALLOW "$W" "git push origin 'refs/heads/feature/*:refs/heads/feature/*'" 'wildcard scoped away from main'

echo "-- --dry-run makes --all/--branches/--mirror inert (must ALLOW)"
check ALLOW "$W" 'git push --dry-run --all origin' 'dry-run before all'
check ALLOW "$W" 'git push --all --dry-run origin' 'dry-run after all'

echo "-- --git-dir identifies the repo by the git dir itself"
check DENY  "$OUT" "git --git-dir=$BARE push origin main" 'bare repo via --git-dir'
check DENY  "$OUT" "GIT_DIR=$BARE git push origin main" 'bare repo via GIT_DIR='
check DENY  "$OUT" "git --git-dir=$W/.git --work-tree=/tmp push origin main" 'git-dir wins over a mismatched work-tree'

echo "-- quoted option values containing spaces"
check DENY  "$W" 'git push -o "ci skip" origin' 'quoted -o value, no refspec, on main'
check ALLOW "$W" 'git push --push-option "deploy ci main" origin feature' 'quoted separate push-option value'
check ALLOW "$W" 'git push --receive-pack "git receive-pack" origin feature' 'quoted receive-pack value'

echo "-- aliases that carry their own push arguments"
check DENY  "$F" 'git publish' 'alias with args: publish = push origin main'
check DENY  "$F" 'git deploy' 'alias with args: deploy = push origin HEAD:main'
check ALLOW "$F" 'git pubfeat' 'alias with args: pubfeat = push origin HEAD, feature branch'

echo "-- cd hidden inside a nested shell"
check DENY  "$OUT" "bash -c 'cd $W && git push origin main'" 'cd inside bash -c reaches a guarded repo'
check ALLOW "$W"   "bash -c 'cd $P && git push origin main'" 'cd inside bash -c reaches an unguarded repo'

echo "-- quote-aware tokenizer: a repo path containing a space"
check DENY  "$OUT" "git -C '$SPACED' push origin main" 'quoted -C value with an embedded space'
check DENY  "$OUT" "git --git-dir='$SPACED/.git' push origin main" 'quoted --git-dir value with an embedded space'
check DENY  "$OUT" "cd '$SPACED' && git push origin main" 'cd into a quoted path with an embedded space'

echo "-- --work-tree/GIT_WORK_TREE never select the repo"
check DENY  "$OUT" "git -C $W --work-tree=/tmp push origin main" '-C selects the repo; --work-tree is ignored'
check DENY  "$OUT" "git -C $FIXTURE -C woodrow push origin main" 'chained relative -C resolves against the previous one'
check DENY  "$P"   "git -C $FIXTURE -C woodrow push origin main" 'chained relative -C, cwd different from the first -C target'

echo "-- --recurse-submodules takes a value"
check DENY  "$W" 'git push --recurse-submodules on-demand origin' 'recurse-submodules value not misread as the refspec'
check ALLOW "$W" 'git push --recurse-submodules on-demand origin feature' 'recurse-submodules with an explicit non-main refspec'

echo "-- --repo names the remote explicitly"
check DENY  "$F" 'git push --repo origin main' '--repo (separate form) names the remote, main refspec follows'
check DENY  "$F" 'git push --repo=origin main' '--repo=origin (attached form) names the remote, main refspec follows'
check ALLOW "$F" 'git push --repo origin feature' '--repo names the remote, non-main refspec follows'

echo "-- line continuations are joined before splitting"
check DENY  "$F" $'git push origin HEAD \\\nmain' 'push split across an escaped newline'
check DENY  "$F" $'git push origin ma\\\nin' 'backslash-newline pair is deleted, not replaced with a space (ma\\+nl+in = main)'

echo "-- configured remote.<name>.push refspecs on a refspec-less push"
git -C "$F" config remote.origin.push 'HEAD:refs/heads/main'
check DENY  "$F" 'git push origin' 'configured remote.push maps HEAD to main from a feature branch'
git -C "$F" config --unset remote.origin.push

echo "-- segments split AFTER tokenizing, not before (quoted separators are not commands)"
check ALLOW "$W" "git commit -m 'release; git push origin main'" 'quoted separator inside a commit message'
check ALLOW "$W" 'echo "git push origin main"' 'quoted push text is not executed'
check DENY  "$W" 'git status; git push origin main' 'unquoted separator still splits'
check DENY  "$W" $'git status  # don\'t worry\ngit push origin main' 'comment apostrophe does not swallow the following push'

echo "-- same-name refspec: empty destination with a non-empty source"
check ALLOW "$F" 'git push origin feature:' 'same-name push via empty destination'
check ALLOW "$F" 'git push origin HEAD:' 'HEAD-colon-empty resolves to the current branch, feature'
check DENY  "$W" 'git push origin HEAD:' 'HEAD-colon-empty resolves to the current branch, main'
check DENY  "$F" 'git push origin main:' 'main as source with an empty (same-name) destination'

echo "-- refspec-less push with no explicit remote, resolved via @{push}"
git -C "$F" update-ref refs/remotes/origin/main refs/heads/main
git -C "$F" config branch.feature/thing.remote origin
git -C "$F" config remote.origin.push 'refs/heads/feature/thing:refs/heads/main'
check DENY "$F" 'git push' '@{push} resolves through branch.remote + remote.push to main'
git -C "$F" config --unset branch.feature/thing.remote
git -C "$F" config --unset remote.origin.push
git -C "$F" update-ref -d refs/remotes/origin/main

git -C "$F" config push.default matching
check DENY "$F" 'git push' 'push.default=matching is denied outright, even from a feature branch'
git -C "$F" config --unset push.default

echo "-- @{push} comparison uses the whole branch name, not just its last path component"
git -C "$F" update-ref refs/remotes/origin/release/main refs/heads/feature/thing
git -C "$F" config branch.feature/thing.remote origin
git -C "$F" config branch.feature/thing.merge refs/heads/release/main
git -C "$F" config push.default upstream
check ALLOW "$F" 'git push' '@{push} resolves to origin/release/main, which is not main'
git -C "$F" config --unset branch.feature/thing.remote
git -C "$F" config --unset branch.feature/thing.merge
git -C "$F" config --unset push.default
git -C "$F" update-ref -d refs/remotes/origin/release/main

echo "-- refspec-less push WITH an explicit remote: configured remote.push wins over the branch fallback"
git -C "$W" config remote.origin.push 'HEAD:release'
check ALLOW "$W" 'git push origin' 'configured remote.push redirects away from main even though branch is main'
git -C "$W" config --unset remote.origin.push

echo
echo "pass=$pass fail=$fail"
[[ $fail -eq 0 ]]
