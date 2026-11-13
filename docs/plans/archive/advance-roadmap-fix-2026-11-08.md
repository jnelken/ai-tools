# advance-roadmap fix session — 2026-11-08

## Status

Root cause fixed, validated, merged and pushed; the installer synced the deployed source.

## Decisions

- Classify Cursor's `ActionRequiredError` usage-limit banner as a live quota hit.
- Leave host-level Xcode cache warnings and transcript search matches unchanged.

## Findings

- Digest entries 1–4 and 7 share one cause: `usage.py` required the limit banner at the start of a line, while Cursor prefixes it with `ActionRequiredError:`. The worker then returned exit 1 with no limit record, so the run was classified as an error.
- Entries 5–6 are `xcrun` cache writes denied from a post-tool hook; the surrounding Git commands completed and produced expected results.
- Entries 8–11 are `rg`/source inspection output, not runtime exceptions or extraction attempts.
- Codex logs also show an unauthenticated Notion MCP startup message. Codex continued, so it did not cause a lane failure.

## Open threads

- [x] Add regression coverage for the prefixed Cursor banner.
- [x] Run the relevant advance-roadmap tests.
- [x] Merge and push the fix; deploy from the maintained installer.

## Approach

Update the shared limit-banner detector and exercise it through the real `usage.py live-limit` CLI. Preserve the worker's existing reservation behavior: after a reserved provider hits a live limit, mark the run quota-paused without spending an unreserved fallback.
