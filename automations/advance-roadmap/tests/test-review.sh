#!/bin/zsh
# review.sh with fake providers: reviewer is never the worker's provider, limits
# fail over, the verdict comes only from the reviewer's final answer (never the
# echoed prompt), and every round leaves one line in the review log.
set -eu

# Scheduled worker variables must not change the fake reviewer's routing or quota behavior.
if [ "${ADVANCE_ROADMAP_TEST_ENV_CLEAN:-0}" != 1 ]; then
  exec env -i HOME="$HOME" PATH="$PATH" TMPDIR="${TMPDIR:-/tmp}" \
    ADVANCE_ROADMAP_TEST_ENV_CLEAN=1 zsh "$0" "$@"
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-review-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM
# Fixture commits must not depend on the user's signing setup or hooks.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

ROOT="$TMP/root"; BIN="$TMP/bin"; REPO="$TMP/repo"; LOG="$TMP/reviews.jsonl"
mkdir -p "$ROOT/lib" "$BIN"
cp "$HERE/review.sh" "$ROOT/"
cp "$HERE/lib/models.sh" "$HERE/lib/tokens.py" "$HERE/lib/reviewlease.py" "$ROOT/lib/"
# Fake usage.py: `gate` refuses the providers in FAKE_GATED; anything else records its last arg.
cat > "$ROOT/lib/usage.py" <<'PY'
import json, os, re, sys
args = sys.argv[1:]
if "effective-review-strategy" in args:
    strategy = args[args.index("--strategy") + 1]
    if strategy == "cross-provider":
        print(json.dumps({"strategy": strategy, "reason": None}))
    elif "cursor" in os.environ.get("FAKE_GATED", "").split():
        print(json.dumps({"strategy": "cross-provider", "reason": "cursor gated until 2026-11-03T10:30:00-05:00"}))
    else:
        print(json.dumps({"strategy": strategy, "reason": None}))
    sys.exit(0)
if "gate" in args:
    provider = args[args.index("--provider") + 1]
    if provider in os.environ.get("FAKE_GATED", "").split():
        print(json.dumps({"provider": provider, "state": "over", "reason": "5h 71% >= 70%"}))
        sys.exit(3)
    sys.exit(0)
if "parse-limit" in args:
    print(json.dumps({"provider": args[args.index("--provider") + 1], "exact_cli_text": args[args.index("--text") + 1],
                      "reset_epoch": 1760396400, "reset_at": "2025-10-13T19:00:00-04:00"}))
    sys.exit(0)
def _load_result(path):
    raw = open(path).read()
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except ValueError:
        pass
    for line in reversed(raw.splitlines()):
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "result" in obj:
            return obj
    return None

if "is-error" in args:
    result = _load_result(args[args.index("--result-json") + 1]) if "--result-json" in args else None
    err = isinstance(result, dict) and (
        result.get("is_error") or result.get("subtype") in ("error", "error_during_execution"))
    sys.exit(0 if err else 1)
if "live-limit" in args:
    # Mirror usage.live_limit_banner: provider JSON is_error, else banner + (nonzero/no final).
    transcript = open(args[args.index("--transcript-file") + 1]).read() if "--transcript-file" in args else ""
    final = ""
    if "--final-file" in args:
        try:
            final = open(args[args.index("--final-file") + 1]).read()
        except OSError:
            pass
    exit_code = int(args[args.index("--exit") + 1]) if "--exit" in args else None
    result = _load_result(args[args.index("--result-json") + 1]) if "--result-json" in args else None
    if isinstance(result, dict):
        if result.get("outcome") == "limit_hit":
            print(" ".join(str(result.get("limit_text") or "").split()) or "provider reported limit_hit")
            sys.exit(0)
        err = result.get("is_error") or result.get("subtype") in ("error", "error_during_execution")
        if err:
            blob = "\n".join(str(result.get(k) or "") for k in ("result", "error", "error_message", "message"))
            m = re.search(
                r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
                r"|quota exceeded|out of (?:usage|credits|quota)\b)", blob, re.I | re.M)
            if m:
                end = blob.find("\n", m.start())
                print(blob[m.start():end if end >= 0 else len(blob)].strip())
                sys.exit(0)
            if re.search(r"usage.?limit|hit your session limit|quota.?exceeded", blob, re.I) and blob.strip():
                print(blob.strip().splitlines()[0].strip())
                sys.exit(0)
            # Capacity/overload in is_error JSON is not a quota limit — leave it for capacity fallback.
            sys.exit(1)
    m = re.search(
        r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
        r"|quota exceeded|out of (?:usage|credits|quota)\b)", transcript, re.I | re.M)
    if not m:
        sys.exit(1)
    end = transcript.find("\n", m.start())
    banner = transcript[m.start():end if end >= 0 else len(transcript)].strip()
    if final.strip() and (exit_code is None or exit_code == 0):
        sys.exit(1)
    print(banner)
    sys.exit(0)
