# Accurate roadmap recovery notices

## Status

Implemented and verified. The owner explicitly approved delivery; deployment uses the normal push and installer path.

## Decisions

- Preserve existing error alert thresholds and skip/quota behavior.
- Track outstanding failures by repository and ticket, branch or item identity.
- Only observed worker exit 0 with shipped work confirms recovery of that same identity.
- Report recovery per item and keep other outstanding failures visible.

## Findings

- A global streak reset allowed an unrelated blocked-no-item planner to announce recovery.
- Outstanding failures must survive parallel no-op results and quota pauses.

## Open threads

- [x] 25 unit tests and the shell integration suite pass; a read-only replay of the actual incident produces no false recovery.
- [x] Delivery authorized explicitly, including standing push/deploy permission for repositories under `~/Dropbox/code`. The release uses the normal push and installer path.

## Approach

Use structured run records; no model calls, logs parsing or invented recovery evidence.
