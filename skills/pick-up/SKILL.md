---
name: pick-up
description: Resume where a previous session left off — read this repo's IN_PROGRESS.md in Plan mode and present a plan to finish the work it lists.
disable-model-invocation: true
---

# pick-up

The other end of [[close-out]]. Close-out writes what still needs a human into
`.claude/IN_PROGRESS.md`; pick-up reads it back in a fresh session, checks it against
the repo's current state, and turns it into a plan the user approves before anything
runs.

Nothing in this skill mutates state. It ends at an approved plan.

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

Leave the file alone. Pick-up doesn't check boxes or rewrite `IN_PROGRESS.md`;
close-out owns those edits at the end of the session.

## 4. Present the plan

`ExitPlanMode` with a plan that:

- Follows the file's own ordering — its "Next step" section is the previous session's
  considered judgment about what comes first, not an arbitrary list.
- Names the concrete first action, not a category of action.
- Surfaces every decision the file left to the human as an explicit choice in the plan,
  rather than quietly picking one. That's why those items were written down instead of
  executed.
- Lists what you found already done or stale, and why.
- Flags anything with real-world side effects (live account mutations, prod changes,
  outbound messages) before the step that causes it.

Completion criterion: every unchecked item in the file appears in the plan — as a step,
as a decision for the user, or as an explicit "already done / no longer applies" with
evidence. An item that exists in the file and nowhere in the plan is the failure mode
this skill exists to prevent.

## Notes

- Multiple repos can each carry their own `IN_PROGRESS.md`. This skill reads the current
  repo's. If the user wants a cross-repo sweep, ask which repos rather than guessing.
- The file is gitignored local session state. Never `git add` it, and never commit a
  change to it as part of picking up.
