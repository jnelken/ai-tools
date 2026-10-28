# Quota-attuned agent fan-out: roadmap batch, per-session gating, slackagent usage

> **Suggested execution:** Sonnet with high reasoning: well-scoped Python, zsh and Node edits to existing, well-tested code, but they touch concurrency, gate semantics and hooks. Step up to Opus for slice C (shared budget and leases) if lease and wait semantics get subtle. Step down to Haiku for slices A1, A2 and D3 on their own.

**Status (2026-10-28 15:30):** approved. Wave 1 is dispatched to Superset workspaces: A+B in ai-tools and D in slackagent. C and E follow once A merges. This file is the running doc. Each slice's agent updates its own section's open threads on its branch.

Repos:
- `~/Dropbox/code/ai-tools`: the dev checkout. `~/.ai-tools` is its read-only deploy clone.
- `~/Dropbox/code/slackagent`

## Context

The 10:30 advance-roadmap batch (`automations/advance-roadmap/daily_batch.py`) runs out of quota before anything is implemented. The incident the other agent described was the **2026-10-27** batch:
- 24 repos were planned, 5 lanes at a time.
- 10 Codex planners took Codex's 5-hour window from 41% to its 70% gate, roughly 3 points each. All 10 found no work.
- Planning then fell back to Claude Opus with high effort: 8 planners, about $21. They hit the session limit at 10:42–10:43.
- Cursor's worker pool was already at 100% until 11-03, so nothing could have been implemented anyway.
- Since 10-15: 277 runs, 22 shipped.
- The 10-28 batch repeated it: 20 of 25 planners returned `blocked_no_item`. 11 of those verdicts were lost as `skipped-quota` and 5 runs were `quota-paused`.
- At 15:27 on 10-28, the live probe (`claude_quota`) read Claude 5h at 16% and 7d at 19%, while `providers-usage.json` still said 0% (F3). Codex 5h was at 100% and Cursor at 100%.

Separately:
- Slackagent's own Claude and Codex calls draw on the same windows, and their usage is thrown away.
- Interactive sessions fan out agents with no awareness of headroom.

**Goals:**
1. Plan only what can be carried through to implementation.
2. Size every fan-out (batch lanes, interactive sessions, Superset terminals) from live headroom and historical cost.
3. Measure slackagent's token use per call and per tool call.
4. Never drop an unfinished investigation.

## Decisions so far

- **Claude fallback planner:** Sonnet with medium effort. Codex stays first.
- **Claude batch cap:** keep 70% of the 5-hour window, now counting projected in-flight usage.
- **Per-session gating (you asked if I agree): yes.**
  - The batch, interactive fan-outs and slackagent all draw on one account-wide 5-hour window. A gate that only the batch respects can't stop the overlap.
  - One shared ledger of leases plus a live reading lets each spawner see the others.
  - Caveats: estimates are noisy (p80 plus a live re-read each wave), and usage from other machines only shows up through the live reading.
- **Running docs** cover both interactive planning sessions and headless roadmap planners. A planner killed by quota leaves partial triage that the next run reuses.
- **Gate strength:** Superset fan-out terminals wait in a queue on the budget, using no model while waiting. In-process `Agent` / `Workflow` calls over budget are denied, with a reason pointing to `fanout`.
- **Slackagent usage surface:** a private web `/usage` page plus a weekly owner Slack DM summary.
- **Fan-out goes through Superset terminals**, headless or not, so it counts for the leaderboard. Saved as a feedback memory on approval.
  - Memory `project_superset-production-run`, read from v1.30.2 source, says subagent transcripts already count as sessions, and that only merged PRs need a workspace binding.
  - Your instruction wins either way; this note is only so we can re-check the memory.

## Findings (evidence)

**F1. No-work verdicts are thrown away, so the same repos are fully re-triaged daily.** Zero `skipped-unchanged` runs 10-21 to 10-27, for five reasons:
- **(a) Lost at the barrier.** A verdict is saved only after a bookkeeping *worker* model runs (`run.sh:912-976`). `daily_batch.defer_ready` cancels no-work items too, and run.sh exits at `run.sh:849-855` before `verdict.py save`.
- **(b) Lost to the Stop hook.** `status-glyph-check.sh` forces an extra turn in headless planners. The glyph line replaces the result that held the fence, so `run.sh:661` records `error`. On 10-27 this was koan-master, lunchmoney and openclaw-vps: $9.21 of finished triage.
- **(c) Global fingerprint.** `lib/verdict.py:105` hashes every repo and every issue.
- **(d) Any pending plan disables the check for every repo.** `run.sh:525` uses a global `pending-plan*.json` glob, and mailcrush's plan disabled it for lanes 1–13.
- **(e) The verdict lasts 24 hours,** the same as the batch interval (`verdict.py`, `ORCHESTRATOR.md:179`).

