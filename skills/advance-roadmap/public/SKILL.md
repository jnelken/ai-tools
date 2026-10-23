---
name: advance-roadmap
description: Implement one scoped item from a software roadmap or plan in the available repository, verify its acceptance criteria, and report the result. Use for requests to advance a roadmap or build the next planned item.
---

# Advance Roadmap

Complete one actionable roadmap item using the user's repository and available coding
tools. This skill installs no scheduler, requires no particular agent CLI or issue
tracker, and grants no standing permission to publish, merge, or deploy.

## Select a bounded item

Read the repository's instructions, the named roadmap or plan, and relevant code. Use
`ROADMAP.md` or `docs/plans/` when the user requests the next item without naming a file.
If no source exists, ask for the intended work rather than inventing a roadmap.

Prefer the user's named item. Otherwise choose a small, unblocked item consistent with
the roadmap's ordering and dependencies. State the chosen outcome, acceptance criteria,
and relevant constraints before implementing. Surface unresolved product decisions;
the unblock-roadmap workflow can resolve them separately. Do not substitute speculative
code cleanup when no actionable item exists.

## Implement and verify

- Confirm the working directory and existing Git state. Preserve unrelated changes and
  use an isolated branch/worktree if repository conventions or concurrent work require it.
  Do not switch an occupied worktree's branch or overwrite another session's work.
- Build the smallest complete change satisfying the criteria. Follow established project
  patterns and update documentation made stale by the change.
- Run checks appropriate to the behavior changed. Report exact failures and distinguish
  observed pre-existing failures from regressions with evidence. Do not report checks as
  passed when they could not run.
- Review the net diff for scope, correctness, and accidental credentials or local artifacts.
  Use an independent reviewer when the user's workflow requires one and it is available;
  do not invent a review or silently claim the requirement was satisfied.

## Finish accurately

Mark the roadmap item complete only when its stated criteria hold. If it requires a
merged PR or successful deployment, a local implementation is still pending those steps.
Use repository and session authorization for commits, pushes, PRs, merges, and deployment;
the phrase "advance roadmap" alone does not establish permission for every external action.
Do not merge or deploy simply to clear a checkbox.

Report what changed, verification, the branch/PR when present, and remaining actions.
If blocked, record reproducible evidence and the smallest next step in the existing plan
or handoff source. Create external issues only when requested or otherwise authorized.
Stop after the selected item; continuing indefinitely requires an explicit request.

Without repository access, explain the missing capability and provide a bounded
implementation plan based on supplied material. Do not pretend code was changed.
