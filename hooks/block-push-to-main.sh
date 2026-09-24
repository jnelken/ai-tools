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
#     message (quoted, even one containing a literal `;`/`&`/`|`), an echoed
#     example, a trailing `#` comment, or an unrelated chained command is not
#     a push target. Only the arguments of a push segment are read, and a
#     segment boundary is only ever an UNQUOTED `;`/`&`/`&&`/`||`/`|`/newline.
#   * It never treats `main` on the SOURCE side of a refspec as a push to
#     main: `origin main:release` writes to `release`. A same-name refspec
#     with an empty destination (`origin feature:`) is likewise read as
#     pushing to a branch named `feature`, not to `main` — `origin HEAD:`
#     resolves to the CURRENT branch's name, so it only denies when that
#     branch is `main`.
#   * It does not assume the subcommand is the second word, so global options
#     (`-C <dir>`, `-c k=v`) do not hide the verb.
#   * It does not assume the push targets the repo the shell happens to sit
#     in. `git -C <dir> push`, `--git-dir`/`GIT_DIR=`, and a `cd` in an
#     earlier segment all move the target, and the denylist match and branch
#     lookup follow. Without this, `git -C <repo> push origin main` run from
#     /tmp bypassed the guard completely. Precedence: a `--git-dir`/`GIT_DIR=`
#     path (bare repo included) identifies the repo by the git dir itself and
#     wins outright; otherwise `-C` selects it, resolved cumulatively (each
#     `-C` relative to the previous one, and the first relative to the
#     segment's running cwd); otherwise the running cwd. `--work-tree`/
#     `GIT_WORK_TREE=` play no part in that precedence at all — git selects
#     the repo from the git dir, or from `-C`/cwd discovery, and a work-tree
#     only relocates the working files, never which repo a push targets.
#   * It does not treat every refspec-less push as a branch push: `--tags`
#     ships tags only, and `--help`/`--dry-run` write nothing at all. A
#     refspec-less push with NO explicit remote is resolved via
#     `git rev-parse @{push}` — the same lookup git itself uses, folding
#     together `push.default`, `branch.<b>.remote`/`pushRemote`,
#     `remote.pushDefault` and `remote.<r>.push` with no network access —
#     falling back to "deny iff the current branch is main" when no upstream
#     is configured. `push.default=matching` is checked first and denied
#     outright, since `@{push}` errors on it and it can push every
#     same-named branch (main included) regardless of the current one. A
#     refspec-less push WITH an explicit remote instead checks that remote's
#     configured `remote.<name>.push` refspecs directly, which wins over the
#     current-branch fallback (e.g. `remote.origin.push = HEAD:release` from
#     `main` pushes `release`, not `main`).
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

