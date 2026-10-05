#!/bin/zsh
# Cross-model review for advance-roadmap. The worker calls this before merging
# (WORKER.md Step 5b); a model from a different provider reads the branch diff.
#
#   review.sh --repo PATH --worker cursor|codex|claude --round N --log FILE [--base REF]
#
# Reviewer: Cursor's work → Codex, Codex's → Cursor, Claude's → Codex; the
# remaining provider is the fallback when the first is limited or missing. The
# reviewer is read-only. Findings go to stdout, ending in one line:
#   VERDICT: clean | VERDICT: findings
# Each round appends one JSON line to --log, which run.sh folds into the run
# record — that line is the proof the review happened.
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
USAGE_PY="$ROOT/lib/usage.py"
TOKENS_PY="$ROOT/lib/tokens.py"

CLAUDE="${ADVANCE_ROADMAP_CLAUDE_BIN:-/Users/jake/.local/bin/claude}"
AGENT="${ADVANCE_ROADMAP_AGENT_BIN:-/Users/jake/.local/bin/agent}"
CODEX="${ADVANCE_ROADMAP_CODEX_BIN:-/opt/homebrew/bin/codex}"

CODEX_REVIEW_MODEL="${ADVANCE_ROADMAP_CODEX_REVIEW_MODEL:-gpt-5.6-sol}"
CURSOR_REVIEW_MODEL="${ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL:-auto}"
CLAUDE_REVIEW_MODEL="${ADVANCE_ROADMAP_CLAUDE_REVIEW_MODEL:-claude-opus-5}"
# Reviews run cheap and are repeated; the worker that fixes findings runs at high
# effort (worker.sh). Cursor Auto has no effort knob.
REVIEW_EFFORT="${ADVANCE_ROADMAP_REVIEW_EFFORT:-low}"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

REPO="" WORKER="" ROUND="" LOG="" BASE="origin/main"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --worker) WORKER="$2"; shift 2 ;;
    --round) ROUND="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$REPO" ] && [ -d "$REPO/.git" ] || { echo "FATAL: --repo must be a git checkout" >&2; exit 2; }
[ -n "$ROUND" ] && [ -n "$LOG" ] || { echo "FATAL: --round and --log required" >&2; exit 2; }
# Beside reviews.jsonl, i.e. in the run dir — the same file run.sh and worker.sh append to.
USAGE_LOG="${LOG:h}/model-usage.jsonl"

case "$WORKER" in
  cursor) reviewers=(codex claude) ;;
  codex)  reviewers=(cursor claude) ;;
  claude) reviewers=(codex cursor) ;;
  *) echo "FATAL: --worker must be cursor, codex or claude" >&2; exit 2 ;;
esac