**F2. Planning ignores whether anything can implement.** The only worker check is at release (`daily_batch.py:342-346`), after all planning. No-work bookkeeping also needs a worker.

**F3. The Claude gate is blind.**
- `usage.py:327 probe_claude` reads the statusline cache, which has been stale since Oct 24. An expired window reads as 0% (`usage.py:340`).
- The live probe `~/.ai-tools/lib/claude_quota.py quota()` (OAuth usage endpoint, fresh) is never used.
- Gates are point-in-time checks with no reservations, so five lanes passed the gate at once.

**F4. Planners are costly.**
- They read the whole 500 KB snapshot (199 issues, 5–7 jq passes) and re-read the skill docs (95–129k cache writes).
- Six of the eight called the advisor tool: about 708k uncached Opus tokens in total.
- Claude is hardcoded to Opus with high effort: `run.sh:68` (override `ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL`), `planapproval.py:213`, `worker.sh:29`, `review.sh:28`.

**F5. A Superset outage loses the whole day.** The date directory is created at `daily_batch.py:497`, before the Superset check at `:516-518`. 10-25 and 10-26 failed this way.

**F6. Slackagent.**
- `src/claude.js` runs `claude -p --output-format json` through `agent-run` (`src/runtime-profile.js`). It keeps only `.result`, discarding `usage`, `total_cost_usd`, `num_turns`, `session_id` and `modelUsage`.
- `src/codex.js` runs `codex exec --ephemeral -o file` and records no usage.
- Runners: household, history, email (Haiku, `LIGHTWEIGHT_ARGS`), owner-mail (Mailcrush MCP, `--tools ''`, so MCP calls are the only tool calls), and the Codex fallback. All are wired in `src/index.js:74-147`.
- `agent-run` already logs quota start/finish snapshots labeled `agent-run:runtime/text` to `~/.claude/state/quota-snapshots.jsonl`.
- About 1,046 transcripts in `~/.claude/projects/-Users-jake-Dropbox-code-slackagent/` can be backfilled.
- SQLite store: `src/memory.js`. Private web UI: `src/web.js`, with `web_sessions` auth.

**F7. Cost data available for estimation.**
- `runs.jsonl` `.tokens` (durable). Medians: Codex planner ~71k tokens, Claude planner ~251k, Cursor worker ~59k, Claude worker ~264k, Codex review ~29k.
- `quota-snapshots.jsonl` start/finish rows from `claude-session` and `agent-run`.
- Codex `rate_limits` in its rollout files.
- Claude Code transcripts, with per-message usage, including subagents.

**F8. Fan-out tooling.**
- No fan-out helper exists in `~/.ai-tools/bin`.
- Superset terminals are created with `superset terminals create --workspace "$SUPERSET_WORKSPACE_ID" --local --cwd <dir> --command '…' --json` and read with `superset terminals read` (see `skills/handoff/SKILL.md:102-131`).
- The CLI lives at `/Applications/Superset.app/Contents/Resources/resources/bin/superset` (`lib/workspaces.py:33`).

## Open threads

