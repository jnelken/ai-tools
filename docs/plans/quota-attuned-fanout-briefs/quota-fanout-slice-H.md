You are implementing slice H of the quota-attuned fan-out plan in Jake's ai-tools repo: automatic review fallback while Cursor is gated. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools.

**Why:** the 10-29 batch planned nothing (finding F10 in docs/plans/quota-attuned-fanout.md). The default `cursor-then-codex` review strategy hard-requires a Cursor review cycle (automations/advance-roadmap/review.sh: `run_required_review cursor cursor-cycle || exit $?`), Cursor's auto pool is at 100% until 11-03, and the pre-plan gate in daily_batch.py correctly skips all planning when the required reviewer is gated. Jake decided on 10-29: **when Cursor is gated, switch automatically to cross-provider review** (Codex reviews Claude/Cursor work, Claude reviews Codex work, never self-review), and go back to cursor-then-codex once Cursor is available. This MUST merge before the 10:30 batch on 10-30.

Read: review.sh (strategies, run_required_review, the cross_provider_reviewers logic, gates), worker.sh (how it calls review.sh), daily_batch.py (the pre-plan gate and RoadmapBudget admission from PRs #9 and #13, including the cross-provider rule that the reviewer must differ from the worker), lib/usage.py (gate and pick-* commands; the reviewer role uses Cursor's api pool), the tests under automations/advance-roadmap/tests, and docs/agent-budget.md.

Build:
- **One shared decision function** (in usage.py, or a small lib helper both shell and Python call): effective review strategy = the configured strategy, unless it's cursor-then-codex and the Cursor reviewer gate is over. In that case, cross-provider, with a reason string. An explicit ADVANCE_ROADMAP_REVIEW_STRATEGY=cross-provider still means cross-provider.
- **review.sh** uses the effective strategy and records it, plus the fallback reason, in its review log rows. With Cursor available, behavior is byte-for-byte unchanged.
- **daily_batch.py admission and the pre-plan gate** use the same effective strategy, so planning happens only when the cross-provider rule can be satisfied for some worker in pick-worker-chain (a reviewer different from the worker, under its gate).
- **Visibility:** the batch summary and batch.json note "review fallback: cross-provider (cursor gated until <reset>)".

Rules:
- **Tests:**
  - Cursor gated with Codex and Claude available: admission passes and review.sh runs cross-provider.
  - Cursor gated with only Claude available: no admission (Claude can't review itself).
  - Cursor available: unchanged.
  - An explicit cross-provider setting stays cross-provider.
  
  Run `uv run --with pytest python -m pytest automations/advance-roadmap/tests -q` and the shell suites under zsh.
- **Live system:** don't touch ~/.claude/automations/advance-roadmap state, launchd, Linear or GitHub, other than your PR.
- **Running doc:** add an H note to docs/plans/quota-attuned-fanout.md.
- **Shipping:** commit, `git push -u`, and open a ready PR (Summary / Changes / Validation, manual QA only). Do not merge. End with the PR URL.
