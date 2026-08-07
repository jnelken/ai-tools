---
name: backfill-linear-ticket
description: Create a Linear issue from the current git branch (or reuse one already found in the branch name/PR title) and attach it to the open GitHub PR's title and body. Use when the user wants to add/backfill a Linear ticket onto an existing branch or PR that doesn't have one yet, e.g. "backfill a ticket for this", "attach a Linear issue to this PR", "this branch needs a ticket".
---

# Backfill Linear Ticket

## Gather context first

Before anything else, collect:
- Current branch: `git branch --show-current`
- Open PR (if any): `gh pr view --json number,title,body,url 2>/dev/null || echo "no-pr"`

## Steps

### Step 1 — Guard rails

If the current branch is `main` or `master`, stop and tell the user to switch to a feature branch first.

### Step 2 — Detect an existing ticket

Search for a Linear ticket ID (pattern: `[A-Za-z]+-\d+`) in:
1. The branch name
2. The current PR title

Normalize any found ID to uppercase (e.g. `con-123` → `CON-123`).

- **Ticket found**: reuse it — skip to Step 4 with the found ticket. Print `Using existing ticket TICKET-ID`.
- **No ticket found**: continue to Step 3.

### Step 3 — Create a Linear issue

1. Use `mcp__claude_ai_Linear__list_teams` to get available teams.
   - If a team with key `CON` or name `Engineering` exists, select it automatically without asking.
   - Otherwise show the list and ask the user which team to use.

2. Derive the issue title from the PR title (or branch slug if no PR exists), stripping any existing ticket decoration:
   - `[CON-123] Title` → `Title`
   - `Title (CON-123)` → `Title`

3. Use `mcp__claude_ai_Linear__save_issue` to create the issue:
   - `team`: the resolved team name or ID
   - `title`: the cleaned title
   - `description`: 2-3 sentence summary of the change + `\nBranch: <branch>\nPR: <url>` (omit PR line if no PR exists)

4. Extract the `identifier` (e.g. `CON-42`) and `url` from the response.
   Print: `Created Linear issue IDENTIFIER: <url>`

### Step 4 — Sync the ticket into the PR

If no open PR exists, skip this step and remind the user to add the ticket to the PR when they create one.

1. Strip any existing ticket decoration from the current PR title:
   - `[CON-123] Title` → `Title`
   - `Title (CON-123)` → `Title`

2. Format the new title as: `Title (IDENTIFIER)`

3. Prepend `Linear issue: <url>` to the PR body, replacing any existing `Linear issue:` line.
   Preserve all other body content.

4. Run: `gh pr edit --title "..." --body "..."`

5. Print the final PR URL.

## Important

- DO NOT use EnterPlanMode or ExitPlanMode tools.
- Run multiple independent tool calls in parallel when possible.
- If Linear issue creation fails, report the error and ask whether to proceed without a ticket.
