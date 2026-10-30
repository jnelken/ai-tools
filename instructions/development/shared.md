## PR Descriptions

Default structure:

```markdown
## Summary
<One sentence: what problem exists (if applicable). One sentence: what the solution does. One sentence: impact or why it matters.>

## Preview
<label>: <deploy-preview URL of the changed screen, with any flag params>

## Changes
- High-level bullet describing a behavior or capability change
- High-level bullet describing a behavior or capability change

## Validation
- [ ] manual QA step
- [ ] manual QA step
```

Apply this when creating new PRs (`gh pr create`) and when editing existing ones (`gh pr edit`).

**Optimize every section for skimmability.** A reviewer should get the full picture in the time it takes to scroll past it — short lines, no filler words, no hedging. If a sentence can lose words without losing meaning, cut them.

**Summary** is 1–3 sentences max: problem → solution → impact. Omit whichever sentences don't apply (e.g. a pure refactor has no user-facing problem sentence). Keep it short enough that a reviewer can absorb it before opening the diff.

**Preview** applies only in repos with deploy previews (today woodrow); omit it elsewhere and when nothing is visible in a preview. Link the deepest screen that shows the change, not the app's front page, with `?<flag_key>=true` for any feature flag gating it; one `<label>: <url>` line per distinct screen; the bare host only for app-wide changes. The repo's PR guide owns the host and entity rules (woodrow: `docs/guides/pr-descriptions.md`; `pnpm run pr:create -- --preview "<label>: <path>"` fills the host in once the PR number exists).

**Changes** lists what changed at the behavior or capability level — not code mechanics. "Grouping state is now persisted per-view" not "moved grouping into useViewState hook." Omit this section for very small single-focus PRs where the Summary already says it all.

**No implementation specifics without a measurable impact.** Don't mention file names, file renames, function/variable names, line numbers, "refactored X into Y," or which layer/hook/module something moved to — a reviewer gets that from the diff itself. Only mention an implementation detail when it changes something a reviewer can observe or measure (latency, bundle size, query count, a new failure mode) — state the impact, not the mechanism. The one exception: a PR that is explicitly *about* a refactor or rename (the code change is the entire point) — there, name what moved.

**Validation lists manual QA checks only** — what a human should click through in the running app/system to confirm the change behaves as intended. Do not write "all tests pass," "ran lint," "typecheck succeeds," or list routine automated commands; CI already reports those.

**Describe the net diff vs the base branch, not the journey.** The reviewer only sees the final state — the diff between the base branch and `HEAD`. Do not reference scaffolding, false starts, or approaches that were tried and removed mid-branch; if it isn't on the base branch and isn't in `HEAD`, mentioning it just confuses the reviewer. When generating or refreshing a PR description, base it on `git diff <base>...HEAD` and the file-level changes that survive into `HEAD`, not on the per-commit log.



## Linear

**Personal DEV ticket policy (2026-10-22):** Create a ticket only when Jake explicitly
requests one, or when planning concrete future work that will not be done immediately on the
current branch. A PR does not need a DEV ticket. Never backfill a ticket merely to describe work
already underway or completed. Hygiene/refactor observations go through
`~/.ai-tools/bin/hygiene-log`, not into Linear, unless Jake explicitly requests tickets.
Work-environment CON PRs retain `create-pr`'s required ticket backfill.

**Completed personal tickets:** Preserve their requirements, decisions and comments on confirmed
merged PRs with `~/.ai-tools/bin/linear-pr-archive --issue DEV-N --directory <private-export-dir> --apply`. Retain original planning tickets. Delete legacy `create-pr` backfills only when their
creation provenance is verified, recorded as `backfill_evidence` in the reviewed plan, and every
archive comment has been read back successfully (`--use-plan --apply --delete`). Keep sources
without a merged PR or with documents/assets that have not been exported. Never infer backfill
provenance merely from a ticket's age, Done status, or missing PR.

