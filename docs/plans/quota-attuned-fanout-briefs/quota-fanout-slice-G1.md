You are implementing slice G1 of the quota-attuned fan-out plan in Jake's ai-tools repo: the core of a universal token-reservation ledger. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools.

## Jake's requirement (10-29)

Every agent run, globally (Codex, Claude, and Cursor too), reserves a number of tokens for its work in one shared ledger, and updates the reservation if its estimate changes. Different launchd runs and sessions don't know about each other, so the ledger is how they avoid hitting usage limits. If there aren't enough tokens to reserve, the agent WAITS until the ledger clears a reservation. A reservation clears by logging how many tokens the job actually took, plus the Linear ticket, session id(s) and agent name (or task/goal). That log feeds future estimate improvements. Don't log every turn: one ledger entry per completed output (for example, one code review of one PR). The goal is that no working agent ever gets interrupted by the hard usage cap.

**Decision (Jake, 10-29): hand-started interactive sessions are never blocked.** They record a reservation and show a one-line warning when headroom is short. Only headless, launchd and fanout agents wait.

## Starting point

PR #8 (`lib/agent_budget.py`, `bin/agent-budget`, `bin/fanout`, `hooks/agent-budget-gate.py`) and PR #13 (batch admission) already provide pid-bound percentage leases in `~/.claude/state/agent-budget/leases.json`, plus `agent-budget run --wait`, `allow`, estimates from history, and calibration samples. Read those files, docs/agent-budget.md, and docs/plans/quota-attuned-fanout.md (Decisions, F7, F9, O11, F10), then extend rather than rewrite.

## Build (G1 = ledger core; coverage of every launcher is G2, a later PR)

1. **Tokens as the unit.**
   - A reservation stores reserved tokens (new input + cache_write + output, with cache_read tracked separately) and the derived % per provider window, using the calibrated factor.
   - Keep backward compatibility with existing pct-only leases.
   - Add fields: agent (claude/codex/cursor), model, role, task/goal, linear_id, repo, session_ids[], interactive flag, created/updated, pid with process start time, ttl.
2. **New subcommands:**
   - `agent-budget reserve [--tokens N | --role R] --agent A [--model M] --task T [--ticket DEV-N] [--repo R] [--wait] [--interactive] --json` prints the lease id. With `--interactive` it never blocks: it records the reservation, prints a one-line stderr warning if headroom is short, and returns 0.
   - `agent-budget update --lease ID (--tokens N | --add-tokens N) [--session-id S]` revises the estimate. If the increase doesn't fit, it returns a non-zero "would exceed" status but never kills anything; the caller decides.
   - `agent-budget settle --lease ID [--actual-json F | --input/--cache-write/--cache-read/--output ...] [--session-id S ...] --outcome ok|error|limit|timeout|cancelled` releases the lease and appends ONE completed row to `~/.claude/state/agent-budget/ledger.jsonl`. The row holds: lease_id, agent, model, role, task, linear_id, repo, session_ids, reserved_tokens, actual {input, cache_write, cache_read, output}, the window % before and after if known, duration_s, outcome, and over_run (actual > reserved).
   - `agent-budget run` becomes reserve → exec → watch → settle:
     - It discovers session ids: Claude from the result JSON or a hook-written file; Codex from the stdout header or `--session-out`; Cursor where available.
     - At exit it computes actual tokens from the transcript or rollout, reusing the parsers in automations/advance-roadmap/lib/tokens.py.
     - While running, it polls the transcript usage every ~60s. When consumption passes 80% of the reservation, it calls `update` to extend, recording an over-run if the extension doesn't fit, and never interrupts the child.
3. **Fair waiting.** A small FIFO wait queue file under flock. Waiters admit in arrival order, and a job that fits isn't starved by a big job at the head: allow bounded bypass, then reserve for the head. Show the waiting state in `agent-budget status`.
4. **Estimates come from the ledger.** `estimates()` prefers ledger.jsonl rows (actual tokens per role, task kind and agent) and falls back to the existing sources (runs.jsonl, agent-usage.jsonl, transcripts, samples). Calibration pairs each settled row's actual tokens with the window % before and after, where recorded.
5. **Nested runs adopt their parent's lease.** If AGENT_BUDGET_LEASE_ID is set (exported by `run`, the batch and fanout), a nested `reserve` or `run` adopts that lease (updating its estimate and adding session ids) instead of double-reserving. Document this contract for G2.

Rules:
- **Tests (lib/test_agent_budget.py):**
  - token↔pct conversion and backward compatibility;
  - reserve, update and settle, with exactly one ledger row per settle and idempotent settle;
  - an interactive reservation never blocks;
  - wait-queue fairness and no starvation;
  - the 80% watch extends a reservation or records an over-run without killing the child (use a fake child and transcript);
  - nested adoption;
  - estimates preferring ledger rows.
  
  Use temp HOME only. Run `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q`.
- **Scope:** don't wire launchers yet (agent-run, claude-session, worker/review internals, Superset presets, shell shims). Those are G2. Write a short G2 plan into docs/plans/quota-attuned-fanout.md: the exact choke points and the adoption contract.
- **Shipping:** commit, push -u, open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
