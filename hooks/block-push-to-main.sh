#!/usr/bin/env bash
# Claude Code PreToolUse hook on Bash: blocks a git push whose DESTINATION
# resolves to the `main` branch. Pushes from the user's own terminal are
# unaffected — this only fires when Claude initiates the bash call.
# Opt-in per repo via a denylist (see step 0) — off everywhere else.
#
# CANONICAL COPY — vendored byte-identically in:
#   Concentro-Inc/woodrow        .claude/hooks/block-push-to-main.sh
#   Concentro-Inc/api            .claude/hooks/block-push-to-main.sh
#   Concentro-Inc/folio-platform .claude/hooks/block-push-to-main.sh
#   jnelken/ai-tools             hooks/block-push-to-main.sh  (global install)
# Changing one means changing all four. Verify with tests/block-push-to-main.test.sh,
# which sits beside this file in every home and builds its own fixture.
#
# WHAT THIS IS. A guardrail against an agent pushing to `main` by accident,
# not a sandbox against one trying to evade it. It reads the command text it
# is handed; it does not execute a shell parser, resolve variables, or expand
# `$(…)`. Anything that can only be reached by deliberately disguising a push
# is out of scope by construction — see KNOWN LIMITS at the bottom. The real
# backstop for that threat model is a server-side branch protection rule or a
# git `pre-push` hook, both of which see resolved refs instead of text.
#
# Things it deliberately does NOT do, because each produced a false block or
# a false pass in practice:
#
#   * It never scans the whole command string for the word `main`. A commit
#     message, a trailing `#` comment, or an unrelated chained command is not
#     a push target. Only the arguments of a push segment are read.
#   * It never treats `main` on the SOURCE side of a refspec as a push to
#     main: `origin main:release` writes to `release`.
#   * It does not assume the subcommand is the second word, so global options
#     (`-C <dir>`, `-c k=v`) do not hide the verb.
#   * It does not assume the push targets the repo the shell happens to sit
#     in. `git -C <dir> push`, `--git-dir`/`--work-tree`, `GIT_DIR=`, and a
#     `cd` in an earlier segment all move the target, and the denylist match
#     and branch lookup follow. Without this, `git -C <repo> push origin main`
#     run from /tmp bypassed the guard completely. A `--git-dir`/`GIT_DIR=`
#     path — bare repo included — identifies the repo by the git dir itself
#     and wins over `--work-tree`, which only relocates the working files.
#   * It does not treat every refspec-less push as a branch push: `--tags`
#     ships tags only, and `--help`/`--dry-run` write nothing at all.
#   * A `!`-shell alias's own arguments are not modeled — it is detected as a
#     push (so a bare push on `main` is still caught), but what it actually
#     pushes is opaque to us.
#
# The current branch comes from the push segment's effective directory, not
# from CLAUDE_PROJECT_DIR, which in a worktree session points at the root
# checkout and previously misread a feature branch as main.
set -u

# jq is needed both to parse the hook input and to emit the deny decision.
# Without it we can't do either — fail open rather than erroring on every
# Bash call. (Vendored copies run on machines we don't control.)
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)

# The directory the command runs in wins; the project dir is only a fallback.
default_dir="${cwd:-${CLAUDE_PROJECT_DIR:-.}}"

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# Shell quoting is not this hook's job to emulate, but a quoted or escaped ref
# must still be read as that ref: `origin "main"` and `origin ma\in` both reach
# git as `main`. Strip the quoting characters before comparing.
unquote() {
  local s="$1"
  s="${s//\"/}"
  s="${s//\'/}"
  s="${s//\\/}"
  printf '%s' "$s"
}

# Shell punctuation must not hide the verb or disguise the ref: `(git push …)`,
# `$(git push …)`, `` `git push …` `` and `{ git push …; }` all run a push, and
# the closing bracket would otherwise leave the last ref as `main)`.
strip_shell_punct() {
  local s="$1"
  while :; do
    case "$s" in
      '$('*) s="${s#\$(}" ;;
      '('*)  s="${s#(}" ;;
      '{'*)  s="${s#\{}" ;;
      '`'*)  s="${s#\`}" ;;
      *) break ;;
    esac
  done
  while :; do
    case "$s" in
      *')') s="${s%)}" ;;
      *'}') s="${s%\}}" ;;
      *'`') s="${s%\`}" ;;
      *';') s="${s%;}" ;;
      *) break ;;
    esac
  done
  printf '%s' "$s"
}

