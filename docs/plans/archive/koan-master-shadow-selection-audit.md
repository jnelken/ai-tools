# Koan Master shadow selection audit

> **Status:** Implemented before 2026-11-14; archived by advance-roadmap. Evidence: PR #28 merged as 97f4193 (7bd9c15 continuous no-work bookkeeping fix) and outcome record 062156a; every thread is checked.

## Status

Complete. The repair is deployed and the audit-owned hold has been removed.

## Decisions

- Created `~/.claude/automations/advance-roadmap/session-hold.json` as `channel-loop-audit/20261114-114702` after confirming the loop. No other hold existed and no worker was active.
- The shadow scheduler remains observational. The active source of the loop was the separate `com.jake.advance-roadmap-quota` LaunchAgent.
- Continuous no-work results with new blockers must perform one bookkeeping pass immediately, then rely on the settled unchanged-verdict gate. They must not defer indefinitely to an unloaded finite batch.

## Findings (with evidence)

- `scheduler.py --status` reports `mode: shadow`; its latest koan-master decision is `wait`. Its LaunchAgent was idle with exit code 0.
- `com.jake.advance-roadmap-quota` is loaded on a one-minute interval with `ADVANCE_ROADMAP_CONTINUOUS=1`; the finite `com.jake.advance-roadmap` daily controller is not loaded.
- Runs `20261113-224506`, `20261114-004935`, and `20261114-045132` all have the same koan-master fingerprint (`0c8b55feeca0ffc2`), unchanged DEV-68/DEV-66 hashes, `changed: []`, and `blocked-no-item` outcomes. They used 92,157, 84,379, and 103,090 planner tokens respectively.
- Each continuous result was marked `bookkeeping_pending`; `verdict.py check` consequently forced re-triage. Because the finite controller is unloaded, its deferred DEV-68 blocker had no eventual worker. The live carried verdict is `batch-verdicts/koan-master.json` (stamp `20261114-045132`).
- Deployed source equals this worktree before the repair (`quota_scheduler.py` SHA-256 `aeabe…fc9155`), and the recent `d73ecb2` guard only cooled down already-skipped outcomes; it could not break the pending-bookkeeping retry cycle.

## Open threads

- [x] Verify the scheduler’s effective mode and live work state — shadow is inactive/observational; quota scheduler is the active controller; no worker or lock was present.
- [x] Compare the three run records and verdict inputs — identical inputs and outcome, with deferred bookkeeping as the trigger.
- [x] Determine whether deployed guards already address the repetition — they do not cover pending bookkeeping.
- [x] Complete regression checks, deploy the repair, remove this session’s hold, and monitor a true scheduler tick without manually sending messages — PR #28 merged as `97f4193`; `~/.ai-tools/install.sh` deployed it locally. The first post-release quota tick started typey.site and mailcrush continuations, not koan-master; koan-master has no new run record after `20261114-045132` while those workers occupy both slots.

## Approach

Settle the first continuous no-work bookkeeping pass instead of deferring it. The existing carried-verdict gate then suppresses unchanged planners; changed inputs still reopen triage.

## Outcome

Channel time range: 2026-11-13 22:45 through 2026-11-14 04:52 EST (plus the post-release observation at 12:00 EST). Evidence: three identical koan-master fingerprints and `blocked-no-item` verdicts; each continuous run deferred bookkeeping because the finite batch controller was unloaded. Confirmed incident: yes — `com.jake.advance-roadmap-quota`, not shadow mode, re-planned the same unchanged deferred verdict. Fix: continuous no-work results now run their one bookkeeping pass, so later unchanged ticks skip without another planner call. Regression coverage: focused replay test, the run-record end-to-end suite, shell syntax, and diff check passed. The hold owned by `channel-loop-audit/20261114-114702` was removed after deployment. No concrete blocker remains; two unrelated current workers hold the scheduler slots, and no koan-master re-selection occurred during observation.
