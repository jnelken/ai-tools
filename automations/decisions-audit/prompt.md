ultracode: run Jake's weekly audit of his replies to Zen "Decisions" blocks on this Mac (__HOST__). Jake approved this recurring multi-agent run; use the Workflow tool.

1. Run `date -u +%Y-%m-%d` for today. The window is the 7 days ending today (UTC). Build two slices that tile it (for example 2026-11-16..2026-11-20 and 2026-11-20..2026-11-23) with `since` and `until` as ISO dates.
2. Check `memex daemon status --no-update-check`. If it is stopped, say so at the top of the report; do not start it.
3. Call Workflow with scriptPath __SRC__/audit.js and args {"window": "<start>..<end>", "slices": [{"since": "...", "until": "..."}, ...]} as real JSON. Collectors may read ~/.claude/projects/**/*.jsonl directly when memex lacks a record.
4. Compare the report with the standing rules in ~/.ai-tools/instructions/development/work.md ("Do not ask (decisions audit, 2026-11-23)"). Flag any kind whose explicit-only accept rate newly clears 0.85 with at least 10 answered, and any existing rule a counterexample argues against. Propose changes as Decisions for Jake; never edit work.md, memory or any instruction file.
5. State at the top that this report covers __HOST__ only: the other Mac (jake1 or jake2) is not in memex and SSH between the Macs does not work, so its report is merged by hand. Also flag: failed slices (zero sessions scanned counts as failed), a stopped memex daemon, a stale index (latest indexed record older than 2 days), and a record count far below the previous report.

You cannot write files. Print the COMPLETE report as your final reply (the runner saves your stdout), then end with exactly one line: `AUTOMATION_RESULT: ok` or `AUTOMATION_RESULT: failed <reason>`. Read-only everywhere. Do not touch git, Linear, Slack or any PR. Never run `op`.
