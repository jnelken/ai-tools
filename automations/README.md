# Automations

Scheduled headless `claude -p` runs, launched by macOS `launchd` on my machines.
`install.sh` symlinks each automation's script(s) into `~/.claude/automations/<name>/`,
but **the launchd plist is not symlinked or auto-loaded** — paths/usernames can differ
per machine, and you don't want a stray automation running immediately on a fresh
checkout. Wire it up manually per machine:

```bash
cp automations/<name>/<plist>.plist ~/Library/LaunchAgents/
# edit the plist if paths/username differ from this machine
launchctl load ~/Library/LaunchAgents/<plist>.plist
```

Each automation keeps its own `logs/` locally under `~/.claude/automations/<name>/logs/`
— that's runtime output, not repo content, so it's `.gitignore`d and never committed.

## `worktree-wrapup`

Off-peak (2:45am / 7:45am / 12:45pm / 5:45pm local time), picks the dirtiest/most-recently
touched repo under `~/Dropbox/code`, finishes obvious low-risk loose ends, commits a WIP,
and writes `NEXT-STEPS.md`. Single source of truth for the actual workflow is the
[`wrapup-repos`](../skills/wrapup-repos/SKILL.md) skill — `run.sh` just invokes
`claude -p` headlessly against it, so editing the skill changes both the on-demand
`/wrapup-repos` and this scheduled job.

After each run, `run.sh` also regenerates `~/.claude/automations/worktree-wrapup/dashboard.html`
via `gen-dashboard.py` — a self-contained (file://-safe) status page covering run history,
`NEXT-STEPS.md` decision lists, `(auto)` commits, and disabled repos across `~/Dropbox/code`.
Open it directly in a browser to check the automation's state without digging through logs.
