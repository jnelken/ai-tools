---
name: advance-roadmap
description: Ship ONE planned item end-to-end from a personal repo's ROADMAP.md, or from docs/plans/ when the repo has no roadmap — pick a qualifying repo under ~/Dropbox/code, implement the item on a branch, verify with the repo's test/build, move it to Shipped, then merge to main locally and push. No PR. Use on-demand ("/advance-roadmap", "work a roadmap item", "advance the roadmap", "ship something off the roadmap") or via an unattended scheduled run. Sibling of [[wrapup-repos]] but NOT the same: this one pushes.
---

# Advance the roadmap

Take exactly ONE item from a personal repo's `ROADMAP.md` from "planned" to "shipped and on `main`"
in a single run. Implementation, verification, roadmap bookkeeping, and the push — all of it, or
none of it.

Candidate repos are the direct children of `/Users/jake/Dropbox/code`.

## How this differs from `wrapup-repos`

They look similar and they are not. Read this before assuming shared behavior:

| | `wrapup-repos` | `advance-roadmap` (this skill) |
|---|---|---|
| Input | whatever is dirty / recent | a `ROADMAP.md` planned item |
| Working tree | expects it dirty | **requires it clean** |
| Branch | current branch, whatever it is | new branch off fresh `origin/main` |
| Remote | **never contacts it** | fetches, and **pushes `main`** |
| Output | `NEXT-STEPS.md` decision list | shipped feature + updated `ROADMAP.md` |

Because this skill pushes, its safety rules are stricter, not looser.

## Two ways this runs

- **On-demand (interactive):** the user invoked you directly. If they named a repo or an item,
  honor that over the auto-selection below. Otherwise auto-select, and say which item you picked
  before you start building.
- **Scheduled (headless / unattended):** no human is watching. Follow the safety rules to the
  letter, and prefer stopping with a clear note over guessing.

## Hard safety rules (never violate)

- **Personal repos only.** Run `git -C <repo> remote get-url origin` and require the owner to be
  `jnelken`. `ROADMAP.md` is a personal-repo convention; Concentro repos (`woodrow`, `api`,
  `folio-platform`, anything under `Concentro-Inc`) track direction in Linear and must NEVER be
  touched by this skill. Pushing `main` on a work repo is the worst thing this skill could do.
- **Clean tree or skip.** `git -C <repo> status --porcelain` must be empty. Uncommitted work means
  the user is mid-thought there; branching, merging and pushing around it tangles their diff. Pick
  a different repo — never stash, reset, or commit their WIP to get started.
- **Shared preflight, not a reimplementation.** Run `~/dotfiles/bin/git-safe-to-autocommit <repo>`
  and skip the repo on a non-zero exit. It refuses repos mid-rebase/merge/cherry-pick/revert/bisect
  and repos with a detached HEAD. Do not inline your own version of this check — a divergent copy
  is exactly what let a sibling automation commit on top of a stuck rebase for two weeks unnoticed.
- **Skip live sessions.** If any `.claude-sessions/*.md` at the repo root has `updated_at_epoch`
  within the last 15 minutes, someone is working there right now. Pick a different repo.
- **Never force anything.** No `--force`, no `--force-with-lease`, no `git reset --hard`, no
  `git clean -fd`, no rewriting published history. If the final `git push` is rejected as
  non-fast-forward, STOP — leave the branch and its commits in place, and report it.
- **Never open a PR** and never delete a branch the user created.
- **Never commit secrets or build junk** (`.env`, `*.pem`, tokens, `node_modules/`, `dist/`,
  `.next/`, `build/`, `*.log`). Respect `.gitignore`.
- **Honor `.noroadmap`.** A repo with a `.noroadmap` file at its root has opted out. Never create,
  modify, or delete `.noroadmap` — it is the user's toggle.
- **All-or-nothing.** If verification fails and you can't fix it cleanly, do NOT merge or push.
  Leave the work on its branch, say so, and stop. A half-shipped roadmap item on `main` is worse
  than no run at all.

## Step 0 — Read run memory

Read `/Users/jake/.claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md`
if it exists. It records which repos had actionable roadmap content on prior runs, what was shipped,
and what was deliberately skipped. Check the repo it names first — it's the cheapest lead you have.
It is a hint, not a constraint: if that repo no longer qualifies, move on without ceremony.