# Quote-aware tokenizer: splits $1 on unquoted whitespace AND on an unquoted
# `;`, `&`, `&&`, `||`, `|` or newline — each of those emitted as its own
# operator token, even when glued directly onto a word (`foo;bar`, `a&&b`) —
# keeping a `'…'` or `"…"` run — or a backslash-escaped character — inside
# one token instead of letting its embedded whitespace or punctuation end the
# token early. An unquoted `#` starting a word (not glued onto a preceding
# character) opens a comment that is skipped up to but not including the
# next real newline, so the newline itself still becomes its own operator
# token; this keeps an apostrophe inside a comment (`# don't`) from being
# misread as opening a quote that swallows the rest of the command. Callers
# that want segment boundaries (`inspect_command`) walk the resulting token
# list for those operator tokens; callers that just want one segment's words
# (everywhere else) never see one, since a segment is tokenized only after
# it has already been split at its operators. Populates the global
# `raw_tokens` array directly (rather than handing back a value to copy in)
# so an empty result stays a zero-element array without ever passing through
# a `"${arr[@]}"` expansion — bash 3.2 (macOS's default `/bin/bash`) raises
# "unbound variable" under `set -u` for that expansion on an empty array,
# even though `${#arr[@]}` is fine. Tokens keep their quote/backslash
# characters intact, so the existing two-array split (raw_tokens for the
# `#`-comment test, `unquote` producing tokens) keeps working unchanged. An
# unterminated quote just runs to the end of the string rather than erroring.
# This replaces plain whitespace-splitting (`read -a`), which is why a value
# like `-o "ci skip"` no longer needs to be re-merged after the fact — the
# tokenizer already keeps it as one token.
tokenize() {
  local s="$1" i=0 n=${#1} c token="" started=0
  raw_tokens=()
  while (( i < n )); do
    c="${s:i:1}"
    case "$c" in
      ' ' | $'\t')
        (( started )) && raw_tokens+=("$token")
        token=""
        started=0
        i=$((i + 1))
        ;;
      '"' | "'")
        started=1
        token+="$c"
        i=$((i + 1))
        while (( i < n )) && [[ "${s:i:1}" != "$c" ]]; do
          token+="${s:i:1}"
          i=$((i + 1))
        done
        if (( i < n )); then
          token+="$c"
          i=$((i + 1))
        fi
        ;;
      \\)
        started=1
        token+="$c"
        i=$((i + 1))
        if (( i < n )); then
          token+="${s:i:1}"
          i=$((i + 1))
        fi
        ;;
      ';' | '&' | '|' | $'\n')
        (( started )) && raw_tokens+=("$token")
        token=""
        started=0
        case "$c" in
          '&')
            if [[ "${s:i+1:1}" == '&' ]]; then
              raw_tokens+=("&&"); i=$((i + 2))
            else
              raw_tokens+=("&"); i=$((i + 1))
            fi
            ;;
          '|')
            if [[ "${s:i+1:1}" == '|' ]]; then
              raw_tokens+=("||"); i=$((i + 2))
            else
              raw_tokens+=("|"); i=$((i + 1))
            fi
            ;;
          *)
            raw_tokens+=("$c")
            i=$((i + 1))
            ;;
        esac
        ;;
      '#')
        if (( started )); then
          token+="$c"
          i=$((i + 1))
        else
          while (( i < n )) && [[ "${s:i:1}" != $'\n' ]]; do
            i=$((i + 1))
          done
        fi
        ;;
      *)
        started=1
        token+="$c"
        i=$((i + 1))
        ;;
    esac
  done
  (( started )) && raw_tokens+=("$token")
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
# a no-refspec push to main. Per `git push -h`: `--force-with-lease` and
# `--signed` take a value only in their attached `=` form (bare, they're
# flags) so they are deliberately not listed here — only the always-separate
# (or attached-or-separate) options are.
push_opt_takes_value() {
  case "$1" in
    -o | --push-option | --receive-pack | --exec | --repo | --upload-pack | \
    --recurse-submodules) return 0 ;;
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
  local spec="$1" branch="$2" dst src
  spec="${spec#+}"                       # leading + is force, not part of the ref
  if [[ "$spec" == *:* ]]; then
    src="${spec%%:*}"                    # everything before the first colon
    dst="${spec##*:}"                    # everything after the last colon
    if [[ -z "$dst" ]]; then
      # An empty destination on a refspec with a colon is one of two
      # things. Empty on BOTH sides (`:` or, force-prefixed, `+:`) is the
      # "matching branches" form — it pushes every local branch that also
      # exists on the remote, main included. Empty destination with a
      # non-empty SOURCE (`<src>:`) is instead a same-name push: <src> goes
      # to a remote branch of the same name, exactly as if the colon were
      # omitted — `HEAD:` resolves to the CURRENT branch's name, the same
      # way a bare `git push origin HEAD` does below.
      if [[ -z "$src" ]]; then
        return 0
      elif [[ "$src" == "HEAD" ]]; then
        dst="$branch"
      else
        dst="$src"
      fi
    fi
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

