You are implementing slice C2 of the quota-attuned fan-out plan in Jake's ai-tools repo: fix agent-budget's Codex calibration, then wire the daily roadmap batch to agent-budget. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools (the read-only deploy clone).

Read first:
1. docs/plans/quota-attuned-fanout.md: section C (the daily_batch consumer bullet), Findings F2, F7 and F9, and open threads O11 plus the "wire daily_batch to agent-budget" thread.
2. docs/agent-budget.md, lib/agent_budget.py, automations/advance-roadmap/daily_batch.py (especially the A4 admission function from PR #9: implementation_headroom / skip_planning), automations/advance-roadmap/lib/usage.py and lib/tokens.py.

**Part 1, O11 (do this first; it blocks part 2).**
- **Problem.** `agent-budget status` calibrates Codex at about 0.00078 %/token from 2,066 samples, so a planner at p80 ~88k tokens is estimated at 69% of the 5-hour window. Ground truth from 10-27: 10 Codex planners moved the 5-hour window from 41% to 70%, about 3 points per ~71k-token planner, so the factor should be about 4e-5.
- **Find the unit mismatch** in how Codex rate_limits samples are paired with tokens. Candidates: per-turn versus cumulative total_token_usage, cached versus new input, primary versus secondary window, percent versus fraction.
- **Fix it** and add a regression test built from a real-shaped rollout fixture.
- **Sanity-check Claude's factor too.** It is currently ~1.1e-4, so a subagent at p80 130k is estimated at ~14%. Compare it against F9: two Sonnet agents plus a conducting session used ~2 points/min. If samples mix Opus and Sonnet, consider per-model factors.

**Part 2, batch wiring.**
- **Admission.** Replace the A4 stub with agent-budget admission in daily_batch.py:
  - lanes per poll = min(--lanes, how many more jobs fit);
  - a planner reserves its own cost on the planner provider plus its expected implementation (worker plus review) at a per-repo dispatch rate (historical share of dispatch_worker; default 0.3);
  - a dispatch plan converts its reservation into a worker plus review reservation on the first provider in pick-worker-chain order that fits;
  - release waves only release reservations that still fit after a usage refresh.
  
  All reservations go through agent_budget leases, so they're shared with interactive fan-out.
- **Ordering:** pending dispatch or resume plans first, then repos whose verdict check reports changes, then repos with no verdict. Carried-verdict repos remain skipped in run.sh.
- **Visibility:**
  - `batch.json` gains a `budget` block: per-provider live, reserved and headroom figures, plus held or skipped repos with reasons.
  - The Slack summary (quota_line) gains one hold line.
  - `daily_batch.py --dry-run` prints each repo's verdict-check result, live headroom, and the lanes it would use.
- **Keep everything from PR #9 intact:** the pre-plan gate semantics, cross-provider reviewer rules and preflight.

Rules:
- **Tests:** extend tests/test_daily_batch.py and lib/test_agent_budget.py; test_quota_replay.py gets the 10-27-shaped admission check. Run `uv run --with pytest python -m pytest automations/advance-roadmap/tests lib -q` and the shell suites under zsh.
- **Live system:** don't touch ~/.claude/automations/advance-roadmap state, launchd, Linear or GitHub, other than your PR.
- **Running doc:** tick O11 and the wiring thread in docs/plans/quota-attuned-fanout.md, with evidence.
- **Shipping:** commit coherently, `git push -u origin <branch>`, and open a ready PR (Summary / Changes / Validation with manual QA only). Do not merge. End with the PR URL.
