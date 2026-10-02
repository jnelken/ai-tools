---
name: fix-stale-docs
description: Use when you find, or are asked to find, stale documentation — docs naming files, monitors, commands or behavior that no longer exist — and want to fix or delete it and push without a review cycle. Scans a repo for dead path references, picks the right open (preferably approved) branch, edits docs only, and pushes. Trigger phrases include "this doc is stale", "fix stale docs", "sweep docs for dead references", "clean up outdated README", "delete docs that just restate the code".
---

# fix-stale-docs

Standing permission (Jake, 2026-10-02): stale-doc fixes may be committed and pushed to **any open branch in the related repo, preferably an approved one**, with no review. Deleting docs that merely restate the code is equally fine. **Docs only** — code, config and Linear state keep their normal gates.

## 1. Find candidates

```bash
python3 ~/.claude/skills/fix-stale-docs/scripts/stale-refs.py <repo-dir>      # add --all to include dated/plan/CHANGELOG files
```

Lists backticked paths that don't exist, resolving `repo-name `path`` against `~/code/<repo-name>` and hinting `moved? <new path>` when the basename lives elsewhere. Output is **candidates, not verdicts** — read each line. Known false-positive shapes: a path in a sibling repo whose name sits more than ~40 characters earlier on the line; generated or gitignored files; paths that are deliberately historical ("previously lived in X as `path`") or consumer-relative (an action input's default like `.github/linear-routing.json`, which names a path in the *calling* repo). Skipped by default because they are historical records: `CHANGELOG*`, `docs/plans/`, dated files, `decisions/`.

Stale prose has no path to scan for. Also grep for what the session just changed (a renamed monitor, a removed flag, a moved config) across the sibling repos — that is how the stale refs this skill was born from were found.

**Scan the branch you will edit, not a root checkout.** A root checkout of `main` can lag the PR branches; a ref that is dead on `main` may already be fixed (or newly broken) on the branch.

## 2. Fix or delete

- **Rewrite** when the doc says something true-but-wrong-address: repoint the path, keep the rationale.
- **Delete** when the doc only restates what the code makes obvious (a file-by-file tour, a table copied from a schema). Check inbound links first (`git grep -n '<doc-name>'` in this repo and siblings) and fix or drop them in the same commit.
- **Leave** historical records (decision docs, plans, changelogs) and anything you can't verify. If unsure whether it is stale, verify against the code or live system before editing; report what you couldn't confirm.
- Don't point at a doc that isn't on the target repo's default branch yet (an unmerged PR) — it will dangle. Point at the file that exists.

## 3. Pick the branch

1. `gh pr list --state open --json number,headRefName,reviewDecision,isDraft,author`
2. Prefer: **APPROVED** > own PRs > anyone else's. Skip drafts unless nothing else fits.
3. Use the PR branch's existing worktree (`git worktree list`). Never edit in a root checkout (`~/code/api`, `~/code/woodrow`), and **never switch branches inside a worktree** — if the branch has no worktree, create one for it.
4. `folio-platform` is the exception: work in the root checkout on the open `updates-*` batch branch (see its CLAUDE.md).
5. Before editing: `git status --porcelain` and `git log @{u}..HEAD` must be empty (if not, the tree isn't only yours — stop and coordinate).

## 4. Commit and push

- One commit, docs paths only (`git add docs README.md …`, not `-A` over the whole tree). Say *why* the docs were stale.
- Run steps as separate commands or check each exit status. A `&&` chain that stops early can leave you committing half of what you meant to.
- If a pre-push hook blocks a docs-only push on something unrelated, report it; don't use `--no-verify` unless the repo's own notes allow it.
- Push, then confirm `gh pr view <n> --json reviewDecision` — an approval that got dismissed is worth telling Jake.

## 5. Report

Per repo: branch/PR, commit, what changed (and what was deleted), what was left alone and why, and anything that needs a human (docs you couldn't verify; a PR description that no longer covers the diff). Don't rewrite the PR body unless asked.
