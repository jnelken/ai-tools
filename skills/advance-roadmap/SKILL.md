---
name: advance-roadmap
description: >-
  Ship planned items end-to-end, one per thread (a repo may run a few threads at once once a plan approver clears them), from a personal repo's ROADMAP.md, from docs/plans/ when the
  repo has no roadmap, or from a Linear issue carrying a repo label. Pick a qualifying repo under
  the personal code dir, implement the item in its own Superset workspace, verify with the repo's
  test/build, move it to Shipped, then open a PR and merge it. Never pushes code to main directly.
  When nothing is actionable anywhere, runs one
  bounded, behavior-preserving code-health refactor instead; after each clean ship, files what it
  noticed as code-health tickets. Use on-demand ("/advance-roadmap", "work a
  roadmap item", "advance the roadmap", "ship something off the roadmap") or via an unattended
  scheduled run. After merging, watches the production deploy (usually Vercel) and fixes forward
  until it's green, within a bounded number of attempts. Sibling of [[wrapup-repos]] but NOT the same: this one merges to main. Sibling of
  [[unblock-roadmap]] too: that skill only asks Jake questions and records his answers as
  directives; this is the only skill that ever acts on them.
---

> **Personal machines only (OMEN, JXIV).** `install.sh` doesn't link this skill or its automation on
> the work laptop (`~/dotfiles/bin/is-personal-machine`), and `run.sh` refuses to run there.

# Advance the roadmap

Take items from planned → shipped on `main`: one per **thread**, and a repo may run a few threads
side by side when the plan approver clears them. Stop cleanly with blockers recorded when there's
nothing to do.

**Every item ships as a merged PR from its own Superset workspace.** `run.sh` creates a workspace
(a git worktree under `~/.superset/worktrees/`, outside Dropbox) on the item's branch and runs the
worker in its terminal; the worker pushes the branch, opens a PR, and merges it once the review
passes. That's the only output Superset's Production Run leaderboard credits — a direct push to
`main` counts for nothing. With the Superset app down, a dispatch is skipped (`skipped-superset`)
and its plan kept for the next run. Merged workspaces are deleted by the next run, once Superset
has recorded the merge (`lib/workspaces.py`). Why and how: [`../../docs/plans/production-run.md`](../../docs/plans/production-run.md).

**Every item gets a Cursor review cycle; once that cycle is clean, Codex gets one final pass.**
Cursor reviews each round read-only, including its own work. Cursor handles findings from either
reviewer; Codex is never repeated after feedback. If findings remain after 3 Cursor rounds, mark
them in the code as `FIXME(advance-roadmap review)` (bugs) or
`TODO(advance-roadmap review)` (lesser) and list them in the summary. If either required reviewer is
unavailable, stop before merging. Codex runs only after the initial Cursor cycle is clean. If Codex
finds issues, Cursor handles them and gets up to 3 follow-up rounds. `runs.jsonl` records the review
phases and flags an unreviewed ship.

**Nothing to ship? Clean something up.** When a repo has no dispatchable item, its first thread runs
one bounded, behavior-preserving refactor (`work_kind: code_health`) — an open `code-health` ticket
first, otherwise an audit against a shared rubric — instead of stopping. And after every clean ship,
the worker runs a short code-health retrospective on the repo it just touched and files up to 2
deduped `code-health` tickets. Those tickets rank below all feature work. Rubric, retrospective and
refactor caps: [`CODE_HEALTH.md`](CODE_HEALTH.md). A code-health pass never runs beside a feature thread.

**One batch a day: 10:30, `daily_batch.py`.** launchd starts it once; it lists the personal repos and
plans each one in parallel (at most `ADVANCE_ROADMAP_LANES`, default 5, planners or workers at once).
No refills, no quota retries, no second batch the same calendar day. (`supervisor.py`'s sprint
windows are the retired scheduler; its helpers are still imported.) The batch runs in four phases:

1. **Plan.** One planner per repo (*thread 1*), read-only, picks one item and lists its
   `touchpoints`. Once thread 1 has planned a *feature*, a planner for the repo's *next thread*
   starts, shown the earlier threads' plans, up to `ADVANCE_ROADMAP_THREADS_PER_REPO` (default 2,
   at most 3; `--threads`). A later thread picks the next item that isn't theirs, owns no bookkeeping,
   and answers `no second thread` when there's nothing independent to do.
2. **Approve.** At the barrier — every planner finished, no worker started — each repo with two or
   more ready plans goes to the **plan approver** ([`PLAN_APPROVAL.md`](PLAN_APPROVAL.md)). It
   rules on every thread after the first: `approve` (parallel), `serialize` (starts after the named
   thread has merged) or `defer` (planned afresh tomorrow), and writes down every overlap it accepts. Thread 1
   is never deferred or serialized, but it does wait at the barrier for the slowest approval (the
   rulings run side by side). If the approver fails or can't run, the extra threads defer and nothing
   else changes. That is the only way two workers ever share a repo.
3. **Build.** Workers are released at the barrier, two threads of a repo at least 20s apart, a
   serialized thread only after its predecessor ends. Each worker's brief carries the approver's
   acknowledged conflicts and the sibling threads (WORKER.md *Threads*).
4. **Summarize.** When the last thread has stopped, `daily_batch.py` posts **one** Slack summary
   through Slackagent (ships first, no-item repos collapsed, threads grouped by repo). Individual
   runs inside a batch post nothing; the consecutive-failure alert is separate and still fires.
   A lone `run.sh` outside a batch still posts its own line.

