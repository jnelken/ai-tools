# advance-roadmap → Production Run: PRs from Superset workspaces, wide bursts, parallel lanes

> **Suggested execution:** Sonnet 5 with high reasoning: the plan names every file and the order of changes, so it's mostly careful shell and Python edits plus skill-doc rewrites. Step up to Opus 5 for Phase 4's concurrency and state rework and the orchestrator's change to dispatching several items at once. Step down to Haiku 4.5 for the stale-doc fixes and launchd plist edits.

> **Status (2026-10-06):** Phase 1 shipped and verified ([ai-tools#1](https://github.com/jnelken/ai-tools/pull/1)):
> scheduled runs now ship as merged PRs from Superset workspaces, and Superset credits them (ai-tools#1 and
> wav-explorer#1 are linked with `merged_at` set; roaddmap#1 shipped during the code swap without a workspace,
> so it isn't credited). Phase 1 step 8 was a no-op: mailcrush's review bot is already manual-only.
> **Phase 3 shipped as serial sprint windows:** hourly ticks inside 10:30–15:30 and 16:00–21:00, daily, no
> overnight runs. **Phase 4 (lanes) is Jake's to unblock:** Claude Code's auto-mode classifier refused to
> create `supervisor.py` ("create unsafe agents"), and Jake will sort that out. The lane-mode edits (`run.sh`
> lane mode, per-repo pending plans, locked `usage.py` writes, ORCHESTRATOR *Parallel lanes*) sit
> uncommitted in the Superset workspace `~/.superset/worktrees/ai-tools/advance-roadmap/parallel-lanes`.
> Phase 2 (supply) is not started.

## Context

Jake wants the scheduled `/advance-roadmap` runs to move him up the Superset "Production Run" leaderboard. The research turned up mechanics that change what matters most.

**How Superset scores** (read from the app's bundled source, v1.30.2):
- **Tokens, sessions and width count every agent transcript on the machine**, not only Superset ones. Width is the median, over only the 15-minute slots that have activity, of distinct sessions active in that slot. Days are counted in UTC. Every subagent and every `codex exec` or `claude -p` call is its own session.
- **Output (`agentPrsMerged`) is the only metric tied to Superset.** A PR counts only if it is merged on GitHub, linked to a Superset workspace, and that workspace has a terminal-agent binding or is archived. The branch needs an upstream (`push -u`). The workspace must not be deleted until Superset has recorded `merged_at`. Only the active org's PRs are published (J3u is the personal org on JMIV).
- **Cost** is API list price for all tokens, divided by merged PRs. It caps your tier, so runs that produce no PR count against you.
- **Tier bars** (top tier): 6.2 parallel sessions, 27.9M tokens/session, 7.5 PRs/week, 26/30 active days, ≤$100/PR. All are trailing-30-day medians, and you're unranked below 8 active days.

**Where advance-roadmap stands:**
- It ships plenty: 36 ships in 11 days, about 23 a week.
- **None of them count.** The rule is "never open a PR"; the worker merges `--ff-only` to `main` in the root checkout and pushes. The Superset DB holds zero PRs that qualify.
- It's strictly serial: launchd fires hourly at :45 with a 2-hour grid plus a mkdir lock, and Cursor did 54 of 54 worker runs.
- Lone runs between 01:45 and 05:45 add 1–2-session slots, which drag the width median down.
- **The ticket queue sets the limit.** Every day with no ships (9/30–10/3) had an empty queue. Today no feature item can be dispatched anywhere, and all 6 code-health tickets are on cooldown. 8 tickets sit in Needs Input, and several more are blocked on credentials.

**Verdict on Jake's four ideas:**

| Idea | Verdict |
|---|---|
| 1. Business hours | **Yes.** Concentrated, wide bursts raise the width median. Weekdays only would cap active days at about 22/30, below the top two tiers, so keep a weekend burst. |
| 2. Parallelize across repos | **Yes, but only after Phases 1–2.** It needs Superset workspaces (worktrees) and a fuller queue. Parallel lanes over an empty queue add nothing. |
| 3. Project-manager agent | **As a deterministic supervisor script, not an LLM** (decided). A polling LLM adds tokens with no PRs, which pushes against the cost cap. |
| 4. Provider switching | **Already exists** in `lib/usage.py` and `worker.sh` failover. It only needs to be made safe under concurrency, and it should prefer the cheap worker (Cursor Auto) because of the cost-per-PR cap. |

**Decisions made:**
- Auto-merge after the cross-model review, using a merge commit.
- Run window: weekdays plus a weekend burst.
- JMIV stays home with the lid open and plugged in.
- The supervisor is a deterministic script.

## Phase 0: Preconditions (Jake, about 10 minutes)

1. In the Superset app, confirm you've opted in to the leaderboard. Record the baseline: width, tokens/session, PRs/week, active days, $/PR, and USD/day from the usage view.
2. Keep the **J3u org active** on JMIV. Each publish rewrites 30 days of PR counts from the active org only.
3. Set the Superset app to open at login. It publishes only while it's running.
4. Register any missing repos as Superset projects when they first get work: knowledge-graph, tailwind-quiz, train-race, roaddmap, wav-explorer and gmail-status-tracker are missing today.

## Phase 1: Ship as PRs from Superset workspaces (still serial, the biggest lever)

This takes credited PRs from 0 to the existing ship rate. It also moves builds out of the root checkout, in line with the CLAUDE.md rule against building there, and out of Dropbox, which removes the ableton-catalog lag problem.

1. **Spike first.** Launch `worker.sh` inside a Superset workspace terminal and confirm the agent hooks write a `terminal_agent_bindings` row. They should, because the child process inherits `SUPERSET_TERMINAL_ID` and `~/.superset/hooks/notify.sh` uses it.
   - If that doesn't work, fall back to `superset agents create --local --workspace <id> --agent cursor-agent --prompt-file …`, with failover driven by `superset agents read`.
   - Call the CLI by its absolute path, `/Applications/Superset.app/Contents/Resources/resources/bin/superset`. Never put `~/.superset/bin` on PATH, because its `claude`/`codex` wrappers would shadow the real binaries.
2. **Workspace per item.** After the orchestrator picks an item, `run.sh` runs `superset ws create --local --project <id> --name <slug> --branch roadmap/<slug> --base-branch main`. The worktree lands in `~/.superset/worktrees/<projectId>/<name>`.
3. **Remove the worktree blockers:**
   - `conductor/ORCHESTRATOR.md:266` skips any `.git` that is a file. Allow it for lane worktrees.
   - `review.sh:43` checks `[ -d "$REPO/.git" ]`. Accept a `.git` file too.
   - `lib/verdict.py:96` only fingerprints directories with a `.git` directory.
   - `worker.sh:144,164` point `--workspace`/`-C` at `$CODE_DIR`. Point them at the worktree path.
4. **WORKER.md Steps 3 and 7.**
   - No `checkout -b` in the root checkout.
   - Push with `git push -u origin roadmap/<slug>`.
   - Open the PR with `gh pr create`, with the body in Jake's Summary/Changes/Validation format, generated from the net diff.
   - Merge with `gh pr merge --merge`. Use `--squash` only where merge commits aren't allowed (tailwind-quiz).
   - Step 7b's deploy watch is unchanged. Fix-forward `fix:` commits also go through a PR, so nothing pushes straight to `main`.
5. **After the merge,** run `git -C <root> pull --ff-only` only when the root checkout is clean and on `main`, so the local `main` stays current.
6. **Clean up workspaces only after `merged_at` is set.** Run `superset ws delete` only once `sqlite3 -readonly ~/.superset/host/3f528b40-…/host.db` shows `pull_requests.merged_at` set for the linked PR. Put the same check into `clean-sswts`.
7. **Change the rules and docs:**
   - `SAFETY.md:73`: replace "Never open a PR" with the PR-and-merge rule. Keep never-force and never push `main` directly.
   - Update `SKILL.md` (description "No PR", the comparison table) and `conductor/SKILL.md:116`.
8. **mailcrush review bots:** skip `roadmap/*` branches in `.github/workflows/claude-code-review.yml` (and `claude.yml` if it triggers on PRs), so every automation PR doesn't also fire a paid review.

## Phase 2: Queue supply (the limit on everything after this)

1. **Slicer step.** When the orchestrator returns `blocked_no_item`, slice one ticket that is blocked only because it's too broad before falling back to code-health. Today's candidates: koan-master DEV-66/68, slackagent DEV-94, knowledge-vault DEV-12, and the broad mailcrush builds.
   - The slices become PR-sized child tickets with a `blocked by` chain and the same `repo/*` label, filed with the `linear` CLI. Adapt `~/.claude/skills/to-issues`.
   - Never slice tickets that are blocked on a human decision or a credential.
2. **Decision flow.** At window open, the supervisor sends one push notification with the digest of Needs Input tickets, so Jake can clear it with `/unblock-roadmap` in about 10 minutes.
3. **One "credential hour"** for Jake to clear:
   - ableton-catalog DEV-120 (Anthropic credentials) and DEV-124 (Dropbox config)
   - mailcrush's GCP/Twilio blockers
   - typebeat DEV-118 (tunnel)
   - typey.site's Netlify publish and PostHog env settings
4. **Code-health per lane.** Code-health stops being a single global fallback that never chains. An idle lane may take its own repo's code-health ticket, capped at 1 code-health PR per repo per day, with the existing 24-hour cooldown kept. That cap guards against churn that looks manufactured on a flaggable public board.

## Phase 3: Sprint windows (idea 1)

Jake's spec (2026-10-06): **two sprints a day, every day — 10:30–15:30 and 16:00–21:00 local** —
across as many repos as are available. His work-org Superset sessions run under the same username,
so they count toward the same board, and the windows overlap his working day.

**Shipped (serial, until lanes land):** the plist ticks hourly inside the windows (10:30, 11:30, …,
14:30, 16:00, …, 20:00) with a 1-hour base cadence, and each tick chains feature ships for up to 55
minutes. Nothing fires overnight. Note: the 16:00 window crosses the UTC day boundary (8pm EDT).

**With lanes (Phase 4):** the plist launches `supervisor.py` once at 10:30 and once at 16:00, each
running its window to the end:
2. **No overnight runs.** No new dispatch in the last 45 minutes of the window; lanes already running finish.
3. The supervisor holds `caffeinate -i` while it runs. It skips the window, logging `skipped-superset-down`, if the Superset app isn't running.
4. **An empty queue ends the window early** instead of trickling bookkeeping runs that cost tokens and produce no PR. Keep the unchanged-verdict gate. A Linear ticket change reopens dispatch.

## Phase 4: Parallel lanes plus the deterministic supervisor (ideas 2, 3 and 4)

**Hard requirement (Jake): the job must keep working when Claude's usage is spent.** The
supervisor is plain Python with no model call; every role keeps a non-Claude first choice
(Codex orchestrates, Cursor builds, the reviewer is never Claude first). Claude stays a fallback only.

1. **New `supervisor.py`** in `~/.ai-tools/automations/advance-roadmap/`, launched by the Phase 3 plist:
   - singleton `flock`;
   - lanes: one per available repo — as many as there are dispatchable repos, with at most one
     lane per repo — capped only by provider headroom (step 4), not a fixed N;
   - a lane is done when its worker result file exists, its binding's `last_event_type` is `Stop` or `Failed`, and its PR is merged or a blocker is recorded;
   - when a lane frees, it pops the next queued item, re-checks that the repo is clean and not busy, and dispatches.
2. **Orchestrator multi-dispatch:**
   - `schemas/orchestrator-result.json` gets `dispatches: [...]`, a ranked list of up to 2N items across distinct repos, and takes `busy_repos` as input.
   - Change `ORCHESTRATOR.md` from "exactly one" (lines 28 and 443) to that list.
   - Re-run the orchestrator only when the queue drains or the Linear snapshot changes. That saves about 100–155k tokens per refill.
3. **Concurrency fixes:**
   - Replace the mkdir `run.lock` (`run.sh:206-222`) with per-repo `flock` plus the supervisor singleton.
   - Use per-lane `pending-plan-<repo>.json` and per-lane verdict state.
   - Give run stamps a lane suffix and millisecond resolution.
   - Remove the `runs.jsonl` last-line chain read at `run.sh:568`; lanes replace chaining.
   - Make writes to `providers-usage.json` atomic (tmp file plus rename, under `flock`) in `lib/usage.py:141-146, 630-659`.
   - Pass explicit previous-run log paths instead of "the newest `run-*.log` is this run" (`ORCHESTRATOR.md:149-153`).
4. **Provider-aware lanes (idea 4):**
   - Before each dispatch, call `usage.py pick-worker-chain`. Lane count is the smaller of N and what the providers' headroom allows.
   - Codex is both the orchestrator and the main reviewer, so its 5-hour window is the shared bottleneck. The reviewer chain already falls back to Claude.
   - Keep Cursor Auto as the primary worker for cost per PR.
5. **Tally (the PM view):** extend `gen-dashboard.py` and `serve.py` (:8421) with live lanes and a daily tally: PRs merged, estimated $/PR from the logged tokens, lane-hours used. Each lane's workspace also shows up in the Superset UI, where Jake can watch or steer it.

## Trade-offs to watch (hypotheses; the page says its floors will move)

- **Width vs. tokens per session:** every subagent, `exec` call and review round is a separate low-token session. Fanning out more raises width but dilutes tokens per session, so don't add fan-out for its own sake.
- **Median output:** if PR output is a median of daily counts, a zero day hurts more than a big day helps. After the Phase 0 baseline, consider keeping a reserve of ready items rather than draining the queue in one burst.
- **Anti-gaming:** the board is public and every entry can be flagged. Every PR must be a real ticket's change.

## Stale docs to fix in the same pass

- "Every six hours" in `ORCHESTRATOR.md:164,546`, `SAFETY.md:24`, `verdict.py:5` and `gen-dashboard.py:484`.
- `dotfiles/claude/instructions/superset-workspaces.md`: wrong port (`:48214` is the other org; J3u is `:48511`) and wrong worktree path (it's `~/.superset/worktrees/…`).
- `dotfiles/claude/CLAUDE.md`'s note that advance-roadmap pushes local `main`. It no longer will.
- The `_none.md` ledger still says slackagent is dirty.

## Verification

1. **Phase 1:** run one interactive `/advance-roadmap` on a repo with a ready item (code-health cooldowns lift on 10/7, between about 04:00 and 14:00). Confirm:
   - the worktree is under `~/.superset/worktrees/` and the root checkout is untouched;
   - `host.db` has a `terminal_agent_bindings` row for the workspace;
   - `workspace_pull_requests` links the PR and `merged_at` is set;
   - the production deploy is green;
   - after the next 2-hour publish, the leaderboard shows `agentPrsMerged` ≥ 1.
2. **Phase 3:** the plist loads (`launchctl print gui/$UID/com.jake.advance-roadmap`), nothing fires overnight, and with Superset closed the window logs a skip.
3. **Phase 4:** a dry run with N=2 on two repos. Confirm:
   - two workspaces, no lock collisions, both PRs merged;
   - `runs.jsonl` has two rows with distinct stamps;
   - killing one lane mid-run lets its per-lane pending plan resume it on the next refill.
4. **After 7 days,** compare the leaderboard numbers against the Phase 0 baseline: width ≥ 3, PRs/week ≥ 7.5, active days on track for ≥ 26/30, $/PR under the target band.

**Critical files:** `~/.ai-tools/automations/advance-roadmap/` (`run.sh`, `worker.sh`, `review.sh`, `lib/usage.py`, `lib/pendingplan.py`, `lib/verdict.py`, `gen-dashboard.py`, `serve.py`, `schemas/orchestrator-result.json`, the launchd plist and `install.sh`), `~/.ai-tools/skills/advance-roadmap/` (`SKILL.md`, `SAFETY.md`, `WORKER.md`, `CODE_HEALTH.md`), `~/.ai-tools/skills/conductor/` (`SKILL.md`, `ORCHESTRATOR.md`), the `clean-sswts` skill, and mailcrush's workflows. After approval, copy this plan to `~/.ai-tools/docs/plans/`.
