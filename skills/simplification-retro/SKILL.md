---
name: simplification-retro
description: Sweep finished work for the complexity it made unnecessary — dead scaffolding, compensating mechanisms, redundant paths, orphaned guards, per-instance patches with a root-cause fix available, and indexes the work staled. Covers everything the work touched, not just code: config, shell functions, git refs and worktrees, generated manifests, instruction files and agent memory. Applies only provably-dead, trivially-reversible, non-invocable findings on its own; everything else is a numbered offer. Trigger phrases include "simplification retro", "what did this make unnecessary", "what can we delete now", "sweep for leftovers", "retro this work", "clean up after this change".
---

# Simplification Retrospective

You are sweeping **finished** work for the complexity it rendered unnecessary.

The question is **"what does this work now make deletable?"** — not "is this code clean?" That distinction is the whole reason this skill exists, and it is what keeps it from degenerating into a worse `/simplify`. You are looking *away* from the diff, at what the change orphaned elsewhere.

A member of the retrospective class — see [`docs/retrospectives.md`](../../docs/retrospectives.md) for the field schema and the shared action tiers. This skill's fields:

- **Trigger** — work completed: a merged or review-ready PR, a finished task, a named branch or date range.
- **Corpus** — everything the work touched, plus what it orphaned elsewhere.
- **Detection** — the six residue kinds below, each with a recognition test and an evidence bar.
- **Action** — tiered per finding.
- **Destination** — applied Tier 1 changes, a numbered offer list for Tiers 2–3, and an appended `memory.md` entry.
- **Skip when** — the work was a one-file change that added nothing and retired nothing; or the sweep was already run against this same work.

## Invocation

```
/simplification-retro [<branch|PR number|"this session">]
```

No argument: the current branch versus its base.

## When NOT to use

- The question is **"is this new code clean?"** — that's `/simplify` (reuse, simplification, efficiency, altitude over the diff; applies fixes directly) or [[tighten]] (TS structural and type-level wins, one high-confidence finding at a time). Both look *at* the diff. This skill looks at what the diff made removable elsewhere. If someone asks you to clean up a component's props, hand them those two and stop.
- The question is **"what did this session leave hanging?"** — that's [[close-out]]: unfiled findings, stray processes, unpushed worktrees, un-announced PRs. Overlapping surface, opposite question. close-out asks what's *unfinished*; this asks what's *surplus*.
- The user just wants worktrees or branches tidied for their own sake — that's [[clean-sswts]] and [[cleanup-local-branches]]. This skill reports those **only** when the completed work is what made them dead.
- The work was trivial — a typo, a one-line copy change, a version bump. Say so and skip. A six-kind sweep over a two-line diff is noise.

## Scope

Establish what "the work" was first, then sweep these:

| Surface | What to look at |
|---|---|
| Code | `git diff <base>...HEAD`, plus call sites of anything it deleted or replaced |
| Config | `.gitconfig`, `.npmrc`, `tsconfig`, CI workflow files, `.superset/config.json`, env samples |
| Shell & dotfiles | aliases, functions, `~/dotfiles/zsh/lib/*.zsh`, `bin/` scripts |
| Git state | local and remote branches, worktrees, tracking refs, stale tags |
| Generated artifacts | manifests, lockfiles, generated clients, flag usage reports |
| Instructions | `CLAUDE.md`, `AGENTS.md`, skills, commands, hook scripts |
| Indexes | `README` tables, `MEMORY.md`, registry docs, docs that enumerate paths |
| Agent memory | memory files describing a mechanism this work changed or removed |

## Detection — the six residue kinds

Each needs **evidence**, not suspicion. Name the search that proved it. A finding you can only phrase as "might not be needed" is not a finding — drop it.

### 1. Dead scaffolding
Artifacts of a workflow the work retired.
**Test:** does anything still reference it, and did the work remove its last reason to exist?
**Example:** the `w2oodrow`/`w3oodrow` worktrees and their `main2`/`main3` branches — parallel checkouts from a workflow nobody ran any more, holding zero unique commits.

### 2. Compensating mechanism
Something built to work around a cause that is now fixed.
**Test:** find the compensation's original justification. Is that condition still reachable?
**Caution:** a root-cause fix often applies only *going forward*. Check whether existing instances were repaired too — see Common mistakes.

### 3. Redundant path
Two or more mechanisms doing one job, with at most one in real use.
**Test:** enumerate every entry point for the job. Count actual uses of each.
**Example:** after `gpstack` was deleted, `~/.gitconfig`'s `push-stack` alias and `/create-pr base=` both still covered stacked PRs — one job, two survivors.

