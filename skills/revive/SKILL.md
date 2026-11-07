---
name: revive
description: Revive a dead Claude Code session (a Remote Control thread whose host went offline, a terminal session that crashed, a Superset tab whose claude process died), but first find out why it died and propose a guardrail so it doesn't happen again. Takes a claude.ai/code/cshk_ link, a session_ id, a local session UUID, a local_<uuid> desktop id or a title fragment; with no argument it looks for unarchived dead sessions and asks which one. Works across machines (this Mac and the OMEN via `ssh omen-wsl`). Trigger phrases include "/revive", "/undead", "revive this session", "resume this dead thread", "this remote session died", "bring this claude.ai session back", "why did this session die", "find my dead sessions".
---

# revive

Three steps, always in this order: **find it → autopsy → guardrail → revive.** Never resume
first and diagnose later: a resumed session overwrites the evidence (its transcript tail,
"last active" time, the claude.ai connection state).

Helper: `scripts/revive.py`. It is read-only, stdlib-only Python, and prints JSON. It runs
remotely through stdin, so the remote host doesn't need ai-tools installed:

```bash
S=~/.claude/skills/revive/scripts/revive.py
python3 $S <cmd> ...                          # this machine
ssh omen-wsl python3 - <cmd> ... < $S        # OMEN (WSL; also reads C:\Users\*\.claude)
```

Subcommands: `parse <arg>`, `locate <uuid>`, `match --created ISO [--updated ISO] [--title T]`,
`scan [--days N]` and `autopsy <uuid|path>`. Run `--help` for the details.

## Facts this skill relies on

- **A cshk link carries the API session id.** In `claude.ai/code/cshk_<X>`, the first 24
  characters of `<X>` are the `session_` id (`cshk_01B3LiTcBTrJso1eFknbVf334Rj…` →
  `session_01B3LiTcBTrJso1eFknbVf33`). `revive.py parse` does this.
- **No file maps a dead bridge session back to its local UUID.** While the process is alive,
  `~/.claude/sessions/<pid>.json` records `bridgeSessionId`. The file is gone once the
  process dies. Map the session instead by the claude.ai title plus its created/updated
  times: `revive.py match` takes the transcript whose first and last timestamps fall within a
  few seconds of them. A score under about 60s with `title_in_file: true` is a confident match.
- **The transcript lives on the machine that hosted the session.** Remote Control
  (`entrypoint: sdk-cli`) and terminal (`cli`) sessions write `~/.claude/projects/*/<uuid>.jsonl`
  on that host only. Check both machines: this one, then `omen-wsl`, or `mcp__memex__search`
  the title, which searches both.
- **Remote Control can reattach to the original thread** (per `claude remote-control --help`; not yet verified for a session dead for days — the step 4 check decides). `claude remote-control
  --session-id <session_…>`, run in the original cwd on the original host, reattaches to that
  claude.ai thread. `claude --resume <uuid> --remote-control "<title>"` keeps the
  conversation but opens a **new** claude.ai thread. Try reattach first.
- **Look at the session list without signing in on the user's behalf.** claude.ai gates Remote
  Control threads behind "Sign in again to verify your device". Click **Not now** and read the
  title, sidebar and "Your computer is no longer connected" banner. Signing in is the user's
  job.

## 1. Find the session

**With an argument.** Run `revive.py parse <arg>`, then branch on `kind`:

| kind | how to find the transcript |
|---|---|
| `uuid` | `locate <uuid>` here, then on `omen-wsl` |
| `desktop_session_id` | `mcp__ccd_session_mgmt__get_session` for its cwd and state; the transcript has the same UUID |
| `claude_ai_link` / `bridge_session_id` | Get the title and the created/updated times: use a session-listing tool if one is connected (ToolSearch for `claude-code-remote list_sessions`; its records include `title`, `created_at`, `updated_at`, `environment_kind: bridge`, `connection_status`). Otherwise open the link in Claude in Chrome, click Not now, and read the title. Then run `match` on each machine. Without exact times, memex-search the title to get a time window and `match` with `--slack-seconds 3600`. |
| `title_or_unknown` | memex-search the text across machines, then confirm with `match` or `locate` |