# 0. Only enforce in repos listed in the denylist. The denylist is a sibling of
# this script, so one file works in both homes:
#   * global install (~/.claude/hooks/) — denylist present, it names the repos
#     to guard. `dirname "$0"` is the symlink's directory, not its target, so
#     this finds ~/.claude/hooks/block-push-to-main.denylist.
#   * vendored in a repo (<repo>/.claude/hooks/) — no denylist; being committed
#     inside the repo IS the opt-in, so guard that repo and nothing else.
script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)" || exit 0
denylist="$script_dir/block-push-to-main.denylist"

# repo_name_of <dir> -> a stable identity for the repo at <dir>: the `origin`
# repo name when there is one, else the shared git dir. Identity, not path —
# every worktree and Superset workspace of one repo must answer the same, or a
# denylist entry would guard the main checkout and miss its worktrees. The
# `--git-common-dir` fallback is what makes that hold for a repo with no remote,
# where each worktree's toplevel basename differs.
repo_name_of() {
  local dir="$1" root remote name common
  # Gate on something that succeeds for a normal repo, a bare repo, and a
  # plain `.git` directory alike, so a `--git-dir`-style path is recognized
  # too — `--show-toplevel` alone would reject all three of those.
  git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || return 1
  root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)
  # A bare repo, or `$dir` sitting inside a `.git` directory itself, has no
  # work tree — `--show-toplevel` prints nothing there without failing.
  # Fall back to `$dir` itself so identity still resolves for either.
  [[ -n "$root" ]] || root="$dir"
  remote=$(git -C "$root" remote get-url origin 2>/dev/null || true)
  if [[ -n "$remote" ]]; then
    name="${remote%.git}"
    name="${name##*/}"
    printf '%s' "$name"
    return 0
  fi
  common=$(git -C "$root" rev-parse --git-common-dir 2>/dev/null || true)
  if [[ -n "$common" ]]; then
    case "$common" in
      /*) : ;;
      *) common="$(cd "$root" && cd "$(dirname "$common")" 2>/dev/null && pwd -P)/$(basename "$common")" ;;
    esac
    printf '%s' "$common"
    return 0
  fi
  printf '%s' "${root##*/}"
}

# is_guarded <dir> -> 0 when this hook should enforce for the repo at <dir>
is_guarded() {
  local repo_name line own_name
  repo_name=$(repo_name_of "$1") || return 1
  [[ -n "$repo_name" ]] || return 1

  if [[ ! -f "$denylist" ]]; then
    # Vendored mode: enforce only for the repo this script is committed in —
    # including its worktrees, which is why this compares repo identity and
    # not whether the script path sits under the target path.
    own_name=$(repo_name_of "$script_dir") || return 1
    [[ "$repo_name" == "$own_name" ]] && return 0
    return 1
  fi

  # `|| [[ -n "$line" ]]` so a final entry with no trailing newline still counts.
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [[ -z "$line" ]] && continue
    [[ "$line" == "$repo_name" ]] && return 0
  done < "$denylist"
  return 1
}

# 1. Cheap pre-filter; the real parse is below.
[[ "$cmd" == *git* ]] || exit 0

# git-level options that consume the NEXT token, so a global option does not
# hide the subcommand that follows it.
git_opt_takes_value() {
  case "$1" in
    -C | -c | --git-dir | --work-tree | --namespace | --exec-path) return 0 ;;
    *) return 1 ;;
  esac
}

# Push options that consume the NEXT token. Without these the value is misread
# as the remote, which shifts the real remote into the refspec slot and hides
# a no-refspec push to main.
push_opt_takes_value() {
  case "$1" in
    -o | --push-option | --receive-pack | --exec | --repo | --upload-pack) return 0 ;;
    *) return 1 ;;
  esac
}

# Ordinary subcommands, listed so the alias lookup below stays off the hot path
# — this hook runs on every Bash call and `git status` must not pay for a
# `git config` subprocess.
is_common_verb() {
  case "$1" in
    status|log|diff|show|add|commit|fetch|pull|rebase|merge|checkout|switch|restore|\
    branch|worktree|config|rev-parse|ls-files|ls-remote|stash|tag|remote|reset|\
    cherry-pick|describe|blame|grep|clean|init|clone|apply|symbolic-ref|for-each-ref|\
    update-ref|notes|bisect|reflog|submodule|archive|gc|fsck|am|format-patch|\
    shortlog|whatchanged|count-objects|check-ignore|cat-file|mv|rm|revert|help)
      return 0 ;;
    *) return 1 ;;
  esac
}

