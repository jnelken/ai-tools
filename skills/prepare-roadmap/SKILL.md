---
name: prepare-roadmap
description: Interactive-only sweep across every personal repo under ~/Dropbox/code that is currently blocked from advance-roadmap — a dirty working tree, or open "decisions needed" questions — and asks Jake how to resolve each one, one repo at a time. Writes no code and runs no git: it persists each answer immediately as a `## Directive` section on that repo's Linear ticket, and may record the answer in the repo's own markdown (ROADMAP.md, docs/plans/, IN_PROGRESS.md), which a later /advance-roadmap run reads, commits, and acts on. Use on-demand ("/prepare-roadmap", "unblock the roadmap repos", "clear the roadmap blockers", "sweep the blocked repos"). Complementary to [[advance-roadmap]], never a replacement — this skill only asks and records; that one is the only thing that ever writes code, commits, or pushes.
---

# Prepare the roadmap

`/advance-roadmap` refuses to touch a repo with a dirty working tree, and it can't
decide a product/taste question that's the user's to make — so both classes of repo sit
blocked indefinitely, run after run, until a human clears them. This skill is that human
pass: sweep every candidate repo, surface what's blocking each one, ask Jake one question
at a time, and record his answer immediately — so a sweep he abandons halfway through
still leaves every repo he did answer fully unblocked.

**This skill is interactive-only.** It exists to ask questions a human has to answer, so
it has no scheduled/unattended mode. If invoked headlessly, say so and stop.

## The hard rule: no code, no git — prose and Linear only

`/prepare-roadmap` may write in exactly two places: **Linear** (directives, and the tickets
to carry them), and a repo's own **markdown** — `ROADMAP.md`, `docs/plans/*.md`, other docs,
and `.claude/IN_PROGRESS.md` — where an answer belongs in the prose.

Never anything else. Specifically never:

- **Any non-`.md` file in a target repo.** No source, config, JSON, lockfile, or script. If
  an answer implies a code change, that's the directive's job to describe and
  `/advance-roadmap`'s job to make.
- **Any git command that mutates.** No `git add`, `git commit`, `git checkout --`,
  `git stash`, `git reset`, no branching, no pushing. Reads only: `git status`, `git diff`,
  `git log`. Prose edits are left **uncommitted** for `/advance-roadmap` to land.
- **Implementation of any kind**, running tests or builds, or deciding which roadmap item
  gets built.

This split is deliberate: `/advance-roadmap` is the one skill hardened (allowlists,
preflight, ff-only merges, all-or-nothing verification) to safely mutate these repos and push
`main`. Keeping this skill to prose and Linear means it stays safe to run often, and
everything risky stays confined to the skill already built for it.

### Editing a tracked `.md` dirties the tree — so it needs its own directive

This is the trap, and getting it wrong locks a repo out of **every future run**. Writing into
a tracked file makes `git status --porcelain` non-empty, which is precisely the condition
`/advance-roadmap` refuses to start on. So recording an answer in `ROADMAP.md` would, by
itself, guarantee that answer never gets acted on.

The rule is therefore: **whenever a prose edit changes `git status`, the directive you write
must cover the file you edited.** Include the edited path in the canonical snapshot, and make
the instruction commit-shaped so the next run commits it. Never write a prose edit you do not
also authorize the cleanup of.

Two consequences worth stating plainly:

- **A gitignored `.md` needs no directive.** `.claude/IN_PROGRESS.md` is gitignored in most
  repos that have one, so editing it doesn't change `git status` and doesn't block anything.
  Check with `git -C <repo> check-ignore -q <path>` rather than assuming — if it is *not*
  ignored, it's a tracked edit and the rule above applies in full.
- **Never write a destructive instruction for a file you wrote.** "Discard it" covering your
  own prose edit is incoherent. Commit-shaped only, and attribute it honestly: the instruction
  is `/prepare-roadmap`'s ("commit the ROADMAP.md decision edits recorded by
  /prepare-roadmap"), not a quote of something Jake said.

