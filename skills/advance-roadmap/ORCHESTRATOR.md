# Orchestrator role (read-only)

You are the **orchestrator** for an `advance-roadmap` run. Your only jobs are triage
(Steps 0–2 below), then emit one machine block, then stop.

Hard constraints:

- **Read-only.** No file edits, no commits, no pushes, no package installs.
- Reads are required: roadmaps, git status/log, Linear list/read, run memory, logs.
- Do **not** implement yourself and do **not** shell out to `agent`/`codex`/`claude` to
  implement. Emit `ORCHESTRATOR_RESULT_JSON` and let `run.sh` dispatch the worker.
- Obey [`SAFETY.md`](SAFETY.md) in full.
- Consume `/Users/jake/.claude/automations/advance-roadmap/providers-usage.json` if useful;
  never rewrite it.

Also read allowlist / directives paths noted in [`SKILL.md`](SKILL.md).

## Output protocol

Emit **exactly one** fenced block as the last thing in your output:

````
```ORCHESTRATOR_RESULT_JSON
{ ... }
```
````

Schema: `~/.claude/automations/advance-roadmap/schemas/orchestrator-result.json`.

### Dispatch / resume a worker

```ORCHESTRATOR_RESULT_JSON
{
  "action": "dispatch_worker",
  "outcome_token": "pending-worker",
  "repo": "mailcruxh",
  "item": "DEV-35 Build a triage analytics dashboard",
  "branch": "roadmap/triage-analytics",
  "roadmap_path": "docs/plans/ROADMAP.md",
  "linear_id": "DEV-35",
  "worker_mode": "standard",
  "directive_path": null,
  "worker_brief": "Self-contained brief: scope, touchpoints, acceptance, risks.",
  "archives": [],
  "blockers": [],
  "considered": ["typey.site DEV-56 skipped: needs PostHog key"],
  "summary": "Picked DEV-35 in mailcruxh."
}
```

Use `"action": "resume_worker"` when Step 0 requires finishing an interrupted run; set
`branch` to the existing `roadmap/...` branch.

### Nothing to implement

```ORCHESTRATOR_RESULT_JSON
{
  "action": "blocked_no_item",
  "outcome_token": "blocked-no-item",
  "repo": null,
  "item": null,
  "branch": null,
  "roadmap_path": null,
  "linear_id": null,
  "worker_mode": "standard",
  "directive_path": null,
  "worker_brief": null,
  "archives": ["ai-tools/docs/plans/foo.md"],
  "blockers": [
    {"repo": "typey.site", "linear_id": "DEV-54", "detail": "Attended visual QA required: ..."}
  ],
  "considered": ["..."],
  "summary": "Five-line summary for the log."
}
```

`action` / `outcome_token`: `blocked_no_item` / `blocked-no-item`, or
`nothing_qualified` / `nothing-qualified`.

`run.sh` still invokes a bookkeeping worker so Step 2b comments, archives, and Step 8
ledger writes happen with write tools — put everything they need in this JSON.

---

# Steps (orchestrator owns these)

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
| a `skipping — …` line only, no `=== advance-roadmap run` header | usage/orchestrator gate fired | nothing to resume |
| `=== advance-roadmap exit=` / legacy `=== claude exit=0` plus ledger row | completed | nothing to resume |
| non-zero exit, **or no exit line at all** | killed mid-flight | reconcile, below |

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

Before walking repos, query active Linear issues once as described in Step 2. After removing
`human-only` issues, collect those carrying `do-next`. Put their explicitly labeled `repo/*`
directories at the front of the repo walk, preserving Linear's status/priority order; then use the
normal prior-run-first order for everything else. A `do-next` issue without a repo label is still a
Step 2b blocker, not permission to infer a repo.

This override applies only to **new selection**. Step 0 always reconciles an interrupted prior run
first, including its existing branch and dirty work, before any `do-next` item may dispatch. The
label changes queue order, not eligibility: every repo safety gate and Step 2 skip rule still
applies. If the first `do-next` item is blocked, record/comment the blocker normally and continue
to the next `do-next` item, then the ordinary queue.

