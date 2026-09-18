# ai-tools

Personal AI coding tools — Claude Code and Cursor skills, slash commands, hooks, automations, and statusline I author and use across machines.

## What's here

### Skills (`skills/`)

| Skill | Purpose |
|---|---|
| [`advance-roadmap`](skills/advance-roadmap) | Ship one planned `ROADMAP.md` item (or a `docs/plans/` doc, if no roadmap) in a personal repo end-to-end — branch, build, verify, mark shipped, merge + push `main` |
| [`babysit-pr`](skills/babysit-pr) | Iteratively address automated reviewer threads (Codex, CodeRabbit, etc.) on the current PR |
| [`logo-gestalt`](skills/logo-gestalt) | Gestalt logo design: symbol inventory, shared-vector matching, SVG + raster preview board, vision critique |
| [`cleanup-local-branches`](skills/cleanup-local-branches) | Phased cross-repo cleanup of stale branches + worktrees with reflog-recovery log |
| [`datadog-tofu-sync`](skills/datadog-tofu-sync) | Reconcile `infra/datadog/` (OpenTofu) with live Datadog state — import monitors, fix stale IDs |
| [`datadog-tool-selection`](skills/datadog-tool-selection) | Guide for picking the right Datadog tool for an observability question |
| [`deploy-koyeb`](skills/deploy-koyeb) | Deploy a local service directory to a Koyeb app/service from a worktree or branch |
| [`handoff`](skills/handoff) | Close out the session, then spawn a successor agent in the right Superset workspace with state, not transcript |
| [`kosha-triage`](skills/kosha-triage) | Migrate stale ClickUp captures into the markdown vault, then delete the originals |
| [`monthly-retro`](skills/monthly-retro) | Generate a stakeholder-readable monthly retrospective from commit history |
| [`monthly-retro-commits`](skills/monthly-retro-commits) | Export commit-level effort data (TSV, lines-changed sorted) for the retro skill |
| [`move-diff`](skills/move-diff) | Relocate uncommitted changes to a different branch/worktree |
| [`peer-review`](skills/peer-review) | Run Codex code review locally before pushing |
| [`post-pr`](skills/post-pr) | Take a finished PR the rest of the way: review until clean, watch CI, post to `#pr-review`, badge + undraft |
| [`pr-review-gaps`](skills/pr-review-gaps) | Find sswt PRs never posted to `#pr-review`, with a hold-list for deliberately parked ones |
| [`rebase-after-squash`](skills/rebase-after-squash) | Resolve rebase-after-squash-merge conflicts cleanly |
| [`rum-review`](skills/rum-review) | Query Datadog RUM data, synthesize findings into categorized issues |
| [`scoutmail`](skills/scoutmail) | Monitor all Spark email accounts and surface only new messages that genuinely require attention |
| [`simplification-retro`](skills/simplification-retro) | Sweep finished work for the complexity it made unnecessary — dead scaffolding, redundant paths, orphaned guards — across code, config, shell, git state and docs |
| [`screenshot-pr`](skills/screenshot-pr) | Capture one signature screenshot from the deploy preview, embed in PR description |
| [`slack-gif-creator`](skills/slack-gif-creator) | Build animated GIFs optimized for Slack |
| [`superset-config`](skills/superset-config) | Configure superset.sh (agentic IDE) project scripts — setup/run/teardown |
| [`tab-triage`](skills/tab-triage) | Flush open Chrome tabs through chrome-tab-org, then triage the JSON log into vault notes and ClickUp tasks |
| [`use-spark`](skills/use-spark) | Query the Spark email client CLI — emails, calendar, contacts, scheduling |
| [`weekly-product-changelog-and-announcement`](skills/weekly-product-changelog-and-announcement) | Generate + post a weekly per-engineer changelog (internal) and a customer-facing product announcement (external) from cross-repo git history |
| [`wrapup-repos`](skills/wrapup-repos) | Wrap up in-progress work in one repo unattended — commit + write a decision list |

Each skill is a directory with a `SKILL.md` (required) plus optional `scripts/`, `references/`, `assets/`.

### Slash commands (`commands/`)

| Command | Purpose |
|---|---|
| [`/resolve-conflict`](commands/resolve-conflict.md) | Guide Claude through git merge/rebase conflict resolution with step-by-step reporting |

### Agents (`agents/`)

| Agent | Purpose |
|---|---|
| [`react-pro`](agents/react-pro.md) | React performance/architecture specialist agent |
| [`typescript-pro`](agents/typescript-pro.md) | TypeScript advanced-types/full-stack specialist agent |

### Hooks (`hooks/`)