(Headless runs use `/Users/jake/Dropbox/code` as cwd, which is why that memory directory is the
right one. Don't guess a different path.)

## Step 1 — Find a qualifying repo

For each direct child of `/Users/jake/Dropbox/code` that is a git repo, in prior-run-first order:

1. Skip it if `.noroadmap` exists at the root.
2. Skip it unless `origin` is a `jnelken/*` repo.
3. Skip it if its `.git` is a **file** rather than a directory — that's a linked worktree of a
   repo already in this list (e.g. `m2ailcruxh` → `mailcruxh`). Operating on `main` from a
   second checkout of the same repo tangles both.
4. Find the roadmap **anywhere in the repo**. Its location is not a criterion — if a
   `ROADMAP.md` exists, this repo is a candidate. Don't check a fixed list of directories;
   search:

   ```
   find <repo> -iname 'ROADMAP.md' \
     -not -path '*/node_modules/*' -not -path '*/.git/*' \
     -not -path '*/dist/*' -not -path '*/build/*' -not -path '*/.next/*' \
     -not -path '*/vendor/*'
   ```

   Known homes so far are the root (`dubsketch`) and `docs/plans/` (`mailcruxh`), but treat
   those as examples, not as the set. A new repo putting one somewhere else must still be found.

   If several turn up, prefer the shallowest path, and among equals the one with real planned
   items per the next check. Note which file you used — Step 6 has to edit that same file.
5. **No `ROADMAP.md` anywhere? Fall back to `docs/plans/`.** A plan doc there is a legitimate
   source of work — treat each `docs/plans/*.md` as a candidate item and pick one per Step 2.
   This is what makes `ai-tools`, `koan-master` and `lunchmoney` candidates at all. But plan docs
   are not roadmap items and rot differently, so gate them harder:

   - **Verify the plan is actually unbuilt before picking it.** Nothing ever marks a plan doc
     done. `ai-tools/docs/plans/repo-hygiene-and-session-docs.md` is fully implemented —
     `hooks/personal-repo-hygiene-check.sh` and `hooks/session-doc-*.sh` all exist and run — and
     the doc still sits there looking like pending work. Grep the repo for the files, hooks or
     symbols the plan names; if they're there, the plan is history. **This check is mandatory**,
     and it is the single most likely way this fallback wastes a run.
   - **Skip ideation captures.** A doc that calls itself a brainstorm, or whose "Open Questions"
     section is load-bearing, is asking for taste decisions that are the user's to make — that's
     both `koan-master/docs/plans/*`. Surface it, don't resolve it.
   - **Skip execution records.** A doc written as a log of work already performed ("Executed live
     2026-08-18", "signed off") documents the past, not the future — that's two of the three in
     `lunchmoney/docs/plans/`.
   - **Step 2's skip list still applies in full**, external services and credentials especially:
     `lunchmoney/docs/plans/email-to-lunchmoney-setup.md` needs a Cloudflare Worker and Gmail
     OAuth, so it's out regardless of how well specified it is.
   - A plan's own `> **Suggested execution:**` block names a model and effort. If it asks for
     more than this run has, treat that as the author's warning that the work is too ambiguous
     to do unattended, and prefer a different item.

   A repo with a `ROADMAP.md` does **not** get this fallback — the roadmap is the source of
   truth there, and `docs/plans/` holds its architecture write-ups rather than its queue.

6. **Does it qualify?** It qualifies if it has at least one concrete unbuilt item under a
   forward-looking section. That section is **not always called `## Planned`** — these all
   count, and there are only a handful of repos so read the headings rather than pattern-matching:
   - `## Planned` (dubsketch)
   - priority tiers — `## P0 — Ship Blockers`, `## P1 — Next 4-6 Weeks`, `## P2`, `## P3` (mailcruxh)
   - `## Upcoming Features`, `## Future Enhancements`, `## In Progress`

   What does **not** count: `## Shipped (reference)`, `## Completed (for reference)`,
   `## Current State`, or a section whose items are all struck through / marked done. Judge by
   whether a real unbuilt item is described — not by file length or heading wording.
7. Apply the clean-tree, preflight, and live-session checks from the safety rules.

Take the first repo that passes everything. **If no repo qualifies, stop and report that no
actionable roadmap was found** — record it in memory (Step 8) and do nothing else. Do not invent
roadmap items, do not go looking for other work to do, do not fall back to `wrapup-repos` behavior.

Two roadmaps in this directory are well-formed enough to work from directly, and they look
nothing alike — expect the shape to vary:

- [`dubsketch/ROADMAP.md`](/Users/jake/Dropbox/code/dubsketch/ROADMAP.md) — the format exemplar:
  numbered `### N. Title` items each carrying `**Status:**`, `**What:**`, `**Why:**`, `**Scope:**`
  and `**Touchpoints:**`, plus a `## Priority` table and a `## Related docs` table.
- [`mailcruxh/docs/plans/ROADMAP.md`](/Users/jake/Dropbox/code/mailcruxh/docs/plans/ROADMAP.md) —
  priority-tiered (`P0`–`P3`) with prose-bullet items. Many carry a `Touchpoints:` bullet and are
  perfectly actionable; the tier is the priority signal, so prefer a lower-numbered tier.

Other repos' roadmaps are looser still; adapt rather than demanding either shape.

## Step 2 — Pick ONE item

Read the whole roadmap, plus the repo's `CLAUDE.md`, `README*`, `PRODUCT.md`, and `package.json`
scripts, so you're choosing against real project conventions.

**Prefer** items that:
- list concrete `**Touchpoints:**` (actual `src/...` paths) — an item without them is a wish, not a
  plan, and is usually underspecified for an unattended run;
- have logic that can be **unit-tested**, so "it works" is provable without a human;
- are clearly scoped, with success criteria obvious from the item's own `**Scope:**` bullets.

**Skip** items that:
- need **by-ear or visual QA** to confirm — anything the repo routes to a `docs/verification.md`
  checklist, or whose payoff is a subjective judgment ("sounds better", "feels smoother");
- require a **server, external service, new infrastructure, credentials, or a paid API**;
- are **large or risky** — audio-graph rewires, data-model migrations, broad renames, anything the
  roadmap itself flags as depending on another unbuilt item;
- depend on a **product/taste decision the user hasn't made** (an open question in `PRODUCT.md`, a
  "name TBD", two alternatives with no pick). Surface it, don't decide it for them.

If every planned item is skippable, that's a legitimate outcome — but not a silent one: write the
blockers per Step 2b, report which items you considered and why each was skipped, record it in
memory, and stop.

**One item per run.** Don't chain a second one because the first went fast.

## Step 2b — When nothing ships, write the blockers to `.claude/IN_PROGRESS.md`

A run that picks nothing is only worth anything if it says **why**, somewhere the user will look.
[[pick-up]] reads `.claude/IN_PROGRESS.md`, so that's where an open question goes — in the repo it
belongs to, phrased so it can be answered in one pass at `/pick-up` time.

**Write a note when:**
- every candidate item was skipped at Step 2;
- an item was picked but implementation stalled on a judgment call that is the user's to make
  (a product decision, two viable designs, a `PRODUCT.md` open question) — see Steps 4 and 5;
- a `docs/plans/` doc turned out to be already implemented. "Archive this?" is the user's call,
  and leaving it unasked is what let `ai-tools`' plan doc sit there looking pending for months.

**Don't write when** the run shipped cleanly, or when no repo qualified at all — there's no repo to
write into, so that outcome lives in run memory (Step 8) only.

### The `.gitignore` trap — check this first, it bites hard

`.claude/IN_PROGRESS.md` is untracked local state. Writing it into a repo that doesn't ignore it
makes `git status --porcelain` non-empty, which trips this skill's own clean-tree rule and locks
that repo out of **every future run**. A note explaining why nothing shipped would thereby
guarantee nothing ever ships again.

Only `koan-master` and `jakenelken.com` currently ignore it. `ai-tools`, `lunchmoney`, `dubsketch`
and `mailcruxh` do **not**. So:

1. `git -C <repo> check-ignore -q .claude/IN_PROGRESS.md`
2. If it is not ignored, add a `.claude/IN_PROGRESS.md` line to `.gitignore` and land that one-line
   change through Step 7's flow (branch off fresh `origin/main` → commit → `--ff-only` merge →
   push) **before** writing the note.
