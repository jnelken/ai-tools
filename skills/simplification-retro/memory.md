# Simplification Retro — Run Memory

Append-only log of runs. Read it before a sweep so a finding can be reported as **persisted**, **new**, or **resolved since last run** rather than as a first look.

**This file is committed, not gitignored.** The repo syncs skills across machines; local-only memory would break run-to-run continuity the first time the skill ran from a different host. Contents are operational metadata — paths, branch names, SHAs — nothing secret.

New entries go at the bottom. Never rewrite a prior entry; a declined finding stays in the log as declined, so the next run doesn't re-litigate it.

---

## 2026-09-18 — pre-history (NOT a skill run)

**This entry is the corpus the skill was built from, not a run of it.** The findings below came out of a targeted investigation plus Jake's direct instructions and several review rounds — not from the detection tests in `SKILL.md`, which did not exist yet. Do not treat it as a baseline for persisted/new/resolved comparison; the first real run starts that clock.

Originating work: `woodrow` branch `con-3739-vde-input-vertical-inset` (PR #1542) and the `~/dotfiles` change that followed. The session's actual task was diagnosing why Superset stopped showing a PR badge for that workspace.

Residue found, by kind:

- **Orphaned guard / dangerous dead path** — `gpstack` in `~/dotfiles/zsh/lib/30-git.zsh` derived its push refspec from each branch's upstream. On a branch tracking `origin/main` (which git's default `branch.autoSetupMerge` produces for `git switch -c foo origin/main`) it computed `foo:main` and would have force-pushed a feature branch onto the trunk. **Resolved** — function deleted at Jake's instruction (`~/dotfiles` commit `5c8f0a3`). Note the tier lesson: zero references anywhere, fully recoverable, local to one repo — Tier 1 by every test except the human-invocable-surface condition, which is exactly why that condition exists.
- **Redundant path** — `gpstack`, the `push-stack` alias in `~/.gitconfig`, and `/create-pr base=` all covered stacked PRs. **Open** — the `push-stack` alias survives and was flagged to Jake; it does not carry `gpstack`'s bug (it pushes same-name, no `branch:main` refspec). Left deliberately; re-check whether it has any real uses.
- **Per-instance patch with a root-cause fix available** — `git branch --set-upstream-to` per branch, vs. `branch.autoSetupMerge = simple`. **Resolved** — added to `~/.gitconfig` (machine-local; that file is not tracked in `~/dotfiles`). Caveat recorded in `SKILL.md`: the fix is forward-only and does not repair branches that already track `origin/main`, and an explicit-refspec push still leaves no upstream unless `-u` is passed.
- **Dead scaffolding** — `/Users/jake/code/w2oodrow` and `w3oodrow` worktrees with branches `main2`/`main3`, plus `origin/main3`. Zero unique commits against `main`; the only dirty file was a `.superset/config.json` edit already superseded on `main`. **Resolved** — worktrees removed, local branches deleted, `origin/main3` deleted (was `56992d65`; restore with `git push origin 56992d65:refs/heads/main3`).
- **Superseded store** — `~/.superset/local.db` holds project rows from an older data model, including dead entries for `w3oodrow` and `w4oodrow` that no longer exist on disk. **Open, deliberately untouched** — terminal and browser history still write to that file, so it is not purely dead.
- **Stale index** — `~/.ai-tools/README.md`'s skills table is missing ~19 shipped skills. **Open, out of corpus** — it was already stale before this work. Recorded here only because it was observed; under the skill's scope rule a future run should not report it.

Working correctly — do not change:
- `main2`-style trunk mirrors are protected from `gdelm` by `git_trunks_re` in `30-git.zsh`. That protection is why they survived every routine `gp` and had to be removed by hand; it is not a bug.
- `gdelm`'s second deletion criterion (0 commits ahead of main) was checked against the new `autoSetupMerge` setting and creates no new risk — a fresh branch off main was already `ahead == 0` regardless of upstream, and worktree-checked-out branches are skipped.
