# claude-skills

Personal Claude Code skills, slash commands, and hooks I author and use across machines.

## What's here

### Skills (`skills/`)

| Skill | Purpose |
|---|---|
| [`babysit-pr`](skills/babysit-pr) | Iteratively address automated reviewer threads (Codex, CodeRabbit, etc.) on the current PR |
| [`cleanup-local-branches`](skills/cleanup-local-branches) | Phased cross-repo cleanup of stale branches + worktrees with reflog-recovery log |
| [`datadog-tofu-sync`](skills/datadog-tofu-sync) | Reconcile `infra/datadog/` (OpenTofu) with live Datadog state — import monitors, fix stale IDs |
| [`datadog-tool-selection`](skills/datadog-tool-selection) | Guide for picking the right Datadog tool for an observability question |
| [`deploy-koyeb`](skills/deploy-koyeb) | Deploy a local service directory to a Koyeb app/service from a worktree or branch |
| [`monthly-retro`](skills/monthly-retro) | Generate a stakeholder-readable monthly retrospective from commit history |
| [`monthly-retro-commits`](skills/monthly-retro-commits) | Export commit-level effort data (TSV, lines-changed sorted) for the retro skill |
| [`move-diff`](skills/move-diff) | Relocate uncommitted changes to a different branch/worktree |
| [`peer-review`](skills/peer-review) | Run Codex code review locally before pushing |
| [`rebase-after-squash`](skills/rebase-after-squash) | Resolve rebase-after-squash-merge conflicts cleanly |
| [`rum-review`](skills/rum-review) | Query Datadog RUM data, synthesize findings into categorized issues |
| [`screenshot-pr`](skills/screenshot-pr) | Capture one signature screenshot from the deploy preview, embed in PR description |
| [`slack-gif-creator`](skills/slack-gif-creator) | Build animated GIFs optimized for Slack |
| [`superset-config`](skills/superset-config) | Configure superset.sh (agentic IDE) project scripts — setup/run/teardown |
| [`use-spark`](skills/use-spark) | Query the Spark email client CLI — emails, calendar, contacts, scheduling |
| [`wrapup-repos`](skills/wrapup-repos) | Wrap up in-progress work in one repo unattended — commit + write a decision list |

Each skill is a directory with a `SKILL.md` (required) plus optional `scripts/`, `references/`, `assets/`.

### Slash commands (`commands/`)

| Command | Purpose |
|---|---|
| [`/resolve-conflict`](commands/resolve-conflict.md) | Guide Claude through git merge/rebase conflict resolution with step-by-step reporting |

### Hooks (`hooks/`)

| Hook | When | Purpose |
|---|---|---|
| [`post-yesterdays-ccusage.sh`](hooks/post-yesterdays-ccusage.sh) | SessionStart | Post daily Claude + Codex token-usage summaries to Slack, catching up missed days |

Hooks need extra wiring in `~/.claude/settings.json` — see [`hooks/README.md`](hooks/README.md).

### Automations (`automations/`)

| Automation | Schedule | Purpose |
|---|---|---|
| [`worktree-wrapup`](automations/worktree-wrapup) | 2:45am / 7:45am / 12:45pm / 5:45pm daily (launchd) | Headlessly wrap up in-progress work in one repo via the `wrapup-repos` skill |

Scheduling (the launchd plist) needs manual per-machine setup — see [`automations/README.md`](automations/README.md).

## Install

Clone, then run the installer to symlink everything into `~/.claude/`:

```bash
git clone https://github.com/jnelken/claude-skills.git ~/code/claude-skills
cd ~/code/claude-skills
./install.sh
```

The installer creates symlinks at:
- `~/.claude/skills/<name>` → `~/code/claude-skills/skills/<name>` (per skill)
- `~/.claude/commands/<name>.md` → `~/code/claude-skills/commands/<name>.md` (per slash command)
- `~/.claude/hooks/<name>` → `~/code/claude-skills/hooks/<name>` (per hook)
- `~/.claude/automations/<name>/<script>` → `~/code/claude-skills/automations/<name>/<script>` (per automation script; scheduling itself is separate, see [`automations/README.md`](automations/README.md))

Safety: it **refuses to overwrite** existing non-symlink files/directories — manually `rm -rf` the conflicting path first if you want this repo's version.

## Update flow

```bash
cd ~/code/claude-skills && git pull       # pull latest from any machine
# edit anything in ~/.claude/{skills,commands,hooks}/ — symlinks mean the repo sees it
git add -A && git commit -m "..." && git push
```

## Sharing with coworkers

These are personal skills and may reference my specific workflows/repos. If you find one useful, copy it locally and adapt — don't symlink directly from this repo into your `~/.claude/` unless you want my future edits to land in your config.