- [ ] **O1.** Is `CLAUDE_QUOTA_MANAGED` (set by `claude-session` and `agent-run`) enough to exempt headless runs from the Stop hook, or is a dedicated variable needed? Check that `run.sh`'s Claude call goes through `claude-session`.
- [ ] **O2.** Does `--disallowedTools advisor` actually suppress the advisor in `claude -p`? Smoke test before relying on it.
- [ ] **O3.** Does `codex exec --json` give per-turn token usage and per-tool items with `--ephemeral`? Check `codex exec --help` and run a sample.
- [ ] **O4.** Do slackagent's transcripts land in `-Users-jake-Dropbox-code-slackagent/` when launched under launchd plus `agent-run` runtime profiles? Match a fresh `session_id` from the result JSON.
- [ ] **O5.** Can `lib/runmemory.py` write the Step 8 ledger row directly (CLI form) for no-op no-work runs?
- [ ] **O6.** Where are Claude Code subagent transcripts stored (`<session>/` directory next to the `.jsonl`?), for per-subagent-type cost?
- [x] **O7.** Answered: running docs cover sessions and planners; Superset waits and Agent is denied; web page plus weekly DM.
- [ ] **O8.** Does slackagent's task scheduler (`src/task-scheduler.js`, the `daily-imessage.js` pattern) support a weekly cadence as it is, or does it need a small extension?
- [ ] **O10.** `superset agents wait` and `agents explain` reported slice D's agent as "ended its own session" 13 seconds after a hook, while its screen showed it still working. The likely cause is a nested `claude -p` the agent ran, whose SessionStart/End hooks inherit the terminal's Superset hook env and get charged to it. `bin/fanout` (C) can't rely on hook state alone. Either strip the Superset hook env from nested headless runs (`agent-budget run`), or detect completion from a result file or PR. Wave 1 is monitored by polling for the PR plus a screen-spinner check instead.
- [ ] **O9.** Do the Codex rollout files exist for planners run with `-s read-only` (no `--ephemeral`)? Needed so a killed Codex planner's transcript can be salvaged (E2).

## Approach (slices; each ships as its own PR from its own Superset workspace)

### A. Roadmap: stop wasting planning (ai-tools, `automations/advance-roadmap/`)

- **A1. Headless runs skip the Stop hook.**
  - `hooks/status-glyph-check.sh` exits 0 under the headless marker (O1).
  - `run.sh` fence fallback: take the last assistant message containing an `ORCHESTRATOR_RESULT_JSON` fence, excluding the prompt echo (`run.sh:659-660`).
- **A2. Verdicts survive.**
  - `defer_ready` cancels only dispatch and resume items.
  - The `batch-cancel` branch saves the verdict.
  - No-op no-work results (`archives == []`, `closeouts == []`, all blocker IDs already in the previous verdict) write the ledger row with `runmemory.py` (O5) and the verdict, with no worker.
  - `verdict.py --repo <target>` scopes the fingerprint (repo print, `repo/<target>` and unlabeled issues, its deploy, `linear_ok`, allowlist, skill).
  - The pending-plan glob at `run.sh:525` becomes `pending-plan-$TARGET_REPO*.json` when a target is set.
  - Batch runs carry the verdict for 72h. Update `ORCHESTRATOR.md:179`.
- **A3. Cheaper planners.**
  - Per-repo filtered snapshot for the planner (target plus unlabeled, no Done or Canceled).
  - Claude planner and approver on Sonnet with medium effort, through `lib/models.sh` defaults.
  - Advisor disallowed in headless runs (O2).
- **A4. No implementation budget, no planning.** Before launching planners, require headroom for one implementation on some worker provider plus the required reviewer (slice C). Otherwise record `skipped-quota` with the reason. Carried verdicts and pending plans are untouched.
- **A5. A Superset outage doesn't burn the day.** Create the date directory only after the Superset, snapshot and usage preflight passes.

### B. Live headroom (ai-tools)

- `lib/usage.py probe_claude` uses `claude_quota.quota()`, falling back to the statusline file.
- A reading older than 15 minutes is stored as `used_pct: None`, so the gate reports `unknown` rather than 0%.

### C. Shared budget and per-session gate (ai-tools)

- **New `lib/agent_budget.py` and `bin/agent-budget`:**
  - `status` prints per-provider live %, reserved %, headroom, and the estimated cost per role.
  - `run --provider P --role R [--wait] -- CMD` takes a lease (blocking while waiting, using no model), runs CMD, then releases. A lease is also freed when its pid dies or its TTL (p90 duration) expires.
  - `allow --provider P --role R --want N` prints how many fit, for spawners that launch in-process.
- **Leases:** `~/.claude/state/agent-budget/leases.json` under `flock`, so they are shared by the batch, every interactive session and slackagent.
- **Cost model:** p80 tokens per (provider, role) from `runs.jsonl`, the shared ledger (D) and Claude Code transcripts (subagents, O6). Converted to window % with per-provider factors learned from `quota-snapshots.jsonl` start/finish deltas and Codex `rate_limits`.
  - Seeds: Codex about 3 points per planner (measured 10-27); Claude conservative until there are 5 or more samples.
  - Samples go to `~/.claude/state/agent-budget/samples.jsonl`.
