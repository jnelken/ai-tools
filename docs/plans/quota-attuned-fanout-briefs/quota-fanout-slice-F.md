You are implementing Linear DEV-320 in Jake's slackagent repo: run the unblock-roadmap pass through Slackagent in a designated Slack channel, using a weekday 8:45 durable question queue, one multiple-choice question at a time, answered with number-emoji reactions. This Superset workspace's worktree is your checkout.

Read first:
1. DEV-320, the spec and acceptance list: `linear issue view DEV-320` (CLI, never MCP).
2. This repo's AGENTS.md and README.md. Then: src/task-scheduler.js and src/daily-imessage.js (the scheduled-job pattern and the weekly usage DM from #6), src/memory.js plus src/memory/*.js (the SQLite store and migrations), src/index.js (Bolt app wiring, the existing reaction handlers for :memo: toggles, src/feedback.js reaction learning from DEV-314), src/linear.js (the Linear client), src/claude.js, src/codex.js, src/runtime-profile.js and src/model-usage.js (runners with usage recording).
3. ~/Dropbox/code/ai-tools/skills/unblock-roadmap/SKILL.md: what counts as blocked, the question style, the `## Directive` format, the Needs Input handling, and the hard rules.
4. ~/Dropbox/code/ai-tools/docs/agent-budget.md: `agent-budget allow --provider P --role R --want 1 --json`.

Design (adjust if the code says otherwise, and say why in the PR):
- **Sweep:** a headless, read-only agent run (Codex first per the Model Router, then Claude; use runtime profiles; read-only sandbox) that follows unblock-roadmap's detection rules and emits a QUESTIONS_JSON fence: a list of {repo, linear_id, blocker, evidence, question, options[2..6], directive_template}. Gate it with `agent-budget allow` before starting. If there's no headroom, post a short deferral note in the channel and retry at the next scheduler tick, at most hourly, during 8:45–18:00 on weekdays.
- **Queue:** a new SQLite table with a migration, durable across restarts. The statuses, fields and idempotency are as in DEV-320.
- **Posting:** one question at a time in `UNBLOCK_ROADMAP_CHANNEL`, with the options numbered 1️⃣…6️⃣ and the bot pre-adding those reactions. Only the owner's (SLACK_OWNER_USER_ID) reaction counts; a thread reply from the owner counts as "other: <text>". On startup, re-read the posted message's reactions to backfill any answer made while the bot was down.
- **Recording (v1: Linear only):**
  - Post the `## Directive` comment and move the ticket out of Needs Input deterministically through src/linear.js. No model call is needed when the option maps to a templated directive; use one small headless model call only to turn a free-text "other" answer into directive prose.
  - Do NOT edit repo markdown in v1. That avoids the dirty-tree trap entirely; note it in the PR as a deliberate v1 limit.
  - Confirm in the thread, then post the next question.
- **Stale check:** before posting each question, recheck the blocker cheaply (Linear state from the API, `git status --porcelain` for dirty-tree blockers). If it's resolved, mark the question superseded and skip it.
- **Loop:** when the queue is empty, re-sweep, until a sweep returns zero questions; then post "roadmap is unblocked" once and idle until the next weekday 8:45. At 8:45 with a non-empty queue, skip the sweep and re-post the pending question.
- **Usage:** every model call goes through the existing runners with a `source` label (unblock-sweep, unblock-directive), so it shows up on /usage.

Also open a small PR in ~/Dropbox/code/ai-tools updating skills/unblock-roadmap/SKILL.md with a "Slack mode (Slackagent, DEV-320)" section describing the above. It's its own branch and PR from an ai-tools Superset workspace; if you can't create one, describe the needed doc change in your slackagent PR body instead.

Rules:
- **Tests (npm test):**
  - queue persistence across a store close and reopen mid-queue;
  - owner-only reaction parsing, ignoring the seed reactions;
  - thread-reply "other";
  - startup backfill;
  - idempotent recording (crash between the Linear write and the status update);
  - superseded skipping;
  - the 8:45 skip-sweep rule;
  - the empty-queue re-sweep loop with termination;
  - the agent-budget deferral;
  - QUESTIONS_JSON parsing, including malformed output.
  
  Use fake Slack, Linear and runner clients; no real Slack or Linear calls in tests.
- **Live system:** don't restart the live service, post to real Slack, or write to real Linear. The conducting session deploys after merge.
- **Shipping:** commit coherently, push -u, and open a ready PR titled with DEV-320 (Summary / Changes / Validation with manual QA steps only). Do not merge. End with the PR URL(s).
