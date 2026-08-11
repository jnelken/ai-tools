---
name: refresh-pr
description: Force-rewrite an existing PR's title and description from the net diff vs base, regardless of what's there now (preserving screenshots and any Slack-share badge), then ensure a Linear ticket. Use when the user wants to regenerate/rewrite a stale PR description, e.g. "refresh the PR description", "rewrite this PR's summary", "the PR body is out of date". For preserving an existing non-empty body and only touching the title, see [[create-pr]] instead.
---

# Refresh PR

## Overview

This skill **force-rewrites** the PR description regardless of whether one already exists. Screenshots and a Slack-share badge already in the body are extracted and re-appended — everything else is regenerated from scratch. It never creates PRs headed anywhere but draft, and it never posts to Slack or changes draft/ready status itself.

## Gather context first

Before anything else, collect:
- `git status`
- `git branch --show-current`
- `git diff HEAD` (staged and unstaged changes)
- `git log --oneline -10`

## Steps

1. Get the base branch using `git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`.
2. Get the **net diff vs base** using `git diff <base-branch>...HEAD` — the rewritten description must reflect this final state, not per-commit history.
3. Get the commit log using `git log <base-branch>..HEAD --oneline` (only as a hint; never describe intermediate or reverted work).
4. Check if a PR already exists using `gh pr view --json number,title,body 2>&1`. If one exists, extract any markdown images from the current body (look for `![...](...)`  or `![...][...]` patterns) to preserve them in the new body. Also check for a trailing `📣 **Posted to Slack**` badge — if present, preserve it too (see step 5).
5. Derive a **fresh** PR title and body from the net diff — discard any existing PR title/body content entirely; never reuse or copy it (except what step 4 pulled out to preserve):
   - **Title**: clear, concise, imperative; derived from the net diff vs base, not from the existing PR title; no "PR:" prefix; Linear ticket appended in parentheses (handled by the backfill section below).
   - **Body**: follow the **PR Descriptions** format in the user's global CLAUDE.md (`~/.claude/CLAUDE.md`) exactly — that doc is the single source of truth for section shape, skimmability rules, and what to omit. Don't duplicate that guidance here; read it fresh each time in case it's changed.
   - **Screenshot preservation**: if the existing PR description contains any markdown images (syntax: `![...](...)` or `![...][...]`), extract them and append them to the end of the new body. Preserve them exactly as they are.
   - **Slack badge preservation**: if the existing PR description ends with a `---` + `📣 **Posted to Slack**` badge, re-append that same badge at the very end of the new body, after screenshots if any. This skill never posts to Slack itself — it's only carrying forward a signal set by the Post-PR Workflow so a refresh doesn't erase it.
   - No `🤖 Generated with` footer.
6. Apply the Linear ticket backfill (same rules as `create-pr`): detect ticket in branch/title — if found, ask whether to use it or create a new one; if not found, create one automatically.
7. Push the branch to origin if not already pushed (`git push -u origin <branch>`). **Before pushing**, if the current branch equals the base branch (e.g. you're on `main`/`master` itself), STOP and confirm with the user — running a PR workflow from the base branch is almost certainly a mistake, and pushing could ship unintended work to production.
8. If a PR exists, **overwrite** both title and body — always pass `--title`; never omit it: `gh pr edit --title "..." --body "..."`. This never touches draft/ready status either way. Otherwise create a new PR **as a draft**: `gh` rejects `--draft` combined with `--web`, so create headless and open the browser separately: `gh pr create --draft --title "..." --body "..."` then `gh pr view --web` (same reasoning as `create-pr`: stays a draft until the Post-PR Workflow shares it to Slack).
9. Output the PR URL.

## Important

- This skill always rewrites the description — use `create-pr` if you want to preserve an existing one.
- Body shape comes from the user's global CLAUDE.md — do not hardcode or duplicate a format here.
- DO NOT use EnterPlanMode or ExitPlanMode tools.
- DO NOT ask for user approval on the PR itself.
- The Linear ticket check is the one exception — only ask if a ticket is already found (use it or create new); auto-create without asking if none is found.
- Run multiple independent tool calls in parallel when possible.
- Return the PR URL so the user can view it.