- **Headroom:** `cap − live − Σ in-flight leases`. Caps reuse `usage.py` (5h 70, 7d 80, Cursor 100). An `unknown` reading allows one lease per provider.
- **Consumers:**
  - `daily_batch.py`: lanes are what fits, re-read each poll. A planner reserves its own cost plus its expected implementation (dispatch rate per repo, default 0.3). Release waves only release reservations that still fit. Ordering: pending dispatch or resume plans, then changed repos, then repos with no verdict. `batch.json` gains a `budget` block, and the Slack summary gains a hold line.
  - **New `bin/fanout`** (Superset terminals): `fanout spawn --label L --provider claude|codex --role R --prompt-file F [--interactive]` opens one terminal per task in `$SUPERSET_WORKSPACE_ID`. If none is set, it uses the repo's workspace through `superset workspaces`. Each terminal runs `agent-budget run --wait -- claude -p … --output-format json > <state>/fanout/<id>/result.json`, so terminals queue themselves on the budget. `fanout status` / `fanout collect` read results back.
  - **PreToolUse hook on `Agent` / `Workflow`:** denies when `allow` returns 0, saying so and pointing to `fanout`. Otherwise it takes a lease, released by `SubagentStop` or the TTL.
- **Instructions:** `instructions/providers/claude-development.md` *Parallelize with Subagents* changes to "fan out via `bin/fanout` (Superset terminals), sized by `agent-budget`". The Model Router note stays.

### D. Slackagent usage measurement (slackagent)

- **D1. Per call.**
  - `ClaudeRunner` parses the result JSON's `usage`, `total_cost_usd`, `num_turns`, `duration_ms`, `session_id` and `modelUsage`, plus a `source` label per runner (household, history, email, owner-mail, iMessage digest, and so on).
  - Timeouts and limit errors are recorded too, with duration. Today's 4:44 iMessage timeout would appear as one.
  - `CodexRunner` switches to `--json` and parses the usage events (O3).
  - New `model_runs` table in `src/memory.js`.