3. Write the note.
4. Confirm `git status --porcelain` is empty afterwards. Leaving the repo dirty is the failure.

### What to write

The file belongs to [[close-out]]; this skill only appends to it, under close-out's conventions:

- Path is `.claude/IN_PROGRESS.md` (create `.claude/` if needed). Use a root `IN_PROGRESS.md` only
  if one already exists there.
- It is a **running document**: read it first, leave every open item another session put there,
  append yours. Never overwrite, never leave a `- [x]` row, never `git add` it.
- Set `_Last updated: <date> (advance-roadmap)` using the real clock (`date +%F`), so it's obvious
  which writer touched it last.

One item per skipped thing, each naming the item **and** the question — with the options where you
can see them:

```markdown
## Decisions needed (advance-roadmap, 2026-09-05)
- [ ] `docs/plans/blue-horizon-design.md` — can't implement: the doc's own Open Questions leave the
      grading stop-condition and the copy-detection response unresolved. Which behaviour do you want?
- [ ] ROADMAP P1 "Group by label" — needs a call: does acting in one label group act everywhere
      (per-message), and do nested labels like `Work/Clients` flatten or nest?
- [ ] `docs/plans/repo-hygiene-and-session-docs.md` — appears fully implemented already
      (`hooks/personal-repo-hygiene-check.sh`, `hooks/session-doc-*.sh`). Archive it?
```