**Any time you create or update a Linear issue, invoke the `linear-ticket-gen` skill first** — never call the Linear MCP tools or GraphQL directly. Concentro (`CON`) issues need exactly one surface label (plus engine labels only when the engine is the ticket's subject). `jnelken`-workspace issues need a `repo/*` label and go through the `jnelken-linear` skill. Details: `~/.claude/instructions/linear.md`.


## Session Retrospectives and Gotcha Docs

At the end of a session, three standing follow-ups apply. Details (triggers, tiers, destinations): `~/.claude/instructions/retrospectives.md`.

- **Datadog Follow-up** — if Datadog tools were used and something slowed you down (see Datadog above).
- **Tooling Follow-up** — if the session hit friction with the tooling around the work, or the work removed/replaced a mechanism. Report-only; name `/session-hardening` as the way to act on it.
- **Gotcha docs** — if investigating one subsystem surfaced several related non-obvious gotchas written down nowhere, commit them to a reference doc without waiting to be asked.

**Self-started retros report; they never delete.** A retro that fires on its own may only produce additive, reversible output — a note, a ticket, a doc, a memory. Removing anything is licensed by explicit invocation only. That's what makes a standing trigger safe to get wrong: the cost of a false positive is a paragraph.

**Exception: fix stale documentation and Linear text on the spot.** When a session's own work makes a doc, README, repo instruction file, or Linear ticket description say something that is no longer true (a "blocked on X" that X's fix resolved, a note about a grant that has since landed), correct the text in the same session, without waiting for `/session-hardening` or asking. Rewriting stale prose to match verified current state counts as a fix, not a deletion. The exception doesn't cover code, config, or Linear state/assignee/cycle changes, which keep the normal tiers, or changing a ticket's status you didn't set. Report what you corrected.


## Committing Work

Commit once per completed idea, not once per step. Hold off creating a commit until the idea being worked on is actually done, then make a single commit for it. If follow-up tweaks or fixes to that same idea come in afterward (before moving on to a new idea) and the commit hasn't been pushed/shared yet, absorb them into that commit via `git commit --amend` rather than stacking a separate commit — this takes precedence over the default "always create new commits" behavior for this in-progress, not-yet-shared case. Once a commit has been pushed or the branch has an open PR, stop amending and fall back to new commits (or the peer-review/push workflow) instead, since rewriting shared history needs a force-push and explicit confirmation.

When a commit only goes through with `--no-verify` because of lint errors already in files you touched, clear them in a follow-up commit on the same PR. Details: `~/.claude/instructions/lint-no-verify.md`.

**"ridealong" marks an intentional, user-requested extra change.** A commit whose message contains the word `ridealong` (or `ride-along`) on a branch/PR is deliberate: don't flag it as stray or unrelated, don't ask whether to drop it, and treat it as in scope (reviewed with the PR, mentioned in the PR description). When you write a ridealong commit — a change the user asked to bundle into a branch whose main task is something else — put the word `ridealong` in the commit message.


## Plan Files

### Running planning docs

- **Open it early.** After orientation, any planning or investigation session opens a running doc: `docs/plans/<slug>.md` in the target repo, or the harness plan file in plan mode, copied to `docs/plans/` on exit.
- **Use these sections:** Status, Decisions, Findings (with evidence), Open threads (checkbox list), Approach.
- **Keep it current:** update it after each finding or agent result.
- **Close threads visibly:** answer and tick with an annotation, or move the thread to a ticket or `IN_PROGRESS.md` and note where. Never delete one silently.
- **Commit:** in personal repos, commit and push it under the existing commit rules.

## Golden Paths

Match process to task size: small task → one prompt; unfamiliar area → explore, plan, build; feature → context, plan, split, parallelize; refactor → success criteria, TDD plan, execute, review; hard bug → debug, one validated fix at a time. Keep thinking and doing in separate contexts. Details: `~/.claude/instructions/golden-paths.md`.


## Refactoring vs Patching

When one feature's fix cycle keeps producing new edge cases faster than it converges, consider consolidating the mechanism instead of layering guards — weighed against the feature's stakes. Details: `~/.claude/instructions/refactoring-vs-patching.md`.