- **D2. Per tool call.** After each Claude run, parse `~/.claude/projects/<cwd-slug>/<session_id>.jsonl` (O4). Record per-turn usage and each `tool_use` (name, input chars, result chars, and the next turn's input-token delta) into `model_tool_calls`.
- **D3. Backfill.** `scripts/backfill-model-usage.mjs` imports the ~1,046 existing transcripts, inferring the source from the first prompt and marking it `backfill`.
- **D4. Shared ledger.** Append one normalized row per call to `~/.claude/state/agent-usage.jsonl` (source `slackagent:<runner>`), which `agent-budget` reads.
- **D5. Access** (repo AGENTS.md rule). Add a private `/usage` page in `src/web.js`, behind the existing session auth, showing:
  - tokens and cost by day and source;
  - top tool calls by tokens;
  - timeouts and limit errors.
- **D6. Weekly owner DM** through the existing task scheduler (O8):
  - totals by source and the week-over-week change;
  - the top three token sinks (sources and tool names);
  - the count of timeouts and limit hits;
  - a link to `/usage`.
  
  It is built from `model_runs` alone, with no model call.

### E. Running planning docs (ai-tools instructions and skills)

- **E1. Rule** in `instructions/development/shared.md` *Plan Files*:
  - Any planning or investigation session opens a running doc right after orientation: `docs/plans/<slug>.md` in the target repo, or the harness plan file while in plan mode, copied to `docs/plans` on exit.
  - Sections: Status, Decisions, Findings (with evidence), Open threads, Approach.
  - Update it after each finding or agent result.
  - Open threads are only closed with an answer, or by moving them to a ticket or `IN_PROGRESS.md`. They are never deleted silently.
  - In personal repos it is committed and pushed, per the existing rule.
- **Skills:** `pick-up` and `close-out` also sweep `docs/plans/*.md` for unchecked open threads.
- **E2. Partial triage for headless planners** (ai-tools, `automations/advance-roadmap/`). The planners stay read-only; the digest is built deterministically, with no model.
  - **When:** a planner ends `quota-paused`, `planning-timeout` or `error` without a valid fence.
  - **What:** run.sh builds `partial-triage-<repo>.md` in `$CODE_DIR/.advance-roadmap/` from its transcript: the Claude session JSONL found by `session_id`, or the Codex rollout (O9). It holds the commands and reads it ran (with truncated outputs), its interim assistant text, and the files and tickets it touched.
  - **Next run:** the planner prompt names the file: "partial triage from `<stamp>`; reuse these findings and don't redo those reads". A completed planner for that repo deletes it. It ages out after 72h, and it is counted in the repo's fingerprint as "unfinished".
  - `ORCHESTRATOR.md` asks planners to state interim conclusions in plain text as they go, so the digest carries meaning.

## Critical files

**ai-tools:**
- `automations/advance-roadmap/{daily_batch.py,run.sh,lib/verdict.py,lib/usage.py,lib/models.sh,lib/planapproval.py,lib/tokens.py}`
- `skills/conductor/ORCHESTRATOR.md`
- `hooks/status-glyph-check.sh`
- new `lib/agent_budget.py`, `bin/agent-budget`, `bin/fanout`, and the hook script
- `instructions/development/shared.md`, `instructions/providers/claude-development.md`
- `skills/{pick-up,close-out}/SKILL.md`

**slackagent:**
- `src/{claude.js,codex.js,index.js,memory.js,web.js}`
- new `scripts/backfill-model-usage.mjs`
- tests next to each

**Reused:** `claude_quota.quota()`, `usage.windows_gate` and caps, `usage.py quota-record`, `verdict.py save/check`, `runmemory.py`, `runs.jsonl` `.tokens`, `quota-snapshots.jsonl`, `agent-run` / `claude-session`, `lib/workspaces.py` superset helpers.

## Execution

- Fan out slices A+B, C, D and E as **separate Superset workspaces** (one PR each). C's leases are what A4 and the batch admission use, so A ships first with a stub that checks only for an available worker. A small follow-up then wires the batch to C.
- Do this round's fan-out by hand with the Superset CLI, since `fanout` doesn't exist until C ships. Keep it to at most 2 concurrent Claude sessions until C is live.
- **Provider:** per the Model Router, Cursor (pool at 100% until 11-03) and Codex (5h at 100%) are gated, so wave 1 runs on Claude Sonnet at high effort.
- **Waves:**
  - **Wave 1:** `ai-tools` branch `quota/roadmap-stop-waste` (A+B), and `slackagent` branch `usage/model-usage-ledger` (D).
  - **Wave 2:** C (`quota/agent-budget`) and E (`docs/running-plan-docs` plus E2), both branched after A merges. Their files are mostly disjoint, but both depend on A's run.sh and daily_batch changes.
- **Merging:** each slice's agent opens a ready PR and stops. The conducting session reviews and merges, because these change the live 10:30 automation.

## Verification

- **ai-tools tests:** `python3 -m pytest automations/advance-roadmap/tests` plus the shell tests.
  - A glyph-after-fence planner still yields its plan.
  - A canceled no-work thread saves its verdict, and the next run is `skipped-unchanged`.
  - A no-op no-work result spawns no worker.
  - A commit in repo B leaves repo A's verdict valid.
  - A pending plan in repo A doesn't disable B's check.
  - A stale Claude reading gives `unknown`.
  - With the worker chain gated, no planners launch.
  - With headroom for 2, at most 2 are in flight.
  - Lease acquire, release, pid death and TTL.
  - The `fanout` dry run builds the right `superset terminals create` command.
  - The hook denies at 0.
  - `test_quota_replay.py` gets a 10-27-shaped fixture.
  - A quota-killed planner leaves `partial-triage-<repo>.md` built from a fixture transcript. The next planner's prompt names it, and a completed run deletes it.
- **slackagent tests:** `npm test`.
  - The result-JSON parser records usage on success, error and timeout.
  - The transcript parser handles a fixture with MCP tool calls.
  - The backfill is idempotent.
  - The `/usage` page requires auth.
  - The weekly DM text is built from `model_runs` fixtures, with no model call.
- **Live:**
  - `agent-budget status` matches `claude_quota` and Codex readings.
  - The next 10:30 batch's `batch.json` has a `budget` block, far fewer planners, and no `error` or `quota-paused` rows.
  - One owner DM with a Mailcrush query appears on `/usage` with its tool calls.
  - A `fanout spawn` of 3 tasks with headroom for 1 runs them one at a time.