**Without an argument.** Collect candidates, newest first:

1. claude.ai: **unarchived** sessions whose host is gone. From the listing tool these are
   `environment_kind: bridge` with a status other than RUNNING and no live process. In Chrome
   they are sidebar rows with the Remote Control badge whose page says the computer is no
   longer connected. Skip anything archived, and skip `cloud` sessions, which can't die this way.
2. `revive.py scan --days 14`, here and on `omen-wsl`. It returns sessions with more than one
   turn and no live process that died mid-turn (`last_entry` is `tool_result` or
   `user_prompt`) or ended on an API error.
3. Superset tabs whose claude died in a crash: `resume-sswts`'s `detect-and-resume.sh` in
   detect mode. If several tabs died in one reboot, hand the batch to `/resume-sswts` and only
   autopsy the reboot here.

If more than one candidate is left, ask the user with AskUserQuestion. Each option shows the
title or first prompt, the host, the cwd and when it died. With exactly one, say which and go on.

**Confirm it is actually dead.** Check `live` in the autopsy and claude.ai's connection
state. If a process still holds it, the session isn't dead: tell the user where it is running
and stop.

## 2. Autopsy: why did it die?

Run `revive.py autopsy <uuid>` on the host. Then add the host's own evidence for the window
from `last_ts` to roughly 12h after:

- **Mac.** `power_events` (pmset keeps about a week; `covers_death: false` means no data, not
  "no sleep"). Read `~/Library/Logs/agenton.log` too, which records lid sleep, Wi-Fi changes
  and busy-session counts per minute (see the `agenton` skill).
- **OMEN.**
  `ssh omen-wsl "powershell.exe -NoProfile -Command \"Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,42,1,1074,6005,6006,6008; StartTime='<start>'; EndTime='<end>'} | Select TimeCreated,Id,Message | Format-List\""`.
  Event 41/6008 is an unclean shutdown, 1074 a restart initiated by a person or app, 42 and
  1 are sleep and wake. Also check that `CLAUDE_CODE_OAUTH_TOKEN` is set (report only
  set/length, **never print the value**), the WSL idle settings in `.wslconfig`, and the
  "Claude RC Keep Awake" task's last result.

Read the evidence against this table. Give the cause one of three labels: **confirmed** (a
direct signal), **likely** (circumstantial) or **unknown**. Never invent one.

| Cause | Evidence | Guardrail to propose |
|---|---|---|
| Host slept or lid closed | a power Sleep event right after `last_ts`; agenton shows lid sleep | keep-awake while sessions are busy (agenton / caffeinate, Windows power plan, `request_keep_awake`) |
| Host rebooted or crashed | Start/Shutdown (Mac); 41/6008/1074 (Windows) | auto-relaunch Remote Control at login (launchd / Task Scheduler) in the same cwd with `--session-id` |
| CLI auto-updated and the RC server restarted | `later_sessions_same_cwd` shows several `sdk-cli` sessions starting together on a newer `version` | have the RC launcher reattach (`--session-id`, or `-c` within 4h) instead of pre-creating a fresh session; or pin updates to a window |
| OAuth expired or logged out | `signals: auth`; the later session starts with /login | `claude setup-token` with `CLAUDE_CODE_OAUTH_TOKEN` exported to every launcher (WSLENV on the OMEN) |
| Rate or usage limit | `signals: rate_limit`, `tail_api_error` | none, or a scheduling change; not a host bug |
| Context exhausted | `signals: context` | earlier `/compact` or `/handoff`; not a revival candidate without compacting |
| Network or VPN drop | `signals: network`; agenton shows a Wi-Fi change | none usually; mention it |
| Killed or closed by hand | `signals: killed` or `interrupted`, no power event | none; ask the user whether it was on purpose |
| WSL shut down (OMEN) | WSL uptime starts after `last_ts`; no Windows reboot | `vmIdleTimeout` / `instanceIdleTimeout`; launch RC from Windows, not inside WSL |

