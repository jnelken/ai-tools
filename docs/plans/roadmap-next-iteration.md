# Next iteration of advance-roadmap

## Status

Ready for implementation. Evidence review and policy decisions are complete. The active 10:30 AM / 3:30 PM Eastern schedule remains in place for the first rollout stage.

## Decisions

- Jake's corrected preference: **quota efficiency**, particularly fewer repeated planning calls. The earlier throughput selection was accidental and is superseded.
- Recommend a persistent queue with bounded, event-driven continuation. Keep batch summaries and caps; decouple work lifetime from the scheduler invocation.
- Confirmed by Jake: automatically resume interrupted work before starting new work. Human-blocked items are a distinct state: park them with one specific question and let unrelated repos proceed. Ordinary test failures, merge conflicts and implementation problems remain agent-owned; interruption itself does not require human input.
- Reuse the existing budget leases, worker launcher, review and merge checks. Avoid a second independent scheduling authority.

## Findings

### Observed baseline

The saved finalized records for **November 3–9, 2026** contain 153 distinct run stamps:

| Outcome | Records |
| --- | ---: |
| No actionable item | 75 |
| Quota skipped | 56 |
| Shipped | 3 |
| Incomplete | 5 |
| Error | 7 |
| Superset skipped | 3 |
| Unchanged skipped | 4 |

89 records have token accounting. Their newly processed tokens total 5,894,204 for planning, 2,146,297 for review and 1,100,181 for workers. Planning for the 75 no-work results alone accounts for **4,732,772** newly processed tokens: about 80% of recorded planning tokens. All 75 no-work results have token accounting. New tokens exclude cache reads and are not a measure of Claude quota or comparable dollar costs across providers.

Using batch assignments, 21 repos produced no-work results; 14 did so four times and one five times. This supports investigating repeat triage, but does not prove that all those calls were avoidable: their inputs may have changed.

Two of the seven batch manifests were left `preparing` or `failed`. Five finalized records say the controller exited before worker launch and retained the pending plan. A disk-full failure and an earlier process-check timeout also appear in the wider October 27–November 9 manifests. Durable reconciliation matters independently of cadence.

**Attribution limitation:** 69 of the 75 no-work records disagree with their batch-assigned repo. Run records prefer the worker/planner-reported repo, and no-work results can omit a repo. Keep assigned repo, reported repo and work identity separately; use assignments when analyzing repo repetition. Do not treat an apparent concentration in the fallback repo as actual work distribution.

The period predates today's schedule and recovery changes and spans earlier fixes. There is no live outcome evidence yet for today's twice-daily version. No causal savings or throughput projection is justified from these totals alone.

Sources: `~/.claude/automations/advance-roadmap/runs.jsonl`, `batches/*/batch.json` and per-run artifacts. Reproduce aggregate figures without model calls or network access:

```sh
python3 docs/plans/roadmap-next-iteration-evidence.py --since 20261103 --until 20261110
```

### Existing continuity and its gaps

- `daily_batch.py` queues repos and plans the whole wave before implementation. Ready work can wait on unrelated planning. This is a code-level observation; start-to-release elapsed time also includes the actual planner and cannot quantify barrier waste by itself.
- `lib/pendingplan.py` preserves a dispatch brief and branch. Reuse defaults to 36 hours and two attempts; `run.sh` clears rejected saved plans and may plan afresh. Expiry can erase a useful dispatch decision even when code remains on its branch.
- `worker.sh` records session IDs but launches fresh model sessions. Resume uses the existing branch and assignment, rather than reattaching the prior conversation.
- `WORKER.md` writes repo notes at Step 8, after the work. A hard interruption can precede that write. `lib/partialtriage.py` preserves bounded planner findings, but does not provide a structured checkpoint for every worker/review/merge phase.
- `lib/verdict.py` already caches no-work decisions. Its fingerprint includes repo state, scoped issues, docs and other inputs. `lib/linearscope.py` includes unlabeled issues in every repo's scope, so such issues can invalidate many verdicts. Recent artifacts show shared issue IDs repeatedly appearing in changed-input lists. Validate relevance before narrowing these dependencies.
- Global Claude start/finish snapshots exist, but other sessions can overlap. Quota deltas are evidence of concurrent consumption, not exact attribution to this job.

## Approach

### 1. Make each work item survive scheduler restarts

Use a local transactional state store, preferably SQLite, outside Git. Key work by repo identity plus ticket/item identity; attempts and scheduler invocations are children of that work. Persist the branch/workspace, brief, acceptance criteria, dependency fingerprint, provider session IDs, review findings, test evidence and next action. Explicit phases: planning, ready, implementing, reviewing, shipping, verifying, done; waiting states carry a reason and next eligibility condition.

On startup, reconcile saved state against processes, budget leases, workspace/branch state, reviews and PR/merge evidence. Adopt live workers; resume dead ones from the last valid phase. Mark already-completed external actions complete rather than repeating them. Expiry triggers validation or escalation, never silent loss of work history. Imports from pending plans/manifests must be idempotent and retain their source artifacts.

