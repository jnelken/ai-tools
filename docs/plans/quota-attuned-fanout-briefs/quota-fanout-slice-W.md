You are fixing a quota-admission bug in Jake's ai-tools repo (lib/agent_budget.py). This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools. It must merge before the 10:30 roadmap batch on 10-30, so keep the change minimal and safe.

## Bug (F11)

`capacity()` (lib/agent_budget.py ~460-470) computes headroom as `cap - live - reserved` for EVERY window of a provider, using the same per-job `cost` and the same `reserved` percentages.
- Those percentages are calibrated only against the **five_hour** window: the Codex rollout sampling at ~164 reads `five_hour`, and the Claude snapshot pairing at ~293-294 reads `five_hour`.
- The 7-day/weekly windows are far larger, so one job uses a much smaller share of them, but they're charged at the 5-hour rate.

Live symptom on 10-29: with Claude 5h at 0% and 7d at 53% (cap 80), `agent-budget allow --provider claude --role subagent --want 4` returned 1. Codex weekly at 70% (cap 80) limits batch admission to about 1 Codex job, even though the weekly window has room for many.

## Fix

- **Per-window factors.** Calibrate a tokens→% factor for each window (Claude: five_hour, seven_day; Codex: five_hour, weekly) from the samples you already collect:
  - Codex rollout `rate_limits` carry both the primary and secondary windows. Check `usage.parse_codex_rate_limits`.
  - Claude quota-snapshots start/finish rows carry both five_hour and seven_day.
  - Apply the same interval/merging logic per window, with the same seed-until-≥5-samples rule.
  - Default seed for long windows: derive it from the observed ratio of Δlong/Δfive_hour across paired samples. If there aren't enough samples, use a conservative ratio, but not 1.0; justify the value from the data in a comment.
- **Leases record cost per window** (`pct` becomes a per-window dict, keeping backward compatibility with old scalar `pct` leases by treating them as five_hour and scaling them for long windows). `capacity()` subtracts each window's own reserved and own cost.
- **`status --json`** shows each role's estimate per window.
- **Tests:**
  - Claude at 5h 0% and 7d 53% admits several subagents; at 5h 65% it admits 0 or 1.
  - Codex weekly at 70% no longer limits admission to 1 when 5h is low.
  - Backward compatibility for old leases.
  - Calibration with both windows, plus the sparse-sample default.
  
  Run `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q`.
- **Watch for conflicts:** PR #16 (branch quota/reservation-ledger) is concurrently reworking lib/agent_budget.py to use tokens as the unit. Keep this fix small and self-contained, so #16 can merge it in. Note the change in docs/agent-budget.md and add F11 to docs/plans/quota-attuned-fanout.md.
- **Shipping:** commit, push -u, and open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
