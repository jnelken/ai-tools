#!/bin/zsh
# review.sh with fake providers: reviewer is never the worker's provider, limits
# fail over, the verdict comes only from the reviewer's final answer (never the
# echoed prompt), and every round leaves one line in the review log.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-review-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM
# Fixture commits must not depend on the user's signing setup or hooks.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

ROOT="$TMP/root"; BIN="$TMP/bin"; REPO="$TMP/repo"; LOG="$TMP/reviews.jsonl"
mkdir -p "$ROOT/lib" "$BIN"
cp "$HERE/review.sh" "$ROOT/"
cp "$HERE/lib/models.sh" "$HERE/lib/tokens.py" "$ROOT/lib/"
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

out="$(review cursor 1 FAKE_CODEX=findings)"
check "cursor's work → codex reviews"        "$(field reviewer)" codex
check "reviewer session id logged"           "$(field session_id)" 11111111-2222-3333-4444-555555555555
check "verdict from the final answer only"   "$(field verdict)" findings
case "$out" in *"x.py:1 bug"*) echo "ok   findings printed" ;; *) echo "FAIL: no findings in output"; exit 1 ;; esac

review codex 2 FAKE_AGENT=clean >/dev/null
check "codex's work → cursor reviews"        "$(field reviewer)" cursor
check "clean verdict"                         "$(field verdict)" clean
check "round recorded"                        "$(field round)" 2
review_default codex 3 FAKE_AGENT=clean >/dev/null
check "Cursor available keeps default strategy" "$(field strategy)" cursor-then-codex

out="$(review_default cursor 1 FAKE_GATED=cursor FAKE_CODEX=clean)"
check "gated Cursor falls back to Codex" "$(field reviewer)" codex
check "fallback strategy is logged" "$(field strategy)" cross-provider
check "fallback reason is logged" "$(field fallback_reason)" "cursor gated until 2026-11-03T10:30:00-05:00"
case "$out" in *"strategy=cross-provider"*) echo "ok   fallback execution is cross-provider" ;; *) echo "FAIL: fallback strategy missing from output"; exit 1 ;; esac

review_default codex 1 ADVANCE_ROADMAP_REVIEW_STRATEGY=cross-provider FAKE_GATED=cursor FAKE_AGENT=clean >/dev/null
check "explicit cross-provider remains configured" "$(field strategy)" cross-provider
check "explicit strategy has no fallback reason" "$(python3 -c "import json; print('fallback_reason' in json.loads(open('$LOG').readlines()[-1]))")" False

review_default codex 1 ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY=cross-provider \
  ADVANCE_ROADMAP_BUDGET_REVIEW_FALLBACK_REASON="cursor gated until reserved reset" \
  ADVANCE_ROADMAP_BUDGET_REVIEWERS=claude FAKE_AGENT=clean >/dev/null
check "batch reservation pins its strategy" "$(field strategy)" cross-provider
check "batch reservation pins fallback reason" "$(field fallback_reason)" "cursor gated until reserved reset"
check "reserved reviewer is used after Cursor resets" "$(field reviewer)" claude

out="$(review cursor 1 FAKE_CODEX=limit FAKE_CLAUDE=clean)"
check "limited codex → claude reviews"       "$(field reviewer)" claude
case "$out" in *"reviewer=codex"*) echo "FAIL: limited reviewer's text leaked"; exit 1 ;; *) echo "ok   no stale text from limited reviewer" ;; esac
grep -q "usage limit" "$ROOT/limits.txt" && echo "ok   limit recorded" || { echo "FAIL: limit not recorded"; exit 1; }
check "limited reviewer logged with its reset" \
  "$(python3 -c "import json; l = json.loads(open('$LOG').readlines()[-1])['limited']; print([(h['provider'], h['cause'], h['reset_epoch']) for h in l])")" \
  "[('codex', \"You've hit your usage limit. Resets 5pm.\", 1760396400)]"

rc=0; review claude 1 FAKE_CODEX=limit FAKE_AGENT=limit >/dev/null || rc=$?
check "all reviewers limited → exit 3"       "$rc" 3
check "logged as unavailable"                "$(field verdict)" unavailable
check "one log row per completed review stage" "$(wc -l < "$LOG" | tr -d ' ')" 9

rc=0; review cursor 1 ADVANCE_ROADMAP_CODEX_BIN=/nonexistent FAKE_CLAUDE=clean >/dev/null || rc=$?
check "missing binary → next reviewer"       "$(field reviewer)" claude

review cursor 1 FAKE_GATED=codex FAKE_CLAUDE=clean >/dev/null
check "codex over its gate → claude reviews" "$(field reviewer)" claude
check "gated reviewer logged"                "$(field gated)" "['codex']"

rc=0; review claude 1 FAKE_GATED="codex cursor" >/dev/null || rc=$?
check "every reviewer gated → exit 3"        "$rc" 3
check "gated round logged as unavailable"    "$(field verdict)" unavailable

: > "$ROOT/limits.txt"
out="$(review cursor 1 FAKE_CODEX=capacity FAKE_CLAUDE=clean)"
check "codex at capacity → claude reviews"   "$(field reviewer)" claude
check "capacity is not unparsed"             "$(field verdict)" clean
check "capacity reviewer logged"             "$(field capacity)" "['codex']"
check "skipped reviewer's session not kept"  "$(field session_id)" claude-session
[ -s "$ROOT/limits.txt" ] && { echo "FAIL: capacity recorded as a quota limit"; exit 1; } || echo "ok   capacity is not a quota hit"
case "$out" in *"at capacity"*) echo "ok   capacity skip printed" ;; *) echo "FAIL: no capacity note"; exit 1 ;; esac

