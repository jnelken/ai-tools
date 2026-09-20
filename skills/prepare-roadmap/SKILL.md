---
name: prepare-roadmap
description: Interactive-only sweep across every personal repo under ~/Dropbox/code that is currently blocked from advance-roadmap — a dirty working tree, or open "decisions needed" questions — and asks Jake how to resolve each one, one repo at a time. Never touches a repo itself: it persists each answer immediately as a directive file that a later /advance-roadmap run reads and acts on. Use on-demand ("/prepare-roadmap", "unblock the roadmap repos", "clear the roadmap blockers", "sweep the blocked repos"). Complementary to [[advance-roadmap]], never a replacement — this skill only asks and records; that one is the only thing that ever writes code, commits, or pushes.
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
doesn't decide which roadmap item to build. Everything it produces is a plain-text
directive written **outside** every repo, for `/advance-roadmap` to carry out later.

This is deliberate, not a limitation to work around: `/advance-roadmap` is the one skill
hardened (allowlists, preflight, ff-only merges, all-or-nothing verification) to safely
mutate these repos and push `main`. Splitting "decide" from "do" means this skill can stay
simple and safe to run often, and the risky half stays confined to the skill already built
for it.

Corollary: **never write anything under a target repo's `.claude/`**, including
`.claude/IN_PROGRESS.md`. That file is owned by [[close-out]] and read by [[pick-up]]; a
third writer racing those two is exactly the kind of divergent-copy bug the ecosystem
here works hard to avoid. An answered "decisions needed" question is instead recorded in
this skill's own directive file, and `/advance-roadmap` folds it into `ROADMAP.md`'s prose
as part of the bookkeeping commit it already makes (its Step 6) — the same place Jake
himself resolves these by hand today (see `project_advance-roadmap-runs.md`'s
"mailcruxh suddenly qualified" case: he answered nine open decisions by editing the
roadmap item text directly, not `.claude/IN_PROGRESS.md`).

## Where directives live

One file per repo, named for it, in a directory next to `/advance-roadmap`'s own logs —
deliberately **outside every target repo** so writing one never makes a repo's tree dirty
and never needs a `.gitignore` negotiation:

```
/Users/jake/.claude/automations/advance-roadmap/directives/<repo>.md
/Users/jake/.claude/automations/advance-roadmap/directives/archive/<repo>-<stamp>.md   (consumed ones, kept for audit)
```

Create the `directives/` directory if it doesn't exist yet. Never write into any other
location, and never write inside the repo itself.

### Directive file format

```markdown
---
repo: mailcruxh
recorded_at: 2026-09-13T14:22:00-04:00
recorded_by: prepare-roadmap
---

## Dirty-tree resolution
<Omit this whole section if the repo's tree was already clean.>

Recorded `git status --porcelain` at the time Jake gave this instruction — advance-roadmap
scopes its authorization to exactly these paths, and must re-check live status against
this list before acting (see the staleness rule below):

```
 M src/foo.ts
 M src/bar.ts
?? src/newfile.ts
```

Jake's instruction, verbatim: <e.g. "Commit it all as one commit" / "Split into two:
(1) src/foo.ts and src/bar.ts as one commit about X, (2) src/newfile.ts as a second
commit about Y" / "Discard all of it" / free-form as he actually said it>

## Answered decisions
<Omit if none. One block per item that had an open "decisions needed" question.>

- **Item:** <the roadmap item name / heading, or the exact `.claude/IN_PROGRESS.md` line>
  **Question:** <the open question, as originally posed>
  **Answer:** <Jake's literal answer>
  **Carry into ROADMAP.md:** yes — fold this into the item's own text (a `**Decided:**`
  line and/or an `**Unattended-ready**` marker) as part of the Step 6 bookkeeping commit.

## Go-ahead
- [ ] Resolve the dirty-tree section above exactly as instructed (skip if none).
- [ ] Fold every answered decision above into `ROADMAP.md` (skip if none).
- [ ] Then: <"proceed through the normal Step 1/2 selection now that this repo qualifies"
  | "specifically implement: <item name>" | "just clean up — don't implement anything
  yet, wait for the next run">
```

