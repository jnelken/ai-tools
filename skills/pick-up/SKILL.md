---
name: pick-up
description: Resume where a previous session left off — read this repo's IN_PROGRESS.md in Plan mode, present a plan to finish the work it lists, and reconcile the file with anything found already done or stale.
---

# pick-up

The other end of [[close-out]]. Close-out writes what still needs a human into
`.claude/IN_PROGRESS.md`; pick-up reads it back in a fresh session, checks it against
the repo's current state, and turns it into a plan the user approves before anything
runs.

The only thing this skill writes is the handoff file itself, and only to reconcile it
with what it finds (step 5). It touches no other state before the plan is approved.

## 1. Enter Plan mode first

Before reading anything, load and call `EnterPlanMode`:

```
ToolSearch("select:EnterPlanMode,ExitPlanMode")
```

Then call `EnterPlanMode`. Skip the call if the session is already in plan mode (e.g.
launched with `--permission-mode plan`) — calling it again is harmless but noise.

Planning starts before the read so that everything discovered in step 2 lands inside a
mode that can't act on it by reflex. A picked-up item is often mid-flight work with a
live blast radius; the user approves the resumption, not this skill.

## 2. Read the handoff file

Look in this order, take the first that exists:

1. `.claude/IN_PROGRESS.md` — close-out's convention, the normal case.
2. `IN_PROGRESS.md` at the repo root.

If neither exists, say so plainly and stop. Do **not** substitute `NEXT-STEPS.md` —
that's `wrapup-repos`' snapshot of one run, with different ownership and update
semantics, and close-out documents merging the two as an anti-pattern. Do not
reconstruct a to-do list from `git log` either. No file means no handoff; offer to
survey the repo instead, and let the user decide.

Read the whole file, including its context section — not just the checkboxes. Then
**follow every pointer it makes**: a named commit, a source file's header comment, a
`CLAUDE.md` gotcha list. Those pointers exist because the previous session paid for
that knowledge; a plan that skips them replans a solved problem, and in repos with
hard-won DOM/API quirks it re-walks a dead end the file explicitly warns about.

## 3. Verify each open item against current state

The file is a snapshot from a previous session and items go stale — someone may have
finished, abandoned, or invalidated one since it was written. For each unchecked item,
confirm cheaply that it's still real: does the file it names still exist, does
`git log` since the file's `_Last updated:_` date already contain the work, is the
stated blocker still a blocker.

Classify every item as **still open**, **already done**, or **stale/invalid**, with the
evidence for anything not still open. Don't silently drop an item — an item you
believe is done is a claim the user gets to see and correct.

Note the exact edits this implies for the file, but don't make them yet — plan mode
permits writing only the plan file, and a verdict the user is about to correct must not
already be on disk. Step 5 applies them once the plan is approved.

## 4. Present the plan

`ExitPlanMode` with a plan that:

- Follows the file's own ordering — its "Next step" section is the previous session's
  considered judgment about what comes first, not an arbitrary list.
- Names the concrete first action, not a category of action.
- Surfaces every decision the file left to the human as an explicit choice in the plan,
  rather than quietly picking one. That's why those items were written down instead of
  executed.
- Lists what you found already done or stale, and why.
- States the edits step 5 will make to the handoff file — which items get checked off,
  struck, or annotated. The user is approving those edits too, and this is their one
  chance to say "no, that isn't done" before a wrong verdict is written down.
- Flags anything with real-world side effects (live account mutations, prod changes,
  outbound messages) before the step that causes it.

Completion criterion: every unchecked item in the file appears in the plan — as a step,
as a decision for the user, or as an explicit "already done / no longer applies" with
evidence. An item that exists in the file and nowhere in the plan is the failure mode
this skill exists to prevent.

## 5. Reconcile the handoff file

If step 3 found nothing stale, skip this — rewriting a file that was already accurate is
churn, and it costs the `_Last updated:_` date its meaning.

Otherwise, once the plan is approved, edit the handoff file **before starting the work**.
A session that dies mid-task should still leave the file more accurate than it found it,
and that only holds if the reconciliation is the first thing that lands. Apply the
verdicts the user approved, not the ones you arrived at — if they corrected one, theirs
is the one that goes in the file.

- Check off items verified done, each with a compressed note of the evidence
  (`- [x] … — landed in a1b2c3d`). That evidence is what stops a later session
  re-verifying the same thing.
- Mark stale or invalidated items as such with a one-line why, rather than deleting them
  silently. A dropped item reads as an item nobody ever wrote down.
- Rewrite prose that a verdict falsified. A context section asserting something that is
  no longer true is worse than a stale checkbox, because nothing signals it as stale —
  if the file says "we decided not to build X" and X shipped, that sentence has to go.
- Leave still-open items, and the reasoning behind them, exactly as they are.
- Update `_Last updated:_`, and append a `_Picked up:_` line with the date and session id
  so the provenance of the edit is visible next to close-out's own record.

Then start the work. What this skill owns is reconciling the file with reality;
[[close-out]] still owns adding new open items and decisions at the end of a session.

## Notes

- Multiple repos can each carry their own `IN_PROGRESS.md`. This skill reads the current
  repo's. If the user wants a cross-repo sweep, ask which repos rather than guessing.
- The file is gitignored local session state. Step 5 edits it on disk and nothing more —
  never `git add` it, and never commit a change to it as part of picking up.
- Step 5 only ever *reconciles*: it records what is already true. It never marks an item
  done because the plan intends to do it, and never adds items — that's close-out's job.
  If picking up reveals new work, it belongs in the plan, not appended to the file.