### Writing `.claude/IN_PROGRESS.md`, which you share with two other skills

That file is owned by [[close-out]] and read by [[pick-up]], so treat it as a **running
document you append to**, never a file you author: read it first, leave every open item
another session put there, and follow close-out's own conventions — resolve a line by
**deleting** it rather than leaving a `- [x]` row behind, and stamp
`_Last updated: <date> (prepare-roadmap)_` so it's obvious which writer touched it last.
Three writers on one file is workable only if each of them is additive.

### Where an answered decision should land

Prefer the place Jake himself resolves these by hand: **the roadmap item's own text**. See
`project_advance-roadmap-runs.md`'s "mailcruxh suddenly qualified" case — he answered nine
open decisions by editing the roadmap item text directly, not `.claude/IN_PROGRESS.md`. So:

- The decision itself goes into `ROADMAP.md` (or the `docs/plans/` doc) as a `**Decided:**`
  line on the item, where it stays useful to whoever reads the roadmap next.
- The matching `- [ ]` line in `.claude/IN_PROGRESS.md`, if one exists, gets deleted — it's
  no longer an open question.
- Either way the directive records **where you put it**, so `/advance-roadmap` commits your
  edit instead of folding the same decision in a second time. That's what the
  `**Recorded in:**` field in the directive format is for.

You may also leave the prose to `/advance-roadmap` entirely: write only the directive, with
`**Recorded in:** not yet — fold into <path>`. That's the right choice when the wording
needs judgment about an item you don't have full context on.

## Where directives live: on the repo's own Linear ticket

A directive is a `## Directive` section appended to the **description of the Linear work
ticket `/advance-roadmap` would resolve for that repo** — the ticket linked from the repo's
roadmap index, or the highest-priority eligible issue carrying that repo's `repo/*` label
(team **Dev**, workspace `jnelken`). Nothing is written to disk anywhere: Linear is the
persisted state, not merely the place the conversation happened.

Two mechanics make that findable and safe, and they are separate on purpose:

- **The `roadmap-directive` label on that same ticket is the marker.** Apply it whenever
  you write a directive. `/advance-roadmap` finds every pending directive in a single
  `list_issues(team: "Dev", label: "roadmap-directive")` call per run, and clears the label
  once it has acted. The label means *a directive here is pending* — nothing more.
- **The label is not an authorization.** It sits on an ordinary work ticket that Jake edits
  by hand, so it can go stale in ways a purpose-built object couldn't. What actually
  authorizes a destructive instruction is the recorded `git status` snapshot matching the
  live tree exactly, checked by `/advance-roadmap` at execution time. Write the snapshot
  carefully; it's the real safety mechanism.

Because the directive rides on a real work ticket, there is no risk of `/advance-roadmap`
mistaking a directive for an item to implement — the ticket *is* legitimate work, and the
directive only changes whether that work can start.

### When a blocked repo has no eligible ticket, file one

A repo whose roadmap is file-only (`ROADMAP.md` or `docs/plans/` with no mirrored Linear
issue), or that has no roadmap at all and only a dirty tree, has nothing to carry a
directive. Writing inside the repo is forbidden by the hard rule above, so **create the
ticket** — that is allowed, and it is the only way such a repo ever gets unblocked.

Four constraints keep that from becoming a mess:

1. **Only ever after Jake has answered — never during discovery or triage.** The ticket is
   created as part of persisting an answer, in the same breath as the directive itself. So a
   sweep Jake abandons halfway leaves no fabricated tickets behind: no answer, no ticket.
   This ordering is the whole safety property; don't pre-create tickets for candidates you
   haven't asked about yet.
2. **Invoke [[linear-ticket-gen]] first.** The global `CLAUDE.md` requires it for any Linear
   issue you create, and it owns the parts this skill shouldn't re-derive: resolving the
   team, and the two-pass overlap check (scoped, then workspace-wide). **If an existing issue
   already covers the work, attach the directive to that one instead of filing a new
   ticket** — that check is what stops this from slowly duplicating the board.