| Hook | When | Purpose |
|---|---|---|
| [`ai-tools-sync.sh`](hooks/ai-tools-sync.sh) | SessionStart | Keep the deploy clone (`~/.ai-tools`) synced with `origin/main`, re-link on change, and nudge about a dirty deploy clone or leftover `--dev` links |
| [`post-yesterdays-ccusage.sh`](hooks/post-yesterdays-ccusage.sh) | SessionStart | Post daily Claude + Codex token-usage summaries to Slack, catching up missed days |
| [`pick-up-nudge.sh`](hooks/pick-up-nudge.sh) | SessionStart | Nudge to run `/pick-up` when the repo has an `IN_PROGRESS.md` with open checklist items |
| [`set-process-name.sh`](hooks/set-process-name.sh) | PreToolUse (Bash) | Label agent-spawned node processes `{agent}-{script}-{branch}` so Activity Monitor shows origins — see [named-node-processes.md](hooks/named-node-processes.md) |
| [`set-process-title.cjs`](hooks/set-process-title.cjs) | via `NODE_OPTIONS` | Injector applying the title to any node process; MCP servers self-label from their script basename |
| [`block-push-to-main.sh`](hooks/block-push-to-main.sh) | PreToolUse (Bash) | Block a Claude-issued `git push` that would target `main`, with a per-repo allowlist |
| [`enforce-claude-symlinks.sh`](hooks/enforce-claude-symlinks.sh) | git `pre-commit` hook (not a `settings.json` hook — runs only from the dev checkout) | Reject a commit that adds an unsymlinked regular file under `~/.claude/` |
| [`claude-symlink-hygiene.sh`](hooks/claude-symlink-hygiene.sh) | SessionStart | Nudge about new unsymlinked files in `~/.claude/{hooks,commands,agents}/` before they become a blocker |
| [`personal-repo-hygiene-check.sh`](hooks/personal-repo-hygiene-check.sh) | SessionStart | Nudge personal-repo conventions — misplaced plan files, malformed ADRs, missing `PRODUCT.md`/`DESIGN.md`, missing `.superset/config.json` |
| [`session-doc-start.sh`](hooks/session-doc-start.sh) | SessionStart | Create/refresh the live `.claude-sessions/<id>.md` presence doc for the worktree, plus its durable global mirror |
| [`session-doc-stop.sh`](hooks/session-doc-stop.sh) | Stop | Mechanically refresh the session doc's timestamp and `git diff --stat` summary |
| [`session-doc-end.sh`](hooks/session-doc-end.sh) | SessionEnd | Delete the local session doc on clean exit; mark its global mirror `ended_cleanly` |
| [`hygiene-scan-all.sh`](hooks/hygiene-scan-all.sh) | SessionStart (async) | Regenerate the cross-repo `~/.claude/HYGIENE.md` hygiene report cache |

Hooks need extra wiring in `~/.claude/settings.json` — see [`hooks/README.md`](hooks/README.md).

### Automations (`automations/`)

| Automation | Schedule | Purpose |
|---|---|---|
| [`advance-roadmap`](automations/advance-roadmap) | Every 6 hours — 4:45am / 10:45am / 4:45pm / 10:45pm (launchd) | Headlessly ship one planned `ROADMAP.md` item in a personal repo via the `advance-roadmap` skill |
| [`wrapup-repos`](automations/wrapup-repos) | Sunday 2:45am, weekly (launchd) | Headlessly wrap up in-progress work in one repo via the `wrapup-repos` skill |

Scheduling (the launchd plist) needs manual per-machine setup — see [`automations/README.md`](automations/README.md).

### Statusline (`statusline/`)

| Script | Purpose |
|---|---|
| [`awesome-statusline.sh`](statusline/awesome-statusline.sh) | Bash statusline for Claude Code — context/usage-limit bars with a momentum-based color gradient, blink at high-risk pace, cost/session time, Node.js version |

