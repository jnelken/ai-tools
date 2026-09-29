---
name: session-hardening
description: Session retrospective (not monthly-retro). Turn friction and gaps hit while working in a session into concrete fixes that make the next session faster — a wrong or missing doc, skill or instruction, a hook or permission that misfired, flaky or slow tooling, a manual step done twice, an assumption traced to stale memory, or a mechanism the work just made dead. General counterpart to the Datadog Debugging Follow-ups (which owns observability gaps). Report-only when self-started by the CLAUDE.md trigger; when invoked by name it also applies provably safe, trivially reversible fixes, with everything else a numbered offer. Trigger phrases include "session hardening", "harden this", "what slowed us down", "what would have made this easier", "session follow-ups", "what can we delete now", "retro this session".
---

# Session Hardening

You are turning **friction from this session** into fixes, so the next session doesn't hit it.

The question is **"what got in the way, and what change stops it recurring?"** — not "is this code clean?" You are looking at the tooling *around* the work: docs, skills, hooks, permissions, config, scripts, memory, instruction files. The Datadog follow-up asks this for observability; this skill asks it for everything else.

A member of the session-retrospective class — see [`docs/session-retrospectives.md`](../../docs/session-retrospectives.md) for the field schema, the action tiers and the self-start rules. This skill's fields:

- **Trigger** — the session hit tooling friction or a gap (see kinds below), or its work removed or replaced a mechanism.
- **Corpus** — the friction moments in this session: failed or retried tool calls, corrections from Jake, assumptions that turned out wrong, steps done by hand more than once. For a removed mechanism, also what it orphaned elsewhere.
- **Detection** — the gap kinds below, each with a recognition test and an evidence bar: the concrete moment in the session that hit it.
- **Action** — report-only when self-started; tiered per finding when invoked by name.
- **Destination** — a findings note and an appended `memory.md` entry always; applied Tier 1 changes and a numbered Tier 2–3 offer list on invocation only; one bundled Linear ticket for gaps not fixable in-session.
- **Skip when** — no friction was hit, the session was trivial (a typo, a version bump), the only gaps are Datadog/observability ones (the Datadog follow-up owns those), or the sweep already ran against this session.

## Invocation

```
/session-hardening [<branch|PR number|"this session">]
```

No argument: this session.

**Invoked vs self-started.** The `## Session Retrospectives` section of `~/.claude/CLAUDE.md` self-starts the *note* — after a session that hit tooling friction, the gaps get written up without anyone asking. That self-started note is **report-only**: it never applies a change, because editing shared tooling is licensed by explicit invocation, not by a standing trigger. Reaching this skill by name unlocks steps 5–7. If you arrived here from the CLAUDE.md trigger rather than a `/session-hardening` call, run steps 1–4, emit the findings, and stop.

## When NOT to use

- **"Is this new code clean?"** — `/simplify` (over the diff, applies fixes) or [[tighten]] (TS structural wins). They look *at* the diff.
- **"What did this session leave hanging?"** — [[close-out]]: unfiled findings, stray processes, unpushed worktrees. close-out asks what's *unfinished*; this asks what *slowed the work down*.
- **Datadog / observability gaps** — the Datadog Debugging Follow-ups section of `~/.claude/CLAUDE.md` owns them, including its `telemetry`-labelled ticket. Hand them there; don't duplicate.
- **Tidying worktrees or branches for its own sake** — [[clean-sswts]], [[cleanup-local-branches]]. Report those here only when the session's work is what made them dead.
- **Trivial sessions.** Say so and skip. A sweep over a two-line change is noise.

## Detection — the gap kinds

Each needs **evidence**: name the moment in the session (the failed command, the correction, the wrong assumption) and the search or read that traced it to a cause. "Might be useful someday" is not a finding — drop it.

### 1. Wrong, stale or missing knowledge
A doc, skill, `CLAUDE.md` rule, README or memory note that was wrong, out of date or absent when the session needed it.
**Test:** did the session act on it, get burned, and have to correct course? What single edit would have prevented that?
**Stale text is fixed on the spot** per the `CLAUDE.md` exception — that is not a Tier 2 offer.

### 2. Misfiring automation
A hook, permission prompt, allowlist gap, launcher or scheduled job that blocked, nagged, or did the wrong thing.
**Test:** reproduce it or quote its output. What condition tripped it, and is the fix a config change, a script fix or an allowlist entry?

### 3. Repeated manual work
A sequence done by hand two or more times in the session (or across recent sessions) that a skill, alias, script or shell function would cover.
**Test:** count the repetitions. Does a tool already do it, unused or unknown? Then the gap is discoverability (a skill description, a doc pointer), not a new tool.

