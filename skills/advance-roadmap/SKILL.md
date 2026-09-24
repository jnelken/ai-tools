---
name: advance-roadmap
description: >-
  Ship ONE planned item end-to-end from a personal repo's ROADMAP.md, from docs/plans/ when the
  repo has no roadmap, or from a Linear issue carrying a repo label. Pick a qualifying repo under
  ~/Dropbox/code, implement the item on a branch, verify with the repo's test/build, move it to
  Shipped, then merge to main locally and push. No PR. Use on-demand ("/advance-roadmap", "work a
  roadmap item", "advance the roadmap", "ship something off the roadmap") or via an unattended
  scheduled run. Sibling of [[wrapup-repos]] but NOT the same: this one pushes. Sibling of
  [[prepare-roadmap]] too: that skill only asks Jake questions and records his answers as
  directives; this is the only skill that ever acts on them.
---

# Advance the roadmap

Take exactly ONE item from planned → shipped on `main`, or stop cleanly with blockers recorded.

Candidate repos: direct children of `/Users/jake/Dropbox/code`. Check
`~/.claude/automations/advance-roadmap/allowlist.txt` first when present, and
`directives/<repo>.md` from [[prepare-roadmap]] (the only dirty-tree exception).

## Architecture (orchestrator / worker)

| Role | Default | May mutate? | Doc |
|---|---|---|---|
| **Orchestrator** | Codex **Sol** high → Claude Opus high | **No** | [`ORCHESTRATOR.md`](ORCHESTRATOR.md) |
| **Worker** | Cursor Auto → Codex Sol → Claude Opus | **Yes** | [`WORKER.md`](WORKER.md) |
| **Safety** | — | binds both | [`SAFETY.md`](SAFETY.md) |

`run.sh` owns routing and `providers-usage.json` (`lib/usage.py`):

1. Refresh usage (Claude statusline, Codex app-server/session log, Cursor dashboard API ∪ prior limit hits).
2. Launch read-only orchestrator (**Sol first**).
3. Parse the `ORCHESTRATOR_RESULT_JSON` fence.
4. Dispatch `worker.sh` (Cursor → Codex → Claude) with same-run failover on limits.
5. Persist usage for the next tick. Skip only when **no orchestrator** remains.

Linear routing labels:

- `do-next` puts an eligible ticket ahead of all fresh work on the next run, but never ahead of
  Step 0's interrupted-run reconciliation.
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
3. [`WORKER.md`](WORKER.md) — Steps 3–8 + worker result fence
