You are implementing slice X of the quota-attuned fan-out plan in Jake's ai-tools repo: raise the weekly quota cap to 90% for all providers. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools. Merge it before the 10:30 roadmap batch on 10-30 if possible, so keep it minimal.

**Decision (Jake, 10-29, final at 17:55):** set the weekly limits to **90%** for all providers, instead of 80%. This keeps a buffer below the hard limit so running jobs are never cut off. agent-budget admission still projects a job's weekly cost against 90%. Cursor's monthly included pool is NOT a weekly window, so leave its gate as is.

Change:
- **automations/advance-roadmap/lib/usage.py:** change the default of MAX_SEVEN_DAY_PCT (env ADVANCE_ROADMAP_MAX_WEEKLY_PCT) from 80 to 90. claude_gate and codex_gate keep the weekly window, now capped at 90. The env override still works.
- **lib/agent_budget.py (per-window admission from PR #17):** the long windows use usage.py's MAX_SEVEN_DAY_PCT (90), not a new hard-coded number. Projected per-window costs and reservations still apply, so headroom is 90 - live - reserved.
- **Docs and visibility:**
  - docs/agent-budget.md;
  - automations/README.md where it describes weekly caps;
  - gen-dashboard.py, if it draws a weekly cap line: move it to 90;
  - the batch summary, if it mentions weekly causes.
- **Tests:** update the existing weekly-cap tests to the 90% cap. Add:
  - weekly at 85% with 5h at 10% admits work (it used to be blocked at 80);
  - weekly at 90% blocks;
  - weekly at 88% with a job whose projected weekly cost is 3 points doesn't admit;
  - a Cursor monthly pool at 100% still gates.
  
  Cover both the usage.py gates and agent-budget capacity, for Claude and Codex. Run `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q` and the shell suites under zsh.
- **Conflicts:** PR #16 (branch quota/reservation-ledger) is concurrently editing lib/agent_budget.py, so keep your diff small. Note the decision under Decisions in docs/plans/quota-attuned-fanout.md.
- **Shipping:** commit, push -u, and open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