rc=0; review claude 1 FAKE_CODEX=capacity FAKE_AGENT=http503 >/dev/null || rc=$?
check "every reviewer at capacity → exit 3"  "$rc" 3
check "capacity round logged as unavailable" "$(field verdict)" unavailable
check "both capacity reviewers logged"       "$(field capacity)" "['codex', 'cursor']"

out="$(review cursor 1 FAKE_CODEX=echo)"
check "a transcript-only verdict is unparsed" "$(field verdict)" unparsed

# Successful final answer + banner on its own line in the transcript is not a live limit.
# (A prefix like "echoed prior log:" would dodge the anchored detector and not exercise suppression.)
: > "$ROOT/limits.txt"
cat > "$BIN/codex" <<'SH'
#!/bin/zsh
last=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { last="$2"; shift; }; shift; done
print "echoed prior log:"
print "You've hit your usage limit. Resets 5pm."
print -r -- "reviewer=codex
VERDICT: clean" > "$last"
print "session id: 11111111-2222-3333-4444-555555555555"
SH
chmod +x "$BIN/codex"
review cursor 1 >/dev/null
check "echoed banner + clean final is clean" "$(field verdict)" clean
check "echoed banner does not fail over"     "$(field reviewer)" codex
[ -s "$ROOT/limits.txt" ] && { echo "FAIL: echoed banner recorded as a limit"; exit 1; } \
  || echo "ok   echoed banner is not a live limit"
case "$out" in *"VERDICT: unparsed"*) echo "ok   unparsed printed" ;; *) echo "FAIL: no unparsed note"; exit 1 ;; esac

# Zero-exit JSON is_error:true must fail over (needs --result-json); Claude is the next reviewer.
: > "$ROOT/limits.txt"
# Recreate the default codex stub first (overwritten above), then run with agent json-limit.
for p in codex; do
  cat > "$BIN/$p" <<SH
#!/bin/zsh
name=$p; mode="\${FAKE_${p:u}:-clean}"
last=""; while [ \$# -gt 0 ]; do [ "\$1" = "-o" ] && { last="\$2"; shift; }; shift; done
print "session id: 11111111-2222-3333-4444-555555555555"
print -r -- "echoed prompt: VERDICT: clean      (no findings)"
body="reviewer=\$name
x.py:1 bug: something
VERDICT: \$mode"
if [ -n "\$last" ]; then print -r -- "\$body" > "\$last"; fi
SH
  chmod +x "$BIN/$p"
done
review codex 1 FAKE_AGENT=json-limit FAKE_CLAUDE=clean >/dev/null
check "json is_error limit fails over"       "$(field reviewer)" claude
check "json is_error verdict is clean"       "$(field verdict)" clean
[ -s "$ROOT/limits.txt" ] || { echo "FAIL: is_error JSON was not recorded as a limit"; exit 1; }
echo "ok   json is_error is a live limit"

# Zero-exit JSON overload must use capacity fallback (not unparsed), without a quota hit.
: > "$ROOT/limits.txt"
out="$(review codex 1 FAKE_AGENT=json-capacity FAKE_CLAUDE=clean)"
check "json is_error capacity fails over"    "$(field reviewer)" claude
check "json is_error capacity is clean"      "$(field verdict)" clean
check "json capacity reviewer logged"        "$(field capacity)" "['cursor']"
[ -s "$ROOT/limits.txt" ] && { echo "FAIL: json capacity recorded as a quota limit"; exit 1; } \
  || echo "ok   json capacity is not a quota hit"
case "$out" in *"at capacity"*) echo "ok   json capacity skip printed" ;; *) echo "FAIL: no json capacity note"; exit 1 ;; esac

out="$(review codex 1 FAKE_AGENT=findings)"
check "cursor's JSON final answer is parsed" "$(field verdict)" findings
check "cursor reviewer session id logged"    "$(field session_id)" agent-session
case "$out" in *"echoed prompt"*) echo "FAIL: transcript printed instead of the final answer"; exit 1 ;; *) echo "ok   only the final answer printed" ;; esac

# Cursor completes; Codex is then gated before launch. Its stage has no session.
# Clear the earlier default-strategy integration row so it cannot satisfy the one-time Codex pass.
: > "$LOG"
rc=0; review cursor 1 ADVANCE_ROADMAP_REVIEW_STRATEGY=cursor-then-codex FAKE_AGENT=clean FAKE_GATED=codex >/dev/null || rc=$?
check "required Codex gate → exit 3" "$rc" 3
check "required Codex gate logged" "$(field reviewer)" codex
check "gated Codex has no Cursor session" "$(field session_id)" None
# A batch reservation selects one cross-provider reviewer. Limits cannot
# spill to a healthy provider which has no lease.
rc=0; review cursor 1 ADVANCE_ROADMAP_BUDGET_REVIEWERS=codex FAKE_GATED=codex FAKE_CLAUDE=clean >/dev/null || rc=$?
check "unleased reviewer cannot be a quota fallback" "$rc" 3
check "unleased fallback is unavailable" "$(field verdict)" unavailable
check "unleased fallback recorded as gated" "$(field gated)" "['codex', 'claude']"

review cursor 1 ADVANCE_ROADMAP_BUDGET_REVIEWERS=claude FAKE_CODEX=findings FAKE_CLAUDE=clean >/dev/null
check "reserved cross-provider reviewer is used" "$(field reviewer)" claude

echo "all passed"
