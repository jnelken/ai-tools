# Shared agent budget

`agent-budget` and `fanout` are installed as maintained-source links in
`~/.local/bin`. They also run directly from this checkout's `bin/` directory.
They use Python's standard library; only `fanout spawn` needs the local Superset CLI.

```sh
agent-budget status --json
agent-budget allow --provider codex --role planner --want 5
agent-budget run --wait --provider codex --role planner -- codex exec '…'
fanout spawn --label investigate --provider codex --role planner --prompt-file /path/to/prompt
fanout spawn --label implement --provider claude --role worker --prompt-file /path/to/prompt --interactive
fanout status
fanout collect <id>
```

Workspace selection prefers `--workspace ID`, then `SUPERSET_WORKSPACE_ID`, then
`superset workspaces list --local --json`. Fallback matches the cwd's Git toplevel
or nearest containing worktree path (resolving symlinks and ignoring archived
workspaces); no match reports a clear `--workspace` hint. `--dry-run` on
`spawn` prints the exact Superset argv and planned model argv without creating
state, a terminal, or a model call. Headless Claude emits a JSON result; Codex
emits its JSON event stream. `collect` returns the saved output as a string so
both formats survive intact. Failed CLI launches have a nonzero `exit` file.
A result without an `exit` file is still pending. Interactive results contain
the Superset launch response; completion follows terminal closure, not hook state.

Leases live in `~/.claude/state/agent-budget/leases.json`. All admissions, releases,
and reads use a separate stable flock file; writes replace the JSON atomically.
Each lease stores its owner PID and process start identity (`ps lstart` on macOS;
`/proc` start ticks on Linux). Reads reap dead/reused PIDs and expired TTLs. Old
PID-only leases are retired because their owner incarnation cannot be verified.
Admitted children (fanout terminals, batch jobs, and gated subagents) hold leases, and so does
anything that calls `reserve` or `run` (see the ledger below). A hand-started interactive session
that has not called `reserve` holds none: its consumed usage is in the live reading, and the 30%
above the five-hour 70% cap is reserved for interactive use. One that has, with `--interactive`,
holds a lease that counts against headless capacity like any other. `run` owns its lease, forwards
TERM/INT/HUP to the command's process group, preserves exit status, and settles in `finally`.
Waiting shells back off to 30 seconds and consume no model tokens. They admit in arrival order
(see Fair waiting).

Caps come from roadmap `usage.py`: 70% over five hours, 80% weekly, and 100% for
Cursor's selected pool, including that module's environment overrides. Claude
uses the live OAuth quota helper. Other providers use fresh saved state or the
existing probes. Fresh saved windows whose reset has passed read as zero, using
roadmap reset semantics. Readings older than 15 minutes are unknown; an unknown window
permits at most one active lease for fanout/batch admission, subject to any known
window. The in-process hook allows calls when no usable reading exists.
Reservations are conservatively charged to every window (and Cursor pool).

Role costs use nearest-rank p80 **newly processed** tokens (input + cache writes +
output, excluding cache reads), matching the roadmap ledger. Multiple models in
a run's role are summed. Inputs are roadmap runs, the optional shared usage ledger,
subagent transcripts, and completed headless fanouts. Streaming transcript rows
are deduplicated by API message id; per-field maxima retain the final usage.
Adjacent metadata preserves agent type while all child types share the subagent role.

