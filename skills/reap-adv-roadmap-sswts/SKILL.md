---
name: reap-adv-roadmap-sswts
description: >-
  Safely clear leftover Superset workspaces (sswts) left by advance-roadmap worker
  runs, using the worker's finished banner as the primary signal (not PR state).
  Use when the user says "reap roadmap workspaces", "clear advance-roadmap sswts",
  "clean up finished roadmap tabs", "reap exit=0 finished workspaces", or after a
  batch of advance-roadmap runs left worktrees piled up. Complements clean-sswts
  (PR-state sweep) and workspaces.py cleanup (merge-recorded tagged workspaces).
---

# Reap advance-roadmap sswts

Advance-roadmap workers leave a durable banner in the workspace terminal when they
stop (`automations/advance-roadmap/worker.sh`):

```text
=== worker used=<provider> exit=<rc> finished <date> ===
```

Example: `=== worker used=cursor exit=0 finished Sun Nov 15 09:04:10 EST 2026 ===`

Those workspaces often linger even after the PR shipped — failover tabs, setup
shells, and multi-provider retries make a naive "all exit=0" check too strict, and
`workspaces.py cleanup` only reaps workspaces Superset has tagged + recorded as
merged. This skill reaps by **terminal evidence**.

Related: [[clean-sswts]] (PR-state sweep across projects), [[self-destruct]]
(`reap-workspace.sh`), advance-roadmap `lib/workspaces.py cleanup` (merge-gated).

## Scope

Default: **all local projects** (roadmap leftovers span many repos). Narrow to one
project only when the user names it.

Never touch a **root checkout** (`worktreePath` equals the project's path from
`superset projects list`). Never delete a **dirty** worktree. Never delete the
workspace the current session is cwd'd inside.

## Tab classification

For each terminal in a workspace, read the buffer
(`superset terminals read --local --workspace <id> --terminal <tid>`) and classify
by the **last** matching finished banner (regex):

```text
=== worker used=(\S+) exit=(\d+) finished (.+) ===
```

| Class | Rule |
|---|---|
| `finished0` | last banner has `exit=0` |
| `finishedN` | last banner has `exit≠0` (keep the code + parsed finish time) |
| `setup` | no worker banner near the end; buffer is package-manager install (`npm install` / `npm ci` / `pnpm` / `yarn` / `bun install`, `added N packages`, …) **or** a short idle shell after auto-open (dotfiles sync / prompt only) |
| `other` | anything else (live agent, empty, non-banner failure) |

Multi-tab is fine. Setup tabs are allowed alongside finished workers — Superset
auto-opens install shells when a workspace boots.

## Reap rule

A non-root workspace is **reapable** when:

1. It has ≥1 terminal classified `finished0`.
2. Every terminal is `finished0`, `finishedN`, or `setup` (no `other`).
3. Every `finishedN` finish timestamp is **≤ the latest `finished0` timestamp**.
   Non-zero exits after the last success mean a later failure — **keep**.
4. Worktree is clean (`git status --short` empty). Prefer structural root check
   over name heuristics.
5. Optional consistency check: `DEV-###` (or similar) in the successful tabs'
   tails matches the ticket embedded in the workspace name/branch when one is
   present. Mismatch → surface, don't auto-reap.

Timestamps come from the banner's `finished <date>` field (not `createdAt` on
the pane — that is when the tab opened). Parse forms like
`Sun Nov 15 09:04:10 EST 2026`.

## Delete path

One call per workspace, sequential (each touches the owning clone):

```bash
REAP=~/.claude/skills/self-destruct/scripts/reap-workspace.sh
sh "$REAP" <id> <project-root> <worktreePath> <branch>
```

Report each `reaped …` / `FAILED …` line. Recovery SHAs land in
`~/.claude/state/self-destruct.log`.

## Survey helper

```bash
python3 ~/.claude/skills/reap-adv-roadmap-sswts/scripts/survey.py
python3 ~/.claude/skills/reap-adv-roadmap-sswts/scripts/survey.py --project mailcrush
python3 ~/.claude/skills/reap-adv-roadmap-sswts/scripts/survey.py --json
```

Prints reapable / keep buckets with exit ordering and tickets. Reap only what the
rule marks reapable; surface the rest for a human call.

## Report shape

```text
Surveyed N local non-root workspaces → R reaped, K kept

Reaped:
  <project>  <branch>  tabs=[finished0×2, setup×1]  last0=<date>  ticket=DEV-329

Kept:
  <project>  <branch>  reason=nonzero-after-success (exit=124 @ <date> > last0)
  <project>  <branch>  reason=dirty
  <project>  <branch>  reason=other-tab
```

## Evolving this skill

When a new false positive/negative shows up in a live sweep, tighten the rule
here (and in `scripts/survey.py`) in the same change — don't leave the heuristic
only in chat history.
