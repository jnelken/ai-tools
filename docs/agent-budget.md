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

Pass `--workspace ID` when `SUPERSET_WORKSPACE_ID` is absent. `--dry-run` on
`spawn` prints the exact Superset argv and planned model argv without creating
state, a terminal, or a model call. Headless Claude emits a JSON result; Codex
emits its JSON event stream. `collect` returns the saved output as a string so
both formats survive intact. Failed CLI launches have a nonzero `exit` file.
A result without an `exit` file is still pending. Interactive results contain
the Superset launch response; completion follows terminal closure, not hook state.

Leases live in `~/.claude/state/agent-budget/leases.json`. All admissions, releases,
and reads use a separate stable flock file; writes replace the JSON atomically.
Each read reaps dead owner PIDs and expired TTLs. `run` owns its lease, forwards
TERM/INT/HUP to the command's process group, preserves exit status, and releases
in `finally`. Waiting shells back off to 30 seconds and consume no model tokens.
This is polling admission; waiting terminals have no guaranteed FIFO ordering.

Caps come from roadmap `usage.py`: 70% over five hours, 80% weekly, and 100% for
Cursor's selected pool, including that module's environment overrides. Claude
uses the live OAuth quota helper. Other providers use fresh saved state or the
existing probes. Readings older than 15 minutes are unknown; an unknown window
permits at most one active lease for its provider, subject to any known window.
Reservations are conservatively charged to every window (and Cursor pool).

Role costs use nearest-rank p80 **newly processed** tokens (input + cache writes +
output, excluding cache reads), matching the roadmap ledger. Multiple models in
a run's role are summed. Inputs are roadmap runs, the optional shared usage ledger,
subagent transcripts, and completed headless fanouts. Streaming transcript rows
are deduplicated by API message id; per-field maxima retain the final usage.
Adjacent metadata preserves agent type while all child types share the subagent role.

Conversion factors remain seeded until five positive measurements exist for a
provider, then use p80 percentage points/token. Paired Claude snapshots match
launch/session ids to ledger usage, or read the boundary's transcript. Reset-crossing,
stale, zero and negative deltas are excluded. Codex uses cumulative token deltas
paired with rate-limit deltas within one reset window. Account-wide Claude deltas
can include concurrent work, so their estimates deliberately err high. Samples
are deduplicated in `samples.jsonl`; parsed files are cached by size and mtime.
Only the five-hour factor is calibrated and is conservatively applied to weekly
windows too. Cursor remains provisional until measured samples are available.

Seeds: Codex 3 points per 70k-token planner (10-27); Claude 10 points per
251k tokens, from F9's 2 points/minute across three sessions, assuming a
15-minute session. Cursor provisionally reserves 5 points per 59k tokens.
TTL defaults to 30 minutes; observed p90 duration gets a 50% margin with a
one-minute floor. Long sessions may outlive their reservation; this is admission
control, not a provider-enforced spend limit or cross-machine lease service.

The installer registers `agent-budget-gate.py` for PreToolUse (Agent/Workflow),
SubagentStop, SessionStart and SessionEnd, preserving existing settings. The
conducting session is tracked even if already above cap. SubagentStop resolves
`toolUseId` from the child's `.meta.json` and releases that exact reservation;
missing metadata or a failed spawn leaves a bounded TTL reservation. Interactive
fanouts adopt the watcher's lease when their terminal id is known.

Nested headless children retain workspace/account attribution but drop
`SUPERSET_AGENT_*`, `SUPERSET_HOST_AGENT_HOOK_URL`, `SUPERSET_TERMINAL_ID` and
Claude's parent session markers. This prevents their lifecycle hooks from being
reported as the enclosing terminal's agent. Completion always uses the exit file.

For isolated tests, set `HOME`, `AGENT_BUDGET_CLAUDE_HOME` and
`ADVANCE_ROADMAP_CODEX_SESSIONS` to temporary directories, disable probes with
`ADVANCE_ROADMAP_USAGE_PROBES=0`, and mock Claude's quota helper. The account-root
override controls budget data sources; Claude's existing helper still follows
its own HOME/config rules. No tests should use live credentials or launch models.