**Never depends on Claude.** Every role has a non-Claude first choice: Codex orchestrates, Cursor
builds, the reviewer is never Claude first, and the scheduling around them is plain shell and
Python. Claude is only ever a fallback, so the job keeps shipping when Jake's Claude usage is spent.
Anything added here (a scheduler, a slicer, the plan approver) must keep that true: the approver is
Codex Sol first, Claude Opus only as the fallback. Code review uses Cursor rounds, then one Codex
final pass by default; the selectable `cross-provider` review strategy keeps the earlier Claude
fallbacks.

**Keep going while there's time (lone runs).** A `run.sh` outside a batch — a manual run — that
ships a feature cleanly (`shipped`, deploy green) starts another run — fresh orchestrator pass,
fresh item — as long as fewer than `ADVANCE_ROADMAP_CHAIN_MINUTES` (default 40; `0` = one item)
have passed since the first run started. Anything else (blocked, failed, red deploy, no item,
quota) ends the chain, and so does leaving the sprint window. A code-health ship never chains.
Interactive runs follow the same rule: after the worker reports a
feature `shipped`, go back to [`../conductor/ORCHESTRATOR.md`](../conductor/ORCHESTRATOR.md) Step 0 if
under 40 minutes. (For one-off human-driven work, use [[conductor]] instead.)

Candidate repos: direct children of the personal code dir, `CODE_DIR=$(~/.ai-tools/bin/personal-code-dir)` (`~/Dropbox/code` on
the personal machines; if it exits non-zero, this machine has no personal repos — stop). Check
`~/.claude/automations/advance-roadmap/allowlist.txt` first when present, and query Linear once for
pending directives — tickets carrying an unconsumed `## Directive` section, via the `linear` CLI
only (see `SAFETY.md`) — from [[unblock-roadmap]] (the only dirty-tree exception).

## Architecture (orchestrator / worker)

| Role | Default | May mutate? | Doc |
|---|---|---|---|
| **Orchestrator** | Codex **Sol** high → Claude Opus high | **No** | [`../conductor/ORCHESTRATOR.md`](../conductor/ORCHESTRATOR.md) |
| **Worker** | Cursor Auto → Codex Sol → Claude Opus | **Yes** | [`WORKER.md`](WORKER.md) |
| **Plan approver** | Codex **Sol** high → Claude Opus high | **No** | [`PLAN_APPROVAL.md`](PLAN_APPROVAL.md) |
| **Reviewer** | Selected strategy: Cursor rounds, then one Codex final pass by default | **No** | [`WORKER.md`](WORKER.md) Step 5b |
| **Safety** | — | binds both | [`SAFETY.md`](SAFETY.md) |

`run.sh` owns routing and `providers-usage.json` (`lib/usage.py`):

1. Refresh usage (Claude statusline, Codex app-server/session log, Cursor dashboard API ∪ prior limit hits).
2. Launch read-only orchestrator (**Sol first**), failing over to the next planner under its gate
   when one prints a limit banner.
3. Parse the `ORCHESTRATOR_RESULT_JSON` fence. No valid plan → record a `planner_error` and
   dispatch nothing (no worker, bookkeeping, pending plan or carried verdict).
4. Dispatch `worker.sh` (Cursor → Codex → Claude) in the item's Superset workspace terminal, with
   same-run failover on limits.
5. Persist usage for the next tick. Skip only when **no orchestrator** remains.

Every role — planner, worker, reviewer, plan approver — skips a provider over its usage gate:
Claude and Codex at 70% of the 5-hour window or 80% of the weekly one, Cursor at 95% of the pool
the role draws from (env-overridable; see `automations/README.md`). A run stopped by usage is
recorded as `quota-paused`, which never counts toward the error alert.
6. On a clean ship under the chain limit, re-exec for the next item (lone runs only; inside a daily
   batch `daily_batch.py` owns the lock, workspace cleanup, thread planning, plan approval and the
   one Slack summary, and nothing chains).

Linear routing labels:

- `do-next` is a priority tier above Urgent/P0: it puts an eligible ticket ahead of all fresh
  work in every repo on the next run, but never ahead of Step 0's interrupted-run reconciliation.
  Below it, work is taken in global Linear priority order (Urgent/P0 → High/P1 → Medium/P2 →
  Low/P3 → no priority), across repos — see `../conductor/ORCHESTRATOR.md` Step 1.
- `/goal` launches the selected worker through that provider's native durable goal command
  (`$goal` for Codex; `/goal` for Cursor and Claude).

Interactive `/advance-roadmap`: you are the orchestrator — follow
[`../conductor/ORCHESTRATOR.md`](../conductor/ORCHESTRATOR.md), emit the JSON fence, then dispatch
the worker as [[conductor]] describes (`worker.sh --request-file …`). **Do not implement in-process.**
For human-driven work on an item you name, call `/conductor` directly.

## How this differs from `wrapup-repos`

| | `wrapup-repos` | `advance-roadmap` |
|---|---|---|
| Input | dirty / recent | roadmap / Linear / plans item |
| Tree | expects dirty | **requires clean** (directive exception) |
| Remote | never | **merges a PR into `main`** |

## What to read next

1. [`SAFETY.md`](SAFETY.md)
2. [`../conductor/ORCHESTRATOR.md`](../conductor/ORCHESTRATOR.md) — Steps 0–2d + JSON protocol (lives in the [[conductor]] skill)
3. [`PLAN_APPROVAL.md`](PLAN_APPROVAL.md) — only if you are the plan approver
4. [`WORKER.md`](WORKER.md) — Steps 3–8 (including Step 5b's Cursor/Codex review cycle, Step 7b's deploy watch and Step 7c's retrospective) + worker result fence
5. [`CODE_HEALTH.md`](CODE_HEALTH.md) — the code-health rubric, retrospective, and refactor pass
