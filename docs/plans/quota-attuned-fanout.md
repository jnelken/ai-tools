# Quota-attuned agent fan-out: roadmap batch, per-session gating, slackagent usage

> **Suggested execution:** Sonnet with high reasoning: well-scoped Python, zsh and Node edits to existing, well-tested code, but they touch concurrency, gate semantics and hooks. Step up to Opus for slice C (shared budget and leases) if lease and wait semantics get subtle. Step down to Haiku for slices A1, A2 and D3 on their own.

**Status (2026-10-28 21:40):** wave 1 and E1 are merged and deployed on this machine (`~/.ai-tools` at `87b02bc`).
- **D:** slackagent#6, `ffab00e`. Live, with 953 sessions backfilled.
- **C:** ai-tools#8, `3fb534a`. agent-budget, fanout and the Agent/Workflow gate (fail-open, 6s deadline) are installed.
- **E1:** ai-tools#10, `c02350c`. The running-docs rule plus pick-up/close-out sweeps.
- **A+B:** ai-tools#9, `87b02bc`. Three review rounds; 289 pytest tests, the shell suites and the glyph-hook tests are green. The batch's Claude gate now reads live OAuth (56% at 21:35, against the old stale 0%). `daily_batch.py --dry-run` is OK.

**Wave 2 (10-29 02:15):** launched on fresh windows (Codex 5h 0%, weekly 47%; Claude 0%). C2 (O11 calibration plus daily_batch→agent-budget wiring) is running in `budget-batch-wiring` on Codex Sol high, and E2 (partial triage) in `partial-triage` on Codex Luna high. A monitor watches for their PRs; each gets Codex review lenses plus a small Claude verify, then merge, install and a dry run.

**C2 status (2026-10-29):** implemented and verified in `quota/budget-batch-wiring`; awaiting the conducting session's PR review/merge. O11 calibration and the daily-batch wiring thread are closed with evidence below. Live deployment/state is unchanged.

**Watch at the 10-29 10:30 batch:** expect `skipped-unchanged` rows only from 10-30 on, once the first carried verdicts exist; no `error` rows from the Stop hook; Claude planners on Sonnet with medium effort; and no planning when no worker or reviewer is available.

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
  - Leases cover admitted children only: fanout terminals, batch jobs, and hook-admitted subagents. Do not lease open conducting/interactive sessions. Their consumed usage appears in the live reading; the 30% above the 70% five-hour cap is the interactive reserve.
  - One shared ledger of leases plus a live reading lets each spawner see the others.
  - Caveats: estimates are noisy (p80 plus a live re-read each wave), and usage from other machines only shows up through the live reading.
- **Running docs** cover both interactive planning sessions and headless roadmap planners. A planner killed by quota leaves partial triage that the next run reuses.
- **Gate strength:** Superset fan-out terminals wait in a queue on the budget, using no model while waiting; unknown readings allow one fanout/batch lease per provider. In-process `Agent` / `Workflow` calls over a known budget are denied, pointing to `fanout`. Unknown quota, any exception, malformed stdin, or timeout must fail open (exit 0 without a denial, with one stderr note for errors). The hook uses cached estimates (ten minutes) and quota readings (five seconds), and finishes before the 30-second harness deadline.
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
- **(f) The post-worker fingerprint omits `--deploys`** (`run.sh:969`), so every carried verdict shows `deploy:<repo>` as changed on the next check. Found by the A+B agent on 10-28 in runs back to 10-18.

**F9. Burn rate with two interactive Sonnet agents and this conducting session (10-28).** Claude's 5-hour window went from 16% (15:27) to 35% (15:36), about 2 points per minute. At that rate the 70% batch cap is about 18 minutes away and 100% about 30 minutes away. Wave 1 is already the kind of run this plan is meant to prevent. Review decision: reserve leases for admitted children only; the live reading and 30% interactive reserve cover conducting/open sessions. Do not create SessionStart/End budget leases.

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

