You are implementing slice X of the quota-attuned fan-out plan in Jake's ai-tools repo: remove weekly quota limits from gating and admission for all providers. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools. Merge it before the 10:30 roadmap batch on 10-30 if possible, so keep it minimal.

**Decision (Jake, 10-29 17:50):** "we can remove weekly quota limits entirely from all providers." Only the short windows gate work: Claude five_hour and Codex five_hour. Cursor's monthly included pool is NOT a weekly window, so leave its gate as is.

Change:
- **automations/advance-roadmap/lib/usage.py:** claude_gate and codex_gate stop gating on the 7d/weekly windows. Remove MAX_SEVEN_DAY_PCT (ADVANCE_ROADMAP_MAX_WEEKLY_PCT) from gating; keep parsing and storing the readings for display. A provider-reported hard limit still gates through the existing limit-hit and rate_limit_reached paths. Make sure Codex `rate_limit_reached` can't be caused by the weekly window alone in a way that blocks short-window work. If it can, keep honoring it, since that's the provider refusing; document which.
- **lib/agent_budget.py (per-window admission from PR #17):** capacity, view, allow and reserve consider only the short windows. Long windows keep appearing in `status` (live, factor, samples) but get no cap and never reduce headroom. Keep the calibration code; just stop gating on it.
- **Docs and visibility:**
  - docs/agent-budget.md;
  - automations/README.md where it describes weekly caps;
  - gen-dashboard.py, if it draws a weekly cap line: show the reading without a cap;
  - the batch summary, if it mentions weekly causes.
- **Tests:** update the existing weekly-cap tests to assert the new behavior. Add: a weekly window at 99% with 5h at 10% admits work, both in the usage.py gates and in agent-budget capacity, for Claude and Codex; and a Cursor monthly pool at 100% still gates. Run `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q` and the shell suites under zsh.
- **Conflicts:** PR #16 (branch quota/reservation-ledger) is concurrently editing lib/agent_budget.py, so keep your diff small. Note the decision under Decisions in docs/plans/quota-attuned-fanout.md.
- **Shipping:** commit, push -u, and open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
