You are implementing slice E2 ("Partial triage for headless planners") of the quota-attuned fan-out plan in Jake's ai-tools repo. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools.

Read first:
1. docs/plans/quota-attuned-fanout.md: the E2 bullet, Decisions ("running docs cover sessions and headless roadmap planners"), and O9 (where Codex rollout files live for read-only planners; PR #9 answered it).
2. automations/advance-roadmap/run.sh, the planner section: run_orchestrator, the failover loop, the quota-paused, planning-timeout and error recording, lib/lastfence.py and pending-plan reuse. Also lib/verdict.py (fingerprint) and skills/conductor/ORCHESTRATOR.md.

Build:
- **When:** a planner ends quota-paused, planning-timeout or error without a valid fence.
- **What:** run.sh builds `$CODE_DIR/.advance-roadmap/partial-triage-<repo>.md`, deterministically and with no model:
  - **Source:** the planner's transcript, meaning the Claude session JSONL found by session_id from the result JSON (or by the newest file for that run if it's missing), or the Codex rollout file.
  - **Contents:** the commands and reads it ran, with outputs truncated to ~400 chars each and ~20 KB total; its interim assistant text; and the files and tickets it touched.
  - **Header:** stamp, repo, provider, how the run ended.
- **Next run for that repo:**
  - The planner prompt names the file: "Partial triage from <stamp> (ended <how>): reuse these findings; don't redo those reads unless the fingerprint says they changed."
  - A completed planner for that repo deletes it.
  - It ages out after 72h.
  - Its presence counts as a change in the repo's fingerprint, so it is never skipped as unchanged.
- **ORCHESTRATOR.md:** one short instruction asking planners to state interim conclusions in plain text as they go ("so far: X blocked by Y"), so the digest carries meaning. Also document the partial-triage file next to "A pending plan exists".
- **Privacy:** don't copy secrets. Redact env-like `KEY=value` tokens and bearer strings in the digest.

Rules:
- **Tests:** add fixture transcripts (Claude JSONL and Codex rollout) and cover:
  - a quota-killed planner leaves the file;
  - the next planner's prompt names it;
  - a completed run deletes it;
  - the age-out works;
  - the fingerprint treats it as changed;
  - redaction works.
  
  Run `uv run --with pytest python -m pytest automations/advance-roadmap/tests -q` plus the shell suites under zsh.
- **Live system:** don't touch ~/.claude/automations/advance-roadmap state, launchd, Linear or GitHub, other than your PR.
- **Stay in scope:** another agent (slice C2) is editing daily_batch.py and lib/agent_budget.py. Stay in run.sh, lib/ helpers you add, verdict.py and ORCHESTRATOR.md. If you need daily_batch.py, keep the change minimal and say so in the PR.
- **Running doc:** tick E2 in docs/plans/quota-attuned-fanout.md.
- **Shipping:** commit coherently, push -u, and open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
