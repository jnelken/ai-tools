# Quota-attuned agent fan-out: roadmap batch, per-session gating, slackagent usage

> **Suggested execution:** Sonnet with high reasoning: well-scoped Python, zsh and Node edits to existing, well-tested code, but they touch concurrency, gate semantics and hooks. Step up to Opus for slice C (shared budget and leases) if lease and wait semantics get subtle. Step down to Haiku for slices A1, A2 and D3 on their own.

**Status (2026-10-29 17:20):** #15 (H, the Cursor-gated cross-provider review fallback, 2 rounds) is merged and deployed (`01af84e`). The dry run no longer holds on the Cursor reviewer, but it still shows lanes=0 with 'no worker plus required review fits shared headroom'. That's **F11**: `agent_budget.capacity()` charges the 7d/weekly windows at the 5-hour-calibrated rate, with Codex weekly at 71% and Claude 7d at 53%. **Slice W (per-window factors) is now the last blocker for the 10-30 10:30 batch.** In flight: W (Codex Luna), the #16 G1 review (Claude workflow), and slackagent#7 fixes. The open decision for Jake: DEV-320 channel access (reinstall or channel ID).

**G1 (10-29, branch `quota/reservation-ledger`):** the ledger core is implemented: token reservations, `reserve`/`update`/`settle`, `run` as reserve → exec → watch → settle, a FIFO wait queue, ledger-backed estimates and nested-lease adoption. No launcher is wired yet; G2 below lists the exact choke points.

**Wave 2 (10-29 03:15):**
- **E2, PR #12:** round 2 is verified by Claude. P1-1, P2-3, P2-4 and P2-5 are FIXED. P1-2 (redaction variants) and P2-6 (the per-attempt bound) are PARTIAL, and there are new findings (over-redaction, the batch-cancel save, an unattributable attempt erasing research). Round 3 is written in `~/.claude/state/fanout/review-ai-tools-12/fix-request-r3.txt` and goes out at 07:21.
- **C2, PR #13:** O11 is confirmed fixed on real history (Codex is about 4.6 points per 71k tokens, within 2× of measured). Three verified P2s (pending-plan admission, priority-inverted cancel, error-cleanup lease release) are being fixed. The reset-jitter finding was refuted on real data.
- **Both providers were over the cap at 03:10** (Codex 81%, Claude 72%), and `agent-budget allow` returned 0, so the gate is doing its job.
- **Merge plan:** merge #12 and #13 after the 10:30 batch and the 11:47 check, so the first live run measures #9 alone.

