# DEV-324 Invariant Review Triage

## Status

In progress. Unit 1 (done, committed) covers the shared invariant convention and
the end-to-end review triage prompts. Reusable enforcement examples and focused
fixtures remain for a later implementation unit.

## Decisions

- ai-tools owns maintained development instruction fragments under ADR 0001;
  dotfiles is outside this ticket's scope.
- Keep reviewer verdicts and routing unchanged. Classification happens during
  finding triage, before any fix is dispatched or applied.
- Instance findings keep the existing local-fix path. Invariant findings first
  require a side-effect path inventory and, when there are multiple paths, a
  proposed single owner. Escalate only when that ownership decision exceeds
  approved scope.

## Findings

- `skills/peer-review/SKILL.md` already persists reviewer focus in the worktree
  Git directory and passes the same focus input through both Codex and Claude
  reviewer paths. This is the maintained delivery point for peer-review guidance.
- `automations/advance-roadmap/worker.sh` constructs the scheduled worker's
  prompt; `skills/advance-roadmap/WORKER.md` owns its review-fix procedure.
- `skills/conductor/SKILL.md` owns the interactive conductor's review result
  triage before it dispatches the builder.

## Open threads

- [ ] Add reusable invariant enforcement examples and isolated fixtures (`skills/peer-review/templates/invariants/`; already referenced by the prompts).
- [x] Add regression checks for worker-prompt, reviewer-prompt and profile-composition delivery (test-worker-goal-mode.sh, test-review.sh, test_agent_profiles.py).
- [x] Run the assigned suites for this unit: test-review.sh, test-worker-goal-mode.sh, test_agent_profiles.py pass (run with the ADVANCE_ROADMAP_* env cleared).
- [ ] Complete independent review, PR, merge, and deploy in their reserved phases.

## Approach

1. Document the Invariants convention in `instructions/development/shared.md`.
2. Add instance-versus-invariant triage to conductor and advance-roadmap worker
   procedures, and inject the invariant checklist into the scheduled worker
   prompt.
3. Extend peer-review's existing shared focus input so both reviewer paths
   receive the same invariant checklist.
4. Commit this prompt-delivery milestone; leave template examples and fixtures
   for a subsequent implementation unit.