### 4. Orphaned guard
A conditional, retry, fallback, or validation whose precondition can no longer occur.
**Test:** what input reaches this branch? Can the work's change still produce it?
**Highest false-positive rate of the six.** A guard that is merely *rarely* hit is not orphaned. Demand proof the condition is unreachable, not just unlikely.

### 5. Per-instance patch with a root-cause fix available
The same fix applied N times where one upstream change covers all future cases.
**Test:** count the instances. Is there a single setting, type, helper, or lint rule that makes them unnecessary?
**Example:** `git branch --set-upstream-to` applied per branch, where `branch.autoSetupMerge = simple` covers every branch created from then on.

### 6. Stale index or superseded doc
A README table, `MEMORY.md` line, registry, or instruction describing something that no longer exists.
**In corpus only when the completed work is what staled it.** A doc that was already wrong beforehand belongs to a plain docs pass or [[close-out]], not here. Including it lets the sweep look productive while measuring nothing.

## Steps

### 1. Establish the work
Get the diff (`git diff <base>...HEAD --name-only`), the PR body if there is one, and what the change actually replaced or removed. Write down in one sentence what capability the work retired — that sentence drives kinds 1, 2 and 3.

### 2. Read `memory.md`
Prior runs tell you whether a finding is new, persisted, or resolved. Don't present a known-and-declined finding as fresh.

### 3. Run each recognition test
Six kinds, in order. Record the exact command or search that produced each finding — it goes in the report as evidence and it is what determines the tier.

### 4. Tier every finding
Per the table in [`docs/retrospectives.md`](../../docs/retrospectives.md). Incomplete evidence moves a finding **up** a tier, never down. Anything with a human-invocable surface — alias, function, command, skill — is Tier 2 at best regardless of reference count.

### 5. Apply Tier 1, then report
Make the Tier 1 changes and say what you did. Record a recovery handle for each (SHA, file path, the command that restores it).

### 6. Offer Tiers 2 and 3
Numbered, with the house prompt. Wait. A report is not approval.

### 7. Append to `memory.md`
One dated entry. Never rewrite prior entries.

## Output format

```
## Simplification retro — <work identifier>

Retired by this work: <one sentence>

### Findings

| # | Kind | What | Evidence | Tier |
|---|---|---|---|---|
| 1 | Redundant path | `push-stack` alias in ~/.gitconfig | sole survivor after gpstack removal; 0 uses in 6mo of shell history | 2 |

### Applied (Tier 1)
- <what changed> — restore with `<command>`

### Working correctly — do not change
- <load-bearing things that look like residue but aren't, and why>

### Awaiting your call
1. <Tier 2 finding> — <proposed action>
2. <Tier 3 finding> — <remediation, and why this skill won't do it>

Enter numbers (e.g. `1 4`), a category name, `all`, or `none`.
```

The **"working correctly — do not change"** section is not optional. It is what stops a cleanup tool from eating load-bearing things on its next run, and it is the only place a deliberate non-finding gets recorded.

## Common mistakes

- **Treating a forward-only root-cause fix as retroactive.** `branch.autoSetupMerge = simple` stops *new* branches from inheriting the wrong upstream; it repairs nothing that already has one. Deleting a guard because "the root cause is fixed now" strands every instance that predates the fix. Check the existing population before removing the compensation.
- **Calling a config default Tier 1 because the edit is one line.** Size of diff is not reversibility. A shared config or instruction file changes behavior for every future session — Tier 2.
- **Trusting an empty process search.** `pgrep`/`lsof` run from a Bash tool call match that call's own subshell, and the PIDs vanish a moment later. Confirm with `ps -p <pid>` before calling something dead.
- **Removing a "redundant" path without checking the survivor covers every case.** Two mechanisms usually differ in one edge case that's the reason both exist. Find that case first; if you can't find it, the finding is Tier 2 with the uncertainty stated.
- **Padding with stale-index findings that predate the work.** Out of corpus. It makes a sweep look productive while measuring nothing.
- **Reporting suspicion as a finding.** "This might not be needed" is not evidence. Either produce the search that proves it dead or leave it out.

## Why this exists

A 2026-09-18 session set out to fix one missing PR badge in Superset. The root cause was a single mis-set git tracking ref — but chasing it turned up a shell function that would have force-pushed a feature branch onto the trunk, three overlapping mechanisms for stacked PRs, two abandoned worktrees with their branches, and a stale project row in a legacy database. None of it was the task, none of it was findable by the existing code-quality tools, and all of it was found by accident.

This skill exists so that sweep is deliberate rather than lucky.
