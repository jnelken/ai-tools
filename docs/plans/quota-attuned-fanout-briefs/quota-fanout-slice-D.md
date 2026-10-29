You are implementing slice D of the quota-attuned fan-out plan in Jake's slackagent repo: measuring the token usage of slackagent's model calls and their tool calls. This Superset workspace's worktree is your checkout.

Read first:
1. ~/Dropbox/code/ai-tools/docs/plans/quota-attuned-fanout.md. Your scope is "D. Slackagent usage measurement" (D1–D6) and finding F6. You own open threads O3, O4 and O8.
2. This repo's AGENTS.md, which has the reusable-artifact and privacy rules, and README.md. Then src/claude.js, src/codex.js, src/runtime-profile.js, src/index.js (runner wiring, around lines 60-150), src/memory.js (SQLite), src/web.js (private web UI and auth), src/task-scheduler.js and src/daily-imessage.js (the scheduled-job pattern), and the matching tests in test/.

What to build:
- **D1. Per call.**
  - ClaudeRunner keeps the full result JSON: usage (input, cache_creation, cache_read, output), total_cost_usd, num_turns, duration_ms, session_id, modelUsage. It records one row per call into a new `model_runs` table, with a `source` label for each runner instance (household, history, email, owner-mail, the iMessage digest, and the others you find in index.js).
  - Record failures too: timeouts (with duration), usage-limit errors, other errors.
  - `run()` must still resolve to the same text, so callers don't change.
  - CodexRunner: resolve O3 by checking whether `codex exec --json` gives token-usage events, and per-tool items, with --ephemeral. Check `codex exec --help` and run a tiny prompt; if Codex is rate-limited, use help and docs. Parse them into the same table. If Codex can't report usage, record the call with null tokens and note that in the doc.
- **D2. Per tool call.**
  - After each successful Claude run, parse the session transcript `~/.claude/projects/<cwd-slug>/<session_id>.jsonl`. Resolve O4: confirm where transcripts land under agent-run runtime profiles and launchd, using a real session_id.
  - Write `model_tool_calls` rows: run id, sequence, tool name, input chars, result chars, and the turn's usage.
  - **Privacy:** store metrics only, never message or tool content.
  - Do it asynchronously or best-effort, so it never delays or fails a Slack reply.
- **D3.** `scripts/backfill-model-usage.mjs` imports the ~1,046 existing transcripts in `~/.claude/projects/-Users-jake-Dropbox-code-slackagent/`. Infer the source from the first prompt where you can, mark rows `backfill`, and make it idempotent (keyed by session_id). Add an npm script for it.
- **D4.** Also append one normalized JSON line per call to `~/.claude/state/agent-usage.jsonl`, with fields ts, source "slackagent:<runner>", provider, model, role, session_id, input, cache_write, cache_read, output, cost_usd, duration_ms and outcome. ai-tools' upcoming agent-budget will read it. Write it best-effort with an O_APPEND single write.
- **D5.** A private `/usage` page in src/web.js behind the existing session auth, showing:
  - tokens and cost by day and source (last 30 days);
  - top tool calls by tokens;
  - timeouts and limit errors.
  
  Link it from the existing index or nav if one exists.
- **D6.** A weekly owner DM through the existing scheduler (resolve O8: does it support a weekly cadence, or does it need a small extension?). Contents: totals by source with the week-over-week change, the top 3 token sinks, timeout and limit counts, and the /usage link. It is built from `model_runs` only, with no model call. Pick a quiet weekly slot and keep the scheduling style consistent with the daily iMessage job.

Rules:
- **Tests.** Add tests next to the existing ones for:
  - the result parser (success, is_error, timeout);
  - transcript parsing (add a small fixture with an MCP tool_use and tool_result, no real content);
  - an idempotent backfill;
  - the /usage auth;
  - the weekly DM text.
  
  `npm test` must pass.
- **Don't run the live service.**
  - Don't restart slackagent's launchd job, and don't run the backfill against the real var/ database. The conducting session will do both after merge.
  - Never commit var/, .env or transcript content.
- **Running doc.** As you resolve O3, O4 and O8, tick them in ~/Dropbox/code/ai-tools/docs/plans/quota-attuned-fanout.md with the answer and evidence, plus any new open thread. That file is in the ai-tools main checkout: commit only that file there with a short message and push it, using `git -C ~/Dropbox/code/ai-tools pull --rebase` first. If that checkout is dirty with other work, put your notes in your PR body instead.
- **Shipping.**
  - Commit in coherent steps, then `git push -u origin <branch>`.
  - Open a ready (non-draft) PR with `gh pr create`, body `## Summary` / `## Changes` / `## Validation`. Validation is manual QA steps only, for example "DM the bot a Mailcrush question, then open /usage and see the run with its tool calls". Add a screenshot of /usage rendered with fixture data if you can render it locally.
  - **Do not merge.**
  - End with the PR URL and anything left open.
