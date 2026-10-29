You are implementing slices A and B of the quota-attuned fan-out plan in Jake's ai-tools repo. This Superset workspace's worktree is your checkout. Work only here, never in ~/.ai-tools (the read-only deploy clone).

Read first:
1. docs/plans/quota-attuned-fanout.md, the running plan doc. Your scope is "A. Roadmap: stop wasting planning" (A1–A5) and "B. Live headroom". The evidence is under Findings F1–F5, and you own open threads O1, O2, O5 and O9.
2. CLAUDE.md, and automations/advance-roadmap/ (daily_batch.py, run.sh, lib/verdict.py, lib/usage.py, lib/pendingplan.py, lib/runmemory.py, lib/models.sh, lib/planapproval.py) plus its tests/.
3. skills/conductor/ORCHESTRATOR.md, around line 179 (the "re-derived every 24h" sentence), and hooks/status-glyph-check.sh.

What to build. Exact line numbers are in the doc; re-verify them, they may have drifted.
- **A1.** Exempt headless runs from the status-glyph Stop hook (resolve O1: is CLAUDE_QUOTA_MANAGED enough, or do you need a new ADVANCE_ROADMAP_HEADLESS export in run.sh, worker.sh, review.sh and planapproval.py?). Add a run.sh fallback that recovers the ORCHESTRATOR_RESULT_JSON fence from the last assistant message containing one, never from the prompt echo.
- **A2.**
  - `defer_ready` cancels only dispatch_worker and resume_worker items.
  - The batch-cancel branch in run.sh saves the verdict for non-dispatch actions.
  - A no-op no-work result skips the bookkeeping worker. That means archives empty, closeouts empty, and every blocker linear_id already in the previous verdict. It writes the Step 8 ledger row with runmemory.py (O5) and saves the verdict instead.
  - `verdict.py --repo <target>` scopes the fingerprint and diff; run.sh passes it at both fingerprint calls.
  - The pending-plan glob in the verdict check becomes per-target when TARGET_REPO is set.
  - Batch runs use a 72h max age. Update the ORCHESTRATOR.md sentence to match.
- **A3.**
  - The planner gets a per-repo filtered Linear snapshot: `repo/<target>` plus unlabeled issues, without Done or Canceled. The fingerprint keeps the full snapshot.
  - The Claude planner and approver default to Sonnet with medium effort through lib/models.sh. The env overrides keep working.
  - Disallow the advisor tool in headless Claude planner calls, but only if a smoke test of `claude -p --disallowedTools advisor` shows it works (O2). Otherwise leave it and record why.
- **A4 (stub for now).** Before launching any planner, `daily_batch.py` requires that `usage.py pick-worker-chain` is non-empty and the required reviewer is not gated. Otherwise plan nothing: record skipped-quota with the reason via `quota-record`, and leave carried verdicts and pending plans untouched. Slice C will replace this with a real budget later, so keep it in one small function.
- **A5.** Create the batch date directory only after the Superset, Linear-snapshot and usage preflight succeeds, so an outage doesn't use up the day.
- **B.**
  - `lib/usage.py probe_claude` prefers `~/.ai-tools/lib/claude_quota.py`'s `quota()`. Import it from the repo's `lib/`, resolved relative to this file or through AI_TOOLS_HOME, the same way other code does.
  - It falls back to the statusline file.
  - A reading older than 15 minutes stores `used_pct: None`, so the gate reports "unknown", not 0%.

Rules:
- **Tests.**
  - Extend the existing ones for every behavior above (the list is in the doc's Verification section). Also add a 10-27-shaped fixture to tests/test_quota_replay.py if that's tractable.
  - Run the full suites: `python3 -m pytest automations/advance-roadmap/tests`, plus each `automations/advance-roadmap/tests/test-*.sh`. Every test must pass before you open the PR.
- **Don't touch the live system.** No edits to ~/.claude/automations/advance-roadmap state, no launchctl, no live batch or run.sh runs against real repos, no Linear or GitHub writes besides your own PR.
- **Stay in scope.** C (agent-budget, fanout, hooks for Agent), D (slackagent) and E (running-doc rules and partial triage) belong to other workspaces.
- **Running doc.** As you resolve O1, O2, O5 and O9, tick them in docs/plans/quota-attuned-fanout.md with the answer and its evidence, and add any new open thread you find. Commit the doc with your code; don't let an unfinished investigation disappear.
- **Shipping.**
  - Commit in coherent steps, then `git push -u origin <branch>`.
  - Open a ready (non-draft) PR with `gh pr create`. The body has `## Summary` / `## Changes` / `## Validation`. Validation is manual QA steps only. Describe behavior, not file names.
  - **Do not merge.** The conducting session reviews and merges.
  - End with the PR URL and a short list of anything left open.
