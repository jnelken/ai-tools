# Twice-daily roadmap batches

## Status

Complete. Committed, pushed, installed and activated on 2026-11-10.

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
- [x] Pushed implementation `27c16b0`, ran `~/.ai-tools/install.sh --quiet`, verified the job was idle and reloaded launchd. Live calendar triggers are Hour 10/Minute 30 and Hour 15/Minute 30; no batch was manually launched.

## Approach

Keep the finite batch controller, concurrency/budget checks and saved-plan recovery path.
Split recovery and new work into separate waves; update dashboard schedule history.
