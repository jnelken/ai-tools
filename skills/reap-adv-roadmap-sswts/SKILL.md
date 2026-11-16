---
name: reap-adv-roadmap-sswts
description: >-
  Safely clear leftover Superset workspaces (sswts) left by advance-roadmap worker
  runs — by finished-banner evidence on live tabs, or by dead-pane + clean + no
  diff against main when terminals are gone. Use when the user says "reap roadmap
  workspaces", "clear advance-roadmap sswts", "clean up finished roadmap tabs",
  "reap exit=0 finished workspaces", "reap dead panes", or after a batch of
  advance-roadmap runs left worktrees piled up. Complements clean-sswts (general
  PR-state sweep on non-roadmap / non-work machines) and workspaces.py cleanup
  (merge-recorded tagged workspaces).
---

# Reap advance-roadmap sswts

Advance-roadmap workers leave a durable banner in the workspace terminal when they
stop (`automations/advance-roadmap/worker.sh`):

```text
=== worker used=<provider> exit=<rc> finished <date> ===
```

Example: `=== worker used=cursor exit=0 finished Sun Nov 15 09:04:10 EST 2026 ===`

Those workspaces often linger even after the PR shipped — failover tabs, setup
shells, multi-provider retries, and **dead panes** (workspace still registered,
zero terminals). `workspaces.py cleanup` only reaps workspaces Superset has tagged
+ recorded as merged. This skill reaps by **terminal evidence**, and for dead
panes by **empty diff vs main**.

Related: [[clean-sswts]] (broader PR-state sweep; fine to overlap), [[self-destruct]]
(`reap-workspace.sh`), advance-roadmap `lib/workspaces.py cleanup` (merge-gated).

## Scope

Default: **all local projects** (roadmap leftovers span many repos). Narrow to one
project only when the user names it.

Never touch a **root checkout** (`worktreePath` equals the project's path from
`superset projects list`). Never delete a **dirty** worktree. Never delete a
**held** worktree (a live process cwd **is** the worktree or inside it — not
the reverse; `$HOME` cwd must not mark everything held). Never delete the
workspace the current session is cwd'd inside.

## Path A — tabs with finished banners

### Tab classification

For each terminal, read the buffer
(`superset terminals read --local --workspace <id> --terminal <tid>`) and classify
by the **last** matching finished banner:

```text
=== worker used=(\S+) exit=(\d+) finished (.+) ===
```

| Class | Rule |
|---|---|
| `finished0` | last banner has `exit=0` |
| `finishedN` | last banner has `exit≠0` (keep the code + parsed finish time) |
| `setup` | no worker banner near the end; package-manager install (`npm install` / `npm ci` / `pnpm` / `yarn` / `bun install`, `added N packages`, …) **or** a short idle shell after auto-open (dotfiles sync / prompt only) |
| `other` | anything else (live agent, empty, non-banner failure) |

Multi-tab is fine. Setup tabs are allowed alongside finished workers — Superset
auto-opens install shells when a workspace boots.

### Reap rule (Path A)

1. ≥1 terminal classified `finished0`.
2. Every terminal is `finished0`, `finishedN`, or `setup` (no `other`).
3. Every `finishedN` finish timestamp is **≤ the latest `finished0` timestamp**.
   Non-zero exits after the last success → **keep**.
4. Worktree clean.
5. Optional: `DEV-###` in successful tab tails matches the ticket in the workspace
   name/branch when present. Mismatch → surface, don't auto-reap.

Timestamps come from the banner's `finished <date>` field (not pane `createdAt`).

## Path B — dead panes (zero terminals)

Workspaces with **no terminals** have no finished banner left. Reap only when
there is nothing to lose against main:

1. Zero terminals (`superset terminals list` empty).
2. Worktree exists, is not a root checkout, is **clean**
   (`git status --short` empty), and is not held.
3. **No diff against main** — after one `git fetch origin --prune` per owning
   clone, `git -C <worktree> diff --quiet <main_ref>` succeeds (exit 0). Prefer
   `origin/main`, then `origin/master`, then local `main` / `master`. If no main
   ref resolves → **keep** (don't guess).
4. A non-empty diff, unpushed unique content, or any dirty/untracked file → **keep**.

Do **not** use PR state as a substitute for the empty-diff check on this path.
Squash-merged branches whose tip still differs from main stay kept here (Path A
or [[clean-sswts]] / merge-gated cleanup can still get them when evidence exists).

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

Prints reapable / keep buckets (`path=banner` or `path=dead-pane`). Reap only what
the rule marks reapable; surface the rest.

## Report shape

```text
Surveyed N local non-root → R reaped, K kept

Reaped:
  <project>  <branch>  path=banner     tabs=[finished0×2, setup×1]  last0=<date>
  <project>  <branch>  path=dead-pane  main=origin/main  diff=empty

Kept:
  <project>  <branch>  reason=nonzero-after-success …
  <project>  <branch>  reason=dead-pane-diff-vs-main
  <project>  <branch>  reason=other-tab
```

## Evolving this skill

When a new false positive/negative shows up in a live sweep, tighten the rule
here (and in `scripts/survey.py`) in the same change — don't leave the heuristic
only in chat history.