Conversion factors remain seeded until five positive measurements exist for a
provider, then use p80 percentage points/token (token-weighted account intervals for Codex). Paired Claude snapshots match
launch/session ids to per-session ledger usage, preserving review-round boundaries.
They never pair a role's whole-job total with each session. Ambiguous multi-session
rows are skipped. Transcript calibration counts only assistant messages within the
start/finish interval, so resuming a session does not charge its earlier history.
Reset-crossing,
stale, zero and negative deltas are excluded. Codex merges cumulative new-token deltas from concurrent rollouts against one
account-wide quota delta within a reset window and active interval (15-minute gap
ends an interval). Flat integer-quota intervals contribute tokens; stale parallel
readings use a high-water mark. Each interval is weighted by its token exposure
when taking p80, so short intervals crossing rounded boundaries cannot dominate.
The sanitized 10-27 rollout fixture yields 29 points/~770k tokens (3.8e-5/token).
Claude's account-wide samples do not support separate model factors: concurrent
Sonnet, Opus and advisor work cannot be attributed from quota boundaries alone. Account-wide Claude deltas
can include concurrent work, so their estimates deliberately err high. Samples
are deduplicated in `samples.jsonl`; generated samples are rebuilt to repair old
denominators after an upgrade. Parsed files and bounded transcript intervals are
cached by parser version, size and mtime. Only the five-hour factor is calibrated and is conservatively applied to weekly
windows too. Cursor remains provisional until measured samples are available.

Seeds: Codex 3 points per 70k-token planner (10-27); Claude 10 points per
251k tokens, from F9's 2 points/minute across three sessions, assuming a
15-minute session. Cursor provisionally reserves 5 points per 59k tokens.
TTL defaults to 30 minutes; observed p90 duration gets a 50% margin with a
one-minute floor. Long sessions may outlive their reservation; this is admission
control, not a provider-enforced spend limit or cross-machine lease service. TTLs
use wall-clock time across processes; a large clock jump can change expiry timing.
This known limit is unchanged.

## Reservation ledger

Every agent run reserves **tokens**, revises the reservation if its estimate changes, and settles
it with what the run actually used. A reservation stores `reserved_tokens` (new input + cache
writes + output; cache reads are tracked apart) and the derived `pct` per window at the provider's
calibrated factor, so capacity still compares percentage points. Leases from before the ledger
carry only `pct`; they keep working and convert at the current factor. A lease also carries
`agent` (claude/codex/cursor), `model`, `role`, `kind`, `task`, `linear_id`, `repo`,
`session_ids[]`, `interactive`, `created`/`updated`, the owner pid with its start time, `ttl`, and
the window readings from reservation time. `updated` is a heartbeat: a lease expires `ttl`
seconds after `max(created, updated)`.

```sh
id=$(agent-budget reserve --agent codex --role worker --task "review PR #12" --ticket DEV-7 --repo ai-tools --wait --pid $$)
agent-budget update --lease $id --add-tokens 40000 --session-id $SID     # exit 4 if it would not fit
agent-budget settle --lease $id --outcome ok --session-id $SID           # actual read from the session
agent-budget run --agent codex --role worker --task "review PR #12" --ticket DEV-7 \
    --session-out $f --result-file $out -- codex exec ...               # reserve, exec, watch, settle
```

- **`reserve`** takes `--tokens N` or `--role R` (the ledger-backed estimate), then `--agent`,
  `--model`, `--task` (required), `--ticket`, `--repo`, `--kind`, `--pid`, `--ttl`, `--wait`,
  `--interactive`, `--json`. It prints the lease id (`--json`: the lease). The owner defaults to the
  calling process, past transient shells (never pid 1), and the lease dies with that owner, so a
  reservation made from a CLI that exits is not lost. A shell script should pass `--pid $$`: a
  `$(...)` substitution's parent is a throwaway subshell. Without `--wait`, no headroom exits 3.
- **`--interactive`** never blocks and never queues. It records the reservation, prints one stderr
  line when headroom is short (the lease is marked `over_capacity`), and returns 0, even if the
  ledger itself fails. Only headless, launchd and fanout agents wait. Nothing heartbeats an
  interactive lease yet (G2), so it expires at its ttl unless `update` is called.
- **`update`** takes `--tokens N` or `--add-tokens N` and `--session-id S`. An increase that does not
  fit leaves the lease unchanged and exits 4 (`would_exceed`); `--force` applies it anyway and marks
  the lease `over_run`. It never signals a process; the caller decides. Shrinking always fits, and
  session ids and the heartbeat apply either way.
