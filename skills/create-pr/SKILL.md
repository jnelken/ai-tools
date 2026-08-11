---
name: create-pr
description: Generate a dead-simple PR title/body from the net diff vs base, backfill a Linear ticket, and open the PR as a draft (or update an existing one). Use when the user wants to open a PR from the current branch, e.g. "create a PR", "open a PR for this", "push this and make a PR". Preserves an existing non-empty PR body — only normalizes the title. For force-rewriting an existing description instead, see [[refresh-pr]].
---

# Create PR

## Overview

Push the current branch, generate (or preserve) a minimal PR description, backfill a Linear ticket, and open the PR **as a draft**. New PRs start as drafts on purpose — the Post-PR Workflow (in the user's global CLAUDE.md) marks them ready and badges the description once the PR has actually been shared to Slack. Don't undraft or badge here; that's a different step's job.

## Gather context first

Before anything else, collect:
- `git status`
- `git branch --show-current`
- `git diff HEAD` (staged and unstaged changes)
- `git log --oneline -10`

## Steps

1. Get the base branch: `git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`
2. Get the **net diff vs base** using `git diff <base-branch>...HEAD` — the description must reflect this final state, not per-commit history.
3. Get the commit log using `git log <base-branch>..HEAD --oneline` (only as a hint; never describe intermediate or reverted work).
4. Check if a PR already exists for this branch using `gh pr view --json number,title,body 2>&1`.
5. **If a PR already exists with a non-empty body**: skip description generation entirely — only update the title (for Linear ticket normalization). Jump to step 7.
6. Generate the PR title and body:
   - **Title**: clear, concise, imperative; no "PR:" prefix; Linear ticket appended in parentheses (handled by the backfill section below).
   - **Body**: follow the **PR Descriptions** format in the user's global CLAUDE.md (`~/.claude/CLAUDE.md`) exactly — that doc is the single source of truth for section shape, skimmability rules, and what to omit. Don't duplicate that guidance here; read it fresh each time in case it's changed.
   - No `🤖 Generated with` footer.
7. Push the branch to origin if not already pushed (`git push -u origin <branch>`). **Before pushing**, if the current branch equals the base branch (e.g. you're on `main`/`master` itself), STOP and confirm with the user — running a PR workflow from the base branch is almost certainly a mistake, and pushing could ship unintended work to production.
8. If a PR exists with a non-empty body, only update the title: `gh pr edit --title "..."`. If a PR exists with an empty body, edit title and body. Otherwise create a new PR **as a draft**. `gh` rejects `--draft` combined with `--web` (`the --draft flag is not supported with --web`), so create it headless and open the browser as a separate step: `gh pr create --draft --title "..." --body "..."` then `gh pr view --web`.
9. Use a HEREDOC for the PR body to preserve formatting (body content per the global CLAUDE.md format resolved in step 6):

   ```bash
   gh pr create --draft --title "Imperative title (CON-1234)" --body "$(cat <<'EOF'
   ...body per global CLAUDE.md PR Descriptions format...
   EOF
   )"
   gh pr view --web
   ```

10. Output the PR URL (printed by `gh pr create`, or `gh pr view --json url -q .url`).

## Linear ticket backfill

After determining the PR title (step 6), perform these steps before pushing/creating the PR:

### Detect existing ticket
Check for a Linear ticket ID (pattern: `[A-Za-z]+-\d+`) in:
1. The branch name (from `git branch --show-current`)
2. The proposed PR title

Normalize any found ticket to uppercase (e.g. `con-123` → `CON-123`).

Strip any existing ticket decoration from the title before re-applying:
- Leading prefix: `[CON-123] PR title` → `PR title`
- Trailing suffix: `PR title (CON-123)` → `PR title`

### Ticket found — ask whether to use it or create a new one
If a ticket is found, **ask the user**:
> "Found Linear ticket TICKET-ID in branch/title. Use it, or create a new one? (use/new)"

If the user says **use**: format the final title as `PR title (CON-123)` and skip to pushing.

If the user says **new**: create a new Linear issue using the steps below.

### No ticket found — create one automatically
If no ticket is found, proceed directly to creating a Linear issue without asking.

### Creating a Linear issue
Use `mcp__claude_ai_Linear__list_teams` to find the team:
- Prefer a team with key `CON` or name `Engineering` — select it automatically without asking.
- Otherwise show the list and ask the user which team to use.

Use `mcp__claude_ai_Linear__save_issue` to create the issue:
- `team`: resolved team name or ID
- `title`: the cleaned PR title (no ticket decoration)
- `description`: 2-3 sentence summary + `\nBranch: <branch>\nPR: <url>` (omit PR line if creating before the PR exists)

Extract `identifier` (e.g. `CON-42`) and `url` from the response. If creation fails, report the error and ask whether to proceed without a ticket.

Format the final PR title as: `PR title (IDENTIFIER)` and print:
> `Created Linear issue IDENTIFIER: <url>`

Prepend `Linear issue: <url>` to the very top of the PR body (replacing any existing `Linear issue:` line).

## Important

- Body shape comes from the user's global CLAUDE.md — do not hardcode or duplicate a format here.
- DO NOT use EnterPlanMode or ExitPlanMode tools.
- DO NOT ask for user approval on the PR itself — just analyze and create.
- The Linear ticket check is the one exception — only ask if a ticket is already found (use it or create new); auto-create without asking if none is found.
- You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance.
- Return the PR URL so the user can view it.
