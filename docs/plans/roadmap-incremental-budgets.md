# Incremental roadmap work and budget visibility

## Status

The bounded execution implementation was deployed November 11, 2026. Its first monitored run was paused after finding conflicting workspace-prompt wording; the two interrupted sessions and partial branches were retained. A prompt fix and the newly requested quota-driven scheduler are in development. Shared quota ceilings are unchanged. This is separate from the active shadow scheduler rollout, which remains observation-only while quota-driven scheduling is enabled.

## Decisions

- Jake prefers incrementally finishing a ticket across runs. Resume the exact existing session by default; checkpoints must justify their extra cost compared with native resume.
- Do not require a model-written recap at every boundary or add model calls just to record progress. Persist session identity and recovery metadata mechanically; use existing artifacts and natural milestones for fallback evidence. Generate a compact handoff only when native resume is unavailable or demonstrably needs one.
- Budget the next bounded unit of progress, rather than requiring an entire ticket's implementation and future reviews to fit before anything starts.
- Jake allows considering usage up to 100%. Recommend a job-scoped, logged fallback when nothing fits, with one bounded attempt and existing reservations accounted for; do not silently raise every job's shared limits.
- Replace the twice-daily production schedule with quota-driven continuation. Jake prefers up to two concurrent workers. Persist a provisional reset-based wake before each unit, recheck real account usage and reservations on wake, and continue immediately when headroom remains. Actual provider and longer-window limits still apply; a 60-second launchd tick provides wake granularity without model calls.

## Findings

- Today's resumed morning batch skipped all 24 repos. Its saved Claude reading was 7% five-hour usage; the worker estimate was 82.12 percentage points. Codex had six points of five-hour headroom.
- `daily_batch.py` still calls `implementation_headroom()` with a whole worker plus required review, and the streaming path also reserves a whole job. Existing checkpoints preserve state but do not make admission incremental or enforce a graceful yield.
- `agent_budget.estimates()` combines historical provider/role token quantiles with provider-level token-to-quota calibration. The 82% is a forecast from past sessions, not a measurement of the next ticket or remaining phase. Model/effort/phase/resume cohorts are not distinguished in that estimate.
- Claude calibration pairs account-level start/finish readings with a session's tokens. Overlapping unrelated sessions can contaminate the quota delta; sample quality must be visible and tested before changing the prediction.
- Installed CLI help confirms explicit session resume for both configured harnesses. The launcher currently starts fresh sessions; storing IDs alone is not native session continuation. Claude's CLI dollar-budget option describes API-call spending and must not be treated as a subscription quota percentage control.
- The local snapshot replay on November 11 found 22 Claude calibration samples overlapping another recorded session and one with a possibly open session. Missing finish events do not prove the other session was still active, and no overlap in local records proves account isolation. Sample quality is now reported without changing the estimator pending cohort validation.
- Replaying the saved Claude calibration without flagged samples leaves fewer than five samples: the five-hour factor falls from 0.0002528 to the provisional 0.00003984 seed (about 6.3× lower), and there are no historical model/phase/resume cohorts. Neither number proves the next unit's cost. Preserve the existing conservative factor while collecting comparable new observations and report the sample quality and sparse cohorts alongside forecasts.
- Recovery is still attempted first, but an unfinished item no longer holds unrelated repositories behind a batch-wide barrier.
- The development implementation captures exact sessions from provider streams, validates work identity, account, workspace, branch and HEAD before resuming, and uses a bounded watchdog plus an independent orphan guard. Provider-switch and missing-session fallback, quota stop, review reservation, and forced-stop recovery have fake-provider coverage. A finite 15-minute watchdog and approximately ten-point forecast are initial trial settings, not hard subscription guarantees.
- The first live trial admitted two Codex implementation units with 6.48-point forecasts each and no reviewer reservation; both session IDs were recorded within 0.35 seconds. We stopped them after roughly 69 seconds to repair a conflicting workspace instruction. Their account-wide five-hour reading remained at 7%, which is rounded and confounded by overlapping work; the partial worker records show 43,709 and 52,711 newly processed tokens, with 230,272 and 417,408 cache-read tokens respectively. Both saved sessions still match their assigned workspace and HEAD.
- The workspace prompt now explicitly keeps implementation-only units unmerged, and forced stops cannot be misclassified as authentication failures. The quota-driven controller has fake tests for two concurrent assignments, continuous refill, fresh quota checks, reset wake-up, and an unrelated repository proceeding when another cannot fit. It records a provisional reset before starting work, then updates the wake after each cycle; the launch agent checks due time every minute without model calls.
- On the second monitored trial, ai-tools resumed its exact Codex session and yielded at its quota target; typey.site fell back to Claude when its Codex reservation did not fit, then committed an unmerged implementation milestone. Continuous quota polling elicited HTTP 429 from Claude's usage endpoint, so dispatch was paused again. Usage probes now share a 60-second successful cache and exponential backoff on 429; continuous batch budget refreshes are limited to once per 15 seconds. Stale/unknown quota never authorizes continuous dispatch.
- After deploying the backoff, the quota endpoint recovered and two Claude workers ran concurrently. typey.site resumed its owned native session and yielded to review; ai-tools used provider fallback and yielded with implementation remaining. Both exited normally, and the controller admitted the next ai-tools unit. These observations validate recovery and refill, but do not yet establish a reliable native-versus-fallback cost comparison.
- The dashboard now records account-wide observed deltas with isolation caveats and compares matching provider/model/effort/phase continuation cohorts. The CLIs do not separately attribute all reconstruction, compaction or handoff tokens; whole-unit tokens/time include those costs, and unavailable attribution is recorded as unknown rather than zero. Meaningful native-versus-fallback comparisons require future natural continuations.