# A user alias can expand to a push (`alias.p = push`, or a `!f() { … git push
# … }` shell alias like the push-stack alias in this user's config). Only
# consulted for an unrecognized verb, so the common path stays subprocess-free.
# Leaves the expansion in ALIAS_EXPANSION so a plain alias's own arguments
# (e.g. `alias.publish = push origin main`) can be spliced in by the caller —
# a shell alias's arguments stay opaque to us either way.
ALIAS_EXPANSION=""
alias_is_push() {
  local verb="$1" dir="$2" expansion
  expansion=$(git -C "$dir" config --get "alias.$verb" 2>/dev/null) || return 1
  [[ -n "$expansion" ]] || return 1
  ALIAS_EXPANSION="$expansion"
  case "$expansion" in
    push|push\ *|*[!a-zA-Z0-9_-]push\ *|*[!a-zA-Z0-9_-]push) return 0 ;;
    *) return 1 ;;
  esac
}

# Is this refspec's DESTINATION the main branch?
# $2 is the current branch of the segment's effective directory.
targets_main() {
  local spec="$1" branch="$2" dst
  spec="${spec#+}"                       # leading + is force, not part of the ref
  if [[ "$spec" == *:* ]]; then
    dst="${spec##*:}"                    # everything after the last colon
    # An empty destination on a refspec with a colon is the "matching
    # branches" form (`:` or, force-prefixed, `+:`) — it pushes every local
    # branch that also exists on the remote, main included.
    [[ -z "$dst" ]] && return 0
  else
    dst="$spec"                          # no colon: src and dst are the same
  fi
  # A wildcard destination denies only when its pattern can actually reach
  # main — tested both before and after stripping `refs/heads/`, since the
  # pattern may or may not include that prefix itself. A feature-scoped
  # wildcard like `refs/heads/feature/*` cannot match either form.
  case "$dst" in
    *'*'*)
      # shellcheck disable=SC2053  # $dst is deliberately an unquoted glob here
      [[ main == $dst ]] && return 0
      # shellcheck disable=SC2053  # same: pattern match, not string equality
      [[ refs/heads/main == $dst ]] && return 0
      return 1
      ;;
  esac
  dst="${dst#refs/heads/}"
  # `origin HEAD` while on main pushes main — the refspec never says so.
  [[ "$dst" == "HEAD" && "$branch" == "main" ]] && return 0
  [[ "$dst" == "main" ]]
}

