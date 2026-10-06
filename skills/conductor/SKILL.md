---
name: conductor
description: >-
  Human-driven "ship one item" without implementing in-process: the current Claude session conducts
  sub-agent CLI processes — Cursor (`agent`) builds on a branch, Codex (`codex exec`, read-only)
  reviews up to 3 rounds — then reports the result, merge commit and deploy status. Input is an item
  you name (Linear DEV-N, a ROADMAP.md entry, a docs/plans/ doc, or a plain brief) or "pick the next
  one" (runs the shared triage in ORCHESTRATOR.md). Personal `jnelken` repos only. Use on-demand
  ("/conductor", "conductor DEV-42", "have cursor build X and codex review it", "ship this with the
  worker pipeline"). The unattended scheduler is [[advance-roadmap]]; this is the same engine
  (worker.sh + review.sh) driven by a human.
---

> **Personal machines only (OMEN, JXIV).** Same gate as `advance-roadmap`.

# Conductor

You are the **conductor**. You decide *what* ships and *whether it's done*; sub-processes do the
building and the reviewing. **Do not implement in-process** — no edits to the target repo from this
session, not even "tiny" ones. If the builder misses something, send it back (a `resume` request),
don't patch it yourself.

| Role | Process | May mutate? |
|---|---|---|
| Conductor | this session | No (reads, request files, `worker.sh`, Linear reads) |
| Builder | `agent -p` (Cursor), launched by `worker.sh` | Yes — branch, commits, merge, push |
| Reviewer | `codex exec -s read-only`, launched by `review.sh` | No |

The contract the builder follows (Steps 2b–8: branch, implement, verify, review loop, roadmap
update, merge, push, deploy watch) is
[`../advance-roadmap/WORKER.md`](../advance-roadmap/WORKER.md); the hard rules binding everyone are
[`../advance-roadmap/SAFETY.md`](../advance-roadmap/SAFETY.md). Read both before dispatching.
Code-health work follows [`../advance-roadmap/CODE_HEALTH.md`](../advance-roadmap/CODE_HEALTH.md).

## 1. Choose the item

- **User named it** — resolve it to a repo under `CODE_DIR=$(~/.ai-tools/bin/personal-code-dir)`
  (non-zero exit → no personal repos on this machine; stop). A Linear issue's repo is its
  `repo/*` label and nothing else (SAFETY.md). Read the ticket with `linear issue view DEV-N`
  (CLI only, never MCP). Read the repo's `CLAUDE.md`, roadmap/plan and `package.json` scripts to
  write a real brief.
- **"Pick the next one"** — follow [`ORCHESTRATOR.md`](ORCHESTRATOR.md) Steps 1–2d for triage. Skip
  its Step 0 log/ledger reconciliation of killed unattended runs unless the repo is sitting on a
  `roadmap/*` branch with unmerged commits (then offer to resume it). Read the Linear snapshot by
  running `python3 ~/.claude/automations/advance-roadmap/lib/linearsnap.py --out <file>`.
- **Plain brief** (no roadmap/ticket) — fine. Set `linear_id` and `roadmap_path` to `null`; the
  builder skips the ticket/roadmap bookkeeping.

**Gates, checked by you before dispatch** (SAFETY.md has the full list): `origin` owner is
`jnelken`; `git status --porcelain` is empty; `~/dotfiles/bin/git-safe-to-autocommit <repo>`
exits 0; no live `.claude-sessions/*.md` (<15 min). You have a human, so on a failed gate or an
open product decision **ask them inline** instead of the scheduled path's Linear `Needs Input`
comment. Never stash, reset or commit their WIP to get a clean tree.

## 2. Dispatch the builder

Write a request file (scratchpad, not the repo) — the same shape `run.sh` produces:

```json
{
  "mode": "implement",
  "stamp": "<YYYYMMDD-HHMMSS>",
  "orchestrator": {
    "action": "dispatch_worker", "outcome_token": "pending-worker",
    "repo": "<dir name>", "item": "<DEV-N title | brief title>",
    "branch": "roadmap/<slug>", "roadmap_path": null, "linear_id": null,
    "worker_mode": "standard", "work_kind": "feature", "directive_ticket": null,
    "worker_brief": "<self-contained: scope, touchpoints, acceptance, risks, repo conventions>",
    "archives": [], "blockers": [], "considered": [], "summary": "<one line>"
  }
}
```

Use `"mode": "resume"` + `"action": "resume_worker"` + the existing branch to finish a parked one,
and `"work_kind": "code_health"` + a `roadmap/health-<slug>` branch for a refactor pass. Schema:
`~/.claude/automations/advance-roadmap/schemas/orchestrator-result.json`.

Then run (foreground; it can take a long time — use `run_in_background` and wait for completion,
don't poll):

```sh
~/.claude/automations/advance-roadmap/worker.sh \
  --stamp "<stamp>" --request-file "<req.json>" \
  --providers cursor \
  --result-file "<dir>/worker-result.json" --status-file "<dir>/worker-status.json"
```

`--providers cursor` pins the builder to Cursor. If Cursor is limited or missing, `worker.sh`
exits `3` and tells you; **ask the user** before failing over (`--providers "codex claude"`) —
unlike the scheduled run, you have someone to ask. The builder invokes
`review.sh --worker cursor`, so Codex reviews (read-only) and the builder fixes findings for up to
3 rounds; leftovers merge marked `FIXME/TODO(advance-roadmap review)`.

To have the builder stop short of merging, say so in `worker_brief` ("stop after Step 5b; leave the
branch, do not merge or push"). It reports `blocked-branch-left`; you then ask the user and send
a `resume` request to land it.

## 3. Verify and report

Read `worker-result.json` (`outcome`, `branch`, `merge_commit`, `deploy`, `review`, `summary`). Do
not take `shipped` on faith for anything that matters: confirm the merge commit is on
`origin/main` (`git -C <repo> branch -r --contains <sha>`), and that `review.rounds >= 1` (a ship
with no logged review is flagged). Then tell the user plainly:

- repo, item, outcome (`shipped` / `shipped-deploy-failed` / `blocked-branch-left` / `failed` /
  `limit_hit`), merge commit, deploy status and URL;
- review rounds and any `FIXME/TODO(advance-roadmap review)` markers left behind;
- anything the builder says needs a manual check (visual/by-ear QA).

On `failed`, `limit_hit` or an incomplete result, report what the log shows and ask how to proceed;
never retry silently in a loop. Don't clean up the builder's branch.

## What this skill does not do

No chaining, no provider-usage gating, no scheduling, no run dashboard — that's
[[advance-roadmap]]'s `run.sh`. It ships through the same PR flow (WORKER.md Step 7), but from a
branch in the root checkout rather than a Superset workspace, so its PRs aren't credited on the
Production Run leaderboard.
