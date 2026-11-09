# Advance-roadmap batch error triage and fix

## Status

Triage complete; implementation and verification pass; ready to merge.

## Decisions

- Remove Cursor from new advance-roadmap worker routes. Keep Cursor quota telemetry, review fallback, and historical result parsing.
- Leave the `xcrun` cache messages unchanged: Git operations continued successfully, so they are runner environment noise.
- Leave Notion OAuth unchanged: the MCP transport reports authentication required while completed lane results do not show a Notion-dependent failure. Jake must reauthenticate if upcoming lanes need Notion.
- Do not edit batch, run, or ledger state and do not start a batch.

## Findings

- Digest entries 1 and 2: run status shows Cursor worker exit 1, no result, and usage limit; Codex and Claude were under gate. The worker chain puts Cursor first.
- Digest entry 5 and Jake report R1: both named workers hit Cursor's usage limit; source selects Cursor for worker and default review routes.
- Digest entries 3 and 4: repeated xcrun temp-cache failures are warnings in Git output; subsequent commands completed.
- Extra log errors: Notion MCP emits OAuth `AuthRequired`; lane results do not depend on it.

## Open threads

- [x] Verify reported lane errors and all matching batch logs.
- [x] Remove Cursor from worker selection and add regression tests.
- [x] Run advance-roadmap and ai-tools tests.
- [ ] Review, merge and push.

## Approach

Keep Cursor quota probes and accounting for usage visibility and review fallback, but route workers only through Codex and Claude. Preserve the existing review strategy and quota-aware fallback.
