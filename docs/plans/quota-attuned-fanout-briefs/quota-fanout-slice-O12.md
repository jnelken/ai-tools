You are fixing open thread O12 in Jake's ai-tools repo (lib/agent_budget.py estimates). This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools.

**Problem (finding F13 in docs/plans/quota-attuned-fanout.md):** with Cursor gated, review is cross-provider, so every Codex worker needs a Claude reviewer. agent-budget estimates the Claude reviewer role at p80 ~261k tokens (~28.5% of the Claude 5h window) from only **2** historical observations. On 10-30 at 10:30, Claude 5h was at 48% (22 headroom), so no worker-plus-review bundle fit and the batch planned nothing, even though Codex had plenty of room. Real Claude PR reviews this week (in-process workflow verifiers and fanout reviewers) used roughly 120–260k tokens.

**Fix:**
- **Thin-sample estimates.** A (provider, role) estimate with fewer than 5 observations must not be used raw. Blend it with a proxy:
  - prefer the same role on other providers, converted by token count rather than by pct;
  - otherwise use a comparable role on the same provider (reviewer→subagent, planner→subagent);
  - weight by sample count, until there are at least 5 role observations.
- **New data source.** Count Claude review runs recorded in ~/.claude/state/agent-budget/ledger.jsonl (if present) and the roadmap runs.jsonl reviewer rows, so the sample count grows.
- **Explainability.** `agent-budget status --json` shows each role's estimate source (observed, blended, or proxy) and its sample count.
- **Safety.** Don't lower any estimate that already has 5 or more observations.

**Tests:**
- a 2-sample reviewer is blended toward the proxy;
- a role with 5 or more samples is unchanged;
- status reports the source.
- Against real history (read-only), report the before and after Claude reviewer pct, and whether 22 points of Claude 5h headroom now admits one Codex worker plus a Claude reviewer.

Run `uv run --with pytest python -m pytest lib automations/advance-roadmap/tests -q`.

**Shipping:** PR #16 (G1) is concurrently editing lib/agent_budget.py, so keep this minimal. Commit, push -u, and open a ready PR. Do not merge. End with the PR URL.
