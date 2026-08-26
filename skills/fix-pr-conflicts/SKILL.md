---
name: fix-pr-conflicts
description: Scan all of the user's open non-draft PRs across woodrow and api for merge conflicts, then resolve each one in its sswt worktree by merging the base branch, fixing conflicts, typechecking, and pushing. Use whenever the user says a PR is "merge blocked", "has conflicts", "can't be merged", asks to "unblock my PRs", "fix the merge conflicts on my PRs", "check my PRs for conflicts", or wants a periodic conflict sweep — even if they only name one repo.
---

# Fix PR Conflicts

Sweep the user's open non-draft PRs in both repos for merge conflicts and clear them. The end state for each conflicting PR: `mergeable: MERGEABLE` on GitHub, with the resolution pushed to the PR branch.

## Step 1 — Scan both repos

For each repo (`/Users/jake/code/woodrow`, `/Users/jake/code/api`):

```bash
gh pr list --state open \
  --json number,title,headRefName,baseRefName,isDraft,mergeable,author,url --limit 100
```

Filter to: `isDraft == false`, author is the user (`jnelken`), and title does not contain `[HOLD` (held PRs are deliberately parked — report them, never touch them).

**`mergeable: UNKNOWN` is normal on first query.** GitHub computes mergeability lazily, and the list call itself triggers computation. When any PR shows `UNKNOWN`, wait ~10s and re-query (up to 3 rounds). Only `CONFLICTING` PRs proceed; report the final scan table either way.

## Step 2 — Locate each PR's worktree

Never resolve in a root repo checkout. Each PR branch usually already has an sswt worktree:

```bash
superset workspaces list | grep -i <headRefName>   # → worktreePath
```

If no workspace exists, create one scoped to the PR: `superset workspaces create --local --project <repo> --pr <number>`.

Before doing anything in the worktree, confirm it's clean (`git status --porcelain`) and on the PR branch. Uncommitted changes may be another agent's or the user's work — stop and surface them rather than mixing them into a merge commit.

## Step 3 — Merge the base branch (don't rebase)

Merging avoids rewriting pushed history, so no force-push and no disruption to reviewers:

```bash
git fetch origin <baseRefName> <headRefName>
git merge origin/<baseRefName> --no-edit
```

Use the PR's actual `baseRefName` — stacked PRs don't base on main.

If the merge output says **"using previous resolution"** for any file, rerere replayed a resolution recorded in another worktree (they share `.git`, and preview branches that merge many PRs poison this cache). Inspect those files as skeptically as fresh conflicts before staging.

## Step 4 — Resolve conflicts

Follow the `/resolve-conflict` skill's procedure: list conflicted files, read both sides (`git show :2:<file>` = ours/PR, `:3:<file>` = theirs/base), classify the pattern, resolve.

The common shape when a sibling PR squash-merged first: both sides rewrote the same region, and the correct resolution is a **manual weave** — take the base branch's (usually larger/newer) structure and graft this PR's specific change into it, not `--ours`/`--theirs` wholesale. Merge import lists by union.

## Step 5 — Verify before committing

Typecheck, with these repo-specific traps:

- **woodrow root `pnpm run typecheck` does not cover `apps/*`.** If the merge touched files under `apps/management`, also run `pnpm run typecheck` inside that app's directory.
- **Pre-existing errors are common** — woodrow PR CI never typechecks, so main drifts. For each error in a file the merge didn't conflict on, check `git diff origin/<base> -- <file>`: if empty (identical to base), the error is pre-existing — note it, don't fix it in this PR. Only errors in merged/conflicted code block the commit.
- Ignore errors from `showcase.generated.stories.tsx`.

Then confirm the commit will contain only merge content: `git status --porcelain` should show only staged modifications from the merge — no strays.

## Step 6 — Commit and push

```bash
git commit --no-verify --no-edit          # merge commit; --no-verify skips pre-existing-lint hook failures
git -C <worktreePath> push origin <headRefName>   # -C avoids the worktree push-hook misfire
```

## Step 7 — Confirm and report

Re-poll `gh pr view <number> --json mergeable` (allow ~15s) until `MERGEABLE`. Sanity-check that `git diff origin/<base>...HEAD --name-only` still lists only the PR's own files — a resolution that leaks unrelated files means the wrong side was kept somewhere.

Finish with a table: PR, repo, previous state, files conflicted, resolution approach, new state. Include skipped PRs (drafts, `[HOLD]`, other authors) with the reason.