Before proposing a guardrail, check that it doesn't already exist or have a ticket. Use the
`linear` CLI (see the `linear-cli` skill; DEV-325 already covers OMEN offline resilience) and
grep ai-tools and dotfiles for the setting. If it is already in place and failed anyway, the
guardrail is fixing it, not adding a second one.

## 3. Propose the guardrail (before resuming)

Show the diagnosis in a few lines: cause and confidence, the evidence, and the guardrail,
named concretely (which file or setting, which repo). Then ask with AskUserQuestion:

- **Apply now.** Make the change in its dev checkout (ai-tools at `~/code/ai-tools` or
  `~/Dropbox/code/ai-tools`, dotfiles, or the host's settings), then commit and push per that
  repo's rules.
- **File it.** Use `jnelken-linear` for the right repo, with the autopsy as evidence.
- **Skip.**

When the cause is `unknown`, propose an observability guardrail instead, one that would
capture the cause next time (for example a debug log file in the RC launcher, or agenton
coverage on that host).

## 4. Revive

Revive only after step 3 is answered. Start in the original **cwd** on the original **host**:

- **Remote Control thread: try reattach once, then fall back to a new thread.** Run each
  in its own tmux session in the original cwd so it outlives this turn, and read the result
  with `tmux capture-pane`:
  1. **Reattach (keeps the original link):**
     `tmux new-session -d -s revive-<slug> -c <cwd> "claude remote-control --session-id <session_…>"`.
     Success prints `✔ <title>` and the thread's claude.ai URL. Reload the cshk link in Chrome;
     the "no longer connected" banner should be gone.
  2. **If that fails for any reason, fall back right away.** Don't stop servers, kill
     processes or wait for the folder to free up:
     `tmux new-session -d -s revive-<slug> -c <cwd> "claude --resume <uuid> --remote-control '<title>'"`.
     That loads the whole conversation into a **new** claude.ai thread. Success shows the
     transcript and `/remote-control is active · … https://claude.ai/code/session_…`. Give
     the user that URL. Once they confirm it works, offer to archive the old cshk thread
     (thread menu ▾ → Archive; ask first). The fallback runs fine alongside other servers in
     the same folder; it was verified on 2026-11-07 while another reattach held it.

  Reattach failures seen so far, all of which go straight to the fallback:
  - "This folder is already served by a terminal `claude remote-control`": a server for that
    cwd is already running, such as the `claude-rc-daemon` LaunchAgent or another reattach.
    Leave it running.
  - "Environment env_… is already being served by … (pid N)": threads from the same dead
    server share one environment, and only one process can serve it. Never kill a reattach
    another session started.
- **Terminal or Superset session.** Run `claude --resume <uuid>` in a new tab in that cwd.
- **OMEN-hosted.** Try launching on the Windows side from SSH, for example
  `ssh omen-wsl "cd /mnt/d/code && powershell.exe -NoProfile -Command \"Start-Process wt.exe -WorkingDirectory 'D:\code' -ArgumentList 'claude','remote-control','--session-id','session_…'\""`.
  Verify it by the claude.ai banner. If reattach fails, fall back to `'claude','--resume','<uuid>','--remote-control','<title>'`
  the same way. If the launch doesn't reach the desktop (SSH can't
  always start GUI processes), give the user the exact command to run at the PC.

**If it died mid-turn** (`last_entry` is `tool_result` or `user_prompt`), don't let it
blindly redo the last action. Make the revived session's first message say that it was
interrupted at `<last tool/prompt>` by `<cause>`, and that it should check the current state
(git status, PR, files) before continuing.

## 5. Report

Report the session (title, host, cwd, UUID), the cause and its confidence, what happened to
the guardrail (applied with commit, filed with ticket link, or skipped), where it is running
now and how to reach it, and the old thread's state. End with the status line.