- [x] **O1.** Answered: no, a dedicated variable is needed; `ADVANCE_ROADMAP_HEADLESS=1`. `CLAUDE_QUOTA_MANAGED` is set only inside `claude_quota.run`, which `claude-session run` and `agent-run` (for Claude) go through; `planapproval.py` calls `claude` directly, so its approver turns never carry it, and any interactive `claude` started inside a managed session would inherit it and lose its sign-off check. The new variable is exported by `run.sh`, `worker.sh`, `review.sh` and set in `planapproval.py`'s subprocess env, and matches the `ADVANCE_ROADMAP_*` pattern the Superset worker launcher re-exports. `run.sh`'s planner does go through `claude-session run`. Evidence: `grep CLAUDE_QUOTA_MANAGED` (only `lib/claude_quota.py`); hook cases in `hooks/tests/status-glyph-check.test.sh`; the replay test checks the marker reaches the planner call. Live reproduction of F1(b) (the four O2 smoke runs, each with the deployed Stop hook active): `claude -p … --output-format json` returned only the `💬 …` line as `result` while the session transcript held the fence in an earlier assistant message, which is what `lib/lastfence.py` recovers.
- [x] **O2.** Answered: no. The advisor is a *server tool* (`server_tool_use` name `advisor`; model `advisorModel` in settings), and none of `--disallowedTools advisor`, `--disallowedTools Advisor` or `--settings '{"advisorModel":null}'` stopped it: every smoke run of `claude -p --model claude-sonnet-5-5 --effort medium` (about $0.3 each) still showed the advisor call and a `claude-opus-5-5` entry in `modelUsage`. Left enabled. The same runs confirmed `claude-sonnet-5-5` and `--effort medium` are accepted. See OA2 for what could work.
- [x] **O3.** `codex exec --help` confirms `--json` emits JSONL and `--ephemeral` is supported together. A tiny ephemeral JSONL prompt emitted `turn.completed.usage` with input, cached input, cache-write input and output counts. Tool items are represented by `item.completed` events; the tiny prompt returned no tool items. A rate-limited run would still produce a call row with null usage.
- [x] **O4.** Real session `116e6692-a910-42d3-a6bb-f82e695624aa` from the `agent-run` runtime/text profile has a transcript at `~/.claude/projects/-Users-jake-Dropbox-code-slackagent/116e6692-a910-42d3-a6bb-f82e695624aa.jsonl`. The launchd wrapper `cd`s to the Slackagent repo root before starting Node, so it uses the same cwd slug; launchd itself was not run.
- [x] **O5.** Answered: yes. `runmemory.py append-ledger --repo R --stamp S --outcome O --detail D` is already a CLI, locked and atomic, and `ADVANCE_ROADMAP_RUNS_DIR` redirects it. `run.sh` now writes the Step 8 row itself for a no-op no-work result; the replay test reads the row back with `runmemory.py has-stamp`, which is what Step 0 uses.
- [x] **O6.** Claude Code stores children at `~/.claude/projects/<encoded-cwd>/<parent-session-id>/subagents/agent-<agent-id>.jsonl`, next to the parent `<session-id>.jsonl`. Adjacent `agent-<id>.meta.json` has `agentType`, `description`, `toolUseId`, `spawnDepth`, and request shape. Evidence: 40 child transcripts on this Mac on 10-28; the slackagent parent `5f74cc31-28d2-4391-86a7-e66ceacbeee3`, child `a7a1d15fadced552d`, has `agentType: Explore` and a spawning `toolUseId`. Assistant rows carry `message.id`, `message.usage` (input, cache creation/read, output), `agentId`, and the parent `sessionId`. Sum usage once per API message id, taking per-field maxima for repeated streaming rows; exclude cache reads consistently with `tokens.py`. The budget retains agent type and aggregates the subagent role. SubagentStop uses this metadata to release the exact spawning tool lease, including out-of-order completions.
- [x] **O7.** Answered: running docs cover sessions and planners; Superset waits and Agent is denied; web page plus weekly DM.
- [x] **O8.** The scheduler is a one-minute tick plus daily and per-item checks; it has no weekly cadence. Added a `kv_state` weekly slot claim, default Monday 09:00 America/New_York after the 08:00 morning digest. The DM is built from usage tables without a model call.
- [x] **O10.** Superset's installed `~/.superset/hooks/notify.sh` v20 checks `SUPERSET_TERMINAL_ID` **or** `SUPERSET_TAB_ID` at line 18; its v1 fallback (lines 276–314) routes `SUPERSET_PANE_ID`/`SUPERSET_TAB_ID` to `/hook/complete`, translating SessionEnd into Stop. Removing only the terminal ID leaves the parent tab's identity active. Nested headless children now strip `SUPERSET_AGENT_*`, `SUPERSET_HOST_AGENT_*`, `SUPERSET_HOOK_*`, `SUPERSET_TERMINAL_ID`, `SUPERSET_TAB_ID`, `SUPERSET_PANE_ID`, `SUPERSET_PORT`, and `SUPERSET_DEBUG_HOOKS`, plus Claude parent-session markers. Workspace/org/account attribution remains. An offline regression reproduces the terminal-or-tab routing guard and confirms a nested SessionEnd cannot dispatch. `fanout` completion still requires `state/fanout/<id>/exit`, never provider hook state. No real model was launched for validation.
- [x] **O11.** Fixed in C2 (2026-10-29). The mismatch was the interval and scope: integer account-wide quota deltas were charged only to the last positive turn's local new tokens, dropping flat intervals and repeating concurrent account increases per rollout. Calibration now merges timestamped rollout observations across the account within reset/active intervals, includes flat-turn tokens, tolerates stale parallel readings, and uses token-weighted p80 so tiny rounded intervals do not dominate. Sanitized real token-count events from the ten 10-27 planners yield 29 points / ~770k new tokens = **3.8e-5**; the available account history rebuild yields **6.4e-5** (74 intervals), or 5.7 points for 88k tokens, versus the old 69. Parser/estimate versions invalidate persisted old samples. Evidence: `lib/test_agent_budget.py::BudgetTests::test_real_shaped_oct27_parallel_rollouts_share_one_account_delta` and flat/reset/idle regressions. No live state was written.
  - Claude sanity: the five saved samples are 2/45012, 2/45025, 2/37111, 3/1689 and 28/256491 points/new-token. Its p80 **1.09e-4** makes 130k tokens about **14.2 points**: at F9's ~2 points/minute across three sessions, that is ~21 minutes/session (versus the seed's 15). Conservative but plausible; the 3/1689 sample demonstrates concurrent account contamination. F9 includes two Sonnet children plus a conductor, and historical runs also contain Opus/advisor usage. These account deltas cannot identify a per-model numerator, so separate Opus/Sonnet factors would invent attribution. Retain the conservative provider factor until isolated, model-attributed boundaries exist (O12).

