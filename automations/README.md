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

## `advance-roadmap`

Daily at 4:45am local time, picks a personal repo under `~/Dropbox/code` whose `ROADMAP.md`
has real planned work, ships exactly ONE item on a branch, verifies with the repo's own
`npm test` / `npm run build`, moves the item to Shipped, then merges to `main` locally and
**pushes**. No PR. Single source of truth for the workflow is the
[`advance-roadmap`](../skills/advance-roadmap/SKILL.md) skill — `run.sh` just invokes
`claude -p` headlessly against it, so editing the skill changes both the on-demand
`/advance-roadmap` and this scheduled job.

This is the one automation here that contacts a remote. Its guardrails (personal `jnelken`
repos only, clean tree required, no force-anything, all-or-nothing on failed verification)
live in the skill, not in `run.sh`. `run.sh` adds a single-instance lock so two runs can
never fight over the same repo's `main`, and it runs on Opus rather than Sonnet because it
writes and tests real features.

The 4:45am slot sits two hours after `wrapup-repos` on purpose: that job commits WIP, and
this one refuses to start on a dirty tree.

## `wrapup-repos`

Off-peak (2:45am / 7:45am / 12:45pm / 5:45pm local time), picks the dirtiest/most-recently
touched repo under `~/Dropbox/code`, finishes obvious low-risk loose ends, commits a WIP,
and writes `NEXT-STEPS.md`. Single source of truth for the actual workflow is the
[`wrapup-repos`](../skills/wrapup-repos/SKILL.md) skill — `run.sh` just invokes
`claude -p` headlessly against it, so editing the skill changes both the on-demand
`/wrapup-repos` and this scheduled job.

After each run, `run.sh` also regenerates `~/.claude/automations/wrapup-repos/dashboard.html`
via `gen-dashboard.py` — a self-contained (file://-safe) status page covering run history,
`NEXT-STEPS.md` decision lists, `(auto)` commits, and disabled repos across `~/Dropbox/code`.
Open it directly in a browser to check the automation's state without digging through logs.
