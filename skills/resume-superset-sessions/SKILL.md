---
name: resume-superset-sessions
description: Use after a machine crash, power loss, or forced restart to bring Claude Code sessions that were running inside Superset workspace terminal tabs back to life. Superset restores the tabs themselves (same terminal, a fresh idle shell) but the claude process inside each one is gone; this skill relaunches `claude --resume <session-id>` in the exact same tab it died in, and separately sweeps for crashed sessions that were running in plain (non-Superset) terminals. Trigger phrases include "resume my crashed sessions", "the machine died, bring my claude sessions back", "restore interrupted claude tabs", "my computer restarted, resume everything", "power went out, get my sessions back". Distinguishes a session that crashed mid-flight from one the user exited on purpose - only the former gets auto-resumed.
---

# Resume Superset Sessions

Finds Superset terminal tabs that were hosting a live Claude Code session at
the moment of a crash/reboot, and relaunches `claude --resume <session-id>`
in the same tab. Also sweeps for crashed sessions that were never in a
Superset tab at all (plain terminal windows) and prints manual resume
commands for those, without touching them.

## What it does

When the machine loses power or crashes, Superset restores each workspace's
terminal tabs on the next launch: same `terminal_id`, a fresh idle `zsh -l`,
and a frozen visual snapshot of whatever was on screen at the moment of
death. That snapshot is **not recoverable data** — it's renderer-side xterm
state the Electron UI painted before the crash, not a live buffer. The
`claude` process that used to run in that tab is simply gone.

`detect-and-resume.sh` figures out, deterministically, which restored tabs
match that shape and resumes them via `claude --resume`. Tabs that were
cleanly exited, tabs that are still genuinely alive, and tabs with no claude
history at all are all left exactly as they are.

## How identification works

Ground truth comes from Superset's host database:
`~/.superset/host/<orgId>/host.db` (sqlite, WAL) — always opened
**read-only** (`sqlite3 "file:...?mode=ro"`), never written.

- `terminal_agent_bindings(terminal_id PK, workspace_id, agent_id,
  agent_session_id, started_at, ...)` — `agent_session_id` *is* the Claude
  Code session UUID. This row survives a reboot because Superset re-uses the
  same `terminal_id` when it restores a tab.
- `terminal_sessions(id PK, status, created_at, ...)` — `status='active'`
  only means "never explicitly disposed." It does **not** mean the tab (or
  the agent that used to be in it) is alive.

A restored tab is a **RESUME** candidate only when all of these hold:

1. It's in the daemon's live terminal list right now (`superset terminals
   list --workspace <id> --json`) — closed tabs are excluded there
   automatically.
2. A `terminal_agent_bindings` row exists for it with a non-null
   `agent_session_id`.
3. **The binding predates the pty**: `binding.started_at` is *earlier* than
   the tab's `createdAt`. That ordering means the shell itself was
   re-created *after* the agent was bound to it — the only way that happens
   is Superset recreating the pty on restart. A healthy, never-interrupted
   tab has the opposite order (bind happens after the shell already exists).
4. No live claude process still owns that session id (checked via
   `~/.claude/sessions/<pid>.json` + `kill -0`, plus a `pgrep` fallback).
5. A transcript exists for the session
   (`~/.claude/projects/*/<session-id>.jsonl`).
6. The tab's shell is currently idle — never send a resume command into a
   tab that's mid-command.

Tabs that fail only the ordering check (binding looks normal, but no claude
process owns the session) are classified **CLEAN** — the agent exited on its
own before the crash, and typing into that tab is a deliberate choice, not
an automatic one.

**Governing principle: no kill event, no crash.** A session with no live
process is never presumed dead-by-accident. Every RESUME/TO-BE-RESUMED
classification requires positive evidence that a kill event postdates the
session's last activity: the pty-recreation ordering proof for materialized
tabs, or a transcript last-write falling in the window just before boot time
for dormant tabs and the sweep. A session whose last activity is *after* the
most recent boot ended for some ordinary reason (user exit, tab close) and
is reported CLEAN — resuming it is a human decision.

### Dormant tabs (lazily-materialized workspaces)

Superset materializes a workspace's ptys **lazily**: after a reboot, a
restored tab does not exist in the daemon at all — `terminals list` omits it
and `terminals send` returns "Not found" — until the user first views that
workspace in the UI. The binding row in `host.db` is there the whole time,
so these sessions are still identifiable; they just can't be typed into yet.

The script reports these as **TO-BE-RESUMED**, naming the origin workspace.
Because a dormant tab lacks the `created_at` ordering proof (the pty was
never re-created), the substitute check is transcript mtime: the session
must have written its `.jsonl` within the lookback window before boot time,
i.e. it was actually alive when the machine went down. Two ways to resume
them:

1. Open the workspace in Superset (the tab materializes, `created_at`
   updates, the ordering check starts working) and rerun the skill, or
2. Rerun with `--create-tabs --apply` — resumes each one in a **new** tab in
   its origin workspace via `terminals create`. The dormant tab's binding
   row is untouched (different terminal_id), so nothing is overwritten.

