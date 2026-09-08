---
name: advance-roadmap
description: Ship ONE planned item end-to-end from a personal repo's ROADMAP.md, from docs/plans/ when the repo has no roadmap, or from a Linear issue carrying a repo label — pick a qualifying repo under ~/Dropbox/code, implement the item on a branch, verify with the repo's test/build, move it to Shipped, then merge to main locally and push. No PR. Use on-demand ("/advance-roadmap", "work a roadmap item", "advance the roadmap", "ship something off the roadmap") or via an unattended scheduled run. Sibling of [[wrapup-repos]] but NOT the same: this one pushes.
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
- **A Linear issue's repo is its `repo/*` label, and nothing else.** No label, not actionable —
  see Step 2's *Also check Linear*. Never read it out of the title, body, or project name instead:
  a project called "Knowledge Base MVP" is not evidence for the `knowledge-graph` directory, and
  guessing is the same failure mode as the rule above — a feature pushed to `main` in the wrong
  repo.
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

## Step 0 — Read run memory, then reconcile the previous run

Read `/Users/jake/.claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md`
if it exists. It records which repos had actionable roadmap content on prior runs, what was shipped,
and what was deliberately skipped. Check the repo it names first — it's the cheapest lead you have.
It is a hint, not a constraint: if that repo no longer qualifies, move on without ceremony.