- [x] **O9.** Answered: yes. All 329 orchestrator Codex usage rows in the live `runs/*/model-usage.jsonl` have source `codex-rollout`, none `codex-total`, and 162 of the 219 planner session ids in `runs.jsonl` still have a `~/.codex/sessions/*/*/*/rollout-*<id>.jsonl` (the other 57 are probably pruned; not verified). So planners run with `-s read-only` and no `--ephemeral` do leave a rollout to salvage for E2.

- [ ] **OA1.** (A+B) `review.sh` lost its limit, session, gated and capacity log fields and its final-answer-only verdict when review strategies were made selectable (b6b5558, 10-17), and `test-review.sh` plus four replay tests had been red on main since. Restored in the A+B PR; strategy tests pin cross-provider, and OA14 adds a required-stage session regression. Unchecked: whether the dashboards that read `review_limits` look right.
- [ ] **OA2.** (A+B) The advisor still runs in headless planners (O2). Untested options: a prompt line telling the planner not to call it, or a settings overlay for headless runs that unsets `advisorModel`. Measure the saving first (F4: about 708k uncached Opus tokens over 8 planners), and note Sonnet at medium makes it less likely to be called at all.
- [ ] **OA3.** (A+B) Deploying this changes `ORCHESTRATOR.md`, which is in the `skill` fingerprint input, so every carried verdict is invalid once: the first batch afterwards re-triages every repo. A4 bounds the cost (no headroom, no planners). Per-repo fingerprints from then on are not comparable with the old global ones, and nothing migrates them.
- [ ] **OA4.** (A+B) A deferred batch (no worker for bookkeeping) saves its no-work verdict marked `bookkeeping_pending`. The next run re-plans that repo with a `verdict-changes.json` instead of skipping it, so one cheap planner pass buys the blocker comments. A bookkeeping-only worker run from the saved verdict would avoid the planner call; not built.
- [ ] **OA5.** (A+B) `daily_batch.py` is launched once a day (10:30, no retry), so A5 only helps a manual or catch-up run after an outage. Whether the plist should retry on a preflight failure is Jake's call.
- [ ] **OA6.** (A+B) `python3 -m pytest` is not installed for the system Python; the suites were run from a scratchpad venv with pytest 9.2.

