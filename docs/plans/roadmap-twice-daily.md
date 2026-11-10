# Twice-daily roadmap batches

## Status

Implementation verified; deployment next.

## Decisions

- Run daily at 10:30 AM and 3:30 PM Eastern (the Mac's local timezone).
- Use one durable manifest per scheduled slot and preserve overlap prevention.
- Complete saved dispatch/resume work before planning any new work.
- If recovery is unfinished, defer new work rather than bypass it.

## Findings

- launchd and the controller previously allowed only one daily 10:30 batch.
- Pending plans already had priority, but new planners could run before recovery completed.

## Open threads

- [x] Verified scheduled slots, duplicate prevention, recovery-first ordering, blocked recovery and live prior workers: all 61 batch tests passed; plist lint and diff checks passed.
- [ ] Commit, push, install and reload launchd after checking for an active batch.

## Approach

Keep the finite batch controller, concurrency/budget checks and saved-plan recovery path.
Split recovery and new work into separate waves; update dashboard schedule history.
