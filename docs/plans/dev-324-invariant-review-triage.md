# DEV-324 Invariant Review Triage

> **Status:** Final review is in progress; archive this plan in Step 6 after clean review and verification, before opening the PR.

## Status

The item remains active through final review. After a clean current-HEAD review and verification, Step 6 records the shipped commit and moves this document to `docs/plans/archive/` before the PR is opened.

## Decisions

- ai-tools owns maintained development instruction fragments under ADR 0001; dotfiles is outside this ticket's scope.
- Keep reviewer verdicts and routing unchanged. Classification happens during finding triage, before any fix is dispatched or applied.
- Instance findings keep the existing local-fix path. Invariant findings first require a side-effect path inventory and, when there are multiple paths, a proposed single owner. Escalate only when that ownership decision exceeds approved scope.

## Findings

- `skills/peer-review/SKILL.md` persists reviewer focus in the worktree Git directory and passes the same focus input through both Codex and Claude reviewer paths.
- `automations/advance-roadmap/worker.sh` constructs the scheduled worker's prompt; `skills/advance-roadmap/WORKER.md` owns its review-fix procedure.
- `skills/conductor/SKILL.md` owns the interactive conductor's review result triage before it dispatches the builder.
- The invariant example keeps the raw client unexported; both `api.postPayment` and `importRows` delegate writes through `ledger.append`. Its architecture guard rejects direct imports and requires across JavaScript and TypeScript source extensions outside the owner.

## Completed work

- Added instance-versus-invariant classification and the shared owner-and-enforcement convention to worker, conductor, peer-review and development guidance.
- Added documented examples for an unexported raw client, ESLint restrictions, an architecture guard, owner-policy tests, and delegation tests.
- Added isolated clean and violating fixtures, including nested and extensionless bypass cases.
- Verified worker/reviewer prompt delivery and development/runtime profile composition.
- Fixed reviewer findings on installed instruction paths and recursive guard coverage.
- Required checks passed on `edd74df`: invariant fixtures (15), review automation, worker goal-mode routing, and agent profile tests (20); later revisions were reverified as recorded in their run artifacts.
- Cross-provider review round 2 returned clean on `edd74df`.
- Follow-up review findings and fixes are retained in the run artifacts; clean current-HEAD review is still required before the plan is archived as shipped.

## Approach

1. Document the Invariants convention in `instructions/development/shared.md`.
2. Add instance-versus-invariant triage to conductor and advance-roadmap worker procedures, and inject the checklist into the scheduled worker prompt.
3. Extend peer-review's existing shared focus input so both reviewer paths receive the same checklist.
4. Add reusable architecture enforcement examples, fixtures, and regression checks.
5. Fix review findings, rerun verification, and complete shipping through a PR.
