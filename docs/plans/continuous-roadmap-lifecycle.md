# Continuous roadmap lifecycle

## Status
Implementation and isolated validation complete; live observation follows deployment.

## Decisions
- Continuous runs continue through normal milestones; quota, completion, real blockers, or lack of progress can end an invocation.
- Cross-provider review remains mandatory. A reviewer is gated by live availability rather than a reserved review count.
- The stream supervisor owns quota and stalled-process stops. Existing non-continuous batch behavior remains intact.

## Findings
- Continuous runs inherit a bounded-milestone prompt and a one-review claim even though capacity reservations are disabled.
- The stream watchdog currently stops at a fixed elapsed duration rather than lack of activity.
- No-item yield backoff keys to checkout and branch churn, which can cause repeated planning without relevant issue changes.

## Open threads
- [x] Verified provider admission, repeated cross-provider reviews, and turn-boundary quota behavior in isolated tests.
- [x] No-item planning is held on scoped inputs and backoff; routine checkpoints are quiet, while quota and input outcomes are labelled.
- [ ] Deploy and observe a live run when provider capacity permits.

## Approach
Update the existing continuous-mode paths and exercise them with isolated tests before resuming live scheduling.

- No-item bookkeeping is retained in its run artifacts and marked pending, rather than silently claiming it was completed.
- Continuous watchdogs use output inactivity; the independent guard still reaps orphaned workers.

## Follow-up: unchanged-verdict loop

- [x] The expired no-assignment backoff took precedence over a later `skipped-unchanged` verdict, repeatedly selecting apt-sqft and brew-radar. A newer unchanged verdict now wins until inputs change or the 72-hour audit is due.
- [x] An unchanged-only batch receives a 15-minute safety cooldown, and unchanged skips do not generate Slack messages.
- [x] Replayed the live scheduler database in an isolated copy: both affected repositories now wait. Tests cover the expiry, retry, and input-change paths.
- [ ] Deploy and monitor the resumed scheduler.
