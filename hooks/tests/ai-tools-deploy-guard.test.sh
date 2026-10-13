#!/usr/bin/env bash
# Behaviour matrix for ai-tools-deploy-guard.sh.
# Run: bash hooks/tests/ai-tools-deploy-guard.test.sh
HOOK="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/ai-tools-deploy-guard.sh}"
pass=0; fail=0
command -v jq >/dev/null 2>&1 || { echo 'FAIL: tests require jq'; exit 1; }

TMP_DIR=$(mktemp -d) || exit 1
trap 'rm -rf "$TMP_DIR"' EXIT
TMP_DIR=$(cd "$TMP_DIR" && pwd -P)
CLONE="$TMP_DIR/deploy clone"
DEV_CHECKOUT="$TMP_DIR/dev checkout"
mkdir -p "$CLONE/skills/example" "$CLONE/lib" "$DEV_CHECKOUT" "$TMP_DIR/outside"
git init -q "$CLONE" || exit 1
printf 'memory.md\n__pycache__/\n' > "$CLONE/.gitignore"
printf 'tracked\n' > "$CLONE/skills/example/tracked.md"
git -C "$CLONE" add .gitignore skills/example/tracked.md || exit 1
mkdir -p "$CLONE/skills/tracked-memory"
printf 'tracked despite ignore rule\n' > "$CLONE/skills/tracked-memory/memory.md"
git -C "$CLONE" add -f skills/tracked-memory/memory.md || exit 1
ln -s "$CLONE/skills/example" "$TMP_DIR/installed-skill"
ln -s 'deploy clone/skills/example/tracked.md' "$TMP_DIR/installed-file.md"
ln -s 'deploy clone/skills/example/new.md' "$TMP_DIR/dangling-file.md"
ln -s "$CLONE" "$TMP_DIR/clone-alias"
mkdir -p "$CLONE-prefix"

expect() { # expect <exit code> <label> <tool> <path key> <path> <dev-relative path>
  local got reason
  jq -n --arg tool "$3" --arg key "$4" --arg path "$5" \
    '{tool_name:$tool,tool_input:{($key):$path}}' |
    (cd "$TMP_DIR" && AI_TOOLS_HOME="$TMP_DIR/clone-alias///" AI_TOOLS_DEV="$DEV_CHECKOUT" \
      "$BASH" "$HOOK") > "$TMP_DIR/stdout" 2> "$TMP_DIR/stderr"
  got=$?
  reason=$(cat "$TMP_DIR/stderr")
  if [ "$got" = "$1" ] && [ ! -s "$TMP_DIR/stdout" ] &&
    { [ "$got" = 0 ] || [[ "$reason" == ai-tools-deploy-guard:* && "$reason" == *"$DEV_CHECKOUT/$6 instead, commit on main, and the post-commit hook redeploys." ]]; }; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    echo "FAIL: $2 — want exit $1, got $got ($reason)"
  fi
}

while IFS='|' read -r expected label tool key path relpath; do
  expect "$expected" "$label" "$tool" "$key" "$path" "$relpath"
done <<CASES
2|tracked file|Edit|file_path|$CLONE/skills/example/tracked.md|skills/example/tracked.md
2|symlinked skill directory|Edit|file_path|$TMP_DIR/installed-skill/tracked.md|skills/example/tracked.md
2|per-file symlink|MultiEdit|file_path|$TMP_DIR/installed-file.md|skills/example/tracked.md
2|dangling per-file symlink|Write|file_path|$TMP_DIR/dangling-file.md|skills/example/new.md
2|new file|Write|file_path|$CLONE/new.md|new.md
2|new nested directories|Write|file_path|$TMP_DIR/installed-skill/new/nested/file.md|skills/example/new/nested/file.md
0|ignored memory|Edit|file_path|$CLONE/skills/example/memory.md|
0|ignored memory via symlink|Write|file_path|$TMP_DIR/installed-skill/memory.md|
2|tracked memory stays protected|Edit|file_path|$CLONE/skills/tracked-memory/memory.md|skills/tracked-memory/memory.md
0|ignored Python cache|Write|file_path|$CLONE/lib/__pycache__/module.pyc|
0|outside file|Edit|file_path|$TMP_DIR/outside/file.md|
0|clone prefix is outside|Write|file_path|$CLONE-prefix/file.md|
2|notebook path|NotebookEdit|notebook_path|$CLONE/notebook.ipynb|notebook.ipynb
2|relative path|Edit|file_path|deploy clone/skills/example/tracked.md|skills/example/tracked.md
2|dot segments after missing parent|Write|file_path|$CLONE/new/../file.md|file.md
0|dot segments escape clone|Write|file_path|$CLONE/new/../../outside/file.md|
CASES

# Malformed/missing paths fail open, even when a prefix is valid JSON.
for input in 'not json' '{' '{"tool_input":{"file_path":"x"}} {' '{}' '{"tool_input":{"file_path":null}}' '{"tool_input":{"file_path":123}}'; do
  printf '%s' "$input" | AI_TOOLS_HOME="$CLONE" "$BASH" "$HOOK" >/dev/null 2>&1
  got=$?
  if [ "$got" = 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: malformed input — want exit 0, got $got"; fi
done

# Empty PATH only removes the hook's dependencies; Bash uses an absolute path.
printf '%s' '{"tool_input":{"file_path":"/anything"}}' | PATH=/nonexistent "$BASH" "$HOOK" >/dev/null 2>&1
got=$?
if [ "$got" = 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: missing jq — want exit 0, got $got"; fi

echo "ai-tools-deploy-guard: $pass passed, $fail failed"
((fail == 0))