Unlike the other categories, installing this also patches `~/.claude/settings.json` (`statusLine.command`, with a timestamped backup) so Claude Code actually runs it — see [Install](#install) below. Requires `jq` (auto-installed by `install.sh` if missing).

### Docs (`docs/`)

| Doc | Purpose |
|---|---|
| [`retrospectives.md`](docs/retrospectives.md) | The retrospective class — field schema, shared action tiers, and the registry of retros that live here or as `~/.claude/CLAUDE.md` sections |

## Install

**Deploy model:** `~/code/ai-tools` (this checkout) is for *development only*. What actually lands in `~/.claude/*` is symlinked from a separate **deploy clone** at `~/.ai-tools`, which tracks `origin/main` and is never hand-edited. `git push` to `main` from the dev checkout *is* the deploy step — every machine and cloud environment then picks it up the same way, without needing a `~/code/ai-tools` checkout of its own.

One-liner bootstrap (no checkout needed — clones `~/.ai-tools` and links everything from it):

```bash
curl -fsSL https://raw.githubusercontent.com/jnelken/ai-tools/main/install.sh | bash
```

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `AI_TOOLS_HOME` | `~/.ai-tools` | Where the deploy clone lives |
| `AI_TOOLS_REPO` | `https://github.com/jnelken/ai-tools.git` | Remote to clone/update from |
| `AI_TOOLS_REF` | `main` | Branch/ref to track |

`install.sh` works identically in three invocation modes:

1. **Piped from GitHub** (above) — no local checkout at all.
2. **Run from the deploy clone**: `~/.ai-tools/install.sh` — same behavior as piped.
3. **Run from the dev checkout**: `~/code/ai-tools/install.sh` — default behavior is *still* the hosted install (update + link from `~/.ai-tools`). The dev checkout itself is **not** linked unless `--dev` is passed.

Flags: `--dev` (link from the checkout you're running instead of `~/.ai-tools` — temporary, for testing an unpushed change; a plain `./install.sh` afterward flips back), `--no-update` (skip the fetch/merge, just re-link), `--quiet` (only print changes and warnings), `-h`/`--help`.

The installer creates symlinks at:
- `~/.claude/skills/<name>` → `~/.ai-tools/skills/<name>` (per skill)
- `~/.claude/commands/<name>.md` → `~/.ai-tools/commands/<name>.md` (per slash command)
- `~/.claude/agents/<name>.md` → `~/.ai-tools/agents/<name>.md` (per agent)
- `~/.claude/hooks/<name>` → `~/.ai-tools/hooks/<name>` (per hook)
- `~/.claude/automations/<name>/<script>` → `~/.ai-tools/automations/<name>/<script>` (per automation script; scheduling itself is separate, see [`automations/README.md`](automations/README.md))
- `~/.claude/awesome-statusline.sh` → `~/.ai-tools/statusline/awesome-statusline.sh`, plus `~/.claude/settings.json`'s `statusLine.command` is set to run it

It also prunes dangling `~/.claude/*` symlinks left behind by renamed or removed ai-tools content — only links whose target was inside an ai-tools checkout are touched; anything else is left alone.

It also wires a `SessionStart` hook (`ai-tools-sync.sh`) into `~/.claude/settings.json` that keeps `~/.ai-tools` synced with `origin/main` automatically on future sessions — see [`hooks/README.md`](hooks/README.md#ai-tools-syncsh).

Safety: it **refuses to overwrite** existing non-symlink files/directories — manually `rm -rf` the conflicting path first if you want this repo's version. If `~/.ai-tools` has been edited in place (uncommitted changes), the update step warns and skips the fetch/merge rather than resetting it — no local edit is ever silently dropped.

### Cloud environments

Any environment without a persistent `~/code/ai-tools` checkout (Claude Code on the web, Cursor cloud agents, Superset workspaces, CI) just needs the one-liner bootstrap run as its setup step:

```bash
curl -fsSL https://raw.githubusercontent.com/jnelken/ai-tools/main/install.sh | bash
```

- **Claude Code on the web**: put it in the environment's setup script.
- **Cursor cloud agents**: put it in `.cursor/environment.json`'s `install` command.
- **Superset workspaces**: put it in `.superset/config.json`'s `setup` command.

What the bootstrap does **not** carry: `~/.claude/settings.json` is machine-local and not versioned, so beyond `statusLine` and the `ai-tools-sync.sh` `SessionStart` entry that `install.sh` writes, every other hook still needs wiring per [`hooks/README.md`](hooks/README.md) on each new environment. Secrets (webhook URLs, API keys) arrive via environment variables set on the environment itself — never from the repo.

## Dev workflow

```
edit in ~/code/ai-tools → commit → push   # push IS the deploy step
~/.ai-tools/install.sh                    # picks it up on this machine now
                                           # (or just wait — the next session's
                                           # ai-tools-sync hook does this automatically)
```

To test an unpushed change before it's live everywhere else:

```bash
cd ~/code/ai-tools
./install.sh --dev     # ~/.claude/* now points at THIS checkout — loud warning printed
# ... test the unpushed change ...
./install.sh            # flips back to the deployed copy (~/.ai-tools)
```

**The trap:** editing through `~/.claude/...` (instead of `~/code/ai-tools/...`) now edits the *deployed copy*, not the source of truth. `~/.ai-tools` is managed by `install.sh` and gets overwritten on the next clean update — the `ai-tools-sync` hook and `claude-symlink-hygiene.sh` will both flag it as dirty, but the fix is always to move the edit back to `~/code/ai-tools`, commit, and push.

## Sharing with coworkers

These are personal tools and may reference my specific workflows/repos. The one-liner installs *my* config into your `~/.claude` and keeps tracking my `origin/main` automatically via the `ai-tools-sync` hook — running it as-is means my future edits land in your config too. If that's not what you want, fork the repo and point `AI_TOOLS_REPO` at your fork before bootstrapping, or just copy the individual skills/commands you find useful and adapt them locally.
