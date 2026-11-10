# Incremental roadmap work and budget visibility

## Status

Proposed follow-up, November 10, 2026. Current runtime limits are unchanged. This is separate from the active scheduler rollout: its existing shadow/pilot validation must not be mistaken for implementation of the smaller work units described here.

## Decisions

- Jake prefers incrementally finishing a ticket across runs. Resume the exact existing session by default; checkpoints must justify their extra cost compared with native resume.
- Do not require a model-written recap at every boundary or add model calls just to record progress. Persist session identity and recovery metadata mechanically; use existing artifacts and natural milestones for fallback evidence. Generate a compact handoff only when native resume is unavailable or demonstrably needs one.
- Budget the next bounded unit of progress, rather than requiring an entire ticket's implementation and future reviews to fit before anything starts.
- Jake allows considering usage up to 100%. Recommend a job-scoped, logged fallback when nothing fits, with one bounded attempt and existing reservations accounted for; do not silently raise every job's shared limits.

## Findings

- Today's resumed morning batch skipped all 24 repos. Its saved Claude reading was 7% five-hour usage; the worker estimate was 82.12 percentage points. Codex had six points of five-hour headroom.
- `daily_batch.py` still calls `implementation_headroom()` with a whole worker plus required review, and the streaming path also reserves a whole job. Existing checkpoints preserve state but do not make admission incremental or enforce a graceful yield.
- `agent_budget.estimates()` combines historical provider/role token quantiles with provider-level token-to-quota calibration. The 82% is a forecast from past sessions, not a measurement of the next ticket or remaining phase. Model/effort/phase/resume cohorts are not distinguished in that estimate.
- Claude calibration pairs account-level start/finish readings with a session's tokens. Overlapping unrelated sessions can contaminate the quota delta; sample quality must be visible and tested before changing the prediction.
- Installed CLI help confirms explicit session resume for both configured harnesses. The launcher currently starts fresh sessions; storing IDs alone is not native session continuation. Claude's CLI dollar-budget option describes API-call spending and must not be treated as a subscription quota percentage control.

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
- [ ] Implement and validate phase/chunk admission, incremental usage collection, cooperative yield/watchdog behavior and native session adapters.
- [ ] Validate estimator cohorts and concurrent-session contamination using saved data.
- [ ] Compare native resume alone with sparse fallback checkpoints on comparable interrupted work; include generation, cached input, compaction/reconstruction and useful-progress latency in total cost. Choose chunk boundaries from the results.
- [ ] Replay today's zero-admission case: admit one bounded continuation without raising global limits or claiming the whole ticket fits.
- [ ] Test crash/limit/provider-switch recovery, unchanged evidence, zero duplicate merges and explicit reporting of forced versus clean yields.
