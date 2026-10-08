#!/usr/bin/env bash
# Claude Code PreToolUse hook on Bash: blocks two zsh traps that keep biting
# when the Bash tool runs under zsh (the user's login shell), where a command
# written with bash habits silently does the wrong thing.
#
#   1. Unquoted $VAR does not word-split in zsh. `set -- $pair` and
#      `for x in $list` see ONE word, so `set -- $m` with m="id path" passes
#      "id path" as $1 (pup: "invalid digit found in string").
#      Fix: ${=VAR} (zsh split), an array, or spell the args out.
#   2. A word starting with `=` is zsh command-path expansion (`=ls` →
#      /bin/ls), so an unquoted separator like `echo ===` fails with
#      "(eval):1: === not found" and kills the rest of an `&&`/`;` chain.
#      Fix: quote it — echo '==='.
#
# Both are always bugs under zsh, so the hook blocks (exit 2, reason on
# stderr) rather than warns. It is a text heuristic: it strips quoted spans
# before matching and never parses the shell. Append `# zsh-ok` to the command
# to bypass it when a match is deliberate. It does nothing when $SHELL is not
# zsh.
#
# Tests: bash hooks/tests/zsh-word-split-guard.test.sh

set -uo pipefail

[[ "$(basename "${SHELL:-}")" == "zsh" || -n "${ZSH_GUARD_FORCE:-}" ]] || exit 0

cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[[ -n "$cmd" ]] || exit 0
[[ "$cmd" == *"# zsh-ok"* ]] && exit 0

# Drop heredoc bodies first. A body is data fed to stdin, never shell words,
# so neither trap can fire inside one (quoted or not), and its own quotes
# (`don't`, a TS `"a" === b`) would otherwise unbalance the span-stripping
# below. Handles `<<EOF`, `<<-EOF` (tab-indented close), `<<'EOF'`,
# `<<"EOF"` and several heredocs opened on one line; skips `<<<` here-strings.
strip_heredocs() {
  awk '
    function queue(line,   rest, m, d) {
      rest = line
      while (match(rest, /<<-?[ \t]*["\047]?[A-Za-z_][A-Za-z0-9_]*["\047]?/)) {
        m = substr(rest, RSTART, RLENGTH)
        if (RSTART > 1 && substr(rest, RSTART - 1, 1) == "<") { rest = substr(rest, RSTART + RLENGTH); continue }
        dash[n] = (m ~ /^<<-/)
        d = m; sub(/^<<-?[ \t]*/, "", d); gsub(/["\047]/, "", d)
        delim[n++] = d
        rest = substr(rest, RSTART + RLENGTH)
      }
    }
    BEGIN { n = 0; cur = 0 }
    {
      if (cur < n) {
        line = $0
        if (dash[cur]) sub(/^\t+/, "", line)
        if (line == delim[cur]) cur++
        next
      }
      print
      queue($0)
    }'
}

# Then drop single-quoted spans, then double-quoted spans, so quoted text
# (commit messages, jq programs, echo '===') cannot match.
bare=$(printf '%s\n' "$cmd" | strip_heredocs | sed -E "s/'[^']*'//g; s/\"([^\"\\\\]|\\\\.)*\"//g")

problems=()
if printf '%s' "$bare" | grep -Eq '(^|[;&|[:space:]])set[[:space:]]+--[[:space:]]+\$[A-Za-z_]'; then
  problems+=("\`set -- \$VAR\`: zsh does not word-split an unquoted \$VAR, so it becomes ONE positional arg. Use \`set -- \${=VAR}\` or an array.")
fi
if printf '%s' "$bare" | grep -Eq '(^|[;&|[:space:]])for[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+in[[:space:]]+\$[A-Za-z_][A-Za-z0-9_]*[[:space:]]*(;|$)'; then
  problems+=("\`for x in \$VAR\`: zsh loops once over the whole string. Use \`\${=VAR}\`, \`\${(f)VAR}\` for lines, or an array.")
fi
# `==`/`=` inside [[ … ]] / [ … ] is a comparison operator, not expansion.
no_tests=$(printf '%s' "$bare" | sed -E 's/\[\[[^]]*\]\]//g; s/\[[[:space:]][^]]*[[:space:]]\]//g')
if printf '%s' "$no_tests" | grep -Eq '(^|[;&|[:space:]])=+([;&|[:space:]]|$)'; then
  problems+=("unquoted \`===\`-style word: zsh treats a leading \`=\` as command-path expansion (\"=== not found\"). Quote it: '==='.")
fi

((${#problems[@]} == 0)) && exit 0

{
  echo "Blocked by zsh-word-split-guard (the Bash tool runs zsh here):"
  for p in "${problems[@]}"; do echo "  - $p"; done
  echo "Rewrite the command, or append '# zsh-ok' if the match is deliberate."
} >&2
exit 2