For each direct child of `/Users/jake/Dropbox/code` that is a git repo, in that order:

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
   list still applies unchanged. **Issues labeled `human-only` never qualify a repo**, so filter
   them out *before* asking whether anything here is actionable — otherwise a repo whose every
   ticket is human-only presents as a candidate on every run and then dead-ends in Step 2, which
   reads as a near-miss worth investigating when it's actually working as designed. If that filter
   empties the repo's queue and there's no roadmap or plan item either, the repo just doesn't
   qualify.
8. Resolve the ticket you would attempt before applying the clean-tree gate: use the linked ticket
   in the roadmap index, or the highest-priority eligible Linear issue for the repo. Then apply the
   clean-tree, preflight, and live-session checks from the safety rules. **Check for a directive
   first** if the tree is dirty; see Step 1a before skipping outright. If no directive applies,
   report the dirty-state blocker on the resolved ticket per Step 2b before moving on.

Take the first repo that passes everything. **If no repo qualifies, stop and report that no
actionable roadmap was found** — record it in memory (Step 8) and do nothing else. Do not invent
roadmap items, do not go looking for other work to do, do not fall back to `wrapup-repos` behavior.
If a dirty repo would otherwise have qualified and has no directive waiting, say so in the final
summary and point at `/prepare-roadmap` — that's the one thing that ever unblocks it.

## Step 1a — A directive is a bounded exception to clean-tree, nothing more

Before skipping a dirty repo, check for
`/Users/jake/.claude/automations/advance-roadmap/directives/<repo>.md`. Its existence is the
*only* thing that ever lets this skill touch a dirty tree — nothing else does, ever, and its
absence means the clean-tree rule is exactly as absolute as it reads above.

If one exists, read it in full — it has up to three parts (a dirty-tree resolution with the
`git status --porcelain` snapshot recorded when Jake gave the instruction, answered decisions to
fold into `ROADMAP.md`, and a go-ahead checklist) — then:

1. **Resolve the dirty tree exactly as instructed, nothing more.**
   - A commit-shaped instruction ("commit it as one", "split into A and B") only needs its named
     paths to still exist and still be dirty. Leave every other dirty path in the repo untouched —
     new, unrelated WIP that appeared since the directive was written is not what this
     authorization covers, and folding it in anyway is exactly the "commit their WIP without
     asking" failure the clean-tree rule exists to prevent.
   - A destructive instruction ("discard it") requires the **live** `git status --porcelain` to
     match the recorded snapshot *exactly*, byte for byte. Any difference at all: stop, don't
     discard anything, archive the directive unconsumed (move it to `directives/archive/` with
     today's date, so `/prepare-roadmap`'s next sweep re-asks against current state), and skip
     this repo this run.
   - Write commit messages in the repo's own convention (recent `git log`), same as any other
     commit this skill makes, using the intent Jake described rather than a verbatim label.
2. **Archive the directive** — move it to `directives/archive/<repo>-<stamp>.md` — as soon as the
   tree is clean and *before* anything is merged or pushed. It is handoff metadata, never repo
   content: it must never be `git add`ed, and living outside every repo means it never needs a
   `.gitignore` entry to stay that way.
3. **Fold every answered decision into the ticket description** when the item is ticket-backed,
   keeping its acceptance criteria and blockers current. For an older file-only item, record the
   decision on that item's roadmap text. This can unblock an item Step 2 would otherwise still
   skip in the same run.
4. **Continue into this repo's remaining Step 1 checks** (preflight, live sessions) and Step 2
   selection as usual. The directive clears the clean-tree gate specifically — it doesn't exempt
   anything else, doesn't skip verification, and doesn't guarantee this repo is the one item this
   run ships.
5. If the directive's go-ahead says **"just clean up, don't implement anything yet"**: stop once
   the tree is clean and the resolution commit(s) are pushed. Record the outcome (Step 8) and do
   not proceed to Step 2 for this repo this run.

Never write a directive yourself, and never treat a dirty tree as license to guess at Jake's
intent by any other means — a directive is the only voice this skill listens to here.

Roadmaps may be compact Linear indexes. In those files, the linked ticket description is the live
scope and acceptance contract; the roadmap preserves ordering, dependencies, and product context.
Older repos may still carry full prose items or only `docs/plans/` documents, so adapt to the source
that exists instead of requiring one format.

## Step 2 — Pick ONE item

