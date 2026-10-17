#!/bin/zsh
# Review cycle for advance-roadmap. Cursor reviews each round, including Cursor's
# own work. Once a Cursor round is clean, Codex reviews once as the final pass.
#
#   review.sh --repo PATH --worker cursor|codex|claude --round N --log FILE
#       [--base REF] [--strategy cursor-then-codex|cross-provider]
#   (--base defaults to origin's default branch: origin/main, or origin/master where that's it)
#
# Reviewers are read-only. Provider fallback depends on the selected strategy.
# Findings go to stdout, ending in one line:
#   VERDICT: clean | VERDICT: findings
# Each reviewer invocation appends one JSON line to --log, which run.sh folds
# into the run record — those lines prove both review phases happened.
set -u

ROOT="${ADVANCE_ROADMAP_ROOT:-/Users/jake/.claude/automations/advance-roadmap}"
[ -r "$ROOT/lib/models.sh" ] || { echo "review: missing model defaults: $ROOT/lib/models.sh" >&2; exit 1; }
. "$ROOT/lib/models.sh"
USAGE_PY="$ROOT/lib/usage.py"
TOKENS_PY="$ROOT/lib/tokens.py"

AGENT="${ADVANCE_ROADMAP_AGENT_BIN:-/Users/jake/.local/bin/agent}"
CODEX="${ADVANCE_ROADMAP_CODEX_BIN:-/opt/homebrew/bin/codex}"
CLAUDE="${ADVANCE_ROADMAP_CLAUDE_BIN:-/Users/jake/.local/bin/claude}"

CODEX_REVIEW_MODEL="${ADVANCE_ROADMAP_CODEX_REVIEW_MODEL:-$CODEX_MODEL_DEFAULT}"
CURSOR_REVIEW_MODEL="${ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL:-auto}"
CLAUDE_REVIEW_MODEL="${ADVANCE_ROADMAP_CLAUDE_REVIEW_MODEL:-claude-opus-5}"
# Reviews run cheap and are repeated; the worker that fixes findings runs at high
# effort (worker.sh). Cursor Auto has no effort knob.
REVIEW_EFFORT="${ADVANCE_ROADMAP_REVIEW_EFFORT:-low}"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/jake/.local/bin"

REPO="" WORKER="" ROUND="" LOG="" BASE=""
REVIEW_STRATEGY="${ADVANCE_ROADMAP_REVIEW_STRATEGY:-cursor-then-codex}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --worker) WORKER="$2"; shift 2 ;;
    --round) ROUND="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    --strategy) REVIEW_STRATEGY="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
# A worktree (Superset workspace) has a .git file, not a directory.
[ -n "$REPO" ] && [ -e "$REPO/.git" ] || { echo "FATAL: --repo must be a git checkout" >&2; exit 2; }
[ -n "$ROUND" ] && [ -n "$LOG" ] || { echo "FATAL: --round and --log required" >&2; exit 2; }
# Default: origin's default branch — some repos (roaddmap, wav-explorer) are on master.
if [ -z "$BASE" ]; then
  BASE="$(git -C "$REPO" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
  [ -n "$BASE" ] || { git -C "$REPO" rev-parse --verify --quiet origin/main >/dev/null && BASE=origin/main || BASE=origin/master; }
fi
# Beside reviews.jsonl, i.e. in the run dir — the same file run.sh and worker.sh append to.
USAGE_LOG="${LOG:h}/model-usage.jsonl"

case "$WORKER" in
  cursor|codex|claude) ;;
  *) echo "FATAL: --worker must be cursor, codex or claude" >&2; exit 2 ;;
esac
case "$REVIEW_STRATEGY" in
  cursor-then-codex|cross-provider) ;;
  *) echo "FATAL: --strategy must be cursor-then-codex or cross-provider" >&2; exit 2 ;;
esac

case "$WORKER" in
  cursor) cross_provider_reviewers=(codex claude) ;;
  codex) cross_provider_reviewers=(cursor claude) ;;
  claude) cross_provider_reviewers=(codex cursor) ;;
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
CAPACITY='selected model is at capacity|model is (currently )?(at capacity|overloaded)|server is overloaded|overloaded_error|\b(503|529)\b.*(unavailable|overloaded|capacity)|(unavailable|overloaded).*\b(503|529)\b'

record_review() {
  python3 - "$LOG" "$ROUND" "$WORKER" "$1" "$2" "$3" "$REPO" "$REVIEW_STRATEGY" <<'PY'
import json, subprocess, sys, time
log, rnd, worker, reviewer, verdict, phase, repo, strategy = sys.argv[1:9]
head = subprocess.check_output(["git", "-C", repo, "rev-parse", "--short", "HEAD"], text=True).strip()
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"round": int(rnd), "worker": worker, "reviewer": reviewer or None,
                        "strategy": strategy, "phase": phase, "verdict": verdict, "head": head,
                        "at": int(time.time())}) + "\n")
PY
}