## Approach

### Visibility to add

1. Admission decision events: work/attempt/chunk ID; provider, model, effort and phase; live quota and timestamp; other reservations; proposed cost; applicable normal/override ceiling; selected route or exact rejection; next eligibility time. Separate a forecast rejection from an actual provider limit.
2. Forecast quality: predicted versus observed tokens and quota per phase/chunk, sample count, age, model match, quantile and confidence/quality flags. Mark account deltas with overlapping sessions as confounded; do not call them exact job consumption.
3. Continuation: persistent session ID, resumed-from session/attempt, native resume versus checkpoint fallback, checkpoint age, branch/HEAD, next action and reason for yielding. Capture session identity at launch/first stream event so interruption before the final result does not lose it. Measure checkpoint generation tokens/time, resume input and cache-read tokens, compaction/reconstruction cost, and time to useful progress. Compare the complete continuation cost against a native-resume baseline; do not assume resume or checkpoints are free.
4. Useful progress: implementation milestone, tests advanced, findings addressed, review/merge/deploy phase completed, active model time versus waiting, and reconstruction/replanning tokens. Avoid rewarding lines changed or raw call counts as progress.
5. Starvation and exceptions: consecutive zero-admission runs, oldest eligible work, headroom left unused, idle-override eligibility/use, stop/overrun reason and measured outcome after an override.

### Execution changes

- Make admission phase-aware. A review-only continuation must not reserve another whole implementation worker. Future review capacity need not be available before an implementation chunk can make durable, unmerged progress.
- Give each invocation one concrete milestone and a small quota/time target. An initial trial could target roughly ten percentage points or fifteen minutes, but measure boundary overhead before fixing a cadence. Prefer natural phase or milestone boundaries and allow larger chunks when frequent interruption costs more. Targets are forecasts, not guaranteed hard subscription limits.
- Enforce the lifecycle: stream/capture live session identity and progress, monitor cached/shared usage, then yield at a safe boundary. Save available session/artifact pointers without asking the model to repeat its context. A watchdog is the fallback; forced termination retains the native session and last available recovery artifacts and is recorded distinctly from a successful yield. Request an additional handoff only when the fallback needs information those sources do not contain.
- Persist one stable work item across chunks. Resume its exact owned session after checking provider/auth compatibility, ticket, branch/workspace and current HEAD. Never select the globally most recent conversation. Provider switches or unsupported resume use the bounded checkpoint packet and log the reconstruction cost.
- Preserve partial work on the assigned branch. Each chunk may end without a merge. Final verification/review and merge remain required before marking the ticket shipped; old evidence is reused only when its revision and requirements still match.
- If normal admission selects nothing, consider one small continuation under a fresh, explicit job-only ceiling up to 100%, after charging other active reservations. Record why it was allowed. Do not authorize an unbounded whole-ticket worker just by shrinking its reservation number.

## Open threads

- [x] Identify the whole-ticket admission gap and the distinction between saved checkpoints and native resume.
- [x] Verify installed harnesses advertise explicit resume, without starting models.
- [x] Record proposed metrics and the scoped 100% fallback.
- [x] Incorporate Jake's cost constraint: native resume first, no mandatory recap per chunk, and measured checkpoint versus resume overhead.
- [x] Implement and validate phase/chunk admission, incremental usage collection, cooperative yield/watchdog behavior and native session adapters with fake providers.
- [x] Validate estimator cohorts and concurrent-session contamination using saved data; keep the conservative factor until comparable samples justify a change.
- [ ] Compare native resume alone with sparse fallback checkpoints on comparable interrupted work; include generation, cached input, compaction/reconstruction and useful-progress latency in total cost. Choose chunk boundaries from the results.
- [x] Replay November 10's zero-admission case: admit one bounded unit without raising global limits or claiming the whole ticket fits.
- [x] Test crash/limit/provider-switch recovery, unchanged evidence, merged-branch reconciliation and explicit reporting of forced versus clean yields with fake providers.
- [ ] Fix the conflicting workspace instruction found in the first live run; deploy and resume the captured sessions under live monitoring.
- [ ] Implement, validate and enable the user-requested two-worker quota-driven schedule, retaining the separate shadow observations and replacing the twice-daily launch agent.