"Skipped — too ambiguous" is worthless. The test for every line: could the user answer it from the
line alone, without reopening the plan doc?

## Step 3 — Branch off fresh `origin/main`

```
git -C <repo> fetch origin
git -C <repo> checkout -b roadmap/<short-slug> origin/main
```

Fetch first, always. Branching off a stale local `main` means the push at the end of the run gets
rejected *after* all the work is done. If a suitable branch for this item already exists, reuse it
— but rebase it onto fresh `origin/main` before building on it.

## Step 4 — Implement

Build the item as its `**Scope:**` describes, following the repo's own conventions (its `CLAUDE.md`
overrides general guidance). Add or update unit tests for the logic you introduce — that's the
verification story for an unattended run.

**Drive-by improvements are allowed and encouraged** when they clearly make the codebase better and
stay near what you touched: extracting a duplicated helper, deleting dead code the change orphans,
tightening a loose type, a dependency bump that the change actually needs. Leave the repo in better
shape than you found it. Keep them in separate commits from the feature so the diff stays readable,
and stop well short of the refactors Step 2 told you to skip.

## Step 5 — Verify

Run the repo's actual commands — check `package.json` before assuming:

```
npm test          # or the repo's equivalent
npm run build     # this repo family treats build as the typecheck
```

Also run `npm run lint` if it exists and is fast. **Everything must pass.** If something fails and
the fix isn't obvious and contained, stop per the all-or-nothing rule: leave the branch, don't
merge, and report exactly what failed with the error output. If what stopped you was a question
rather than a bug — the fix depends on a decision that's the user's to make — write it up per
Step 2b and name the branch there, so `/pick-up` finds both the question and the work in flight.

## Step 6 — Update the roadmap (and friends)

In the roadmap file you located in Step 1 (not necessarily a root `ROADMAP.md`):
- Move the completed item into the **Shipped (reference)** section, in that section's existing prose
  or list style — don't paste the full planned-item block in.
- **Renumber the remaining planned items** so they stay sequential from 1.
- **Fix every cross-reference the renumber broke** — the `## Priority` table, any "depends on item
  N" notes, and links elsewhere in the repo pointing at the item. This is the step that quietly
  rots a roadmap if skipped.

**If the item came from a `docs/plans/` doc** (the Step 1 fallback) there is no roadmap to update,
and leaving the doc untouched is what created the `ai-tools` situation above — the next run
re-reads it as pending work. Instead:

- Add a status line directly under the doc's title:
  `> **Shipped:** <YYYY-MM-DD> — <commit sha>.`
- Move the file to `docs/plans/archive/`, creating that directory if needed. It stays under
  `docs/plans/`, so this doesn't trip the repo-hygiene check that plan docs live there.
- Fix any link to the doc's old path.

Then, only when relevant:
- **`PRODUCT.md`** — update if the shipped feature changes what the product claims or how it's
  described.
- **`docs/verification.md`** — if the feature is audio-critical (or otherwise needs by-ear/visual
  follow-up you couldn't do headlessly), add or update its checklist entry so the user knows what
  to confirm. Say so in your final summary too.

## Step 7 — Commit, merge, push

- Focused commits with clear messages: the feature, its tests, roadmap bookkeeping, and any drive-by
  work as logically distinct commits. Conventional-commit style matching the repo's log.
- Merge locally into `main` — no PR:

```
git -C <repo> checkout main
git -C <repo> merge --ff-only roadmap/<short-slug>   # fast-forward; it was branched off fresh origin/main
git -C <repo> push origin main
```

- If `--ff-only` refuses because `origin/main` moved during the run, `git fetch` and rebase the
  branch onto the new `origin/main`, **re-run Step 5's verification**, then retry. Never force.
- If the push is rejected, stop and report. Don't force, don't retry with a different flag.

## Step 8 — Update run memory

Write `/Users/jake/.claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md`
(one file, updated in place — never one file per run), with `type: project` frontmatter, recording:
- the date of this run;
- which repos had a qualifying roadmap and which were checked and didn't;
- the item picked, or why none was;
- whether it shipped, and the merge commit hash;
- any item deliberately skipped and the reason — so the next run doesn't re-evaluate it from
  scratch.

Keep it short and current: replace stale run detail rather than appending an ever-growing log. Add
the one-line pointer to `MEMORY.md` if it isn't there yet.

## Final output

End with a 5-line plain-text summary: repo, item shipped (or why none), test/build result, merge
commit hash, and anything left for the user to confirm by hand. If Step 2b wrote blockers, say so
with the path and the item count — that's the user's cue to run `/pick-up` in that repo. In a scheduled run this is what the
user scans in the log.
