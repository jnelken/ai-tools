---
name: advance-roadmap
description: >-
  Ship planned items end-to-end, one per run, from a personal repo's ROADMAP.md, from docs/plans/ when the
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

Take exactly ONE item from planned → shipped on `main` per run, or stop cleanly with blockers recorded.

**Every item ships as a merged PR from its own Superset workspace.** `run.sh` creates a workspace
(a git worktree under `~/.superset/worktrees/`, outside Dropbox) on the item's branch and runs the
worker in its terminal; the worker pushes the branch, opens a PR, and merges it once the review
passes. That's the only output Superset's Production Run leaderboard credits — a direct push to
`main` counts for nothing. With the Superset app down, a dispatch is skipped (`skipped-superset`)
and its plan kept for the next run. Merged workspaces are deleted by the next run, once Superset
has recorded the merge (`lib/workspaces.py`). Why and how: [`../../docs/plans/production-run.md`](../../docs/plans/production-run.md).

**Every item is reviewed by a second model before it merges.** The worker calls `review.sh`, which
has a different provider review the branch diff read-only; the worker fixes findings and re-reviews,
up to 3 rounds. Findings still open after round 3 merge anyway, marked in the code as
`FIXME(advance-roadmap review)` (bugs) or `TODO(advance-roadmap review)` (lesser) and listed in the
summary. `runs.jsonl` records the rounds and flags a ship with no logged review.

**Nothing to ship? Clean something up.** When no item is dispatchable in any repo, the orchestrator
picks one repo for a bounded, behavior-preserving refactor (`work_kind: code_health`) — an open
`code-health` ticket first, otherwise an audit against a shared rubric — instead of stopping. And
after every clean ship, the worker runs a short code-health retrospective on the repo it just
touched and files up to 2 deduped `code-health` tickets. Those tickets rank below all feature work.
Rubric, retrospective and refactor caps: [`CODE_HEALTH.md`](CODE_HEALTH.md).

**Sprints in parallel lanes, twice a day: 10:30–15:30 and 16:00–21:00.** launchd starts
`supervisor.py` at a window's first tick; the later hourly ticks are a watchdog that exits while
it's alive. The supervisor keeps one **lane** busy per available repo (`run.sh` in lane mode, at
most `ADVANCE_ROADMAP_LANES`, never two on one repo): lanes start one at a time, each told which
repos the others hold; a finished lane's slot refills; a lane that finds no work stops new lanes
until a repo frees up or a no-model fingerprint check sees the world change. No new lane in a
window's last 30 minutes, and nothing overnight — lone overnight runs add one-session slots that
drag Superset's width median down, while work packed into the windows overlaps Jake's own
sessions (including his work org's, same username). Lanes don't chain; the supervisor refills
instead. Why: [`../../docs/plans/production-run.md`](../../docs/plans/production-run.md).

**Never depends on Claude.** Every role has a non-Claude first choice: Codex orchestrates, Cursor
builds, the reviewer is never Claude first, and the scheduling around them is plain shell and
Python. Claude is only ever a fallback, so the job keeps shipping when Jake's Claude usage is spent.
Anything added here (a supervisor, a slicer) must keep that true.

**Keep going while there's time (lone runs).** A `run.sh` outside lane mode — a manual run — that
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
| **Reviewer** | a provider other than the worker's (Cursor ↔ Codex; Claude → Codex) | **No** | [`WORKER.md`](WORKER.md) Step 5b |
| **Safety** | — | binds both | [`SAFETY.md`](SAFETY.md) |

`run.sh` owns routing and `providers-usage.json` (`lib/usage.py`):

1. Refresh usage (Claude statusline, Codex app-server/session log, Cursor dashboard API ∪ prior limit hits).
2. Launch read-only orchestrator (**Sol first**).
3. Parse the `ORCHESTRATOR_RESULT_JSON` fence.
4. Dispatch `worker.sh` (Cursor → Codex → Claude) in the item's Superset workspace terminal, with
   same-run failover on limits.
5. Persist usage for the next tick. Skip only when **no orchestrator** remains.
6. On a clean ship under the chain limit, re-exec for the next item (lone runs only; in lane mode
   `supervisor.py` refills the lane instead, and owns the lock, the window and workspace cleanup).

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
3. [`WORKER.md`](WORKER.md) — Steps 3–8 (including Step 5b's cross-model review, Step 7b's deploy watch and Step 7c's retrospective) + worker result fence
4. [`CODE_HEALTH.md`](CODE_HEALTH.md) — the code-health rubric, retrospective, and refactor pass