- **`settle`** takes the actual usage from `--input/--cache-write/--cache-read/--output`, or
  `--actual-json F` (token counts, a Claude/Cursor result JSON, or a Codex usage object), or, with
  neither, from every session id on the lease: Claude transcripts (subagents included), Codex
  rollouts, then `--result-file`. It appends **one** row to `~/.claude/state/agent-budget/ledger.jsonl`
  and releases the lease. `--outcome` is `ok|error|limit|timeout|cancelled`. Settling again returns
  `already_settled` and writes nothing; a lease reaped meanwhile is archived in `reaped.json` for 24
  hours, so a late settle still logs its row. `release` drops a lease with no row; use it only when
  nothing ran.
- **Ledger row:** `lease_id, agent, model, role, kind, task, linear_id, repo, session_ids,
  parent_lease_id, interactive, reserved_tokens, initial_tokens, actual{input, cache_write,
  cache_read, output}, actual_total, actual_source, window_before/after (+ observed_before/after),
  started_at, at, duration_s, outcome, over_run`. `over_run` means the run used more than its final
  reservation, or an extension was forced because it did not fit. One row per completed output (one
  review of one PR), never per turn.
- **`run`** reserves, starts the command in its own process group with `AGENT_BUDGET_LEASE_ID`,
  `AGENT_BUDGET_LEASES` and `AGENT_BUDGET_SESSION_FILE` exported, and watches it. It never touches
  the command's stdout or stdin. Session ids come from `--session-out`, the session file (one id
  per line, for hooks and wrappers), the stdout header or Codex events in `--result-file`, and, last,
  the only transcript or rollout the command started in its cwd (ambiguity yields nothing). For
  Claude, where the result JSON's `modelUsage` and the transcript both cover a session, the larger
  figure per field wins: `modelUsage` also counts advisor and other server-tool models (saved
  planner runs show input 214,797 against 60 in the transcript). Every
  `AGENT_BUDGET_WATCH_INTERVAL` seconds (default 60) it heartbeats the lease, re-reads the usage
  and, past 80% of the reservation, extends it to the larger of 1.5x and 1.25x the usage. An
  extension that does not fit is applied and flagged over-run, with one stderr line; the child is
  never interrupted. A command that never started logs no row. With `--interactive` the command
  keeps the terminal's process group (a TUI needs its controlling terminal, resizes and job
  control), Ctrl-C is the command's own and is not logged as a cancellation, and any failure to
  reserve prints one stderr line and runs the command unreserved.

