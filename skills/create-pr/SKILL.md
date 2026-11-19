---
name: create-pr
description: Generate a dead-simple PR title/body from the net diff vs base, backfill a required CON ticket in the work environment, and open the PR as a draft by default; `draft=false` opens it ready (or update an existing one). In Concentro repos, a new PR whose CON ticket already has an open, unapproved PR stacks onto that PR instead of opening into main. Use when the user wants to open a PR from the current branch, e.g. "create a PR", "open a PR for this", "push this and make a PR". Preserves an existing non-empty PR body — only normalizes the title. For force-rewriting an existing description instead, see [[refresh-pr]].
---

# Create PR


**Personal DEV PRs do not need a ticket.** Reuse an existing ticket when present; otherwise
continue without one. Work CON PRs retain the mandatory backfill procedure below.

## Overview

Push the current branch, generate (or preserve) a minimal PR description, backfill a required CON ticket in the work environment, and open the PR **as a draft** (override with `draft=false`). New PRs start as drafts by default, on purpose — the Post-PR Workflow (in the user's global CLAUDE.md) marks them ready and badges the description once the PR has actually been shared to Slack. Don't undraft or badge here; that's a different step's job.

## Invocation

```
/create-pr                        # PR into the repo's default branch
/create-pr base=feature/<slug>    # sub-PR into a trunk branch (stacked PR)
/create-pr draft=false            # open ready-for-review instead of as a draft
```

`draft=<true|false>` (default `true`) sets the state the PR is created in. Honor `draft=false` only when explicitly passed; otherwise always draft. Options combine (`base=… draft=false`).

`base=<branch>` sets the PR's base for both the net diff and `gh pr create --base`. Without it, the base is the default branch unless step 2's auto-detection finds a trunk the branch was cut from, or, in a Concentro repo, step 4 stacks the PR onto the open PR of the same ticket.

**Trunks.** When cutting a new `feature/<slug>` trunk for closely-knit work (see the Post-PR Workflow section of the global CLAUDE.md), open the trunk's own PR into the default branch right away as a draft — run this skill from the trunk branch with no `base=`. That PR is what eventually gets announced, and it is the only way the sub-work gets a Netlify deploy preview, since previews only build for PRs based on main.

## Gather context first

Before anything else, collect:
- `git status`
- `git branch --show-current`
- `git diff HEAD` (staged and unstaged changes)
- `git log --oneline -10`

## Steps

1. Check if a PR already exists for this branch using `gh pr view --json number,title,body 2>&1`. Steps 2-4 decide the base only for a **new** PR; an existing PR keeps the base it has.
2. Get the base branch:
   - If `base=<branch>` was passed, use it. Verify it exists on origin (`git ls-remote --heads origin <branch>`) and stop if it doesn't.
   - Otherwise start from the default branch, `git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`, then check whether the branch was cut from a `feature/*` trunk rather than from the default branch:

     ```bash
     DEFAULT=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
     MB_DEFAULT=$(git merge-base "origin/$DEFAULT" HEAD)
     for t in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin/feature/); do
       MB_T=$(git merge-base "$t" HEAD)
       [ "$MB_T" != "$MB_DEFAULT" ] && git merge-base --is-ancestor "$MB_DEFAULT" "$MB_T" && echo "candidate trunk: $t"
     done
     ```

     A candidate means the branch forked from that trunk after the trunk left the default branch. Propose it as the base and confirm once with the user — this is the only base-related question; don't ask when nothing matches.
3. Resolve the ticket now, before the base is final: run **Detect existing ticket** and its use/new question from [Linear ticket routing](#linear-ticket-routing) below. If no ticket is found, the routing stays as it is (a trunk's ticket for a sub-PR, otherwise a new issue), and a new issue is still created after step 7 because it reuses the PR title. A brand-new ticket has no other PRs, so step 4 never needs it.
4. **Same-ticket stacking** (Concentro `#pr-review` repos only): when the PR is new, the base is still the default branch, and step 3 kept an existing `CON-N` (not one the user chose to replace with a new ticket), run [Same-ticket stacking](#same-ticket-stacking-concentro) below. It may change the base to another open PR's head branch, and it sets the `Base:` line for step 12.
5. Get the **net diff vs base** using `git diff <base-branch>...HEAD` — the description must reflect this final state, not per-commit history.
6. Get the commit log using `git log <base-branch>..HEAD --oneline` (only as a hint; never describe intermediate or reverted work).
7. **If a PR already exists with a non-empty body**: skip description generation entirely — only update the title (for Linear ticket normalization). Jump to step 8. Otherwise generate the PR title and body:
   - **Title**: clear, concise, imperative; no "PR:" prefix; Linear ticket appended in parentheses (handled by the backfill section below).
   - **Body**: follow the **PR Descriptions** format in the user's global CLAUDE.md (`~/.claude/CLAUDE.md`) exactly — that doc is the single source of truth for section shape, skimmability rules, and what to omit. Don't duplicate that guidance here; read it fresh each time in case it's changed.
   - No `🤖 Generated with` footer.
   - **Preview** (repos with deploy previews, today woodrow): its URL carries the PR number, so prefer the repo's `pnpm run pr:create -- … --preview "<label>: <path>"`, which adds the section after opening the PR. Management portal screens (`apps/management/` changes) go in `--management-preview "<label>: <path>"`; a PR that changes both apps passes both. Opening with `gh pr create` instead, add `## Preview` with `gh pr edit` once the number exists. A PR whose base is not the default branch (a trunk sub-PR or a stacked PR) gets no deploy preview: pass `--base <base-branch>` to `pr:create` and no preview flags.
   - **Stacked PR** (step 4 chose to stack): start the body with `Stacked on #N (same CON-N); merges into it after review.`
8. Push the branch to origin if not already pushed (`git push -u origin <branch>`). **Before pushing**, if the current branch equals the base branch (e.g. you're on `main`/`master` itself), STOP and confirm with the user — running a PR workflow from the base branch is almost certainly a mistake, and pushing could ship unintended work to production.
9. If a PR exists with a non-empty body, only update the title: `gh pr edit --title "..."`. If a PR exists with an empty body, edit title and body. Otherwise create a new PR — **as a draft** unless `draft=false` was passed (then omit `--draft`) — passing `--base <base-branch>` whenever the base resolved in steps 2-4 is not the default branch. `gh` rejects `--draft` combined with `--web` (`the --draft flag is not supported with --web`), so create it headless and open the browser as a separate step: `gh pr create --draft [--base <base-branch>] --title "..." --body "..."` then `gh pr view --web`. Use a HEREDOC for the body to preserve formatting:

   ```bash
   gh pr create --draft [--base <base-branch>] --title "Imperative title (CON-1234)" --body "$(cat <<'EOF'
   ...body per global CLAUDE.md PR Descriptions format...
   EOF
   )"
   gh pr view --web
   ```

10. **Verify the draft state matches the request** — `gh pr view --json isDraft -q .isDraft` should be `true` unless `draft=false` was passed. A repo wrapper (e.g. a `pr:create` script) that opens PRs itself may ignore `--draft`; if the state is wrong, fix it (`gh pr ready --undo` to draft, `gh pr ready` to undraft) and say so. This applies to an existing PR too: don't silently leave a PR in a state the caller didn't ask for.
11. **Stacked PR only:** record it in the parent PR's body (step 4 of [Same-ticket stacking](#same-ticket-stacking-concentro)).
12. Output the PR URL (printed by `gh pr create`, or `gh pr view --json url -q .url`), followed by the `Base:` line when step 4 set one.

## Same-ticket stacking (Concentro)

Follow-ups on one ticket stack into the ticket's open PR instead of reaching `#pr-review` as PRs of their own. A reviewer sees one PR that grows. This applies only to `Concentro-Inc/woodrow`, `Concentro-Inc/api` and `Concentro-Inc/folio-platform`. Personal `jnelken/*` repos and `internal-tools` don't go through `#pr-review`, so they skip it. Stacking happens without asking: it is Jake's standing choice.

1. **Decide.** From the branch's worktree:

   ```bash
   python3 ~/.claude/skills/create-pr/scripts/same_ticket.py decide \
     --repo "$(gh repo view --json nameWithOwner -q .nameWithOwner)" \
     --default "$DEFAULT" --ticket CON-N --branch "$(git branch --show-current)"
   ```

   It lists Jake's open PRs into the default branch with `gh pr list --search "CON-N in:title" --author @me` and keeps only titles carrying the exact `(CON-N)`, so `CON-44` never matches `CON-441`. It skips the current branch's own PR, and it never stacks onto a teammate's PR. It prints JSON with `decision`, `base`, `pr` and `base_line`:
   - `none`: no other open PR carries the ticket. Keep the default branch and print no `Base:` line.
   - `main`: every match is `APPROVED` (`Base: main (#N already approved)`), or the branch can't stack cleanly (`Base: main (could not stack onto #N: <reason>)`). Keep the default branch. An approval must never silently cover code nobody reviewed.
   - `stack`: the lowest-numbered match that isn't approved, and the branch forked from a commit that PR's head already contains. So merging it adds no default-branch commits the parent lacks. Continue with step 2.
2. **Merge the parent's head into the branch,** so the sub-PR's diff holds only the new work. Commit or set aside pending changes first: the merge needs a clean tree. Trial the merge before committing it:

   ```bash
   git merge --no-ff --no-commit "origin/<base>" || { git merge --abort; echo conflict; }
   git rev-parse -q --verify MERGE_HEAD >/dev/null && git commit --no-edit
   git diff --quiet "origin/<base>" HEAD && echo "nothing new"
   ```

   On a conflict, fall back to the default branch: `Base: main (could not stack onto #N: conflicts with <base>)`. If nothing new is left, the work is already in #N: stop and say so rather than opening an empty PR.
3. **Open the sub-PR** with `--base <base>` (steps 5-10). Its title keeps the same `(CON-N)`. `/post-pr` reviews it, runs CI and merges it into the parent's branch with a merge commit without announcing it (its steps 3.5-3.6), exactly as for a trunk sub-PR.
4. **Tell the parent's reviewers it grew.** Read and write the parent's body through a file, never a shell string:

   ```bash
   gh pr view <N> --json body -q .body > "$TMPDIR/parent-body.md"
   python3 ~/.claude/skills/create-pr/scripts/same_ticket.py note \
     --body-file "$TMPDIR/parent-body.md" --pr <sub-PR number> --title "<sub-PR title>"
   gh pr edit <N> --body-file "$TMPDIR/parent-body.md"
   ```

   `note` adds a `## Stacked follow-ups` section after `## Changes` (before the Slack badge when there is no Changes section), or appends to an existing one. It changes nothing else, and running it twice for one sub-PR leaves the body as it was. Skip the `gh pr edit` when it prints `unchanged`.

## Linear ticket routing

**Personal DEV PRs:** Reuse an existing ticket in the branch/title if one exists. Otherwise open
the PR without a ticket. Do not create a ticket for work already underway, and do not call
`backfill-linear-ticket` automatically. Jake may explicitly request a ticket separately.

For personal DEV PRs, skip the remaining backfill subsections and continue with pushing/creating
the PR.

**Work CON PRs:** A ticket is required; run the backfill procedure below. This exception is
specific to the work environment and does not apply to personal DEV issues.


Run **Detect existing ticket** and its use/new question in step 3, before the base is final. Create a new issue, when one is needed, after the title exists (step 7) and before pushing/creating the PR:

### Detect existing ticket
Check for a Linear ticket ID (pattern: `[A-Za-z]+-\d+`) in:
1. The branch name (from `git branch --show-current`)
2. The existing PR's title (step 1), or a ticket the user named for this work
3. The proposed PR title, once step 7 has one (a fallback only: step 4 has already run by then)

Normalize any found ticket to uppercase (e.g. `con-123` → `CON-123`).

Strip any existing ticket decoration from the title before re-applying:
- Leading prefix: `[CON-123] PR title` → `PR title`
- Trailing suffix: `PR title (CON-123)` → `PR title`

### Ticket found — ask whether to use it or create a new one
If a ticket is found, **ask the user**:
> "Found Linear ticket TICKET-ID in branch/title. Use it, or create a new one? (use/new)"

If the user says **use**: keep the ticket, continue with step 4 (same-ticket stacking), and format the final title as `PR title (CON-123)` once step 7 writes it.

If the user says **new**: create a new Linear issue using the steps below.

### No ticket found — sub-PR into a trunk: reuse the trunk's ticket
If no ticket is found and the base (step 2) is a `feature/*` trunk, look up the trunk's own PR (`gh pr list --head <trunk> --state open --json title`) and reuse the ticket in its title without asking. Sub-PRs share their trunk's ticket by default — ticket count no longer dictates PR count. Fall through to creating one only if the trunk PR carries no ticket either.

### Work CON only: no ticket found — create one automatically
Otherwise, proceed directly to creating a Linear issue without asking.

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
- The ticket backfill procedure applies only to work CON PRs. Personal DEV PRs reuse an existing ticket or proceed without one.
- You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance.
- Return the PR URL so the user can view it.
