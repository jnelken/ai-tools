# No-item reporting

## Status
Reconciled with newer main: preserve its static redacted webhook fallback. The separate Slackagent checkout is unavailable on this machine, so its rendering was not exercised.

## Decisions
- Preserve the existing outcome token for consumers; add a scoped backlog assessment to the run record.

## Findings
- The planner returns a no-work action and reasons; the bookkeeping worker's summary can override those reasons in the recorded Slack summary.
- The Nov 18 investigation found excluded DEV-276 and DEV-227's unshipped DEV-223 prerequisite despite unfinished tickets remaining.

## Open threads
- [x] Preserve the snapshot-based assessment and planner reasons in the run record for Slackagent; retain the static webhook fallback's redaction boundary.
- [x] Verify complete, incomplete, empty, and unavailable snapshots and the second-thread path with focused tests. The existing batch suite requires Unix `fcntl` and cannot run on Windows.

## Approach
Use the run's Linear snapshot only for scoped, explicitly qualified counts. Never infer an empty backlog from missing or truncated data.
