---
name: agenton
description: Use when the user asks about the `agenton` lid-sleep keeper or its log (~/Library/Logs/agenton.log) — why Wi-Fi dropped, whether the laptop switched from building Wi-Fi to a phone hotspot, how many Claude/Codex sessions were busy at a given time, whether the Mac slept overnight — or wants to change how it logs (network names/icons, session counts, line format). Trigger phrases include "check the agenton log", "did my laptop fall back to the hotspot", "why is the agenton line missing a minute", "name this network in agenton", "agenton format". Claude never runs `agenton` itself; it reads the log and edits the script.
---

# agenton

`agenton [MINUTES]` is a zsh function the user runs in their own terminal. It disables lid sleep (`sudo pmset -a disablesleep 1`) for N minutes so background agents keep running with the lid closed, appends one line per wall-clock minute to `~/Library/Logs/agenton.log`, then restores sleep. `agentoff` restores sleep immediately; `agentlog [N]` tails the log.

The script is `agenton.zsh` in this skill directory. `~/dotfiles/zsh/lib/45-agenton.zsh` sources it from the deploy clone (`~/.ai-tools/skills/agenton/agenton.zsh`), guarded so a machine without ai-tools still starts a shell.

## Do not run it

`agenton` needs the user's Touch ID/password for `sudo`, blocks its terminal for the whole duration, and changes machine sleep behavior. Tell the user to run it; never invoke `agenton` or `agentoff` from Claude. Reading the log and sourcing the script to test a single function (e.g. `zsh -c 'source agenton.zsh; _agenton_network'`) is fine. To exercise the whole loop safely, stub sudo and point the log at the scratchpad: `AGENTON_LOG=<scratch>/t.log perl -e 'alarm 6; exec @ARGV' zsh -c 'sudo(){ return 0; }; source agenton.zsh; agent_timed 1'`.

## Reading the log

One line per minute:

```
Oct 27 - 11:00am     🏢      WiFi ✓  101ms    claude: ⚡1/12  codex: ⚡0/1
Oct 27 - 11:07am     📱      WiFi ✓  64ms     claude: ⚡1/12  codex: ⚡1/1  🔀 was 🏢
```

- **Icon** is the Wi-Fi network, named from the gateway IP via `AGENTON_NETWORKS` (🏢 building, 📱 phone hotspot). An unnamed network shows its raw gateway IP; 🚫 means no Wi-Fi gateway.
- **🔀 was X** marks the first minute after the network changed. That line is the answer to "did it switch to the hotspot, and when".
- **WiFi ✓ 101ms** is a ping to 1.1.1.1; `(http)` means ping was blocked but an HTTP fetch worked; `✗` means neither.
- **claude / codex `⚡busy/total`**: busy sessions out of live sessions.
- A **missing minute** means the Mac was asleep or the process was killed. Start/end markers (`▶ agenton start`, `■ agenton finished`, `✖ agenton interrupted`) bracket each run.

To answer a question, read the relevant slice of the log (`agentlog 200`, or grep by date) and quote the lines.

## How the data is gathered

| Field | Source | Caveat |
|---|---|---|
| Network | `ipconfig getoption en0 router`, matched against `AGENTON_NETWORKS` globs | macOS redacts the SSID from every CLI tool (networksetup, ipconfig, wdutil, system_profiler — even as root) without Location Services, so the gateway IP stands in for the network. A gateway shared by many networks (e.g. `192.168.1.1`) can't tell them apart. |
| Claude | `~/.claude/sessions/<pid>.json` registry, pids checked live | Counts sessions in any terminal, not just Superset. |
| Codex total | `pgrep -x codex` | `-x` excludes the `codex-code-mode-host` helper; a loose `ps \| grep codex` also matches Playwright MCP and the ChatGPT-for-Chrome host. |
| Codex busy | latest event per session in `~/.codex/turn-activity.jsonl` (`UserPromptSubmit` = active, `Stop` = idle) | No pid in the log, so an interrupted turn that never wrote `Stop` could look busy: events older than an hour are ignored and busy is capped at the live process count. |
| Other agents | `superset snapshot --json` (`agent.id`, `agent.state`) | Only agents in Superset terminals; prints nothing if the Superset host service is down. |

## Changing it

Edit `agenton.zsh` in the **dev checkout** (`~/code/ai-tools/skills/agenton/`, or a worktree of it), commit, and push to `main`; `~/.ai-tools/install.sh` or the next session's sync hook deploys it. Never edit `~/.ai-tools` or `~/.claude/skills/agenton` — they are read-only symlinks to the deploy clone. After a change, open shells need `source ~/.ai-tools/skills/agenton/agenton.zsh` (or a new shell) to pick it up; an `agenton` run already in progress keeps the old code.

- **Name a network:** add `"<gateway glob>=<one emoji>"` to `AGENTON_NETWORKS`; first match wins, so put specific entries before broad ones. Keep icons one emoji wide — the column padding assumes it.
- **Line format:** the line is assembled in `agent_timed` and the date in `_agenton_now`; the `_agenton_log` width (`%-21s`) and the live-print width must stay equal.
- **zsh gotchas hit while building this:** a glob stored in a variable only matches when expanded as `${~var}`; BSD `sed -i` needs `-i ''`; `printf '%-Ns'` pads by characters, so wide emoji need manual padding.
