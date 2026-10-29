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
Only admitted children (fanout terminals, batch jobs, and gated subagents) hold leases. Open interactive sessions
are not leased: their consumed usage is in the live reading, and the 30% above
the five-hour 70% cap is reserved for interactive use. `run` owns its lease, forwards
TERM/INT/HUP to the command's process group, preserves exit status, and releases
in `finally`. Waiting shells back off to 30 seconds and consume no model tokens.
This is polling admission; waiting terminals have no guaranteed FIFO ordering.

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
provider, then use p80 percentage points/token. Paired Claude snapshots match
launch/session ids to per-session ledger usage, preserving review-round boundaries.
They never pair a role's whole-job total with each session. Ambiguous multi-session
rows are skipped. Transcript calibration counts only assistant messages within the
start/finish interval, so resuming a session does not charge its earlier history.
Reset-crossing,
stale, zero and negative deltas are excluded. Codex uses cumulative token deltas
paired with rate-limit deltas within one reset window. Account-wide Claude deltas
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