run_required_review() {
  local who="$1" phase="$2" rc=0 verdict text gate
  gate="$(python3 "$USAGE_PY" --root "$ROOT" gate --role reviewer --provider "$who" 2>/dev/null)"
  if [ "$?" -eq 3 ]; then
    record_review "$who" unavailable "$phase"
    echo "REVIEW REQUIRED: $who is over its usage gate ($gate); do not merge until this review stage succeeds."
    echo "VERDICT: unavailable"
    return 3
  fi
  rm -f "$out.last" "$out.json"
  run_reviewer "$who" "$out" || rc=$?
  if text="$(rg -m1 -i \
      '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
      "$out" || true)" && [ -n "$text" ]; then
    python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$who" --text "$text" >/dev/null 2>&1 || true
    record_review "$who" unavailable "$phase"
    echo "REVIEW REQUIRED: $who was limited; do not merge until this review stage succeeds."
    echo "VERDICT: unavailable"
    return 3
  fi
  if [ "$rc" -eq 127 ]; then
    record_review "" unavailable "$phase"
    echo "REVIEW REQUIRED: $who is unavailable; do not merge until this review stage succeeds."
    echo "VERDICT: unavailable"
    return 3
  fi
  if [ "$rc" -ne 0 ] && rg -q -i "$CAPACITY" "$out" "$out.json" 2>/dev/null; then
    record_review "$who" unavailable "$phase"
    echo "REVIEW REQUIRED: $who's model is at capacity; do not merge until this review stage succeeds."
    echo "VERDICT: unavailable"
    return 3
  fi
  # Codex echoes the prompt (which quotes both verdict lines), so trust the last one.
  verdict="$(rg -o '^VERDICT: (clean|findings)\s*$' -r '$1' "$out" | tail -1)"
  [ -n "$verdict" ] || verdict="unparsed"
  record_review "$who" "$verdict" "$phase"
  if [ -s "$out.last" ]; then cat "$out.last"; else cat "$out"; fi
  echo ""
  echo "(reviewer=$who round=$ROUND phase=$phase verdict=$verdict)"
  if [ "$verdict" = unparsed ]; then
    echo "VERDICT: unparsed — treat the review text above as findings"
  else
    echo "VERDICT: $verdict"
  fi
  return 0
}

run_cross_provider_review() {
  local used="" verdict="unavailable" who rc text gate
  for who in "${cross_provider_reviewers[@]}"; do
    gate="$(python3 "$USAGE_PY" --root "$ROOT" gate --role reviewer --provider "$who" 2>/dev/null)"
    if [ "$?" -eq 3 ]; then
      echo "(skipping reviewer $who — over its usage gate: $gate)"
      continue
    fi
    rm -f "$out.last" "$out.json"
    rc=0
    run_reviewer "$who" "$out" || rc=$?
    [ "$rc" -eq 127 ] && continue
    if text="$(rg -m1 -i \
        '^(you.?ve hit your (session |usage )?limit|hit your session limit|rate limit exceeded|quota exceeded|out of (usage|credits|quota)\b)' \
        "$out" || true)" && [ -n "$text" ]; then
      python3 "$USAGE_PY" --root "$ROOT" record-limit --provider "$who" --text "$text" >/dev/null 2>&1 || true
      continue
    fi
    if [ "$rc" -ne 0 ] && rg -q -i "$CAPACITY" "$out" "$out.json" 2>/dev/null; then
      echo "(skipping reviewer $who — its model is at capacity)"
      continue
    fi
    used="$who"
    verdict="$(rg -o '^VERDICT: (clean|findings)\s*$' -r '$1' "$out" | tail -1)"
    [ -n "$verdict" ] || verdict=unparsed
    break
  done

  if [ -z "$used" ]; then
    record_review "" unavailable cross-provider
    echo "REVIEW UNAVAILABLE: all cross-provider reviewers (${cross_provider_reviewers[*]}) were limited or missing."
    echo "VERDICT: unavailable"
    return 3
  fi
  record_review "$used" "$verdict" cross-provider
  if [ -s "$out.last" ]; then cat "$out.last"; else cat "$out"; fi
  echo ""
  echo "(reviewer=$used round=$ROUND strategy=cross-provider verdict=$verdict)"
  if [ "$verdict" = unparsed ]; then
    echo "VERDICT: unparsed — treat the review text above as findings"
  else
    echo "VERDICT: $verdict"
  fi
}

if [ "$REVIEW_STRATEGY" = cross-provider ]; then
  run_cross_provider_review
  exit $?
fi

# Cursor owns the review cycle. Findings return to the worker, which must use
# Cursor to address them before requesting another round.
run_required_review cursor cursor-cycle || exit $?
cursor_verdict="$(tail -1 "$LOG" | python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')"
[ "$cursor_verdict" = clean ] || exit 0

# Codex gets one final pass after Cursor first reaches clean. If its findings are
# fixed, later rounds are Cursor-only; the Codex pass is not repeated.
if ! python3 - "$LOG" "$REVIEW_STRATEGY" <<'PY'
import json, sys
try:
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
except OSError:
    rows = []
raise SystemExit(0 if any(r.get("strategy") == sys.argv[2] and
                           r.get("phase") == "codex-final" and r.get("verdict") != "unavailable"
                           for r in rows) else 1)
PY
then
  run_required_review codex codex-final
  exit $?
fi