**Nested runs adopt their parent's lease.** If `AGENT_BUDGET_LEASE_ID` (or the matching entry of
`AGENT_BUDGET_LEASES`, `agent=id` pairs) names a live lease for the same agent, a nested `reserve`
or `run` reserves nothing: it raises the lease's estimate to its own `--tokens` if larger, adds its
session ids, fills metadata the parent left empty, and runs. It never settles or releases; the
owner's settle reads every session id on the lease, so the one row covers the nested work. A nested
run for a different agent (a Cursor job's Codex review) takes its own lease, records
`parent_lease_id`, and settles its own row. A dead or settled lease id is ignored.

**Estimates.** `estimates()` prefers ledger rows (`outcome: ok`, measured) for an (agent, role)
bucket once it has three, and a `kinds` entry per task kind with three. A bucket with fewer uses the
older sources (runs.jsonl, agent-usage.jsonl, transcripts, completed fanouts), or its thin ledger
rows when there is nothing else. Sources are never pooled in one percentile. Each settled row whose
window readings before and after are both fresh and rising contributes a calibration sample
(source `ledger`, rebuilt on every history scan). Account-wide deltas include concurrent work, so
they err high, as with the quota-snapshot samples.

**Fair waiting.** `run --wait` and `reserve --wait` queue in `waiters.json` under the same flock
as admission. A waiter keeps its place for as long as its process lives, and admits only when it fits and no earlier waiter for the provider fits. A
later job may overtake a head that does not fit, up to 3 times; after that the head is protected
until releases admit it. A head blocked by live usage rather than by reservations protects nothing,
so smaller jobs never starve behind it. Dead waiters are dropped by pid and start time.
`agent-budget status` lists the waiting jobs with their size, wait and how often they were
overtaken. The batch's `reserve_bundle` and the subagent hook's `acquire` still bypass the queue.

The daily roadmap batch uses the same lease file. Each poll refreshes usage once
and admits at most `--lanes` planners, each reserving its planner cost plus its
repo's historical share of `dispatch_worker` × (worker + required reviews).
Without planning history the dispatch share is 0.3. Pending dispatch/resume plans
come first and reserve a full implementation; when available they also reserve a
planner fallback in case the saved plan cannot be reused. Only thread 1 can use
that saved plan without planner headroom; later threads always reserve a planner.
Changed verdicts precede repos with no verdict. Carried verdicts still pass through `run.sh`'s unchanged gate.

At the planning barrier the planner portion is released. Before each implementation
wave, fresh headroom must support an atomic conversion to full worker + review
leases on the first fitting worker in `pick-worker-chain` order. Cross-provider
reviews remain on a different provider; the default still requires Cursor then
Codex. Calls stay on the reserved route; a later gate/limit stops them without
spending an unleased fallback. Plan approvers reserve planner cost too. Leases
follow the lane PID and release at completion/cancel/timeout (or normal reaping).

A ready job held behind running jobs retries after they finish. If fractional
expectations fill the barrier and nothing can start, the lowest priority held
plan is deferred, freeing its expectation before retrying higher priority plans.
This finite queue never waits all day for a quota reset. Saved plans and verdicts
retain the existing cancellation semantics.

`batch.json.budget` reports provider windows' live, reserved and headroom figures,
plus held/skipped repos and reasons. Both Slack posting paths append one budget
hold line. The bot path uses a local adapter around slackagent's existing formatter
and post helper; it needs no slackagent deployment. `daily_batch.py --dry-run`
prints verdict checks, saved live headroom, and the number of lanes that would be
admitted. It uses saved Linear/deploy/quota snapshots, labels those verdict checks
as saved, and creates no leases, batch state, probes or external requests.

The installer registers `agent-budget-gate.py` for PreToolUse (Agent/Workflow) and
SubagentStop, preserving existing settings and removing its retired SessionStart/End
registrations. Unknown readings, exceptions (including malformed input/state and
filesystem errors), and timeout all fail open with no denial output. Errors emit
one stderr line. An internal six-second deadline writes a note and calls
`os._exit(0)` directly, so broad exception handlers cannot swallow the deadline
and continue into a denial or another lock wait. SIGTERM uses the same exit path.
Estimates are persisted under the budget directory and rebuilt at most every ten minutes. Each admission reads
quota once through `claude_quota`'s shared five-second cache. SubagentStop resolves
`toolUseId` from the child's `.meta.json` and releases that exact reservation;
missing metadata or a failed spawn leaves a bounded TTL reservation. Interactive
fanout reservations are held only by their terminal watcher.

Nested headless children retain workspace/account attribution but drop
`SUPERSET_AGENT_*`, `SUPERSET_HOST_AGENT_*`, `SUPERSET_HOOK_*`,
`SUPERSET_TERMINAL_ID`, `SUPERSET_TAB_ID`, `SUPERSET_PANE_ID`, `SUPERSET_PORT`,
`SUPERSET_DEBUG_HOOKS`, and Claude's parent session markers. Superset's installed
`notify.sh` v20 admits either terminal **or tab** identity at line 18 and routes
the v1 fallback with pane/tab IDs (lines 276–314), so all three must be removed.
This prevents their lifecycle hooks from being reported as the enclosing terminal's agent. Completion always uses the exit file.

For isolated tests, set `HOME`, `AGENT_BUDGET_CLAUDE_HOME` and
`ADVANCE_ROADMAP_CODEX_SESSIONS` to temporary directories, disable probes with
`ADVANCE_ROADMAP_USAGE_PROBES=0`, and mock Claude's quota helper. The account-root
override controls budget data sources; Claude's existing helper still follows
its own HOME/config rules. No tests should use live credentials or launch models.
