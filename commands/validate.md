---
description: Review a PR with code analysis and Chrome MCP UI validation; saves screenshots locally and posts everything as a PR comment
argument-hint: "[PR number | deploy-preview-URL]"
allowed-tools: ["Agent", "Bash", "Glob", "Grep", "Read", "Task", "mcp__chrome-devtools__take_screenshot", "mcp__chrome-devtools__navigate_page", "mcp__chrome-devtools__take_snapshot", "mcp__chrome-devtools__click", "mcp__chrome-devtools__evaluate_script", "mcp__chrome-devtools__wait_for", "mcp__chrome-devtools__fill", "mcp__chrome-devtools__type_text", "mcp__chrome-devtools__resize_page", "mcp__chrome-devtools__list_pages", "mcp__chrome-devtools__select_page", "mcp__chrome-devtools__new_page"]
---

# Validate PR

Perform a full code review **and** Chrome MCP UI validation of a PR, then post the complete findings — including saved screenshots — as a PR comment.

**Argument:** "$ARGUMENTS" — a PR number, a deploy-preview URL, or empty (use current branch).

---

## Step 1 — Identify the PR

- If $ARGUMENTS is a number, use it as the PR number.
- If $ARGUMENTS is a URL, extract the deploy-preview host from it and note it for Step 4.
- If $ARGUMENTS is empty, run `gh pr view --json number --jq '.number'` on the current branch.

Run `gh pr view <number>` and `gh pr diff <number>` to get the full PR context: title, description, and diff.

---

## Step 2 — Code Review

Analyse the diff and produce a structured review covering:

- **Overview** — what the PR does (1–3 sentences)
- **Per-fix breakdown** — for each bug/feature in the PR, assess correctness of the approach
- **Code quality** — type safety, hook usage, component separation per AGENTS.md rules
- **Risks** — cache invalidation, edge cases, regressions

Keep it concise. Flag blockers with ❌, concerns with ⚠️, and positives with ✅.

---

## Step 3 — Determine test URL

Priority order:

1. If a deploy-preview URL was supplied in $ARGUMENTS, use it.
2. Check if the current git worktree has a running dev server: `lsof -i :5173 -t 2>/dev/null` — if a PID exists, verify it's serving this branch by running `lsof -p <pid> | grep cwd` and confirming the path matches the current worktree.
3. If neither, check ports 5174, 5175.
4. If no local server is found, note "No test URL available — UI validation skipped" and jump to Step 6.

---

## Step 4 — Chrome MCP login

Check `AI_USER_PW` is set:
```bash
echo "${AI_USER_PW:?AI_USER_PW is not set — set it in .env or the shell before running /validate}"
```
If unset, stop and ask the user to set it.

Navigate Chrome to the test URL. Log in as `ai-user@concentro.io` / `$AI_USER_PW` if not already logged in (check for a session cookie or a page that shows "AI User" in the sidebar before attempting login).

---

## Step 5 — UI Validation

Create a screenshot output directory:
```bash
SCREENSHOT_DIR="/tmp/validate-pr$(gh pr view --json number --jq '.number' 2>/dev/null || echo 'local')"
mkdir -p "$SCREENSHOT_DIR"
```

For **each changed feature area** identified in the diff, exercise the relevant UI path and capture evidence:

- Use `take_screenshot` with `filePath: "$SCREENSHOT_DIR/<slug>.png"` so screenshots persist to disk.
- Navigate to the affected page/component; interact with it to reproduce the fixed/added behaviour.
- Take a screenshot **before** the interaction (showing the entry state) and **after** (showing the result) where meaningful.
- If a feature requires a collaboration backend (e.g. TipTap/Hocuspocus) and only a local dev server is available, note it and skip that feature — do not fabricate a result.

Common areas to check based on diff content (use judgment — only test what actually changed):

| Diff mentions | What to exercise |
|---|---|
| `ValueDisplayEditor` / `disableArrowNavigation` | Open a dashboard, click a VDE cell, press arrow keys — verify no navigation occurs |
| `DataGrid` / `headerOnly` / `hasRenderableContent` | Trigger a search that returns header-only groups; verify they render without empty-state fallback |
| `sidebar` / `overflow` | Collapse the sidebar, scroll it — verify items are accessible |
| `FilePopover` / `PopoverContent` | Open a checklist file popover near the viewport bottom — verify it clips and scrolls |
| `resolveCommentAuthor` / `initials` | Open Document Studio, add a comment as AI User — verify avatar shows name initials (e.g. "AU"), not "You" |
| `TestFilePanel` / `FileUploader` | Open File Classifier test panel — verify upload section is present |

---

## Step 6 — Compile and post PR comment

Build a single markdown comment that includes:

1. **Code Review** section (from Step 2)
2. **UI Validation** section — one sub-section per tested area, each with:
   - What was tested and the outcome (✅ / ❌ / ⚠️ / ⏭ skipped)
   - A note like: `📸 Screenshot saved: /tmp/validate-pr978/con-2601-comment-avatar.png`
3. A closing line:
   > Screenshots are saved locally in `$SCREENSHOT_DIR` — drag them into this comment on GitHub to attach.

Post with:
```bash
gh pr comment <number> --body "$(cat <<'BODY'
<the compiled markdown>
BODY
)"
```

Print the comment URL so the user can open it and drag in the screenshots.