Read the whole roadmap, plus the repo's `CLAUDE.md`, `README*`, `PRODUCT.md`, and `package.json`
scripts, so you're choosing against real project conventions.

Within a qualifying repo, an eligible Linear issue labeled `do-next` outranks every fresh roadmap,
plan, or unlabeled Linear candidate. When several carry it, use Linear state (`Todo` / `In
Progress` before `Backlog`), then Linear priority, then the query's stable order. Do not let the
label bypass dependencies, safety checks, or the skip rules below.

**Prefer** items that:
- list concrete touchpoints in the ticket or source plan — an item without them is usually
  underspecified for an unattended run;
- have logic that can be **unit-tested**, so "it works" is provable without a human;
- are clearly scoped, with checkable acceptance criteria in the ticket or source plan.

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
- Add a missing repo label yourself only when the issue's own content makes the answer unambiguous.
  Otherwise comment on that issue per Step 2b and leave the label for Jake; adding the wrong label
  could route implementation into the wrong repository.
- **`human-only` is a hard exclusion, and a silent one.** Drop every issue carrying the
  `human-only` label from the batch before you triage it. The label marks work that needs a human
  at a keyboard — a GUI installer, a vendor sign-in, a purchase, a device in hand — so no worker
  provider can finish it regardless of how well the ticket is specified. Unlike a missing repo
  label, this is **not** a Step 2b blocker: don't comment on it, don't mention Jake, and don't
  count it among the items you report as considered. Log `skipped-human-only` plus the `DEV-N` id
  in run memory and move on. It's a flat workspace label that coexists with the issue's `repo/*`
  label, so check labels for both independently.
- **`do-next` is a next-run queue override.** It moves an otherwise eligible issue ahead of all
  fresh work after Step 0 has reconciled any interrupted prior run. It does not override
  `human-only`, repo safety, dependencies, or the Step 2 skip list. A completed issue naturally
  leaves the active queue when Step 7 moves it to `Done`; do not remove the label on the way in.
- **`/goal` selects durable goal execution.** For an issue carrying this label, emit
  `"worker_mode": "goal"`; otherwise emit `"worker_mode": "standard"`. The worker launcher owns
  the provider-specific command syntax. Preserve this mode when resuming an interrupted run by
  re-reading the ticket labels when `linear_id` is present.
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

## Step 2b — Put blockers on the ticket and mention Jake

When a run tries to implement a ticket and cannot continue, put the blocker where the work lives:
on that Linear issue. This applies when the repo is dirty, a required human decision is unresolved,
attended acceptance is required before implementation can safely land, or work stalls on a product
judgment during Steps 4 or 5.

Use `list_comments` on the issue before writing. A blocker comment has this shape:

```markdown
@jnelks advance-roadmap is blocked on this ticket.

**Repo:** `<directory>`
**Blocker:** Dirty working tree
**Details:** `M src/example.ts`, `?? notes.md`
**Needed from you:** Resolve the listed work, or run `/prepare-roadmap` to record how it should be handled.

<!-- advance-roadmap:blocker -->
```

For a decision blocker, replace `Details` with the smallest self-contained question and its viable
options. For attended acceptance, name the device, interaction, visual, or by-ear check required.
Never paste diff contents, credentials, environment values, or other secrets into the comment.

`@jnelks` is Jake's Linear `displayName`; use that exact mention so Linear notifies him. Add one
comment per affected ticket, not one per roadmap bullet. If an issue lacks a repo label, comment on
that same issue and ask which repo it belongs to.

### Deduplicate scheduled-run pings

This job runs every six hours. Find the newest comment containing
`<!-- advance-roadmap:blocker -->`, normalize whitespace in its visible body, and compare it with
the proposed comment. If the repo, blocker type, details, and requested action are unchanged, do
not add another comment and record `blocker-comment-unchanged` in run memory. If any of those facts
changed, create a new top-level comment so the changed `@jnelks` mention produces a fresh signal.
Do not edit the older comment; it is useful history.

Linear comments replace this skill's former `.claude/IN_PROGRESS.md` and `PushNotification`
blocker channel for ticket-backed work. If Linear is unavailable, do not attempt OAuth from a
headless run and do not mutate the repository to create a fallback note. Record the blocker and
the failed comment attempt in run memory and the final summary, then continue evaluating other
repos when safe.
