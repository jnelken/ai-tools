#!/bin/zsh
# review.sh with fake providers: reviewer is never the worker's provider, limits
# fail over, the last VERDICT line wins over the one echoed in the prompt, and
# every round leaves one line in the review log.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/advance-roadmap-review-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM

ROOT="$TMP/root"; BIN="$TMP/bin"; REPO="$TMP/repo"; LOG="$TMP/reviews.jsonl"
mkdir -p "$ROOT/lib" "$BIN"
cp "$HERE/review.sh" "$ROOT/"
print -r -- 'import sys; open(sys.argv[2] + "/limits.txt", "a").write(sys.argv[-1] + "\n")' > "$ROOT/lib/usage.py"

git init -q -b main "$REPO"
git -C "$REPO" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
git -C "$REPO" checkout -q -b roadmap/x
print 'x = 1' > "$REPO/x.py"
git -C "$REPO" add x.py && git -C "$REPO" -c user.name=t -c user.email=t@t commit -q -m feat

# Each fake says which provider it is; FAKE_<P> picks clean | findings | limit.
for p in codex agent claude; do
  cat > "$BIN/$p" <<SH
#!/bin/zsh
name=$p; mode="\${FAKE_${p:u}:-clean}"
last=""; while [ \$# -gt 0 ]; do [ "\$1" = "-o" ] && { last="\$2"; shift; }; shift; done
print -r -- "echoed prompt: VERDICT: clean      (no findings)"
[ "\$mode" = limit ] && { print "You've hit your usage limit. Resets 5pm."; exit 1; }
body="reviewer=\$name
x.py:1 bug: something
VERDICT: \$mode"
if [ -n "\$last" ]; then print -r -- "\$body" > "\$last"; else print -r -- "\$body"; fi
SH
  chmod +x "$BIN/$p"
done

review() {  # review WORKER ROUND [env...]
  local w="$1" r="$2"; shift 2
  env ADVANCE_ROADMAP_ROOT="$ROOT" ADVANCE_ROADMAP_CODEX_BIN="$BIN/codex" \
    ADVANCE_ROADMAP_AGENT_BIN="$BIN/agent" ADVANCE_ROADMAP_CLAUDE_BIN="$BIN/claude" "$@" \
    zsh "$ROOT/review.sh" --repo "$REPO" --worker "$w" --round "$r" --log "$LOG" --base main
}
field() { python3 -c "import json; print(json.loads(open('$LOG').readlines()[-1])['$1'])"; }
check() { [ "$2" = "$3" ] || { echo "FAIL [$1]: expected '$3', got '$2'"; exit 1; }; echo "ok   $1"; }

out="$(review cursor 1 FAKE_CODEX=findings)"
check "cursor's work → codex reviews"        "$(field reviewer)" codex
check "last VERDICT wins over echoed prompt" "$(field verdict)" findings
case "$out" in *"x.py:1 bug"*) echo "ok   findings printed" ;; *) echo "FAIL: no findings in output"; exit 1 ;; esac

review codex 2 FAKE_AGENT=clean >/dev/null
check "codex's work → cursor reviews"        "$(field reviewer)" cursor
check "clean verdict"                         "$(field verdict)" clean
check "round recorded"                        "$(field round)" 2

out="$(review cursor 1 FAKE_CODEX=limit FAKE_CLAUDE=clean)"
check "limited codex → claude reviews"       "$(field reviewer)" claude
case "$out" in *"reviewer=codex"*) echo "FAIL: limited reviewer's text leaked"; exit 1 ;; *) echo "ok   no stale text from limited reviewer" ;; esac
grep -q "usage limit" "$ROOT/limits.txt" && echo "ok   limit recorded" || { echo "FAIL: limit not recorded"; exit 1; }

rc=0; review claude 1 FAKE_CODEX=limit FAKE_AGENT=limit >/dev/null || rc=$?
check "all reviewers limited → exit 3"       "$rc" 3
check "logged as unavailable"                "$(field verdict)" unavailable
check "one log line per round"               "$(wc -l < "$LOG" | tr -d ' ')" 4

rc=0; review cursor 1 ADVANCE_ROADMAP_CODEX_BIN=/nonexistent FAKE_CLAUDE=clean >/dev/null || rc=$?
check "missing binary → next reviewer"       "$(field reviewer)" claude
echo "all passed"