# A backslash immediately before a newline is a shell line continuation, not a
# command separator — join it into a single space before splitting on real
# newlines below, so `git push origin HEAD \` / `main` on two lines is read as
# one push with two refspecs instead of getting cut at the line break. A
# newline NOT preceded by a backslash is left alone; it already behaves like
# `;` in the segment split that follows.
join_continuations() {
  local out="" line
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$out" ]]; then
      out="$line"
    elif [[ "$out" == *\\ ]]; then
      out="${out%\\} $line"
    else
      out="$out"$'\n'"$line"
    fi
  done <<<"$1"
  printf '%s' "$out"
}

# inspect_segment judges one segment. A token that is a command-substitution
# or backtick wrapper, or an argument handed to a shell/eval invocation, can
# hide an entire nested command from the scan below (`"$(git push origin
# main)"`, `sh -c 'git push origin main'`) — including a `cd` that would
# otherwise only be visible as this segment's own first token. Before parsing
# this segment's own verb, inspect_segment finds any such payload and recurses
# into it through inspect_command, so a nested `cd` still updates
# `running_dir` and a nested push is still judged (and, via `deny`'s own
# `exit`, still stops the whole hook). Recursion reuses the shared
# raw_tokens/tokens/i, so this segment's own tokens are rebuilt afterward.
inspect_segment() {
  local segment="$1"
  tokenize "$segment"
  (( ${#raw_tokens[@]} )) || return 0

  # Normalize: drop quoting and any leading shell punctuation, so `(git`,
  # `$(git`, `'git` and `` `git `` are all recognized as the git token.
  tokens=()
  local t
  for t in "${raw_tokens[@]}"; do
    tokens+=("$(strip_shell_punct "$(unquote "$t")")")
  done

  # Rule 1: a token whose RAW (quote-stripped-only) form starts with `$(` or a
  # backtick is a command substitution — checked on the raw token, before
  # strip_shell_punct peels the wrapper off, so a plain quoted multi-word
  # value (e.g. a commit message) is never mistaken for one; the
  # already-peeled `tokens[k]` is what gets recursed into. Rule 2: once a
  # `sh`/`bash`/`zsh`/`eval` token is seen anywhere in the segment, every
  # later argument that isn't itself a flag is treated as its script/command —
  # covers `-c`, `-lc`, and `sudo sh -c` alike without parsing which flag
  # takes a value.
  local payloads=() is_shell_launcher=0 k
  for (( k = 0; k < ${#tokens[@]}; k++ )); do
    case "${tokens[k]}" in
      bash | sh | zsh | */bash | */sh | */zsh | eval) is_shell_launcher=1 ;;
    esac
    # Cheap pre-check before the subshell+unquote below: only a raw token
    # that could possibly start with `$(` or a backtick once its own quoting
    # is stripped is worth checking properly.
    if [[ "${raw_tokens[k]:-}" == *'$('* || "${raw_tokens[k]:-}" == *'`'* ]]; then
      case "$(unquote "${raw_tokens[k]}")" in
        '$('* | '`'*) payloads+=("${tokens[k]}") ;;
      esac
    fi
    if (( is_shell_launcher )) && (( k > 0 )); then
      case "${tokens[k]}" in
        -* | bash | sh | zsh | */bash | */sh | */zsh | eval) : ;;
        *) payloads+=("${tokens[k]}") ;;
      esac
    fi
  done
  if (( ${#payloads[@]} )); then
    local payload
    for payload in "${payloads[@]}"; do
      inspect_command "$payload"
    done
    # Recursing clobbered the shared raw_tokens/tokens/i; rebuild this
    # segment's own tokens before continuing.
    tokenize "$segment"
    tokens=()
    for t in "${raw_tokens[@]}"; do
      tokens+=("$(strip_shell_punct "$(unquote "$t")")")
    done
  fi

  # `cd <dir> && git push …` splits into two segments; carry the directory
  # forward so the second one is judged against the repo it actually enters.
  if [[ "${tokens[0]}" == "cd" && -n "${tokens[1]:-}" ]]; then
    case "${tokens[1]}" in
      /*) running_dir="${tokens[1]}" ;;
      -|'~'*) : ;;
      *) running_dir="$running_dir/${tokens[1]}" ;;
    esac
  fi

  [[ "$segment" == *git* ]] || return 0

  # Walk to the subcommand. Environment assignments may precede `git`; capture
  # the one that relocates the repo (`GIT_WORK_TREE=` is consumed by the loop
  # below like any other token but otherwise ignored — see the -C/--git-dir
  # block's comment for why).
  env_gitdir=""
  i=0
  while (( i < ${#tokens[@]} )) && [[ "${tokens[i]}" != "git" && "${tokens[i]}" != */git ]]; do
    case "${tokens[i]}" in
      GIT_DIR=*) env_gitdir="${tokens[i]#GIT_DIR=}" ;;
    esac
    i=$((i + 1))
  done
  (( i < ${#tokens[@]} )) || return 0
  i=$((i + 1))

  # Skip git's own options, capturing the ones that name a different repo.
  # `--git-dir` (or `GIT_DIR=`) identifies the repo by the git dir path
  # itself — unlike the old `dirname` reduction, this also works for a bare
  # repo (where the git dir IS the repo) and for a plain `.git` directory.
  # `-C` is resolved as we go, cumulatively: an absolute value replaces
  # whatever came before, a relative one is joined onto it — matching git's
  # own "each -C is relative to the previous one" behavior, so `-C a -C b`
  # reaches `a/b`. `--work-tree`/`GIT_WORK_TREE=` are consumed (so their value
  # doesn't get misread as the subcommand) but otherwise ignored — a
  # work-tree only relocates the working files, it never selects the repo.
  opt_gitdir=""
  c_dir=""
  while (( i < ${#tokens[@]} )); do
    case "${tokens[i]}" in
      -C)
        case "${tokens[i+1]:-}" in
          /*) c_dir="${tokens[i+1]}" ;;
          *)  c_dir="${c_dir:-$running_dir}/${tokens[i+1]:-}" ;;
        esac
        i=$((i + 1))
        ;;
      --work-tree)   i=$((i + 1)) ;;
      --work-tree=*) : ;;
      --git-dir)     opt_gitdir="${tokens[i+1]:-}"; i=$((i + 1)) ;;
      --git-dir=*)   opt_gitdir="${tokens[i]#--git-dir=}" ;;
      -*) git_opt_takes_value "${tokens[i]}" && i=$((i + 1)) ;;
      *) break ;;
    esac
    i=$((i + 1))
  done

  verb="${tokens[i]:-}"
  [[ -n "$verb" ]] || return 0

  # Which repo does THIS segment write to? Precedence: --git-dir option, then
  # GIT_DIR=, then the cumulative -C result, then the running cwd. A relative
  # override resolves against `-C`'s own result when one was given (matching
  # git: `-C` changes the effective directory before a relative --git-dir is
  # read), else against the running cwd.
  work_dir="${opt_gitdir:-${env_gitdir:-${c_dir:-$running_dir}}}"
  case "$work_dir" in /*) : ;; *) work_dir="${c_dir:-$running_dir}/$work_dir" ;; esac

  if [[ "$verb" != "push" ]]; then
    is_common_verb "$verb" && return 0
    alias_is_push "$verb" "$work_dir" || return 0
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

  is_guarded "$work_dir" || return 0
  current_branch=$(git -C "$work_dir" symbolic-ref --short HEAD 2>/dev/null || true)

  remote_seen=0
  remote_name=""
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
        # An option that takes a value: skip the value so it isn't misread as
        # the remote or a refspec. The tokenizer already keeps a quoted value
        # (attached via `=`, or as its own separate token) as one token, so no
        # re-merging is needed here — just skip the separate-token form; an
        # attached `=` form has no extra token to skip.
        opt_name="$tok"
        [[ "$tok" == *=* ]] && opt_name="${tok%%=*}"
        if push_opt_takes_value "$opt_name" && [[ "$tok" != *=* ]]; then
          i=$((i + 1))
        fi
        ;;
      *)
        if (( remote_seen == 0 )); then
          remote_seen=1          # first bare token is the remote
          remote_name="$tok"
        else
          refspecs+=("$tok")
        fi
        ;;
    esac
  done

  (( inert )) && return 0

  if (( all_branches )); then
    deny "\`git push $all_branches_tok\` pushes every branch, including main. Run this from your terminal yourself."
  fi

  if (( ${#refspecs[@]} == 0 )); then
    # No refspec: the push follows the current branch's upstream — unless it
    # is a tags-only push, which never updates a branch.
    (( tags_only )) && return 0

    if (( remote_seen == 0 )); then
      # No refspec AND no explicit remote: resolve this exactly the way git
      # itself would, in one lookup. `@{push}` folds together push.default,
      # branch.<b>.remote/pushRemote, remote.pushDefault and remote.<r>.push
      # with no network access — the only case it can't answer is
      # push.default=matching, which it errors on, so that one is checked
      # first and denied outright (it can push every same-named branch,
      # main included, regardless of the current branch).
      push_default=$(git -C "$work_dir" config --get push.default 2>/dev/null || true)
      if [[ "$push_default" == "matching" ]]; then
        deny "\`push.default\` is \`matching\`, which can push every same-named branch including main. Pushes to main are reserved for the user to run from the terminal."
      fi
      push_upstream=$(git -C "$work_dir" rev-parse --abbrev-ref --symbolic-full-name '@{push}' 2>/dev/null || true)
      if [[ -n "$push_upstream" ]]; then
        push_branch="${push_upstream##*/}"
        if [[ "$push_branch" == "main" ]]; then
          deny "Refspec-less push resolves (\`@{push}\`) to \`$push_upstream\`. Pushes to main are reserved for the user to run from the terminal."
        fi
        return 0
      fi
      # `@{push}` has nothing to resolve — no upstream is configured (and,
      # in practice, no remote-tracking ref exists yet for it to point at
      # either). Fall back to git's default `simple` behaviour: push the
      # current branch to a remote branch of the same name.
      if [[ "$current_branch" == "main" ]]; then
        deny "Current branch is main and this push has no refspec. Pushes to main are reserved for the user to run from the terminal."
      fi
      return 0
    fi

    # An explicit remote but no refspec: a configured `remote.<name>.push`
    # redirects the push in place of the current branch — check that FIRST,
    # since it can send a non-main branch to main, or redirect what would
    # otherwise be main away from it. Only fall back to "deny iff on main"
    # when nothing is configured there.
    configured_push=""
    [[ -n "$remote_name" ]] && configured_push=$(git -C "$work_dir" config --get-all "remote.$remote_name.push" 2>/dev/null || true)
    if [[ -n "$configured_push" ]]; then
      while IFS= read -r cfg_spec || [[ -n "$cfg_spec" ]]; do
        [[ -z "$cfg_spec" ]] && continue
        if targets_main "$cfg_spec" "$current_branch"; then
          deny "Configured \`remote.$remote_name.push\` (\`$cfg_spec\`) targets main for this refspec-less push. Pushes to main are reserved for the user to run from the terminal."
        fi
      done <<<"$configured_push"
      return 0
    fi
    if [[ "$current_branch" == "main" ]]; then
      deny "Current branch is main and this push has no refspec. Pushes to main are reserved for the user to run from the terminal."
    fi
    return 0
  fi

  for spec in "${refspecs[@]}"; do
    if targets_main "$spec" "$current_branch"; then
      deny "Detected a push targeting the main branch (\`$spec\`). Pushes to main are reserved for the user to run from the terminal."
    fi
  done
}

# 2. Inspect each segment on its own. Splitting happens AFTER tokenizing, not
# on the raw text, so a `;`/`&`/`|`/newline sitting inside a quoted string —
# a commit message, an echoed example — is just part of that word's token
# and never creates a false segment boundary; only an UNQUOTED occurrence
# (including one glued directly onto a word, `foo;bar`) starts a new
# segment. Each segment is reassembled by rejoining its own tokens with a
# single space, which `inspect_segment` then re-tokenizes itself — lossless
# for our purposes, since two tokens are only ever adjacent without an
# original space between them when an operator token used to sit there, and
# that operator is exactly what got consumed to create the split. A
# backslash immediately before a newline is a shell line continuation, not a
# command separator — join_continuations folds it into a single space before
# tokenizing, so a push spread across escaped newlines is still read as one
# command instead of getting cut at the line break.
inspect_command() {
  local text; text="$(join_continuations "$1")"
  tokenize "$text"
  local seg=() rt joined
  if (( ${#raw_tokens[@]} )); then
    for rt in "${raw_tokens[@]}"; do
      case "$rt" in
        ';' | '&' | '&&' | '||' | '|' | $'\n')
          if (( ${#seg[@]} )); then
            joined="$(IFS=' '; printf '%s' "${seg[*]}")"
            inspect_segment "$joined"
          fi
          seg=()
          ;;
        *)
          seg+=("$rt")
          ;;
      esac
    done
  fi
  if (( ${#seg[@]} )); then
    joined="$(IFS=' '; printf '%s' "${seg[*]}")"
    inspect_segment "$joined"
  fi
}

running_dir="$default_dir"
inspect_command "$cmd"

exit 0

# KNOWN LIMITS — deliberate, given the threat model at the top.
#
#   * It matches on literal text, so a command that merely *contains* a
#     push-to-main pattern is blocked when that text is unquoted and reads
#     like a real invocation: a heredoc writing a script, or an editor
#     payload, still gets denied even though nothing is actually about to
#     run. That is the safe direction; write such files with the Write tool
#     instead. Narrowing it would open `bash <<'EOF' … EOF` as a real bypass.
#     A quoted example or a commit message that merely *mentions* `push`/
#     `main` — including one containing a literal `;`/`&`/`|` — is correctly
#     read as inert text, since segment splitting happens after tokenizing
#     (see `inspect_command`), not on the raw string.
#   * It does not expand variables or command substitution, so a push whose
#     ref only exists after expansion (`git push origin "$B"`) is not seen.
#     Closing this would require executing the command to find out what it
#     does, which is the thing a PreToolUse hook exists to avoid.
#   * Quotes are tokenized (grouped into one word on unquoted whitespace) and
#     then stripped for comparison, not fully parsed, so a branch whose name
#     genuinely contains a quote or backslash is compared without it.
#   * A quoted argument is only re-inspected for a nested command when it's a
#     `$(…)`/backtick substitution, or an argument following `sh`/`bash`/
#     `zsh`/`eval`. Any other quoted argument is opaque, including
#     `echo '…' | bash`, `ssh host '…'`, and `su -c '…'` — the old
#     whitespace-only splitter happened to see into those (and DENY them) by
#     accident of not respecting quoting at all; the quote-aware tokenizer
#     that replaced it deliberately narrows that to the two named cases.
#   * Directory tracking follows every `cd` linearly, with no model of
#     subshell scope or command success: `(cd /tmp); git push origin main`
#     and `cd /missing || git push origin main` are both judged against the
#     wrong repo — a `cd` inside a subshell that has already closed, or one
#     that failed, is still treated as having taken effect. Modelling that
#     would mean modelling shell control flow, which is the same "disguised
#     push" territory this list already excludes.