git -C "$REPO" diff --quiet "$BASE...HEAD" && { echo "FATAL: no changes vs $BASE to review" >&2; exit 2; }
# Inline, so a reviewer in a read-only mode doesn't need to run git. Capped to stay
# under the argv limit; past the cap the reviewer reads the files itself.
DIFF="$(git -C "$REPO" diff "$BASE...HEAD" | head -c 150000)"
[ ${#DIFF} -ge 150000 ] && DIFF="$DIFF
[diff truncated at 150 KB — read the remaining changed files directly: $(git -C "$REPO" diff --name-only "$BASE...HEAD" | tr '\n' ' ')]"

PROMPT="You are reviewing a change another AI agent made in this repository, before it merges to main.
You are read-only: do not edit files, commit, or run anything that writes.

The diff (git diff $BASE...HEAD) is below. Read surrounding files in the repo as needed.
Report only real problems — bugs, broken edge cases, security issues, missing or wrong tests for new logic, clear violations of the repo's CLAUDE.md. Skip style
nits and preferences.

For each finding: file:line, severity (bug | risk | test), one sentence on what is wrong, one on the fix.
End with exactly one line, the last line of your reply:
VERDICT: clean      (no findings)
VERDICT: findings   (one or more findings)

Commits:
$(git -C "$REPO" log --oneline "$BASE..HEAD")

Diff:
$DIFF"

run_reviewer() {
  local who="$1" out="$2"
  case "$who" in
    codex)
      command -v "$CODEX" >/dev/null 2>&1 || return 127
      rm -f "$out.last"
      # `</dev/null` is load-bearing: codex exec reads instructions from stdin, and with $PROMPT
      # also passed as an argument it appends stdin as a <stdin> block — so an inherited open stdin
      # hangs it forever on "Reading additional input from stdin...". This survived only by
      # inheriting a closed stdin from worker.sh; don't rely on that.
      "$CODEX" exec -o "$out.last" -m "$CODEX_REVIEW_MODEL" -c model_reasoning_effort="$REVIEW_EFFORT" \
        -s read-only -C "$REPO" "$PROMPT" >"$out" 2>&1 </dev/null
      local rc=$?
      python3 "$TOKENS_PY" codex "$out" --role reviewer --round "$ROUND" --model "$CODEX_REVIEW_MODEL" \
        --log "$USAGE_LOG" >/dev/null 2>&1
      [ -s "$out.last" ] && { cat "$out.last" >> "$out"; }
      return $rc ;;
    cursor)
      [ -x "$AGENT" ] || return 127
      "$AGENT" -p --mode ask --trust --model "$CURSOR_REVIEW_MODEL" --workspace "$REPO" \
        --output-format json "$PROMPT" >"$out.json" 2>&1
      local rc=$?
      python3 "$TOKENS_PY" cursor-json "$out.json" "$out" --role reviewer --round "$ROUND" \
        --model "$CURSOR_REVIEW_MODEL" --log "$USAGE_LOG" >/dev/null 2>&1
      [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null
      return $rc ;;
    claude)
      [ -x "$CLAUDE" ] || return 127
      ( cd "$REPO" && "$CLAUDE" -p "$PROMPT" --model "$CLAUDE_REVIEW_MODEL" --effort "$REVIEW_EFFORT" \
        --permission-mode plan --disallowedTools "Edit,Write,NotebookEdit" \
        --output-format json ) >"$out.json" 2>&1
      local rc=$?
      python3 "$TOKENS_PY" claude-json "$out.json" "$out" --role reviewer --round "$ROUND" \
        --model "$CLAUDE_REVIEW_MODEL" --log "$USAGE_LOG" >/dev/null 2>&1
      [ -s "$out" ] || cp "$out.json" "$out" 2>/dev/null
      return $rc ;;
  esac
}

out="$(mktemp "${TMPDIR:-/tmp}/advance-roadmap-review.XXXXXX")"
trap 'rm -f "$out" "$out.last" "$out.json"' EXIT INT TERM
used="" verdict="unavailable"
for who in "${reviewers[@]}"; do
  rc=0
  rm -f "$out.last"   # a limited Codex attempt must not supply the next reviewer's text
  run_reviewer "$who" "$out" || rc=$?
  [ "$rc" -eq 127 ] && continue
  if text="$(rg -m1 -i \
      '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
      "$out" || true)" && [ -n "$text" ]; then
    python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$who" --text "$text" >/dev/null 2>&1 || true
    continue
  fi
  used="$who"
  # Codex echoes the prompt (which quotes both verdict lines) — trust the last one.
  verdict="$(rg -o '^VERDICT: (clean|findings)\s*$' -r '$1' "$out" | tail -1)"
  [ -n "$verdict" ] || verdict="unparsed"
  break
done

python3 - "$LOG" "$ROUND" "$WORKER" "${used:-}" "$verdict" "$(git -C "$REPO" rev-parse --short HEAD)" <<'PY'
import json, sys, time
log, rnd, worker, reviewer, verdict, head = sys.argv[1:7]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"round": int(rnd), "worker": worker, "reviewer": reviewer or None,
                        "verdict": verdict, "head": head, "at": int(time.time())}) + "\n")
PY

if [ -z "$used" ]; then
  echo "REVIEW UNAVAILABLE: every reviewer (${reviewers[*]}) was limited or missing."
  echo "VERDICT: unavailable"
  exit 3
fi
if [ -s "$out.last" ]; then cat "$out.last"; else cat "$out"; fi
echo ""
echo "(reviewer=$used round=$ROUND verdict=$verdict)"
[ "$verdict" = "unparsed" ] && echo "VERDICT: unparsed — treat the review text above as findings"
exit 0
