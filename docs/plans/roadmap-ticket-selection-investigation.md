# Roadmap ticket selection investigation

## Status
Complete. Continuous selection fetches a fresh Linear snapshot and reassesses ranked candidates; routine per-repo no-work messages are suppressed.

## Decisions
- Continue interrupted assignments first. For a fresh continuous assignment, reassess the scheduler's current ranked Linear candidates; a prior no-work verdict is evidence, not an authority to reject them unchanged.
- A per-repo no-work result does not establish that Linear is empty. Quiet routine no-work results; retain actionable blockers and errors.

## Findings
- The 2026-11-16 11:04 mailcrush run received 19 candidates from a successful Linear snapshot, but the planner carried an old no-work verdict. The 11:06 Slack message lost the planner's reasons to a synthetic worker summary.
- Its reasons incorrectly described DEV-304 as needing a new assignment; the ticket's blocker instead requests resuming review when available. It called DEV-28 blocked by DEV-33, which was Done, although DEV-28 separately requires attended acceptance.

## Open threads
- [x] Fresh candidate triage bypasses the carried verdict and rechecks selected tickets; saved assignments retain priority, including when a fresh Linear snapshot fails.
- [x] Planner reasons survive no-worker bookkeeping; routine no-item alerts are suppressed while structured blockers, input requests and errors remain visible with an accurate title.
- [x] Covered scheduler refresh and failure, candidate reassessment, notification policy, and records with focused tests. The scheduler continues to try other repositories after per-repo no-work.

## Approach
Keep the existing scheduler's continuation priority and candidate ranking. Narrow the verdict bypass to runs with explicitly selected tickets, keep repository safeguards, and check notification and run-record behavior with focused tests.

The scheduler does not currently issue a global empty-Linear notification; it stays quiet rather than declaring the queue empty without proof. A newly discovered interrupted branch without a saved assignment remains subject to the scheduler candidate gate; do not bypass that gate without trusted recovery evidence.