### 2. Save a small continuation packet during work

Write a bounded packet at phase transitions and after durable milestones: task and scope, completed steps, exact branch/HEAD, dirty changes, tests and their revision, unresolved findings, next command/action, artifact pointers and session IDs. Use atomic writes; preserve previous checkpoints. The controller records facts independently of the model, so a killed model still leaves the last durable state.

Keep the packet small (initial target at most 8 KB of summary plus artifact references). Store large logs separately. Use native provider-session resume only after capability and workspace validation; checkpoint-based continuation must also work after provider switches. Do not blindly replay full transcripts. Revalidate tests/reviews after relevant code changes; never assume an old checkpoint proves current correctness.

### 3. Check cheaply; invoke a planner only when needed

Use one lightweight scheduled controller, initially every 15 minutes, with no model calls on idle ticks. It reconciles state and computes eligibility from local changes and cached external snapshots with bounded refresh intervals. Known quota reset times set `not_before`; completion and input changes can request an earlier tick. Debounce events and retain periodic polling as a fallback.

Priority: finish dispatched/review/merge work first; then ready plans; then materially changed or newly eligible repos. No-work and human-blocked decisions store their relevant dependencies and wake conditions. A comment generated by the job, unrelated ticket edit or budget refresh alone must not trigger full repo triage. Unknown/unlabeled issues need a central classification path plus explicit owner correction rather than being silently dropped.

Use a 24-hour lightweight safety audit for missed events, preserving the current 72-hour full-triage safety cap initially. Measure both in shadow mode before tuning. Neither audit silently discards saved work.

### 4. Admit a small amount of work that can actually finish

Use the existing shared reservations for planning, implementation **and review**, with headroom for interactive usage. Keep the current total concurrency cap of five across all phases. Begin with one active item and at most one ready plan per repo; no speculative second-thread planning in the first version. Limit new planning and daily consumption separately so frequent ticks cannot increase spending unnoticed.

Remove the all-repo barrier: an eligible item may advance when its dependencies, review budget and capacity are ready. Preserve same-repo serialization and cross-repo dependencies. Check quota before each phase, use measured costs as estimates, and checkpoint/hold if capacity falls. Estimates cannot guarantee a worker never hits a quota limit. No model-based quota polling or automatic retry loop inside an exhausted session.

### 5. Make waiting and continuation visible

Retain the existing roadmap dashboard as the interface: show each item's phase, last durable checkpoint, next action, waiting reason, eligible-after time and linked attempts. Distinguish resumable failures from human decisions and infra outages. Coalesce notifications into scheduled summaries, with a single actionable notice for a new blocker. Preserve 10:30 / 15:30 summary times unless separately changed.

### Rollout and evaluation

1. **Baseline and shadow decisions:** normalize assigned identity; join attempts to work; distinguish model time, waiting and missing accounting. Record why each repeat planner invocation would be admitted or skipped. Run replay against saved artifacts without models, merges, messages or external writes.
2. **Durable continuation under the current schedule:** import pending work, checkpoint phases and reconcile restarts while keeping current launch times. Validate that work is preserved before changing cadence.
3. **Dynamic shadow controller:** run the eligibility policy for seven days without dispatch. Compare its decisions with the current scheduler; investigate false skips and stale plans. Current policy remains authoritative.
4. **Limited live trial:** enable one or two repos with a single new controller authority and current-or-lower budget limits. Then expand only after a comparable seven-day window meets the criteria below. Roll back dispatch policy while retaining work records/checkpoints; never launch legacy and new workers simultaneously.

Acceptance criteria:

- Restart, timeout, sleep/wake, provider limit, disk-write failure and late-worker completion never create duplicate dispatch/merge or silently discard an item.
- A valid saved dispatch reaches the next phase without a fresh planning model call; a changed dependency explains any replanning.
- Unchanged no-work/human-blocked inputs cause zero planning model calls between safety audits, including after controller restart.
- Target at least 50% fewer no-work planning tokens over a comparable week, with input-change and accounting coverage reported. This is a trial target, not a predicted saving.
- Track planning tokens per completed work item and total provider consumption, without reducing shipped eligible work or bypassing review to improve the ratio.
- Scripted crash/restart tests recover each phase; external changes invalidate stale test/review evidence. Checkpoint fallback works even when the prior provider session cannot resume.
- Dashboard always explains why an item is waiting and what will wake it. Idle ticks use zero model tokens.

## Open threads

- [x] Review finalized outcomes, accounting coverage and batch failures; reproduction script included.
- [x] Trace dispatch reuse, worker sessions, notes and verdict invalidation.
- [x] Record corrected objective: quota efficiency.
- [x] Jake confirmed: interrupted work resumes automatically first; human-blocked work is parked while unrelated repos continue. These are distinct states.
- [x] No further user decisions block implementation. Defaults: current shared quota limits, total concurrency cap five, 15-minute checks only after shadow validation, bounded checkpoints and the staged rollout above.
- [ ] Execute the staged rollout, beginning with durable recovery under the existing schedule.
