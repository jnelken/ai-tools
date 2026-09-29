---
name: advance-roadmap
description: >-
  Ship planned items end-to-end, one per run, from a personal repo's ROADMAP.md, from docs/plans/ when the
  repo has no roadmap, or from a Linear issue carrying a repo label. Pick a qualifying repo under
  the personal code dir, implement the item on a branch, verify with the repo's test/build, move it to
  Shipped, then merge to main locally and push. No PR. Use on-demand ("/advance-roadmap", "work a
  roadmap item", "advance the roadmap", "ship something off the roadmap") or via an unattended
  scheduled run. After pushing, watches the production deploy (usually Vercel) and fixes forward
  until it's green, within a bounded number of attempts. Sibling of [[wrapup-repos]] but NOT the same: this one pushes. Sibling of
  [[unblock-roadmap]] too: that skill only asks Jake questions and records his answers as
  directives; this is the only skill that ever acts on them.
---

> **Personal machines only (OMEN, JXIV).** `install.sh` doesn't link this skill or its automation on
> the work laptop (`~/dotfiles/bin/is-personal-machine`), and `run.sh` refuses to run there.

# Advance the roadmap

Take exactly ONE item from planned → shipped on `main` per run, or stop cleanly with blockers recorded.

**Every item is reviewed by a second model before it merges.** The worker calls `review.sh`, which
has a different provider review the branch diff read-only; the worker fixes findings and re-reviews,
up to 3 rounds. Findings still open after round 3 merge anyway, marked in the code as
`FIXME(advance-roadmap review)` (bugs) or `TODO(advance-roadmap review)` (lesser) and listed in the
summary. `runs.jsonl` records the rounds and flags a ship with no logged review.

**Keep going while there's time.** A run that ships cleanly (`shipped`, deploy green) starts another
run — fresh orchestrator pass, fresh item — as long as fewer than 40 minutes have passed since the
first run started. Anything else (blocked, failed, red deploy, no item, quota) ends the chain. The
check happens between runs, so the last item may finish past 40 minutes. Scheduled runs chain in
`run.sh` (`ADVANCE_ROADMAP_CHAIN_MINUTES`, default 40; `0` = one item). Interactive runs follow the
same rule: after the worker reports `shipped`, go back to `ORCHESTRATOR.md` Step 0 if under 40
minutes.

Candidate repos: direct children of the personal code dir, `CODE_DIR=$(~/.ai-tools/bin/personal-code-dir)` (`~/Dropbox/code` on
the personal machines; if it exits non-zero, this machine has no personal repos — stop). Check
`~/.claude/automations/advance-roadmap/allowlist.txt` first when present, and query Linear once for
pending directives — tickets carrying an unconsumed `## Directive` section, via the `linear` CLI
only (see `SAFETY.md`) — from [[unblock-roadmap]] (the only dirty-tree exception).

## Architecture (orchestrator / worker)

| Role | Default | May mutate? | Doc |
|---|---|---|---|
| **Orchestrator** | Codex **Sol** high → Claude Opus high | **No** | [`ORCHESTRATOR.md`](ORCHESTRATOR.md) |
| **Worker** | Cursor Auto → Codex Sol → Claude Opus | **Yes** | [`WORKER.md`](WORKER.md) |
| **Reviewer** | a provider other than the worker's (Cursor ↔ Codex; Claude → Codex) | **No** | [`WORKER.md`](WORKER.md) Step 5b |
| **Safety** | — | binds both | [`SAFETY.md`](SAFETY.md) |

`run.sh` owns routing and `providers-usage.json` (`lib/usage.py`):

1. Refresh usage (Claude statusline, Codex app-server/session log, Cursor dashboard API ∪ prior limit hits).
2. Launch read-only orchestrator (**Sol first**).
3. Parse the `ORCHESTRATOR_RESULT_JSON` fence.
4. Dispatch `worker.sh` (Cursor → Codex → Claude) with same-run failover on limits.
5. Persist usage for the next tick. Skip only when **no orchestrator** remains.
6. On a clean ship under 40 minutes into the tick, re-exec for the next item.

Linear routing labels:

- `do-next` is a priority tier above Urgent/P0: it puts an eligible ticket ahead of all fresh
  work in every repo on the next run, but never ahead of Step 0's interrupted-run reconciliation.
  Below it, work is taken in global Linear priority order (Urgent/P0 → High/P1 → Medium/P2 →
  Low/P3 → no priority), across repos — see ORCHESTRATOR.md Step 1.
- `/goal` launches the selected worker through that provider's native durable goal command
  (`$goal` for Codex; `/goal` for Cursor and Claude).

Interactive `/advance-roadmap`: you are the orchestrator — follow `ORCHESTRATOR.md`, emit the
JSON fence, then run:

```sh
~/.claude/automations/advance-roadmap/worker.sh \
  --stamp "$(date +%Y%m%d-%H%M%S)" \
  --request-file /tmp/advance-req.json
```

**Do not implement in-process.**

## How this differs from `wrapup-repos`

| | `wrapup-repos` | `advance-roadmap` |
|---|---|---|
| Input | dirty / recent | roadmap / Linear / plans item |
| Tree | expects dirty | **requires clean** (directive exception) |
| Remote | never | **pushes `main`** |

## What to read next

1. [`SAFETY.md`](SAFETY.md)
2. [`ORCHESTRATOR.md`](ORCHESTRATOR.md) — Steps 0–2 + JSON protocol
3. [`WORKER.md`](WORKER.md) — Steps 3–8 (including Step 5b's cross-model review and Step 7b's deploy watch) + worker result fence