### 4. Fragile or slow tooling
A tool that failed intermittently, needed retries, or cost disproportionate time or context.
**Test:** what was the failure signature? Is there a durable fix (pin, timeout, cache, better error), or only a workaround worth documenting?

### 5. Misleading names or surfaces
A skill, command, flag or file whose name or description sent the session the wrong way.
**Test:** which trigger phrase or path led astray, and where did the session end up instead?

### 6. Newly dead mechanism
Something the session's work made unnecessary: dead scaffolding, a compensating mechanism whose cause is fixed, a redundant path, an orphaned guard, a per-instance patch with a root-cause fix now available, or an index the work staled.
**Test:** find the original justification (or the second path's job) and prove it no longer applies. Search for remaining references.
**Highest false-positive rate.** A guard that is rarely hit is not orphaned; a root-cause fix that only applies going forward does not repair existing instances — check the existing population first. Stale-index findings only count when *this work* staled them.

## Steps

### 1. Establish the session
List what the session did and where it stalled: retries, corrections from Jake, tool errors, "actually, that's wrong" moments. For branch work, also `git diff <base>...HEAD --name-only` and what the change replaced or removed.

### 2. Read `memory.md`
Prior runs tell you whether a finding is new, persisted or resolved. Don't present a known-and-declined finding as fresh.

### 3. Run each recognition test
Six kinds. Record the exact command, quote or search behind each finding — it goes in the report as evidence and determines the tier.

### 4. Tier every finding
Per the table in [`docs/session-retrospectives.md`](../../docs/session-retrospectives.md). Incomplete evidence moves a finding **up** a tier, never down. Anything with a human-invocable surface — alias, function, command, skill — is Tier 2 at best, and shared config or instruction files are Tier 2 regardless of diff size.

### 5. Apply Tier 1, then report
**Invoked runs only.** Make the Tier 1 changes and say what you did, with a recovery handle for each (SHA, path, restore command).

### 6. Offer Tiers 2 and 3
Numbered, with the house prompt. Wait. A report is not approval. File one bundled Linear ticket (via [[linear-ticket-gen]], duplicate check first) for accepted gaps that can't be fixed in-session.

### 7. Append to `memory.md`
One dated entry. Never rewrite prior entries. The log is local per machine and gitignored — do not commit it.

## Output format

```
## Session hardening — <session or work identifier>

Friction hit: <one sentence>

### Findings

| # | Kind | Moment | Fix | Evidence | Tier |
|---|---|---|---|---|---|
| 1 | Wrong knowledge | tried `foo --bar`, got "unknown flag" | correct the flag in skills/x/SKILL.md | flag removed in v2 (changelog) | 1 |

### Applied (Tier 1)
- <what changed> — restore with `<command>`

### Working correctly — do not change
- <things that looked like friction or residue but are load-bearing, and why>

### Awaiting your call
1. <Tier 2 finding> — <proposed action>
2. <Tier 3 finding> — <remediation, and why this skill won't do it>

Enter numbers (e.g. `1 4`), a category name, `all`, or `none`.
```

The **"working correctly — do not change"** section is not optional. It stops a hardening pass from eating load-bearing things next time and is the only place a deliberate non-finding gets recorded.

## Common mistakes

- **Reporting suspicion as a finding.** Cite the moment and the proof, or leave it out.
- **Treating a forward-only fix as retroactive.** `branch.autoSetupMerge = simple` stops *new* branches inheriting a wrong upstream; it repairs none that already have one. Check the existing population before removing a compensation.
- **Calling a config default Tier 1 because the edit is one line.** Diff size is not reversibility; shared config or instructions change every future session.
- **Trusting an empty process search.** `pgrep`/`lsof` from a Bash call match that call's own subshell. Confirm with `ps -p <pid>` before calling something dead.
- **Removing a "redundant" path without finding the edge case that justifies both.** If you can't find it, the finding is Tier 2 with the uncertainty stated.
- **Turning one bad moment into a new mechanism.** A single hiccup earns a doc line or a fix, not a new hook or skill. Prefer the most drift-resistant home: encode it in code, then a code comment, then an existing doc, then memory.
- **Padding.** No friction means no findings. Say so.

## Why this exists

A 2026-09-18 session set out to fix one missing PR badge in Superset. The root cause was a single mis-set git tracking ref — but chasing it turned up a shell function that would have force-pushed a feature branch onto the trunk, three overlapping mechanisms for stacked PRs, two abandoned worktrees, and a stale project row in a legacy database. None of it was the task, and all of it was found by accident. It began as `simplification-retro`, which swept only for what work made deletable.

On 2026-09-29 it was rescoped and renamed: the Datadog follow-up already turns observability gaps into fixes after an investigation, and the same habit applies to every other kind of tooling friction. Newly-dead mechanisms are now one gap kind among six, not the whole skill.