# 2. Inspect each segment on its own. Splitting first is what keeps a commit
# message or an unrelated command on the same line out of the argument list.
segments=$(printf '%s' "$cmd" | tr '\n;|&' '\n\n\n\n')
running_dir="$default_dir"
while IFS= read -r segment; do
  read -r -a raw_tokens <<<"$segment"
  (( ${#raw_tokens[@]} )) || continue

  # Normalize: drop quoting and any leading shell punctuation, so `(git`,
  # `$(git`, `'git` and `` `git `` are all recognized as the git token.
  tokens=()
  for t in "${raw_tokens[@]}"; do
    tokens+=("$(strip_shell_punct "$(unquote "$t")")")
  done

  # `cd <dir> && git push …` splits into two segments; carry the directory
  # forward so the second one is judged against the repo it actually enters.
  if [[ "${tokens[0]}" == "cd" && -n "${tokens[1]:-}" ]]; then
    case "${tokens[1]}" in
      /*) running_dir="${tokens[1]}" ;;
      -|'~'*) : ;;
      *) running_dir="$running_dir/${tokens[1]}" ;;
    esac
  fi

  # `bash -c 'cd /guarded && git push origin main'` hides the `cd` inside a
  # nested shell rather than as this segment's own first token — but the `&&`
  # inside that quoted string still splits the segment (the splitter has no
  # notion of quoting), so the `cd` and its target are tokens of THIS segment,
  # just not at position 0.
  case "${tokens[0]}" in
    bash | sh | zsh | */bash | */sh | */zsh)
      for (( j = 1; j < ${#tokens[@]}; j++ )); do
        if [[ "${tokens[j]}" == "cd" && -n "${tokens[j+1]:-}" ]]; then
          case "${tokens[j+1]}" in
            /*) running_dir="${tokens[j+1]}" ;;
            -|'~'*) : ;;
            *) running_dir="$running_dir/${tokens[j+1]}" ;;
          esac
          break
        fi
      done
      ;;
  esac

  [[ "$segment" == *git* ]] || continue

  # Walk to the subcommand. Environment assignments may precede `git`; capture
  # the two that relocate the repo.
  env_gitdir=""
  env_worktree=""
  i=0
  while (( i < ${#tokens[@]} )) && [[ "${tokens[i]}" != "git" && "${tokens[i]}" != */git ]]; do
    case "${tokens[i]}" in
      GIT_WORK_TREE=*) env_worktree="${tokens[i]#GIT_WORK_TREE=}" ;;
      GIT_DIR=*) env_gitdir="${tokens[i]#GIT_DIR=}" ;;
    esac
    i=$((i + 1))
  done
  (( i < ${#tokens[@]} )) || continue
  i=$((i + 1))

  # Skip git's own options, capturing the ones that name a different repo.
  # `--git-dir` (or `GIT_DIR=`) identifies the repo by the git dir path
  # itself — unlike the old `dirname` reduction, this also works for a bare
  # repo (where the git dir IS the repo) and for a plain `.git` directory —
  # and it wins over `--work-tree` regardless of which comes first on the
  # line, since `--work-tree` only relocates the working files git-dir found.
  opt_gitdir=""
  opt_worktree=""
  opt_c=""
  while (( i < ${#tokens[@]} )); do
    case "${tokens[i]}" in
      -C)            opt_c="${tokens[i+1]:-}"; i=$((i + 1)) ;;
      --work-tree)   opt_worktree="${tokens[i+1]:-}"; i=$((i + 1)) ;;
      --work-tree=*) opt_worktree="${tokens[i]#--work-tree=}" ;;
      --git-dir)     opt_gitdir="${tokens[i+1]:-}"; i=$((i + 1)) ;;
      --git-dir=*)   opt_gitdir="${tokens[i]#--git-dir=}" ;;
      -*) git_opt_takes_value "${tokens[i]}" && i=$((i + 1)) ;;
      *) break ;;
    esac
    i=$((i + 1))
  done

  verb="${tokens[i]:-}"
  [[ -n "$verb" ]] || continue

  # Which repo does THIS segment write to? Precedence: --git-dir option, then
  # GIT_DIR=, then --work-tree option, then GIT_WORK_TREE=, then -C, then the
  # running cwd. Resolve a relative override against the running cwd.
  work_dir="${opt_gitdir:-${env_gitdir:-${opt_worktree:-${env_worktree:-${opt_c:-$running_dir}}}}}"
  case "$work_dir" in /*) : ;; *) work_dir="$running_dir/$work_dir" ;; esac

  if [[ "$verb" != "push" ]]; then
    is_common_verb "$verb" && continue
    alias_is_push "$verb" "$work_dir" || continue
    case "$ALIAS_EXPANSION" in
      push | push\ *)
        # Plain alias: splice its own arguments in front of whatever
        # followed the alias invocation on the command line, so
        # `alias.publish = push origin main` then `git publish` is judged
        # exactly as `git push origin main` would be. `tokens` gets the
        # dequoted words (git dequotes a plain alias's own arguments);
        # `raw_tokens` gets the literal words, keeping the two arrays
        # aligned for the `#`-comment check later.
        read -r -a alias_raw_words <<<"$ALIAS_EXPANSION"
        alias_args_raw=()
        (( ${#alias_raw_words[@]} > 1 )) && alias_args_raw=("${alias_raw_words[@]:1}")
        alias_args=()
        if (( ${#alias_args_raw[@]} )); then
          for w in "${alias_args_raw[@]}"; do
            alias_args+=("$(strip_shell_punct "$(unquote "$w")")")
          done
        fi
        if (( ${#alias_args[@]} )); then
          tokens=("${tokens[@]:0:i+1}" "${alias_args[@]}" "${tokens[@]:i+1}")
          raw_tokens=("${raw_tokens[@]:0:i+1}" "${alias_args_raw[@]}" "${raw_tokens[@]:i+1}")
        fi
        ;;
      *)
        # A `!`-shell alias, or a plain alias not shaped like a bare push:
        # its own arguments are opaque to us — fall through and judge it as
        # a bare push with no refspec.
        : ;;
    esac
  fi
  i=$((i + 1))

  is_guarded "$work_dir" || continue
  current_branch=$(git -C "$work_dir" symbolic-ref --short HEAD 2>/dev/null || true)

  remote_seen=0
  refspecs=()
  all_branches=0
  all_branches_tok=""
  tags_only=0
  inert=0
  while (( i < ${#tokens[@]} )); do
    tok="${tokens[i]}"
    raw="${raw_tokens[i]:-$tok}"
    i=$((i + 1))
    # An unquoted `#` starts a shell comment — everything after it is prose,
    # not arguments. Tested on the raw token so a quoted "#" isn't mistaken
    # for one.
    [[ "$raw" == \#* ]] && break
    [[ -z "$tok" ]] && continue
    case "$tok" in
      --all | --branches | --mirror)
        # `--branches` is git's documented synonym for `--all`. Deferred
        # until after the loop so a `--dry-run`/`-n` anywhere else on the
        # line can still make the whole invocation inert.
        all_branches=1
        all_branches_tok="$tok"
        ;;
      --help | -h | --dry-run | -n)
        # Writes nothing — not a push at all.
        inert=1
        ;;
      --tags)
        tags_only=1
        ;;
      -*)
        # An option that takes a value, quoted with a space in it, splits
        # across several raw tokens on our whitespace-only tokenizer
        # (`-o "ci skip"` -> `-o`, `"ci`, `skip"`; `--push-option="a b"` ->
        # `--push-option="a`, `b"`). Left unmerged, the tail is misread as
        # the remote or a refspec. Merge by tracking the opening quote and
        # consuming tokens until one closes it.
        opt_name="$tok"
        attached_val_raw=""
        if [[ "$tok" == *=* ]]; then
          opt_name="${tok%%=*}"
          attached_val_raw="${raw#*=}"
        fi
        if push_opt_takes_value "$opt_name"; then
          if [[ "$tok" == *=* ]]; then
            merge_val="$attached_val_raw"
          else
            merge_val="${raw_tokens[i]:-}"
            i=$((i + 1))          # separate form: consume the value token
          fi
          qchar=""
          case "$merge_val" in
            '"'*) qchar='"' ;;
            "'"*) qchar="'" ;;
          esac
          if [[ -n "$qchar" ]]; then
            while [[ ( ${#merge_val} -le 1 || "${merge_val: -1}" != "$qchar" ) && $i -lt ${#tokens[@]} ]]; do
              merge_val="$merge_val ${raw_tokens[i]}"
              i=$((i + 1))
            done
          fi
        fi
        ;;
      *)
        if (( remote_seen == 0 )); then
          remote_seen=1          # first bare token is the remote
        else
          refspecs+=("$tok")
        fi
        ;;
    esac
  done

  (( inert )) && continue

  if (( all_branches )); then
    deny "\`git push $all_branches_tok\` pushes every branch, including main. Run this from your terminal yourself."
  fi

  if (( ${#refspecs[@]} == 0 )); then
    # No refspec: the push follows the current branch's upstream — unless it
    # is a tags-only push, which never updates a branch.
    (( tags_only )) && continue
    if [[ "$current_branch" == "main" ]]; then
      deny "Current branch is main and this push has no refspec. Pushes to main are reserved for the user to run from the terminal."
    fi
    continue
  fi

  for spec in "${refspecs[@]}"; do
    if targets_main "$spec" "$current_branch"; then
      deny "Detected a push targeting the main branch (\`$spec\`). Pushes to main are reserved for the user to run from the terminal."
    fi
  done
done <<<"$segments"

exit 0

# KNOWN LIMITS — deliberate, given the threat model at the top.
#
#   * It matches on literal text, so a command that merely *contains* a
#     push-to-main pattern is blocked: a heredoc writing a script, a quoted
#     example, an editor payload, a commit message pairing the words. That is
#     the safe direction; write such files with the Write tool instead.
#     Narrowing it would open `bash <<'EOF' … EOF` as a real bypass.
#   * It does not expand variables or command substitution, so a push whose
#     ref only exists after expansion (`git push origin "$B"`) is not seen.
#     Closing this would require executing the command to find out what it
#     does, which is the thing a PreToolUse hook exists to avoid.
#   * Quote characters are stripped rather than parsed, so a branch whose name
#     genuinely contains a quote or backslash is compared without it.