**Slice F, DEV-320 (added 10-29 03:25 at Jake's request):** run unblock-roadmap through Slackagent in **#agent-blockers** (Jake, 10-29). It uses a weekday 8:45 durable SQLite question queue in a designated channel, asks one multiple-choice question at a time and takes number-emoji reaction answers. When the queue empties it re-sweeps, until nothing is blocked. At 8:45 with a non-empty queue, it skips the sweep and resumes. The brief is `docs/plans/quota-attuned-fanout-briefs/quota-fanout-slice-F.md`; v1 records Linear directives only, with no repo markdown edits. Dispatch is queued for 07:21 with the #12 round 3, after the Codex reset.

**DEV-321 (standalone; rescoped by Jake 10-29 to Slackagent's Linear integration in general):** a spike on routing Slackagent's Linear requests to Linear's in-Slack `@Linear` agent wherever that's cheaper than its own model calls.
- **Keep on the API:** the deterministic paths, `src/linear.js` and `src/unblock.js` (zero tokens).
- **The candidate:** the DEV-315 owner-DM tool loop in `src/integration-tools.js`, which spends a multi-turn model transcript on our quota for every Linear request.
- **Open questions:** does `@Linear` act on bot-authored messages; the `repo/*` label requirement; permissions.
- **Status:** In Progress. The analysis can run unattended, but live Slack tests need Jake's OK.

**10-29 09:10 dispatch (run by hand: the 07:21 session task never fired, because session-only crons only fire while the REPL is idle):** #12 round 3 went to the E2 Codex agent; a Codex verifier is checking #13's three fixes; DEV-320 is building in the `dev-320-slack-unblock` workspace (Codex Sol high) and is In Progress. All of it is on Codex, which had just reset to 0%; Claude was at 73%, over its cap. The batch will have less Codex headroom today, and the #9 gate turns that into skipped-quota rather than waste.

**C2 status (2026-10-29):** implemented in `quota/budget-batch-wiring`; PR #13 review corrections C2R1–C2R4 are fixed with failing-before/passing-after regressions. Awaiting the conducting session's review/merge. O11 calibration and the daily-batch wiring thread are closed with evidence below. Live deployment/state is unchanged.

**Decisions (Jake, 10-29 16:20):**
1. **Universal reservation ledger:** every agent launch reserves tokens, updates its estimate, waits if there's no headroom, and settles with actual tokens, ticket, session ids and task. One ledger row per completed output. This is slice G: G1 is the ledger core (brief `quota-fanout-slice-G1.md`, on Claude Sonnet to save Codex's weekly headroom), and G2 wires every launcher to it.
2. **Hand-started interactive sessions are never blocked:** they reserve and warn.
3. **Review fallback:** when Cursor is gated, review switches automatically to cross-provider. This is slice H (brief `quota-fanout-slice-H.md`, Codex Luna) and must merge before the 10:30 batch on 10-30.

**Slice H note (2026-10-29):** Implemented a shared effective-strategy decision in `lib/usage.py`. The default `cursor-then-codex` now resolves to cross-provider while Cursor's reviewer gate is over/limited; explicit `cross-provider` remains unchanged. Batch admission and reviewer execution consume the same decision, and the worker receives its strategy and fallback reason so remediation follows that route. Batch state/summary refresh the reset-based fallback note with each reservation decision. Admission still requires an under-gate reviewer from the worker chain who is not the worker. Automated and zsh suite results are recorded in the PR; deployment/merge remains required before the 10-30 10:30 batch.

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
  - Leases cover admitted children only: fanout terminals, batch jobs, and hook-admitted subagents. Do not lease open conducting/interactive sessions. Their consumed usage appears in the live reading; the 30% above the 70% five-hour cap is the interactive reserve. **Amended by decision 2 below (10-29):** a hand-started interactive session may now record a reservation (`reserve --interactive`), which counts against headless capacity but never blocks the session. Until G2 wires that in, sessions still hold no lease.
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

**F10. The 10-29 10:30 batch (the first with #8/#9/#10) planned nothing, correctly.**
- **Outcome:** `phase: skipped`, 24/24 `skipped-quota`, 0 model calls, 0 tokens, with the reason "the required cursor reviewer is gated (auto pool 100% >= 100%)". For comparison: 10-27 had 16 skipped-quota, 5 quota-paused and 3 error rows (~898k new and 12.2M cached Claude planner tokens for 8 planners); 10-28 had 11 skipped-quota, 5 quota-paused, 4 blocked-no-item, 4 blocked and 1 archive-only.
- **Why:** the default `cursor-then-codex` review strategy hard-requires a Cursor review cycle (`review.sh`: `run_required_review cursor cursor-cycle || exit $?`), and Cursor's auto pool is at 100% until 11-03. The #9 pre-plan gate saw that nothing could finish review and skipped planning, so nothing was wasted. Claude was also at 93% of its 5h window at 10:30, and quota_paused lists cursor and claude.
- **Not exercised today:** missing-fence `error` rows, the Sonnet-medium planner and verdict saving, because no planner ran.
- **Consequence:** carried verdicts can't start until a day that actually plans, and **the batch will skip every day until Cursor resets on 11-03** unless the review strategy falls back (cross-provider, or Codex-only final review when Cursor is gated). That's a decision for Jake.

**F11. Long windows charged at the 5-hour rate.** `lib/agent_budget.py capacity()` subtracts the same five_hour-calibrated cost and reservations from every window; calibration only reads five_hour deltas. Live evidence: Claude 5h 0% with 7d 53% gave `allow --want 4` → 1, and the 10-29 17:20 dry run held every repo with 'no worker plus required review fits'. The fix is slice W (per-window factors).

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
  - Independent PR #13 reviewers rebuilt a copy of real history and confirmed about **4.57 points per 71k tokens**, versus ~3 measured (within 2×). Their proposed reset-jitter issue was refuted by real data: jitter tolerance produces the identical factor. Calibration/reset handling is unchanged in the review corrections.
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

- [x] **C2R3 (PR #13 review).** Controller cleanup retains leases owned by live planning lanes, just as it retains running worker leases. A controller exception cannot make detached planner model calls disappear from shared headroom; normal PID/TTL reaping releases them later. Evidence: `test_controller_failure_keeps_live_planner_leases_until_process_exit` injects `TimeoutExpired` from a usage refresh while a fake planner is alive. It fails on the reviewed branch, then verifies all transferred leases survive `main()` cleanup with that lane's PID and disappear only after the fake process exits.

- [x] **C2R4 (PR #13 follow-up).** The no-planner admission fallback and `ADVANCE_ROADMAP_BUDGET_PENDING=1` launch flag now apply only to thread 1, matching `run.sh`'s saved-plan reuse rule. Later threads may inherit their parent's ordering metadata but must reserve a fresh planner and expected implementation cost. Evidence: `test_second_thread_of_pending_dispatch_still_requires_planner_budget` fails on `5c9ba1b` because thread 2 is allocated/launched with `planner=None`; with the fix, a saved unstarted dispatch, Codex at 67%, healthy Cursor, `--lanes 1 --threads 2` ships thread 1 while thread 2 is skipped for quota without a planner allocation or launch. The earlier planner-free thread-1 regression still passes.

- [ ] **O12 (calibration).** Accumulate five measured samples/provider before replacing seeds; Cursor currently has no measured token-to-window factor (provisional 5 points/59k tokens). Claude starts at 10 points/251k tokens: F9's ~2 points/min across three sessions × 15 minutes/session. The five-hour factor is conservatively charged to weekly windows too. Connect runtime ledger launch/session ids to quota boundaries wherever callers currently omit them; unmatched boundaries remain uncalibrated rather than guessed.
- [ ] **OG1 (G1).** Reservation and live reading double-count what a running job already consumed (the live window rises as it spends). Rows record `window_before` so a later change can net it out; G1 stays conservative, as the rest of this plan does.
- [ ] **OG1b (G1).** Claude settles take the larger of transcript and `modelUsage` per field because the transcript misses advisor and server-tool usage (six saved planner runs, e.g. input 214,797 vs 60, output 30,297 vs 17,541). Output may also be undercounted by streaming snapshots; a per-field audit of transcript vs `modelUsage` over more runs would tell whether the transcript path (used mid-run and for hand-started sessions) needs a correction factor.
- [ ] **OG1c (G1).** `ledger_row` scans the whole `ledger.jsonl` twice per settle, under the admission flock the 6-second hook also takes. Fine for months of rows; add a settled-id index or tail scan before the ledger reaches tens of thousands.
- [ ] **OG3 (G1, blocked on PR #17).** When F11's per-window costs merge to `main`: merge it in, make leases carry real per-window costs (`window_pct` is uniform and unread today), have `capacity`, `view`, `queue_may_admit` and `reserve_bundle` subtract each window's own cost, and store the window reset ids in `window_before/after` so calibration can exclude rows that cross a reset. The watch's reset detection (a falling live reading) can then use those ids.
- [ ] **OG2 (G2).** Interactive leases have no heartbeat yet, the batch's `reserve_bundle` and the subagent hook bypass the wait queue, and `carve` (above) is undecided. All three belong to G2.
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

**Standalone C implementation (2026-10-28):** `bin/agent-budget`, `bin/fanout`, the lifecycle gate and installer wiring are implemented; see [operator notes](../agent-budget.md). Headless and interactive Superset launches queue without a model; interactive reservations are held by a detached terminal watcher until closure or TTL. Open conducting sessions have no budget lease; only admitted children reserve headroom. Hook errors, unknown quota and timeouts allow the call, and estimates are cached for ten minutes. Hook registration is owned by `install.sh` here (`settings.json` on this Mac is a regular file), so no dotfiles change is needed. Missing child metadata or a failed pre-tool spawn falls back to TTL release. Local leases cover this machine; other machines enter only through live quota readings. Polling waiters had no FIFO guarantee; G1 makes `run --wait` and `reserve --wait` FIFO with bounded bypass.

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
- **E2. Partial triage for headless planners — done.** (ai-tools, `automations/advance-roadmap/`). The planners stay read-only; the digest is built deterministically, with no model. Review follow-up hardens session attribution, secret redaction, failover capture and fingerprint settlement.
  - **When:** a planner ends `quota-paused`, `planning-timeout` or `error` without a valid fence.
  - **What:** run.sh builds `partial-triage-<repo>.md` in `$CODE_DIR/.advance-roadmap/` from the exact transcript session id (Claude result JSON or Codex stdout header); without an attributable id it records no research. It redacts transcript strings before rendering or metadata extraction, including truncated PEM blocks, Authorization and URL credentials, AWS credentials, and secret-named assignments. Combined failed attempts share the 20 KB bound evenly so each attempt header and later findings remain visible. Thread 1 alone owns the digest.
  - **Next run:** the planner prompt names the file: "Partial triage from `<stamp>` (ended `<how>`): reuse these findings; don't redo those reads unless the fingerprint says they changed." A valid completed planner for the targeted repo deletes it, including `blocked_no_item` results whose `repo` is null. Every saved verdict path after consumption, including batch cancellation, settles the digest to absent; its presence counts as a fingerprint change. It ages out after 72h. If later attempts have no attributable research, the existing findings and original age are retained with an unattributable-attempt note.
  - `ORCHESTRATOR.md` asks planners to state interim conclusions in plain text as they go, so the digest carries meaning.

### G. Universal reservation ledger (ai-tools)

Jake, 10-29: every agent run reserves tokens in one shared ledger, updates the reservation when its estimate changes, waits when there are no tokens to reserve, and clears the reservation by logging what it actually used plus the ticket, session ids and task. The log feeds future estimates. Hand-started interactive sessions record a reservation and warn when headroom is short; they are never blocked. Operator reference: [`docs/agent-budget.md`](../agent-budget.md#reservation-ledger).

**G1 (done): the ledger core in `lib/agent_budget.py`** (PR #16; review round 1 below).
- Leases store `reserved_tokens` (the whole-job estimate the row compares against), `held_tokens` (what counts against capacity) and the derived `pct`; pct-only leases still work. New CLI: `reserve`, `update` (exit 4 when an increase does not fit, `--force` records an over-run), `settle` (one `ledger.jsonl` row, idempotent on `lease_id`, archived reaped leases stay settle-able for 24h), `sweep`.
- `run` is reserve → exec → watch → settle. The lease is bound to the wrapper and its child and renewed every `min(60s, ttl/3)` independently of the watch. The watch holds only the *remaining* expected need of the current window period, capped at the role's p95, lowered when idle and rebased when the window resets; a rise that does not fit is applied and flagged over-run. It never signals the child, and learns sessions from `--session-out`, `$AGENT_BUDGET_SESSION_FILE`, `--result-file` or the single new transcript or rollout in its cwd (mid-run too). Usage is counted since the lease began.
- `waiters.json` is a FIFO under the admission flock with bounded bypass (3), no protection for a head that live usage, not reservations, is blocking, dead waiters reaped by pid, and costs recomputed every poll. `status` lists the waiting jobs.
- `estimates()` counts ledger rows from five per bucket, blends with older sources until twenty, and calibrates from each row's fresh before/after window pairs.
- Nested runs get **child leases** (own id, own tokens, through capacity); their tokens roll up into the parent's row. `--adopt` attaches to the parent explicitly; `settle`/`update`/`release` refuse an inherited lease without `--owner`.
- Not done in G1, by design: no launcher calls any of this yet, so no live run writes a row; `reserve_bundle` (batch) and the subagent hook's `acquire` still bypass the queue; leases carry one uniform `window_pct` and the ledger rows' `window_before/after` carry no reset ids until PR #17 (F11, per-window costs) merges.

**PR #16 review round 1 (10-29), all fixed in G1:** (1) adoption silently settled or released the parent and let unrelated jobs ride one reservation; now child leases through capacity with an ownership guard. (2) The watch grew a reservation without bound and never shrank or reset, so a long session reached pct 119 and stalled every headless admission; now remaining-need holds capped at p95. (3) The 60s ttl floor equalled the watch interval, so a running job's lease lapsed before its first tick; now heartbeats at ttl/3 and a 3x-interval floor. (4) Resumed sessions were charged their whole history. (5) `reserve --interactive` could traceback before its try. (6) A dead wrapper released the lease of a child that kept spending; leases now track the child pid and dead ones are swept. (7) Interactive leases counted toward the unknown-reading single-job limit. (8) Queued costs were frozen at enqueue. (9) Three ledger rows replaced all other sources, and a failed settle's `release` could turn the exit status into a traceback.

**G2 (next): wire every launcher.** One lease and one settle per *output*; each choke point below is where a model call starts and ends.

| Choke point | Change |
| --- | --- |
| `lib/claude_quota.py` `run()` (`claude-session run`; also `agent-run` for Claude via `lib/agent_profiles.py:239`) | Wrap the `Popen` in reserve → settle through `agent_budget.run`. It already sets `CLAUDE_QUOTA_MANAGED` and writes start/finish snapshots, so `launch_id` doubles as the settle task id. `--interactive` (child stays in the terminal's process group, fails open) when stdin is a TTY and `ADVANCE_ROADMAP_HEADLESS` is unset. |
| `lib/agent_profiles.py:243` (`os.execvpe` for Codex) | `exec` replaces the process, so nothing can settle. Switch to a supervised child (the same wrapper) and read the Codex session from its cwd rollout. |
| `automations/advance-roadmap/run.sh` `run_orchestrator_codex` (:454) / `run_orchestrator_claude` (:474), `worker.sh` `run_cursor` (:216) / `run_codex` (:234) / `run_claude` (:256), `review.sh` `run_reviewer` (:101), `lib/planapproval.py` `run_codex` (:203) / `run_claude` (:217) | Each already redirects stdout to `$out` and logs a `tokens.py` usage row. Prefix each call with `agent-budget run --result-file "$out" --session-out "$sess" --task … --ticket … --repo …`, so the settle reads the same numbers. Planner, worker and each review round are separate outputs, so each gets its own row (see "carve" below). |
| `automations/advance-roadmap/daily_batch.py` `reserve_bundle` calls (:221, :254) and the lane env (:353) | Export `AGENT_BUDGET_LEASE_ID(S)` for the lane's leases; route admission through `queue_may_admit` so a waiting lane gets head protection. |
| `lib/agent_fanout.py` `budget_command` (:41), `record_completion` (:253) | Add `--task/--ticket/--repo`, `--result-file job/result.json`, and `--interactive` for TUI terminals. Retire the `completions.jsonl` writer once `run` settles (G1 never pools sources, so the overlap is harmless in between). |
| `hooks/agent-budget-gate.py` `acquire` (PreToolUse) and the SubagentStop branch | Keep the lease (the hook cannot wait inside its 30s deadline). On SubagentStop read the child's transcript and `settle` a row instead of dropping the lease silently. |
| SessionStart / UserPromptSubmit / SessionEnd hooks for hand-started Claude sessions | `reserve --interactive --role interactive` (owner = the Claude process), heartbeat with `update --lease $ID --owner --session-id`, `settle --owner` from the transcript at the end. G1 does not heartbeat interactive leases, so they expire at their ttl. |
| Superset presets and shell shims for `claude`, `codex`, `agent` (the dotfiles repo owns the shims) | `agent-budget run --interactive --agent X -- real-cli "$@"` for TTY sessions; `--wait` for `-p` / `exec` / non-TTY. |
| `slackagent` `src/runtime-profile.js` | Already goes through `agent-run`, so it is covered by the first two rows; pass `--task slackagent:<runner>`. D4's `agent-usage.jsonl` writer becomes redundant. |
| `agent-budget sweep` (built in G1; also run by `status` and before every admission) | A crashed run never settles; its lease lands in `reaped.json` and is settled as `error` from its session ids. G2 adds a SessionStart hook call so a quiet machine still sweeps. |

**Adoption contract (for every G2 wrapper).**
- Env, kept by `child_env()`: `AGENT_BUDGET_LEASE_ID` (innermost lease), `AGENT_BUDGET_LEASES` (comma-separated `agent=lease_id`, one per provider in the chain), `AGENT_BUDGET_SESSION_FILE` (one session id per line; a hook or wrapper appends). `run` exports all three; the batch lane and fanout `_execute` must too.
- A nested `reserve` or `run` for the **same agent** as a live lease named there takes a **child lease** (`child_of`): its own id and tokens, admitted through capacity, waiting if asked. Plain `reserve` never returns the parent's id. When the child settles with its parent live it writes no row; its actual tokens, tasks and sessions roll up into the parent's row (`children`). `--adopt` attaches to the parent lease itself (raising its estimate through capacity); only the owner settles it. `settle`, `update` and `release` refuse a lease named in the environment unless `--owner`.
- A nested run for a **different agent** (a Cursor worker's Codex review) takes its own lease with `parent_lease_id` and settles its own row. A dead or already-settled id is ignored.
- Wrappers must pass the owner `--session-out` or `--result-file`, or write `$AGENT_BUDGET_SESSION_FILE`. A run that supplies none is settled from the cwd fallback, or logged with `actual_source: none` and zero tokens, which estimates ignore.
- **Open design for G2, "carve":** the batch reserves a whole lane (planner + worker + reviews) up front so admission happens before planning, yet Jake wants one row per output. Child leases roll nested calls into the lane's single row, and today they are admitted through capacity on top of the parent's reservation (conservative double counting). For outputs that deserve their own row, add a `--carve` mode: the child lease is funded from the parent's remaining reservation (no capacity check, parent reduced by the same tokens), settles its own row instead of rolling up, and returns unused tokens to the parent. Recommend building it first for review rounds.

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

- **C2 PR #13 review validation (2026-10-29):** each of C2R1–C2R3's new regressions failed against reviewed commit `924af03` before its fix, and all three pass after correction. The requested `uv run --with pytest python -m pytest automations/advance-roadmap/tests lib -q` run passed **401 tests and 108 subtests**. All four roadmap shell suites plus `hooks/tests/status-glyph-check.test.sh` passed under zsh (5/5). `git diff --check` and Python compilation passed. The failure-injection fixture waits for its fake planner to exit and verifies PID reaping, so no test-created model process remains. No live automation state, launchd, Linear, deployment clone or calibration/reset code was changed. `origin/main` was already an ancestor; no merge or history rewrite was needed.

- **C2R4 validation (2026-10-29):** the new pending-parent/later-thread regression fails on `5c9ba1b` and passes after restricting saved-plan admission to thread 1. The required Python command passed **402 tests and 108 subtests**; all four roadmap shell suites and the status-glyph suite passed under zsh (5/5). The previous thread-1 no-planner regression and healthy multi-thread case also passed in the targeted check. Python compilation and `git diff --check` passed. `origin/main`'s intervening planning-document changes merge cleanly (`git merge-tree`), so no merge commit is needed. Only isolated fake model processes and temporary account/automation state were used.

- **G1 validation (2026-10-29):** `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q`; new `LedgerTests` in `lib/test_agent_budget.py` cover token↔pct conversion and pct-only compatibility, reserve/update/settle with exactly one ledger row and idempotent settle (including a lease reaped before its settle), interactive reservations that never block or queue, FIFO and bounded-bypass fairness, the 80% watch extending or recording an over-run without killing the child, nested adoption (same and different agent), and estimates preferring ledger rows. All use a temp HOME and fake children.

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