(Headless runs use `/Users/jake/Dropbox/code` as cwd, which is why that memory directory is the
right one. Don't guess a different path.)

### Did the previous run finish?

**An interrupted run gets finished before a new item is started.** This job fires every 6 hours
under launchd and dies for reasons that have nothing to do with the work — a session limit hit
mid-build (`run-20260907-104501.log`), the machine sleeping, launchd killing the process. What it
leaves behind is a repo sitting on `roadmap/<slug>` with real commits nobody will ever merge, while
the next run cheerfully starts something else.

Classify the previous run from the logs in
`/Users/jake/.claude/automations/advance-roadmap/logs/`. **Sort by filename and take the newest —
do not use `latest.log`.** `run.sh` re-points that symlink as its final line, so it lags the run in
flight, and the quota-skip path rewrites it immediately. The newest `run-*.log` is *this* run (no
terminal `=== claude exit=` line yet); the one below it is the previous run.

| Previous log | Meaning | Action |
|---|---|---|
| a `skipping — …` line only, no `=== advance-roadmap run` header | quota gate fired, claude never started | nothing to resume |
| `=== claude exit=0` plus a final summary | completed | nothing to resume |
| `=== claude exit=` non-zero, **or no `exit=` line at all** | killed mid-flight | reconcile, below |

**Do not use "an unmerged `roadmap/*` branch exists" as the resume signal.** Step 5's
all-or-nothing rule leaves exactly such a branch behind *on purpose* every time verification fails,
and nothing about the branch itself distinguishes that from a crash. Re-attempting one every 6
hours is how a run that correctly gave up becomes an infinite loop.

The real discriminator is **whether Step 8 ran**. A run that stopped deliberately — shipped,
blocked, or nothing qualified — appended itself to run memory's `## Run ledger` before exiting. A
run that was killed never got there. So take the previous log's stamp from its header
(`=== advance-roadmap run 20260907-104501 …` → `20260907-104501`) and look that stamp up in the
ledger. **No row → it was interrupted.**

Match on the **stamp, not the date.** This job runs four times a day; a date alone can't tell this
morning's run from last night's. And note what the ledger deliberately doesn't contain: a
quota-gate skip never reached Step 8 either, but its log has no run header, so the table above
already ruled it out before you get here.

**A missing or empty ledger means no prior state, not an interruption.** Go to Step 1 and let this
run write the first row.

### Reconciling an interrupted run

If the interrupted log never names a repo, it died before Step 1 picked one — there's nothing to
reconcile, so go to Step 1. Otherwise work only in the repo it names, and only while it still
passes the safety rules:

1. `git -C <repo> branch --show-current`. An interrupted run usually leaves the repo on
   `roadmap/<slug>` rather than `main`. `git-safe-to-autocommit` won't flag that — it's neither a
   detached HEAD nor a stuck rebase — and Step 3 assumes it starts from `main`, so check directly.
2. **The clean-tree rule still applies, unchanged.** A dirty tree there could be the dead run's
   scratch work or Jake's; you cannot tell, so don't guess. Leave it, report it, pick another repo.
3. If the branch carries commits that aren't in `origin/main`, that half-finished item **is this
   run's one item**. Resume at Step 4, finish what the item's scope calls for, then run Step 5's
   verification in full — never inherit the dead run's results — and continue through Steps 6–8.
   Don't start anything new; the one-item budget is spent. The branch was cut from an `origin/main`
   that has since moved on, so expect Step 7's `merge --ff-only` to refuse: that's the ordinary
   fetch, rebase, re-verify path documented there, not a hard stop.
4. If verification fails and the fix isn't clean and contained, stop per the all-or-nothing rule and
   record the outcome as `blocked-branch-left` in Step 8, naming the branch. That token is what
   keeps the *next* run from resuming it: a branch parked on purpose is documented, not retried.

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
     symbols the plan names. **This check is mandatory**, and it is the single most likely way
     this fallback wastes a run.
   - **A plan you find already implemented gets archived on the spot — don't ask.** Jake doesn't
     do this bookkeeping himself, so an unarchived finished plan is the expected state, not a
     signal. Archive it per Step 6 and keep looking for real work in the same run: archiving is
     bookkeeping, not the one item this run is allowed to ship, so it doesn't spend the budget.
     Archive **only on concrete evidence** — the files, hooks or symbols the plan names actually
     exist. A plan that's *partly* built is a genuine decision (finish it? redesign it?) and goes
     to Step 2b instead. The move is a `git mv` on a tracked file, so it's reversible either way.
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
7. **A Linear issue that explicitly names this repo also qualifies it**, even with no
   `ROADMAP.md` and no `docs/plans/` — see Step 2's *Also check Linear*. Every other gate in this
   list still applies unchanged.
8. Apply the clean-tree, preflight, and live-session checks from the safety rules.

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

If every planned item is skippable, that's a legitimate outcome — but not a silent one. Before you
stop: archive any finished plan docs you found (Step 6's archive rule, landed through Step 7 —
that bookkeeping happens even when nothing ships), write the blockers per Step 2b, report which
items you considered and why each was skipped, and record it in memory.

**One item per run.** Don't chain a second one because the first went fast.

### Also check Linear

Roadmap files aren't the only queue. Jake's personal Linear workspace is `jnelken` (team **Dev**,
key `DEV`), and the global `CLAUDE.md` treats it as where a roadmap item goes *once it's ready to
be picked up or automated* — so an issue sitting there is intent he has already committed to, and
it belongs in the batch you triage.

Linear is an **additional** source, not a replacement. A `ROADMAP.md` item is not demoted because
an issue exists somewhere; read both, then pick one item by the prefer/skip rules above.

- **Query it once**, at triage time. Take `Todo` and `In Progress` first — Jake moved those
  deliberately — then `Backlog`. Ignore `Done`, `Canceled`, `Duplicate`, and `In Review`.
- **The repo comes from the `repo` label.** The workspace carries a `repo` label group with one
  child per directory under `~/Dropbox/code` — `repo/mailcruxh`, `repo/typey.site`, and so on.
  Being a group, it's single-select: one repo per issue.
- **You are the enforcement.** Linear has no custom fields, and required fields are a Jira concept
  Linear deliberately doesn't implement, so nothing stops an issue being filed without the label.
  An issue with no `repo/*` label is a Step 2b blocker, phrased so one line answers it: "DEV-14 has
  no repo label — which repo is it in?" Don't work around it by reading the repo out of prose.
- Label names are **directory names**, because that's what this skill resolves against. Two don't
  match their GitHub repo — `repo/openclaw-vps` is `jnelken/vena-vps`, and `repo/typebeat` has two
  checkouts on disk. Each label's description records the discrepancy; read it before assuming.
- **When you file the blocker, file it in Linear too**: add the missing label yourself if the
  answer is unambiguous from the issue's own content, and otherwise leave it for Jake. Adding a
  label is reversible in a way that pushing to the wrong repo is not.
- **Step 2's skip list applies in full.** Those same six issues need Postgres + pgvector, an LLM
  API key, and an external Instagram ingestion provider, so on today's reading they're out on the
  external-services rule regardless of the repo question. That's a verdict on the current issue
  text, not a permanent denylist — re-read them rather than trusting this paragraph.
- **Close the loop only after the push succeeds** (Step 7): comment the merge commit on the issue
  and set it to `Done`. Don't set anything to `In Progress` on the way in — an unattended run that
  fails verification would leave the board claiming work that isn't happening, with no way to put
  it back.
- **Never block a run on Linear.** If the MCP tools aren't authenticated — the tell is that only
  `authenticate` / `complete_authentication` are exposed, with no `list_issues` — note it in the
  log and carry on with the file-based sources. Never attempt OAuth from a headless run.

## Step 2b — When nothing ships, write the blockers to `.claude/IN_PROGRESS.md`

A run that picks nothing is only worth anything if it says **why**, somewhere the user will look.
[[pick-up]] reads `.claude/IN_PROGRESS.md`, so that's where an open question goes — in the repo it
belongs to, phrased so it can be answered in one pass at `/pick-up` time.

**Write a note when:**
- every candidate item was skipped at Step 2;
- an item was picked but implementation stalled on a judgment call that is the user's to make
  (a product decision, two viable designs, a `PRODUCT.md` open question) — see Steps 4 and 5;

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
- [ ] `docs/plans/liquid-seam.md` — half built: the renderer landed, the resume section didn't.
      Finish it as specced, or was it abandoned on purpose?
```

"Skipped — too ambiguous" is worthless. The test for every line: could the user answer it from the
line alone, without reopening the plan doc?

### Then try to reach the user — best effort, once

The file is the durable channel; a notification is the nudge that makes it timely. After writing
the note, call `PushNotification` **once** (not once per item):

```
PushNotification(status: "proactive",
  message: "advance-roadmap: <N> blocker(s) in <repo> need your call — run /pick-up there")
```

Keep it under 200 characters and lead with the repo. The questions themselves stay in the file —
a notification is a pointer, not a transcript.

**Expect it to be a no-op sometimes, and don't treat that as failure.** The tool suppresses itself
while an interactive Claude terminal is active ("Not sent — this terminal is active"), so a run
that fires while the user is working will log a not-sent result. That's correct behaviour: they'd
see the run in the log anyway. Record the tool's verdict in the run log either way, so it's visible
whether the nudge actually went out.

**What does NOT work from a scheduled run, verified — don't burn a turn retrying it:** the Dispatch
conversation is a Remote Control peer, and a headless `claude -p` session cannot see or message
Remote Control peers. `ListAgents` there lists only local interactive sessions, and `SendMessage`
to Dispatch returns `No agent named '…' is reachable`. `PushNotification` (which reaches the phone
when Remote Control is connected) is the only channel available to this job.

**Notify only when the blockers changed.** This job runs every 6 hours; re-notifying about the same
unanswered questions four times a day is how a useful signal becomes noise the user mutes. Compare
against the blocker set recorded in run memory (Step 8) — if it's unchanged, write the file, skip
the notification, and say so in the summary.

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
- `git mv` the file to `docs/plans/archive/`, creating that directory if needed. It stays under
  `docs/plans/`, so this doesn't trip the repo-hygiene check that plan docs live there.
- Fix any link to the doc's old path.

**Archive finished plans you merely found, too — always, without asking.** Step 1 flags plan docs
that were implemented in some earlier session and never filed away. Same treatment, different
status line, because this run didn't build it:

`> **Status:** Implemented before <YYYY-MM-DD>; archived by advance-roadmap. Evidence: <the files or symbols that prove it>.`

Naming the evidence is what makes the archive auditable — and `git mv` keeps it reversible if the
call was wrong. Commit these as their own bookkeeping commit, separate from any feature work, and
list every doc you archived in the final summary so the user sees what moved. Do this even on a run
that ships nothing else: tidying finished plans out of the queue is a real outcome.

The same rule applies to a **roadmap item whose work is already in the code** — move it to Shipped
with a note of the evidence rather than leaving it in Planned for the next run to re-evaluate.

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
- **Once the push has succeeded**, and only then: if the item came from a Linear issue, comment the
  merge commit on it and move it to `Done`.

## Step 8 — Update run memory

Write `/Users/jake/.claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md`
(one file, updated in place — never one file per run), with `type: project` frontmatter, recording:
- the date of this run;
- an **outcome token** for this run — exactly one of:
  - `shipped` — an item went to `main`;
  - `blocked-branch-left` — an item was picked and its work is parked on a branch (verification
    failed, or a decision blocked it). **Name the repo and branch.** This is the record that stops
    a later run from resuming a branch that was abandoned on purpose;
  - `blocked-no-item` — repos qualified but every candidate was skipped at Step 2; blockers went to
    `.claude/IN_PROGRESS.md`;
  - `nothing-qualified` — no repo passed Step 1;
- whether this run resumed an interrupted previous run, and what state it found;
- which repos had a qualifying roadmap and which were checked and didn't;
- the Linear issues considered, the one shipped (with its `DEV-N` id), and any that were blocked on
  not naming a repo;
- the item picked, or why none was;
- whether it shipped, and the merge commit hash;
- any plan docs archived as already-implemented, so a later run doesn't go looking for them;
- the blockers written to `.claude/IN_PROGRESS.md`, as a short list — this is what the next run
  diffs against to decide whether a notification is warranted, so keep it comparable;
- any item deliberately skipped and the reason — so the next run doesn't re-evaluate it from
  scratch.

Keep it short and current: replace stale run detail rather than appending an ever-growing log. Add
the one-line pointer to `MEMORY.md` if it isn't there yet.

### The run ledger — the one append-only part of that file

Run memory carries a `## Run ledger` table. Append exactly one row for this run as part of this
step, using the stamp from your own log header:

| Stamp | Date | Repo | Outcome |
|---|---|---|---|
| `20260908-044500` | 2026-09-08 | mailcruxh | shipped |

**This table is exempt from the replace-stale rule above** — it is the only thing in the file that
accumulates, and Step 0 reads it to tell a run that stopped on purpose from one that was killed.
Trim it to the last ~10 rows and no further: a row you delete is a run that looks interrupted
forever. A run that never reaches this step correctly leaves no row; that gap is the signal, so
never backfill a row for a run you didn't complete yourself.

## Final output

End with a 5-line plain-text summary: repo, item shipped (or why none), test/build result, merge
commit hash, and anything left for the user to confirm by hand. Say up front whether this run
finished an interrupted previous run or started fresh, and name the outcome token you recorded in
Step 8. If Step 2b wrote blockers, say so
with the path and the item count — that's the user's cue to run `/pick-up` in that repo. In a scheduled run this is what the
user scans in the log.