3. **Mirror what exists; never invent scope.** Two shapes, depending on what's actually there:
   - **A roadmap item the directive unblocks** — title is the item's own name, description is
     its scope and touchpoints as the roadmap states them, plus a pointer to the source file
     and section so the roadmap stays the origin. This is exactly the "mirror individual
     actionable items as lean issues when they're ready to be picked up or automated" path the
     global `CLAUDE.md` already describes, and a directive is precisely that readiness signal.
     Don't restate the whole roadmap, don't add acceptance criteria Jake didn't state, and
     don't promote an item he didn't just green-light.
   - **Nothing but a dirty tree** — title `Resolve uncommitted work in <repo>`, description is
     what the tree contains plus Jake's verbatim instruction. Cleaning the tree is real work,
     so this is an honest ticket rather than a container invented for the occasion.

     **Pair a cleanup-only ticket with the "just clean up" go-ahead.** By existing it newly
     qualifies the repo for `/advance-roadmap` at all (its Step 1: a Linear issue naming a
     repo qualifies it), and a bare go-ahead would invite that run to pick an item and build
     it — which is not what Jake agreed to when he answered a question about uncommitted work.
4. **Label it the way the workspace requires.** `repo/<directory>` is mandatory and
   single-select, and the label name is the **directory** name rather than the GitHub repo —
   `repo/openclaw-vps` is `jnelken/vena-vps`, and each label's description records its own
   mismatch, so read it when the two differ. Add `roadmap-directive`. Leave
   `Bug`/`Improvement`/`Feature` alone unless Jake said which it is; a guessed type label is
   worse than none. File it in state **Todo** — a directive means "this is ready to pick up",
   which is what distinguishes it from the Backlog.

Name every ticket you filed in the Step 3 summary. It's a new object Jake didn't ask for by
identifier, so he should leave the sweep knowing it exists.

### Directive format

Append this as the **last** section of the ticket description. The marker comment is the
first line and carries the repo name, so it is a unique anchor for a later `patch` edit —
`## Directive` alone is not reliably unique in a description that accumulates history.

~~~markdown
<!-- advance-roadmap:directive:apt-sqft -->
## Directive

**Recorded:** 2026-09-23 by /prepare-roadmap · **Repo:** `apt-sqft`

### Dirty-tree resolution

<Omit this whole subsection only if the repo's tree was already clean AND you wrote no tracked
prose edit. A repo that was clean until you recorded a decision in its `ROADMAP.md` now has a
dirty tree, and needs this subsection covering that file — otherwise the clean-tree gate blocks
the very answer you just recorded.>

Snapshot of `git status --porcelain` at the time Jake gave this instruction, in canonical
form — one entry per line as `XY | path`, where each unset slot of the two-character status
field is written as `-`:

```
-M | src/App.tsx
?? | notes.md
A- | src/staged.ts
-M | ROADMAP.md
```

**Instruction (verbatim):** Commit it all as one commit

Where a line is a prose edit this skill made rather than work Jake left behind — `ROADMAP.md`
above — say so, and attribute the instruction to the skill rather than quoting Jake:

**Instruction:** Commit the `ROADMAP.md` decision edits recorded by `/prepare-roadmap`
(`/prepare-roadmap`'s own instruction, not Jake's words)

### Answered decisions

<Omit if none. One block per item that had an open "decisions needed" question.>

- **Item:** <the roadmap item name / heading, or the exact `.claude/IN_PROGRESS.md` line>
  **Question:** <the open question, as originally posed>
  **Answer:** <Jake's literal answer>
  **Recorded in:** `ROADMAP.md` (item "Unit toggle", `**Decided:**` line) — already written,
  just commit it
  <or:> not yet — fold into `docs/plans/ROADMAP.md`

### Go-ahead

<one of:>
Proceed through the normal Step 1/2 selection now that this repo qualifies.
Specifically implement: <item name>.
Just clean up the tree — don't implement anything yet, wait for the next run.
~~~

