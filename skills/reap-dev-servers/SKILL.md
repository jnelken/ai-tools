---
name: reap-dev-servers
description: Use when a dev server (tsx watch, vite, next dev, nodemon, etc.) is stuck holding a port after a Claude Code session ended non-gracefully (disconnect/crash), or when proactively checking your machine for orphaned background dev-server processes left over from past sessions. Trigger phrases include "clean up orphaned dev servers", "check for stuck tsx processes", "kill orphaned dev servers", "port already in use, what's holding it". Distinguishes genuinely orphaned processes from ones still actively supervised by a live Claude session or an open terminal window, so it won't kill someone else's active work.
---

# Reap Dev Servers

Finds Node dev-server processes squatting on a TCP port and kills only the ones that are actually orphaned — left running after their owning Claude Code session or terminal disappeared without cleaning up after itself.

## Why this is needed

Claude Code's Bash tool commonly runs a repo's dev server (`npm run dev`, `tsx --watch`, `vite`, ...) backgrounded so it can keep iterating against a running server. If the session ends cleanly, its background processes get reaped. If it ends hard — disconnect, crash, force-quit — the process tree can be left running, still bound to the port, blocking the next `npm run dev` with "address already in use."

The hard part isn't finding processes on a port (`lsof -iTCP -sTCP:LISTEN`) — it's telling an orphan apart from a process someone else is still actively using. Two processes can carry the exact same-looking title and one is abandoned garbage while the other is a teammate's (or your own other worktree's) live session. Killing the wrong one is a real cost, not a hypothetical — verify this from a second angle whenever confidence is anything less than certain.

## What NOT to assume

- **Don't trust title matching alone.** The labeling scheme (`{agent}-{script}-{branch}`, set by `set-process-name.sh` via `NODE_OPTIONS`) tags both orphaned and perfectly healthy processes identically. A process titled `c-dev-some-branch` is not evidence of anything by itself.
- **Don't trust "reached launchd (PID 1) while walking the ancestry" as orphan evidence on its own.** This was the first, tempting design and it's wrong: processes the Claude Code harness spawns via the Bash tool are detached straight to launchd *by design*, whether or not the owning session is still alive. A perfectly healthy, actively-used background dev server's wrapping shell shows `ppid=1` immediately — reaching launchd fast is completely normal for this category, not a sign of anything.

## The two-track classification `reap.sh` actually uses

**Track A — terminal-owned processes.** Walk the parent chain looking for a recognized terminal/multiplexer anchor (a Superset `pty-daemon`, `Terminal.app`, `iTerm`, `tmux`, `screen`, ...) before reaching launchd. If one shows up, a real terminal window owns this process — leave it alone, no matter how long it's been running or how idle it looks. This is the case for anything started directly in a human's terminal pane rather than via Claude's Bash tool (these often won't even carry the `{agent}-...` title scheme at all — e.g. an app that sets its own `process.title`).

**Track B — Claude-Bash-tool-spawned processes.** These never resolve via Track A (see above), so instead: extract the branch encoded in the title, then check whether *any* currently-running `claude` process's cwd is checked out on that same branch (`git -C <cwd> rev-parse --abbrev-ref HEAD`, sanitized the same way the labeling hook sanitizes it). A match means a live session still owns it. No match anywhere means the session that spawned it is gone — orphaned, safe to kill.

**Anything that doesn't cleanly resolve either way is reported as `UNCERTAIN` and never auto-killed.** This includes titles that don't match the labeling scheme at all and have no terminal anchor either — when in doubt, ask rather than guess.

## Killing an orphan correctly

Don't just kill the port-holding leaf process — if a supervisor above it (e.g. `tsx watch`) is still alive, it'll just respawn a replacement and you'll be chasing a new PID. `reap.sh` enumerates the *entire* subtree under the orphaned process, SIGTERMs all of it, re-checks whether the port actually freed up, and escalates to SIGKILL on whatever's left if it didn't (some processes — `npm` in particular — don't reliably forward signals to children).

## Usage

```bash
# Default: scan, auto-kill high-confidence orphans, report everything found (including what was left alone and why)
bash reap.sh

# Report-only: scan and print findings, never kills. This is what the SessionStart hook uses —
# silent when nothing's wrong, flags orphaned/uncertain candidates when it finds them.
bash reap.sh --report-only

# Dry run: same full reporting as the default mode, but never actually kills anything.
# Use this to sanity-check classification against whatever's really running before trusting it.
bash reap.sh --dry-run
```

When invoked as a skill (not via the hook), run `bash reap.sh` and relay its output to the user: what got killed and why, what's still uncertain and needs their call, and — if asked for a full picture — what was found active and left alone.

## Extending the anchor/branch detection

If you hit a false `UNCERTAIN` for a legitimate terminal app not in the anchor pattern (`ANCHOR_PATTERN` in `reap.sh`), or a live-session agent whose initial isn't in the known set (`AGENT_INITIALS`), add it there rather than loosening the matching logic generally — the conservative defaults (uncertain over orphaned, terminal-owned over guessed) are load-bearing, not incidental.