- [x] **OA7.** (A+B review #1) Transcript-fence recovery now rejects structured provider errors through the same `usage.py is-error` check as reviews. Limit-killed planners record their live limit and fail over; subsequent lanes see the gate. Evidence: `test_quota_replay.py::test_limit_killed_planner_cannot_recover_a_transcript_fence` and `test_limit_killed_planner_with_a_fence_still_fails_over`.
- [x] **OA8.** (A+B review #2) Saved verdicts retain the planning-time fingerprint. Only blocker, closeout and directive issue ids receive post-worker values after settled bookkeeping; only the target repo receives its post-worker print when archives and a reported commit are present. A failed post snapshot leaves issue hashes alone. Evidence: the new-issue replay reports `issue:DEV-8` changed on the next check, plus selective issue/repo and failed-snapshot cases in `test_verdict.py`.
- [x] **OA9.** (A+B review #3) Preflight parses the Linear snapshot and requires `ok is True` before claiming the date. The outage stub now matches real `linearsnap.py`: writes `ok:false` and exits zero. Evidence: `test_daily_batch.py::test_a_linear_outage_also_leaves_the_day_unclaimed`.
- [x] **OA10.** (A+B review #4; refined by OA16) A stale OAuth cache falls back to a trusted statusline reading. Fresh OAuth stays authoritative. Evidence: the stale 10% OAuth / marked 99% statusline regression enforces `over` and `available:false` in `test_usage.py`.
- [x] **OA11.** (A+B review #5) Cross-provider admission requires an available worker/reviewer pair with different providers. Evidence: `test_daily_batch.py::test_cross_provider_cannot_admit_a_worker_as_its_own_only_reviewer` rejects Cursor alone and accepts a chain including Claude with Cursor reviewing.
- [x] **OA12.** (A+B review #6) Admission returns the blocking role, which selects both the live quota record and its cached fallback. Evidence: a healthy worker pool with gated Codex review records reviewer/Codex, cause and reset in `test_reviewer_admission_skip_records_the_reviewer_gate`.
- [x] **OA13.** (A+B review #7) Pre-plan skip records pass the target repo explicitly. Evidence: the no-worker-headroom regression asserts repos `a` and `b` in `runs.jsonl`.
- [x] **OA14.** (A+B review #8) Required review stages clear their session artifacts before checking the gate. Evidence: `test-review.sh` runs a clean Cursor stage followed by gated Codex and asserts Codex's session is null.

- [x] **OA15.** (A+B verification) Running the required status-glyph suite under zsh exposed its pre-existing use of the read-only `status` variable in two test helpers. Renamed the test-local variable to `hook_rc`; the hook behavior is unchanged.

- [x] **O11 (C follow-up).** Wired the daily batch to shared `agent-budget` leases in C2 (2026-10-29), after A+B/PR #9. Every planner reserves itself plus per-repo dispatch share × worker/review cost (default 0.3); completed planning drops only its planner portion. Pending plans reserve full implementation, with a planner fallback when available. Release refreshes usage once and atomically replaces the expectation with a full worker/review bundle on the first fitting chain provider. Interactive leases reduce batch capacity. Running jobs free capacity for later release waves; a fractional barrier cannot deadlock because lower priority held expectations are deferred first. Exact reserved routes prevent unleased fallback; default and cross-provider reviewer rules and preflight remain. Pending → changed → no-verdict ordering uses `verdict.py check` on a temporary copy, leaving actual carried-verdict skipping in `run.sh`. `batch.json.budget`, bot/webhook hold lines and read-only dry-run forecasts provide visibility. Evidence: the new compound admission, interactive contention, conversion, one-lane/five-job release, barrier, ordering, bot-summary, real-shell route and 10-27 replay regressions in the required three Python test files, plus the zsh reviewer reservation checks. Validation results recorded below.

- [x] **C2R1 (PR #13 review).** Pending dispatch/resume admission now tries every eligible planner and then no planner. A raw-open planner gate cannot block a fitting worker/review bundle merely because an optional planner reservation is too expensive. Evidence: `test_pending_plan_can_launch_without_planner_budget` fails on the reviewed branch, then passes with Codex at 68% and a fitting review-only Codex cost; launch exports `ADVANCE_ROADMAP_BUDGET_PENDING=1` and `ADVANCE_ROADMAP_BUDGET_PLANNER=none`.

- [x] **C2R2 (PR #13 review).** Barrier recovery frees the lowest priority reservation across every ready plan, including reservations outside the lane-limited candidate wave. It no longer sacrifices the highest priority changed repo when `--lanes 1` truncates that wave to one item. Evidence: `test_single_lane_barrier_preserves_changed_repo_over_all_reservations` fails on the reviewed branch, then passes with Cursor at 86%, one changed repo and four no-verdict repos; the changed repo ships first while lower priority work is held.

- [ ] **O12 (calibration).** Accumulate five measured samples/provider before replacing seeds; Cursor currently has no measured token-to-window factor (provisional 5 points/59k tokens). Claude starts at 10 points/251k tokens: F9's ~2 points/min across three sessions × 15 minutes/session. The five-hour factor is conservatively charged to weekly windows too. Connect runtime ledger launch/session ids to quota boundaries wherever callers currently omit them; unmatched boundaries remain uncalibrated rather than guessed.
- [x] **E1.** Added the running planning-doc rule, aligned Claude plan-mode guidance, taught pick-up and close-out to account for repo and recent harness plan threads, and added both skills to the README table.

- [x] **OA16.** (A+B round 3) Statusline loading displays used to write freshly timestamped 0% values, which displaced a fresh OAuth observation. Fresh OAuth now wins for 15 minutes. Only statusline files marked `source: rate_limits` can be fallback readings; unmarked legacy files, placeholders and `quota()`'s wrapped statusline fallback are untrusted. The writer marks real inputs and stores missing windows as null. Evidence: real-writer tests with a temporary HOME reproduce missing `rate_limits`, retain the 30-second-old 99% OAuth gate, accept real 0%, and leave missing windows unknown; a separate regression makes fresh OAuth beat a newer marked statusline. All cases are in `test_usage.py`.

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

- `lib/usage.py probe_claude` prefers fresh OAuth from `claude_quota.quota()`; only stale or absent OAuth falls back to statusline files marked `source: rate_limits`.
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
- **Headroom:** `cap − live − Σ in-flight leases`. Caps reuse `usage.py` (5h 70, 7d 80, Cursor 100), including expired-window reset semantics. An `unknown` reading allows one lease per provider for fanout/batch admission; the in-process hook fails open.
- **Consumers:**
  - `daily_batch.py`: lanes are what fits, re-read each poll. A planner reserves its own cost plus its expected implementation (dispatch rate per repo, default 0.3). Release waves only release reservations that still fit. Ordering: pending dispatch or resume plans, then changed repos, then repos with no verdict. `batch.json` gains a `budget` block, and the Slack summary gains a hold line.
  - **New `bin/fanout`** (Superset terminals): `fanout spawn --label L --provider claude|codex --role R --prompt-file F [--interactive]` opens one terminal per task in `$SUPERSET_WORKSPACE_ID`. If none is set, it uses the repo's workspace through `superset workspaces`. Each terminal runs `agent-budget run --wait -- claude -p … --output-format json > <state>/fanout/<id>/result.json`, so terminals queue themselves on the budget. `fanout status` / `fanout collect` read results back.
  - **PreToolUse hook on `Agent` / `Workflow`:** denies when `allow` returns 0, saying so and pointing to `fanout`. With known headroom it takes a lease, released by `SubagentStop` or the TTL. Unknown quota, exceptions and timeout allow the call. No SessionStart/End budget leases.
- **Instructions:** `instructions/providers/claude-development.md` *Parallelize with Subagents* changes to "fan out via `bin/fanout` (Superset terminals), sized by `agent-budget`". The Model Router note stays.

**Standalone C implementation (2026-10-28):** `bin/agent-budget`, `bin/fanout`, the lifecycle gate and installer wiring are implemented; see [operator notes](../agent-budget.md). Headless and interactive Superset launches queue without a model; interactive reservations are held by a detached terminal watcher until closure or TTL. Open conducting sessions have no budget lease; only admitted children reserve headroom. Hook errors, unknown quota and timeouts allow the call, and estimates are cached for ten minutes. Hook registration is owned by `install.sh` here (`settings.json` on this Mac is a regular file), so no dotfiles change is needed. Missing child metadata or a failed pre-tool spawn falls back to TTL release. Local leases cover this machine; other machines enter only through live quota readings. Polling waiters have no FIFO guarantee.

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

- **E1. Rule — done.** Added to `instructions/development/shared.md` under *Plan Files*; Claude plan-mode guidance now copies the harness plan after exit, and pick-up/close-out sweep repo and recent harness plan threads. Both skills are listed in the README:
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
- **C2 validation (2026-10-29):** `uv run --with pytest python -m pytest automations/advance-roadmap/tests lib -q` → **398 passed, 108 subtests passed**. All four `automations/advance-roadmap/tests/*.sh` suites passed under zsh, including the new reserved-reviewer checks; `status-glyph-check.test.sh` and five other hook suites also passed under zsh. Three additional Bash-declared hook suites passed with their declared interpreter. The unrelated clone-lock suite has two installer cases failing on missing `restart_updated_installer` (83 checks pass); its test/installer/helper are unchanged by C2, and the same two failures were reproduced at base `9e865f9` in a temporary detached checkout (removed after verification). `/session-hardening` can address that existing installer-test extraction issue. A blanket zsh invocation is inappropriate for those Bash-declared hook suites (`status` is read-only in zsh). Python compilation, zsh syntax, Node syntax and `git diff --check` passed. All model calls, summaries, state and GitHub/Linear access in tests were isolated/faked; the rollout/calibration audit only read local logs.

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