**Never name a `human-only` item in the go-ahead.** That label marks work `/advance-roadmap` is
required to refuse — a GUI installer, a vendor sign-in, a purchase, a device in hand, a credential
only Jake holds — so a `specifically implement: <item>` line pointing at one hands that skill an
instruction it must ignore, and the repo sits blocked with nothing recording why. This skill never
queries Linear, so the label won't be in front of you; it's the one thing worth checking by hand
before writing that line. If Jake names such an item anyway, record it as a plain note under
*Answered decisions* and leave the go-ahead on the normal Step 1/2 selection.

**Why the canonical snapshot form, and don't "simplify" it away.** In raw
`git status --porcelain` the leading two characters *are* the meaning — ` M foo.ts` and
`?? foo.ts` differ only in leading whitespace. That text now round-trips through Linear's
editor, and while the API path preserves it byte-for-byte (verified 2026-09-23), a human
editing the ticket in Linear's web rich-text editor is exactly what this skill invites.
Writing `-M` / `A-` instead of leaning on spaces removes the entire class of problem:
`/advance-roadmap` parses live porcelain into the same `(status, path)` pairs and compares
them as an unordered **set**, so "matches exactly" never depends on whitespace or line
order surviving a round trip.

Record the path exactly as porcelain prints it, including any quoting git applies to paths
with spaces or special characters, and record a rename (`R  old -> new`) verbatim in the
path field.

### Updating a directive rather than filing a second

If a ticket for this repo already carries the `roadmap-directive` label, a directive is
already pending and `/advance-roadmap` hasn't consumed it yet:

- Don't write a second one. **Edit the existing section** (`save_issue` with a `patch`
  anchored on that repo's marker comment) — adding an answered decision to a directive that
  already holds a dirty-tree resolution, for instance.
- Don't re-ask about that repo in the same sweep. Mention in passing that it's already
  queued, and move on.
- If Jake explicitly wants to change a pending directive, read the current one back to him
  first and confirm before overwriting any part of it.

That keeps "one pending directive per repo" true by construction, which is what lets
`/advance-roadmap` treat two pending directives for one repo as a live ambiguity to refuse
rather than a stale leftover to guess at.

## Step 1 — Discover blocked repos, live

