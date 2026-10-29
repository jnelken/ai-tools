You are implementing the standalone core of slice C ("Shared budget and per-session gate") of the quota-attuned fan-out plan in Jake's ai-tools repo. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools (the read-only deploy clone).

Read first:
1. docs/plans/quota-attuned-fanout.md. Your scope is section C, informed by:
   - Decisions: gate strength. Superset fan-out terminals wait in a queue on the budget; in-process Agent and Workflow calls over budget are denied, pointing to fanout.
   - Findings F3, F7, F8, F9.
   - Open threads O6 and O10, which you own.
2. CLAUDE.md, lib/claude_quota.py (and its test), bin/claude-session, bin/agent-run with lib/agent_profiles.py, automations/advance-roadmap/lib/usage.py (gates, caps, Codex and Cursor probes), automations/advance-roadmap/lib/tokens.py and runs.jsonl's `.tokens` shape. Also skills/handoff/SKILL.md lines ~95-131 (Superset terminal creation) and install.sh, especially wire_claude_quota_hooks: that is how hooks get registered.

**Out of scope.** Do not edit:
- automations/advance-roadmap/daily_batch.py, run.sh or lib/usage.py. Another agent is changing them on quota/roadmap-stop-waste. Wiring the batch to your budget is a follow-up after that merges.
- slackagent.

Import from usage.py; don't modify it.

What to build:
- **`lib/agent_budget.py` + `bin/agent-budget` (Python, stdlib only).**
  - `status [--json]`: per provider (claude, codex, cursor), the live reading, reserved % from active leases, headroom against the caps, and the estimated per-job cost for each role.
    - Claude's live reading comes from claude_quota.quota().
    - Codex and Cursor come from usage.py's probes or its saved providers-usage.json state. Stale (>15 min) counts as unknown.
  - `allow --provider P --role R --want N [--json]`: how many jobs fit now.
  - `run --provider P --role R [--wait] [--label L] -- CMD...`:
    - takes a lease, waiting with backoff when `--wait` is given and using no model while it waits;
    - runs CMD;
    - releases the lease on exit (signal-safe, and the exit code is preserved).
  - `lease` / `release` subcommands for hooks.
  - **Leases:** kept in `~/.claude/state/agent-budget/leases.json` under flock. Each holds provider, role, pct reserved, pid, created and TTL. Dead pids and expired TTLs are reaped on every read.
  - **Cost model:**
    - p80 tokens per (provider, role) from `~/.claude/automations/advance-roadmap/runs.jsonl` `.tokens`, `~/.claude/state/agent-usage.jsonl` (being added by the slackagent slice; tolerate it being absent), and Claude Code transcripts for subagents (resolve O6: where are subagent transcripts stored, and how do you get per-subagent usage?).
    - Tokens are converted to window % by per-provider factors learned from paired readings in `~/.claude/state/quota-snapshots.jsonl` (start/finish rows) and Codex rate_limits samples, saved in `~/.claude/state/agent-budget/samples.jsonl`.
    - Conservative seeds until there are 5 or more samples: Codex about 3 points per ~70k-token planner, measured on 10-27. Claude: derive a seed from F9, where two interactive Sonnet agents plus a conducting session used about 2 points per minute.
    - Roles at minimum: planner, worker, reviewer, subagent, interactive.
  - **Headroom:** `cap − live − Σ active leases`. Reuse usage.py's caps (5h 70, 7d 80, Cursor 100) via import. An unknown reading allows one lease per provider.
- **`bin/fanout` (zsh or Python).**
  - `spawn --label L --provider claude|codex --role R --prompt-file F [--model M] [--effort E] [--interactive] [--workspace ID] [--dry-run]` creates a Superset terminal in `$SUPERSET_WORKSPACE_ID`, or `--workspace`, with the CLI at /Applications/Superset.app/Contents/Resources/resources/bin/superset.
  - Headless mode runs `agent-budget run --wait --provider P --role R -- <claude -p … --output-format json | codex exec … </dev/null>` and writes `~/.claude/state/fanout/<id>/{prompt,result.json,exit}`. Interactive mode starts the agent through `superset agents create`, with a lease that is held until the terminal closes or the TTL runs out.
  - `status` and `collect` read the result files.
  - **Resolve O10:** nested headless runs must not inherit the Superset agent-hook env that gets them misreported as the terminal's agent. Find which env vars Superset injects (inspect `env` inside a Superset terminal; look for SUPERSET_*) and strip them for headless children. Completion is detected from the exit file, not from hook state.
- **PreToolUse hook for `Agent` and `Workflow` + SubagentStop release:** `hooks/agent-budget-gate.sh` or `.py`, registered the way install.sh registers the other hooks.
  - It denies with a clear reason, pointing to `bin/fanout`, when `allow` returns 0 for claude/subagent.
  - Otherwise it takes a TTL lease that SubagentStop releases.
  - If hook registration actually lives in ~/dotfiles (settings render), don't edit dotfiles. Record exactly what needs adding as a new open thread in the doc.
- **Instructions:** `instructions/providers/claude-development.md`, section *Parallelize with Subagents*. Fan-outs go through `bin/fanout` (Superset terminals, headless or interactive) and are sized by `agent-budget allow`. Keep the Model Router note and the existing independence rules.

Rules:
- **Tests:** `lib/test_agent_budget.py` (pytest, matching the lib/test_claude_quota.py pattern) covering p80 estimation, the seed-versus-calibrated factor, headroom with leases, an unknown reading meaning one lease, lease reaping (dead pid and TTL), `run` releasing on a nonzero exit and on SIGTERM, `fanout --dry-run` building the exact superset command, and the hook denying at 0. Use temp dirs; never touch real `~/.claude/state` in tests. Run them, and the existing lib tests, until green.
- **Quota.** Claude is the scarce provider right now, so don't launch real Claude agents to test. Smoke-test fanout with `--dry-run` and one tiny headless `codex exec` at most.
- **Running doc.** Tick O6 and O10 in docs/plans/quota-attuned-fanout.md with the answers and evidence. Add any new open thread, including "wire daily_batch.py to agent-budget after quota/roadmap-stop-waste merges", noting the A4 stub it replaces. Commit the doc with your code.
- **Shipping.** Commit coherently, `git push -u origin <branch>`, and open a ready PR with `gh pr create`, body `## Summary` / `## Changes` / `## Validation` (manual QA steps only). Do not merge. End with the PR URL and anything left open.