**Never name a `human-only` item in the go-ahead.** That label marks work `/advance-roadmap` is
required to refuse — a GUI installer, a vendor sign-in, a purchase, a device in hand, a credential
only Jake holds — so a `specifically implement: <item>` line pointing at one hands that skill an
instruction it must ignore, and the repo sits blocked with nothing recording why. This skill never
queries Linear, so the label won't be in front of you; it's the one thing worth checking by hand
before writing that line. If Jake names such an item anyway, record it as a plain note under
*Answered decisions* and leave the go-ahead on the normal Step 1/2 selection.

The three checkboxes are `/advance-roadmap`'s own scratch space — it checks them off as it
completes each, so a crash mid-directive leaves a clear resume point (folded into its own
Step 0 interrupted-run reconciliation). Leave them unchecked when you write the file.

**Staleness rule, and it differs by instruction type** (this is the part most likely to
be gotten wrong — read it twice):

- A **commit-shaped instruction** ("commit it as one", "split it into A/B") only needs the
  paths it names to still exist and still be dirty at execution time. New, unrelated dirty
  paths that appeared since the sweep are left alone, untouched — they're not what this
  directive is about, and treating their mere presence as "drift" would silently block a
  directive forever the next time Jake made an unrelated edit in the same repo.
- A **destructive instruction** ("discard it") requires the live `git status --porcelain`
  to match the recorded snapshot **exactly** before proceeding. Any difference — Jake
  changed something since — means abort the directive, archive it, and let the next
  `/prepare-roadmap` sweep re-ask with current state. Never discard something the
  recorded snapshot didn't literally describe.

That split is `/advance-roadmap`'s job to enforce, not this skill's — but get the
directive's content right here, because a vague instruction ("clean it up") gives that
skill nothing safe to execute and it will (correctly) refuse and re-block the repo.

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

**Also check for a directive already waiting.** If
`/Users/jake/.claude/automations/advance-roadmap/directives/<repo>.md` already exists for
a candidate, `/advance-roadmap` hasn't consumed it yet — don't ask about that repo again
this sweep. Say so in passing (so Jake knows it's already queued) and move on. If he
explicitly wants to change a pending directive, read it back to him first and confirm
before overwriting.

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

### Asking the dirty-tree question

One `AskUserQuestion` call, two questions:

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

### Asking a decisions-needed question

One question per unresolved line (batch up to 4 per `AskUserQuestion` call when a repo
has several — they're independent). For the options:

- If the line's own text already names two or three concrete alternatives (many do — the
  Step 2b format asks "does X or Y?"), offer those as the options.
- Otherwise offer your single best-guess candidate answer as one option.
- Always include `Skip — leave this open for next sweep` as one of the options.
- The user can always pick **Other** to type a full free-text answer regardless of what
  options you listed — don't strain to make the presets perfect, they only need to cover
  the common case and leave an obvious escape hatch for everything else.

### Persist immediately — before moving to the next repo or the next question

The moment a repo's questions (or as many as Jake chose to answer — "Skip" is a valid,
complete answer) resolve, write or update that repo's directive file right then. Do not
batch writes until the end of the sweep. If Jake stops the session after repo 2 of 5, the
first two directive files must already be sitting there, complete and correct — that's
the entire point of the "doesn't need to be complete" requirement.

Concretely:
- If a directive file for this repo doesn't exist yet, create it with the frontmatter and
  whichever sections apply.
- If one already exists from earlier in *this same sweep* (e.g. you're adding a second
  answered decision to a repo you already wrote a dirty-tree resolution for), append to
  the existing sections rather than overwriting the file.
- A "Skip" answer writes nothing for that item — it simply isn't recorded, so the next
  sweep asks again naturally (there's no partial-answer state to track).

## Step 3 — Final summary

List, per repo: what was recorded (dirty-tree resolution / N answered decisions / both),
and what was skipped. Say plainly which repos now have a directive waiting in
`/Users/jake/.claude/automations/advance-roadmap/directives/` and that the next
`/advance-roadmap` run (scheduled, four times a day) will pick each one up automatically
— or that Jake can run `/advance-roadmap` right now if he wants one acted on immediately.

Do not offer to run `/advance-roadmap` yourself as part of this skill. That's a separate,
explicit invocation — keeping "decide" and "do" as two distinct steps the user chooses to
take is the whole design.