**Do not use `project_advance-roadmap-runs.md` as the primary list.** Its "Blocker sets"
table only counts one blocker class — open `.claude/IN_PROGRESS.md` decisions in repos
`/advance-roadmap` has actually reached — and repos blocked by a dirty tree (the class
this skill exists for) are only ever mentioned in that file's prose, inconsistently, if at
all. A repo that has never once had a clean tree since it was created — Jake's own
observation, not hypothetical — would never appear in a memory-first sweep. Discover live
instead; read memory only afterward, per repo, to add context ("dirty for the Nth run
running") to what you show Jake.

For each direct child of `/Users/jake/Dropbox/code`:

1. Skip if not a git repo (no `.git`), or if `.git` is a **file** (a linked worktree of a
   repo already in the list).
2. Skip unless `git -C <repo> remote get-url origin` resolves to a `jnelken/*` repo. Skip
   silently if there's no origin at all.
3. Skip if `.noroadmap` exists at the repo root.
4. Run `git -C <repo> status --porcelain`.
   - **Non-empty → dirty-tree blocked.** Capture the full output.
   - **Empty →** not dirty-tree blocked. A clean repo with no open decisions (next step)
     is not a candidate for this skill at all — that's `/advance-roadmap`'s normal
     territory, leave it alone.
5. On **every** repo, dirty or clean, look for `.claude/IN_PROGRESS.md` (root `IN_PROGRESS.md` too, same
   fallback order [[pick-up]] uses) and read it directly — it's gitignored in most repos
   that have one, so `git status` won't surface it. Parse its `- [ ]` lines under any
   "Decisions needed" heading. Any unchecked line → decisions-needed blocked. An
   all-checked or missing file is not blocked.

A repo can be dirty-tree blocked, decisions-needed blocked, both, or neither. Both is
common: [[wrapup-repos]] writes its roadmap and judgment-call decisions into this same
`Decisions needed` heading, and the repos it works are usually the dirty ones — so read the
file on dirty repos too, and ask about the tree and the decisions in the same pass. Build
the candidate list from every repo that's at least one of the two.

**Sanity-check your own sweep before asking anything**: the repos it finds dirty should
line up with what `project_advance-roadmap-runs.md`'s prose separately names as dirty
(as of this skill's authoring: `remembering-michael-nelken`, `[archived]mailcrush`,
`apt-sqft`, `gmail-status-tracker`, `llm-prompt-manager`, `roaddmap`, `wav-explorer`, plus
whichever of the actively-tracked four — typey.site, mailcruxh, jakenelken.com, dubsketch —
happen to be dirty right now). If your sweep misses names memory calls out, the discovery
logic above is wrong; fix it before proceeding, don't just note the discrepancy.

**Also resolve each candidate's ticket now**, in the same pass: query Linear once for the
repo labels in play, and for each candidate find the ticket a directive would attach to
(the roadmap index's linked ticket, or the highest-priority eligible `repo/*` issue). A
candidate with no eligible ticket still gets asked about — you file its ticket once he
answers, per *When a blocked repo has no eligible ticket, file one* above. Note which
candidates already carry `roadmap-directive`; those are already queued and get skipped this
sweep.

### No shell available? Fall back to Linear, and say that you did

The live sweep above is the complete view and is always preferred. When this skill runs
somewhere without a shell — a chat surface with Linear access but no `git` — discover from
`/advance-roadmap`'s own blocker comments instead:

1. `list_issues` for team **Dev** filtered to issues updated recently (say `-P14D`) that
   carry a `repo/*` label.
2. Read each one's comments and take the newest containing
   `<!-- advance-roadmap:blocker -->`. Those comments carry the repo, the blocker type, and
   the dirty paths — which is enough to ask Jake about.

**This mode's coverage is partial by construction, and that must be stated, not implied.**
`/advance-roadmap` stops at the first repo that qualifies, so it only ever posts blocker
comments for repos it actually got far enough to evaluate; a repo further down the list has
no comment and is invisible here. Say plainly in the Step 3 summary which mode ran, and in
fallback mode recommend a terminal sweep for the complete picture.

A blocker comment's dirty-path list is prose written for Jake to read, not the canonical
snapshot. In fallback mode you cannot produce a verified snapshot at all, so **only record
commit-shaped instructions** — never a destructive one. A "discard it" answer needs a live
tree to snapshot against; tell Jake it has to wait for a terminal sweep, and record nothing.

## Step 2 — Work the list, one repo at a time

Order doesn't matter much; alphabetical is fine. For each repo, before asking anything,
show Jake enough to decide:

- **Dirty-tree blocker:** the repo name, the full `status --porcelain` output, and
  `git -C <repo> diff --stat` (both tracked and, via `--stat` on a `diff` against an
  empty tree for untracked files if it's not obvious, a one-line note of what's new).
  If memory has a note on this repo (how many runs it's been dirty, what the working
  tree contains per `project_advance-roadmap-runs.md`), mention it — Jake may not
  remember what he left uncommitted three days ago.
- **Decisions-needed blocker:** the repo name and each unresolved question line verbatim.

Also name the ticket the directive will land on, so Jake can object before it's written.

### Asking the questions

In a Claude Code terminal that's one `AskUserQuestion` call; on a chat surface it's
whatever that host's native question mechanism is, and plain numbered questions in the
message if it has none. The turn-taking is always **synchronous, in one live session** —
never file a question and wait for a Linear reply later.

For the dirty tree, two questions:

1. *"How should the uncommitted work in `<repo>` be handled?"* — options:
   - `Commit it all as one commit`
   - `Discard it (drop the uncommitted changes)`
   - `Leave it — skip this repo for now`
   (For a split into multiple commits, or any instruction with real nuance, the user
   picks the auto-provided **Other** and types it — record that text verbatim, don't
   paraphrase it into your own words.)
2. *"Once the tree is clean, should advance-roadmap go ahead and implement a roadmap item
   here too?"* — options:
   - `Yes — implement the next qualifying item`
   - `Just clean up the tree, don't implement anything yet`
   Skip this second question if the answer to the first was "Leave it" — there's nothing
   to build on top of a tree that's staying dirty.

For a decisions-needed blocker, one question per unresolved line (batch up to 4 per call
when a repo has several — they're independent). For the options:

- If the line's own text already names two or three concrete alternatives (many do — the
  Step 2b format asks "does X or Y?"), offer those as the options.
- Otherwise offer your single best-guess candidate answer as one option.
- Always include `Skip — leave this open for next sweep` as one of the options.
- The user can always pick **Other** to type a full free-text answer regardless of what
  options you listed — don't strain to make the presets perfect, they only need to cover
  the common case and leave an obvious escape hatch for everything else.

### Persist immediately — before moving to the next repo or the next question

The moment a repo's questions (or as many as Jake chose to answer — "Skip" is a valid,
complete answer) resolve, write the directive to Linear right then. Do not batch writes
until the end of the sweep. If Jake stops the session after repo 2 of 5, the first two
directives must already be live on their tickets, complete and correct — that's the entire
point of the "doesn't need to be complete" requirement.

Concretely, per repo:

1. **Write the prose first, if you're writing any.** Record the decision in `ROADMAP.md` or
   the `docs/plans/` doc, and delete the now-answered `- [ ]` line from
   `.claude/IN_PROGRESS.md`. Doing this *before* the snapshot is what makes the next step
   cover your own edit — the ordering is the safeguard, not an incidental detail.
2. Re-run `git -C <repo> status --porcelain` and build the canonical snapshot from **that**
   output, not from what you showed Jake earlier in the sweep. The snapshot's whole job is to
   describe the tree at the moment the instruction was given — including any tracked `.md` you
   just edited, which must appear in it (`git check-ignore` tells you whether it will).
3. **If this repo had no eligible ticket, create it now** — per *When a blocked repo has no
   eligible ticket, file one*, via [[linear-ticket-gen]], with its `repo/*` label and in state
   `Todo`. This is the one point in the sweep where a ticket gets created: after the answer,
   never before.
4. `save_issue` on that ticket, appending the `## Directive` section (or `patch`ing the
   existing one, per *Updating a directive* above).
5. `save_issue` again — or in the same call — to add the `roadmap-directive` label. On a
   ticket that already existed use `addLabels`, never `labels`, which would replace the whole
   label set and drop its `repo/*` label. (On a ticket you just created you can pass both
   labels at creation.)
6. A "Skip" answer writes nothing for that item — it simply isn't recorded, so the next
   sweep asks again naturally. There's no partial-answer state to track. **A skipped repo
   gets no ticket either** — if every answer for a repo was "Leave it" / "Skip", nothing at
   all is created for it.

If a repo's only answers were "Leave it" / "Skip", write no directive and apply no label.

## Step 3 — Final summary

Say up front **which discovery mode ran** — full live sweep, or the partial Linear
fallback (and if the latter, that a terminal sweep is needed for the complete picture).

Then list, per repo: what was recorded (dirty-tree resolution / N answered decisions /
both), which ticket now carries it, and what was skipped. **Name every ticket you had to
create**, and say which are cleanup-only — those are the ones whose go-ahead deliberately
stops short of implementing anything.

**Name every file you edited, per repo**, and confirm each tracked one is covered by that
repo's directive. Jake is walking away from this sweep with uncommitted prose edits in repos
he didn't touch himself; he should know exactly which, and that the next run will commit them.
If any tracked edit somehow ended up *not* covered by a directive, say so loudly — that repo
is now blocked until it is.

Close by saying that the next `/advance-roadmap` run (scheduled, four times a day) picks
up each pending directive automatically — or that Jake can run `/advance-roadmap` right now
if he wants one acted on immediately.

Do not offer to run `/advance-roadmap` yourself as part of this skill. That's a separate,
explicit invocation — keeping "decide" and "do" as two distinct steps the user chooses to
take is the whole design.