open(args[1] + "/limits.txt", "a").write(args[-1] + "\n")
PY

git init -q -b main "$REPO"
git -C "$REPO" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
git -C "$REPO" checkout -q -b roadmap/x
print 'x = 1' > "$REPO/x.py"
git -C "$REPO" add x.py && git -C "$REPO" -c user.name=t -c user.email=t@t commit -q -m feat

# Each fake says which provider it is; FAKE_<P> picks clean | findings | limit | json-limit.
for p in codex agent claude; do
  cat > "$BIN/$p" <<SH
#!/bin/zsh
name=$p; mode="\${FAKE_${p:u}:-clean}"
if [ -n "\$CAPTURE_PROMPT" ]; then print -r -- "\$*" > "\$CAPTURE_PROMPT"; fi
last=""; while [ \$# -gt 0 ]; do [ "\$1" = "-o" ] && { last="\$2"; shift; }; shift; done
[ "\$name" = codex ] && print "session id: 11111111-2222-3333-4444-555555555555"
print -r -- "echoed prompt: VERDICT: clean      (no findings)"
[ "\$mode" = limit ] && { print "You've hit your usage limit. Resets 5pm."; exit 1; }
[ "\$mode" = capacity ] && { print "ERROR: Selected model is at capacity. Please try a different model."; exit 1; }
[ "\$mode" = http503 ] && { print "stream error: unexpected status 503 Service Unavailable"; exit 1; }
# Zero-exit JSON with is_error:true — only detectable via --result-json (not transcript alone).
[ "\$mode" = json-limit ] && {
  python3 -c 'import json; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":"You'\''ve hit your usage limit. Resets 5pm.","session_id":"json-limit-session"}))'
  exit 0
}
# Zero-exit overload in JSON: must fail over as capacity, not end as unparsed.
[ "\$mode" = json-capacity ] && {
  python3 -c 'import json; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":"Selected model is at capacity. Please try a different model.","session_id":"json-capacity-session"}))'
  exit 0
}
body="reviewer=\$name
x.py:1 bug: something
VERDICT: \$mode"
# echo: the transcript carries a verdict line, the final answer none.
[ "\$mode" = echo ] && { print "VERDICT: clean"; body="reviewer=\$name
I ran out of time before deciding."; }
if [ -n "\$last" ]; then print -r -- "\$body" > "\$last"
else python3 -c 'import json, sys; print(json.dumps({"type": "result", "result": sys.argv[1], "session_id": sys.argv[2] + "-session"}))' "\$body" "\$name"
fi
SH
  chmod +x "$BIN/$p"
done

review() {  # review WORKER ROUND [env...]
  local w="$1" r="$2"; shift 2
  # These cases are the cross-provider strategy (the default is cursor-then-codex since b6b5558).
  env ADVANCE_ROADMAP_ROOT="$ROOT" ADVANCE_ROADMAP_REVIEW_STRATEGY=cross-provider ADVANCE_ROADMAP_CODEX_BIN="$BIN/codex" \
    ADVANCE_ROADMAP_AGENT_BIN="$BIN/agent" ADVANCE_ROADMAP_CLAUDE_BIN="$BIN/claude" "$@" \
    zsh "$ROOT/review.sh" --repo "$REPO" --worker "$w" --round "$r" --log "$LOG" --base main
}
review_default() {  # default strategy, allowing the fake usage resolver to exercise fallback
  local w="$1" r="$2"; shift 2
  env ADVANCE_ROADMAP_ROOT="$ROOT" ADVANCE_ROADMAP_CODEX_BIN="$BIN/codex" \
    ADVANCE_ROADMAP_AGENT_BIN="$BIN/agent" ADVANCE_ROADMAP_CLAUDE_BIN="$BIN/claude" "$@" \
    zsh "$ROOT/review.sh" --repo "$REPO" --worker "$w" --round "$r" --log "$LOG" --base main
}
field() { python3 -c "import json; print(json.loads(open('$LOG').readlines()[-1])['$1'])"; }
check() { [ "$2" = "$3" ] || { echo "FAIL [$1]: expected '$3', got '$2'"; exit 1; }; echo "ok   $1"; }

# Cursor is disabled even when a saved strategy or reviewer allocation asks for it.
review codex 1 FAKE_CLAUDE=clean >/dev/null
check "codex work uses Claude" "$(field reviewer)" claude
check "clean verdict" "$(field verdict)" clean
review claude 1 FAKE_CODEX=findings >/dev/null
check "Claude work uses Codex" "$(field reviewer)" codex
check "findings verdict" "$(field verdict)" findings
review_default codex 1 ADVANCE_ROADMAP_REVIEW_STRATEGY=cursor-then-codex FAKE_CLAUDE=clean >/dev/null
check "legacy strategy routes cross-provider" "$(field strategy)" cross-provider
check "legacy strategy does not invoke Cursor" "$(field reviewer)" claude
review_default codex 1 ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY=cursor-then-codex FAKE_CLAUDE=clean >/dev/null
check "legacy saved strategy does not invoke Cursor" "$(field reviewer)" claude
rc=0; review codex 1 ADVANCE_ROADMAP_BUDGET_REVIEWERS=cursor FAKE_AGENT=clean >/dev/null || rc=$?
check "Cursor-only saved reservation cannot invoke Cursor" "$rc" 3
check "unavailable reviewer logged" "$(field verdict)" unavailable
rc=0; review codex 1 FAKE_GATED=claude FAKE_AGENT=clean >/dev/null || rc=$?
check "gated Claude does not fall back to Cursor" "$rc" 3
check "gate logged as unavailable" "$(field verdict)" unavailable
review cursor 1 FAKE_CODEX=limit FAKE_CLAUDE=clean >/dev/null
check "legacy Cursor worker reviewed by Claude after Codex limit" "$(field reviewer)" claude
check "limited reviewer is recorded" "$(python3 -c "import json; print(json.loads(open('$LOG').readlines()[-1])['limited'][0]['provider'])")" codex
review cursor 1 FAKE_CODEX=capacity FAKE_CLAUDE=clean >/dev/null
check "capacity fails over for legacy worker" "$(field reviewer)" claude
check "capacity is recorded" "$(field capacity)" "['codex']"
review cursor 1 FAKE_CODEX=echo >/dev/null
check "transcript-only verdict is unparsed" "$(field verdict)" unparsed
rc=0; review codex 1 FAKE_CLAUDE=json-limit >/dev/null || rc=$?
check "JSON quota error makes review unavailable" "$rc" 3
check "JSON limit recorded" "$(python3 -c "import json; print(json.loads(open('$LOG').readlines()[-1])['limited'][0]['provider'])")" claude
rc=0; review codex 1 FAKE_CLAUDE=json-capacity >/dev/null || rc=$?
check "JSON capacity makes review unavailable" "$rc" 3
check "JSON capacity recorded" "$(field capacity)" "['claude']"
review codex 1 FAKE_CLAUDE=findings >/dev/null
check "Claude JSON verdict is parsed" "$(field verdict)" findings
check "review session recorded" "$(field session_id)" claude-session
review codex 1 ADVANCE_ROADMAP_INCREMENTAL=1 ADVANCE_ROADMAP_BUDGET_REVIEWERS=claude FAKE_CLAUDE=clean >/dev/null
rc=0; review codex 2 ADVANCE_ROADMAP_INCREMENTAL=1 ADVANCE_ROADMAP_BUDGET_REVIEWERS=claude FAKE_CLAUDE=clean >/dev/null || rc=$?
check "incremental reviewer cannot reuse reservation" "$rc" 3
review codex 1 ADVANCE_ROADMAP_CONTINUOUS=1 ADVANCE_ROADMAP_INCREMENTAL=1 FAKE_CLAUDE=clean >/dev/null
review codex 2 ADVANCE_ROADMAP_CONTINUOUS=1 ADVANCE_ROADMAP_INCREMENTAL=1 FAKE_CLAUDE=clean >/dev/null
check "continuous reviews can repeat without a reservation" "$(field verdict)" clean
rc=0; review codex 3 ADVANCE_ROADMAP_CONTINUOUS=1 ADVANCE_ROADMAP_INCREMENTAL=1 FAKE_GATED=claude >/dev/null || rc=$?
check "continuous reviews still obey the live gate" "$rc" 3
capture="$TMP/reviewer-prompt"
review codex 1 CAPTURE_PROMPT="$capture" FAKE_CLAUDE=clean >/dev/null
grep -Fq 'Classify each finding as an instance' "$capture" || { echo "FAIL: missing invariant classification"; exit 1; }
echo "all passed"
