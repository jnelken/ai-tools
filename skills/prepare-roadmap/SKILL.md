---
name: prepare-roadmap
description: Interactive-only sweep across every personal repo under ~/Dropbox/code that is currently blocked from advance-roadmap — a dirty working tree, or open "decisions needed" questions — and asks Jake how to resolve each one, one repo at a time. Never touches a repo itself: it persists each answer immediately as a `## Directive` section on that repo's Linear ticket, which a later /advance-roadmap run reads and acts on. Use on-demand ("/prepare-roadmap", "unblock the roadmap repos", "clear the roadmap blockers", "sweep the blocked repos"). Complementary to [[advance-roadmap]], never a replacement — this skill only asks and records; that one is the only thing that ever writes code, commits, or pushes.
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

## The one hard rule: this skill never touches a repo

`/prepare-roadmap` reads. It runs `git status`, `git diff --stat`, and reads files — never
`git add`, `git commit`, `git checkout --`, `git stash`, `git reset`, or any edit to a file
*inside* a target repo. It doesn't implement anything, doesn't run tests or builds, and
doesn't decide which roadmap item to build. Everything it produces is a directive recorded
**in Linear**, for `/advance-roadmap` to carry out later.

This is deliberate, not a limitation to work around: `/advance-roadmap` is the one skill
hardened (allowlists, preflight, ff-only merges, all-or-nothing verification) to safely
mutate these repos and push `main`. Splitting "decide" from "do" means this skill can stay
simple and safe to run often, and the risky half stays confined to the skill already built
for it.

Corollary: **never write anything under a target repo's `.claude/`**, including
`.claude/IN_PROGRESS.md`. That file is owned by [[close-out]] and read by [[pick-up]]; a
third writer racing those two is exactly the kind of divergent-copy bug the ecosystem
here works hard to avoid. An answered "decisions needed" question is instead recorded in
the directive, and `/advance-roadmap` folds it into the ticket (or, for an older file-only
item, into `ROADMAP.md`'s own prose) as part of the bookkeeping it already does — the same
place Jake himself resolves these by hand today (see `project_advance-roadmap-runs.md`'s
"mailcruxh suddenly qualified" case: he answered nine open decisions by editing the
roadmap item text directly, not `.claude/IN_PROGRESS.md`).

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

### When a blocked repo has no eligible ticket

This is the one case the design can't cover, and it must be surfaced rather than worked
around. A repo whose roadmap is file-only (`ROADMAP.md` or `docs/plans/` with no mirrored
Linear issue) has nowhere to carry a directive: writing into the repo is forbidden by the
hard rule above, and **filing a ticket is not this skill's job** — it only asks and records.

So: record nothing, and say so plainly in the Step 3 summary —

> `<repo>` is dirty but has no Linear ticket to carry a directive. File one (or mirror the
> roadmap item into Linear) and re-run `/prepare-roadmap`.

Never invent a ticket to have somewhere to write. An abandoned sweep that left fabricated
tickets behind would defeat the persist-immediately property this skill is built around.

### Directive format

Append this as the **last** section of the ticket description. The marker comment is the
first line and carries the repo name, so it is a unique anchor for a later `patch` edit —
`## Directive` alone is not reliably unique in a description that accumulates history.

~~~markdown
<!-- advance-roadmap:directive:apt-sqft -->
## Directive

**Recorded:** 2026-09-23 by /prepare-roadmap · **Repo:** `apt-sqft`

### Dirty-tree resolution

<Omit this whole subsection if the repo's tree was already clean.>

Snapshot of `git status --porcelain` at the time Jake gave this instruction, in canonical
form — one entry per line as `XY | path`, where each unset slot of the two-character status
field is written as `-`:

```
-M | src/App.tsx
?? | notes.md
A- | src/staged.ts
```

**Instruction (verbatim):** Commit it all as one commit

### Answered decisions

<Omit if none. One block per item that had an open "decisions needed" question.>

- **Item:** <the roadmap item name / heading, or the exact `.claude/IN_PROGRESS.md` line>
  **Question:** <the open question, as originally posed>
  **Answer:** <Jake's literal answer>

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
   - **Empty →** check for decisions-needed blockers (next step). A clean repo with no
     open decisions is not a candidate for this skill at all — that's `/advance-roadmap`'s
     normal territory, leave it alone.
5. On a clean tree, look for `.claude/IN_PROGRESS.md` (root `IN_PROGRESS.md` too, same
   fallback order [[pick-up]] uses) and read it directly — it's gitignored in most repos
   that have one, so `git status` won't surface it. Parse its `- [ ]` lines under any
   "Decisions needed" heading. Any unchecked line → decisions-needed blocked. An
   all-checked or missing file is not blocked.

A repo can be **only** dirty-tree blocked, **only** decisions-needed blocked (rare —
`.claude/IN_PROGRESS.md` implies a prior `/advance-roadmap` run that itself required a
clean tree to get there), or neither. Build the candidate list from every repo that's at
least one of the two.

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
candidate with no eligible ticket is reported, not asked about — see *When a blocked repo
has no eligible ticket* above. Note which candidates already carry `roadmap-directive`;
those are already queued and get skipped this sweep.

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

1. Re-run `git -C <repo> status --porcelain` and build the canonical snapshot from **that**
   output, not from what you showed Jake earlier in the sweep. The snapshot's whole job is
   to describe the tree at the moment the instruction was given.
2. `save_issue` on the resolved ticket, appending the `## Directive` section (or `patch`ing
   the existing one, per *Updating a directive* above).
3. `save_issue` again — or in the same call — to add the `roadmap-directive` label
   (`addLabels`, never `labels`, which would replace the ticket's whole label set and drop
   its `repo/*` label).
4. A "Skip" answer writes nothing for that item — it simply isn't recorded, so the next
   sweep asks again naturally. There's no partial-answer state to track.

If a repo's only answers were "Leave it" / "Skip", write no directive and apply no label.

## Step 3 — Final summary

Say up front **which discovery mode ran** — full live sweep, or the partial Linear
fallback (and if the latter, that a terminal sweep is needed for the complete picture).

Then list, per repo: what was recorded (dirty-tree resolution / N answered decisions /
both), which ticket now carries it, and what was skipped. Call out separately every repo
that was blocked but had **no eligible ticket** to carry a directive, since those are the
ones needing an action from Jake before they can ever be unblocked.

Close by saying that the next `/advance-roadmap` run (scheduled, four times a day) picks
up each pending directive automatically — or that Jake can run `/advance-roadmap` right now
if he wants one acted on immediately.

Do not offer to run `/advance-roadmap` yourself as part of this skill. That's a separate,
explicit invocation — keeping "decide" and "do" as two distinct steps the user chooses to
take is the whole design.