The old ghost tab (frozen snapshot, idle shell) survives a `--create-tabs`
resume and will materialize whenever the workspace is next viewed. Add
`--close-ghosts` to dispose each one automatically — `terminals close`
works on dormant terminals (unlike `send`) — but only after the resumed
session is verified alive in its new tab, because closing a terminal
**clears its binding row**, destroying the session pointer. Same-tab
resumes never leave a ghost: the resume happens in the original tab and
the snapshot just scrolls up into history.

### The overwrite hazard this script protects against

`terminal_agent_bindings` is keyed on `terminal_id`. The moment a *new*
agent is launched in a restored tab, its row's `agent_session_id` is
overwritten — the old session id is gone from the DB with no history. That's
why the script snapshots every binding row for the target workspace(s) to
`~/.claude/state/session-recovery/bindings-<epoch>.json` **before** it sends
a single resume command in `--apply` mode. If anything goes sideways, the
pre-send state is on disk.

## Usage

Run from its own directory by absolute path — dry run first, always:

```bash
# Dry run, current workspace only (matches $PWD against workspace worktreePaths)
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh

# Dry run, every workspace on this host
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --all

# Dry run, one specific workspace from anywhere - matches id, name, branch,
# project name, or worktree path (partial, case-insensitive). Exact-id match
# wins outright; anything ambiguous errors out listing the matches.
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --workspace con-3485

# After reviewing the report, actually send the resume commands
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --apply
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --all --apply

# Also resume dormant tabs (workspaces not viewed in Superset since reboot)
# by opening NEW tabs in their origin workspaces; --close-ghosts disposes each
# old ghost tab once its session is verified alive in the new one
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --all --create-tabs --close-ghosts --apply

# Tune the non-Superset lookback window (default 24h before boot time)
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --window 48

# Skip the non-Superset global sweep entirely
bash /Users/jake/code/ai-tools/skills/resume-superset-sessions/detect-and-resume.sh --no-sweep
```

Show the user the dry-run report before doing anything else. If every
candidate comes back as a clean `RESUME` (all checks green, no
`NEEDS-MANUAL`), it's fine to go straight to `--apply` without asking again
— the dry run already showed exactly what will be sent. If any tab comes
back `NEEDS-MANUAL` or otherwise ambiguous, apply the unambiguous `RESUME`
candidates and hand the ambiguous ones to the user to decide individually —
don't guess on their behalf and don't skip applying the clear ones just
because others are unclear.

In `--apply` mode the script sends `cd <worktreePath> && claude --resume
<session-id>` into each candidate tab (never `--fork-session` — this must
land in the session's original working directory, since transcripts are
keyed by cwd), waits 2 seconds, and reads back the tab so you can confirm
claude actually came up. It never deletes anything and never writes to
`host.db`; the only tab it ever closes is an old dormant ghost under the
opt-in `--close-ghosts` flag, after verifying the resume.

## Limitations

- **Non-Superset terminals get manual commands only.** A session killed
  mid-turn in a plain terminal window (not a Superset tab) has no
  `terminal_agent_bindings` row to key off of at all. The global sweep finds
  these by transcript mtime falling in the window just before boot time and
  prints a `cd <cwd> && claude --resume <id>` for a human to run — it is
  never auto-resumed. Before calling anything "non-Superset" the sweep
  cross-references `host.db` by session id: a session whose workspace simply
  hasn't been viewed since reboot is reported as a dormant Superset tab with
  its origin workspace, not as a plain-terminal session.
- **The statusline `🪪<session-id>` fallback only helps sometimes.** It's
  used for live tabs that have *no* binding row (claude launched by hand,
  not by a Superset agent) to identify what's running in them. After a full
  reboot, the daemon's terminal buffer is empty — the frozen tab you see on
  screen is client-side rendering, not data the daemon persisted — so this
  fallback mostly pays off after an app-level restart, or for a future crash
  where the pty daemon itself survives.
- **A cleanly-exited session left sitting in an open tab is `CLEAN`, not
  `RESUME`.** The binding row still points at that old session id, but no
  process was interrupted — the agent exited normally before the crash ever
  happened. The script reports it as "previously exited (resume manually if
  wanted)" and does not touch it.
- **A tab this skill already resumed shows `order=reversed` + a live
  session forever after** — resuming via `terminals send` doesn't refresh
  the binding row. That combination is classified `LIVE (session
  re-attached after restore)` and left alone; it's the signature of a
  successful recovery, not a problem.
- Idle-detection (`f`) is itself best-effort: it tries to match the tab's
  shell process by start time under the pty daemon, and falls back to
  reading the last line of the live buffer for a bare prompt. If neither is
  conclusive, the tab is reported `NEEDS-MANUAL` rather than guessed at.

## What NOT to do

**Never relaunch an agent in a restored tab before the bindings snapshot has
been written.** `terminal_agent_bindings` is keyed on `terminal_id`, so a
new agent launch overwrites `agent_session_id` in place — there is no
history table. Once that row is overwritten, the old session id for that tab
is unrecoverable from the DB (the `.jsonl` transcript itself is still safe
independently, but you'd have no way to know which tab it used to belong
to). Always let the script's snapshot step run first; never hand-run
`superset terminals send ... claude --resume ...` against a tab this script
hasn't already classified as `RESUME`.
