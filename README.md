# ai-tools

Personal AI coding tools — Claude Code and Cursor skills, slash commands, hooks, automations, and statusline I author and use across machines.

## What's here

### Skills (`skills/`)

| Skill | Purpose |
|---|---|
| [`advance-roadmap`](skills/advance-roadmap) | Ship planned `ROADMAP.md` items (or `docs/plans/` docs, if no roadmap) in personal repos end-to-end, one per run, chaining runs for up to 40 min — branch, build, verify, mark shipped, merge + push `main`. With nothing to ship, runs one behavior-preserving code-health refactor; logs structured hygiene observations after each ship |
| [`agenton`](skills/agenton) | zsh `agenton`/`agentoff`: keep the Mac awake with the lid closed for N minutes and log one line per minute — Wi-Fi network (named by gateway IP; 📱 marks a phone hotspot), ping latency, and Claude/Codex busy/total session counts. Sourced from `~/dotfiles/zsh/lib/45-agenton.zsh`; the skill teaches Claude to read the log and edit the script, never to run it |
| [`conductor`](skills/conductor) | Human-driven "ship one item" that never implements in-process: the Claude session conducts Cursor (`agent`) as builder and Codex (`codex exec`, read-only) as reviewer via `worker.sh` / `review.sh`; owns the shared triage doc `ORCHESTRATOR.md` that `advance-roadmap`'s scheduler also reads |
| [`babysit-pr`](skills/babysit-pr) | Iteratively address automated reviewer threads (Codex, CodeRabbit, etc.) on the current PR |
| [`close-out`](skills/close-out) | Sweep the conversation and machine for orphaned work, account for touched running-plan threads, and offer to copy recent harness plans into the repo |
| [`logo-gestalt`](skills/logo-gestalt) | Gestalt logo design: symbol inventory, shared-vector matching, SVG + raster preview board, vision critique |
| [`cleanup-local-branches`](skills/cleanup-local-branches) | Phased cross-repo cleanup of stale branches + worktrees with reflog-recovery log |
| [`datadog-tool-selection`](skills/datadog-tool-selection) | Guide for picking the right Datadog tool for an observability question |
| [`datadog-add-log-facets`](skills/datadog-add-log-facets) | Create Datadog Logs facets from a path list via Claude in Chrome, leaving display names empty |
| [`deploy-koyeb`](skills/deploy-koyeb) | Deploy a local service directory to a Koyeb app/service from a worktree or branch |
| [`handoff`](skills/handoff) | Close out the session, then spawn a successor agent in the right Superset workspace with state, not transcript; `--cloud` writes the prompt for a claude.ai cloud session instead |
| [`done`](skills/done) | Close this session's Superset terminal tab after checking for its own uncommitted, unpushed, or background work; Superset terminals only, `/done` or explicit request |
| [`kosha-triage`](skills/kosha-triage) | Migrate stale ClickUp captures into the markdown vault, then delete the originals |
| [`jnelken-linear`](skills/jnelken-linear) | File or relabel a personal-workspace (DEV-*) Linear issue — refuses to create one without the `repo/<directory>` label advance-roadmap routes on |
| [`linear-cli`](skills/linear-cli) | Read/write Linear from the shell with the `linear` CLI — the fallback when MCP is logged out or expired, plus the default filters that make `issue list` hide issues that exist |
| [`lunchmoney-hygiene`](skills/lunchmoney-hygiene) | Audit Lunch Money integrity and grouping opportunities; verify cache coverage, compare runs, and surface actionable changes without live financial edits |
| [`monthly-retro`](skills/monthly-retro) | Generate a stakeholder-readable monthly retrospective from commit history |
| [`monthly-retro-commits`](skills/monthly-retro-commits) | Export commit-level effort data (TSV, lines-changed sorted) for the retro skill |
| [`move-diff`](skills/move-diff) | Relocate uncommitted changes to a different branch/worktree |
| [`fix-stale-docs`](skills/fix-stale-docs) | Scan for dead doc references, fix or delete them, push to an open (preferably approved) branch |
| [`peer-review`](skills/peer-review) | Review with Claude for Codex-authored changes, Codex otherwise |
| [`pick-up`](skills/pick-up) | Resume open work from `IN_PROGRESS.md`, running plan docs, and recent harness plans; offer to copy harness plans into the repo; `sweep` flags only evidence-backed done/tracked plan threads |
| [`post-pr`](skills/post-pr) | Take a finished PR the rest of the way: review until clean, watch CI, post to `#pr-review`, badge + undraft |
| [`pr-review-gaps`](skills/pr-review-gaps) | Find sswt PRs never posted to `#pr-review`, with a hold-list for deliberately parked ones |
| [`prod-query`](skills/prod-query) | Read-only production customer-tenant data questions via the api repo's audited `query:prod-readonly` script — CLI contract, RUM id → tenant mapping, safe query cookbook, PII rules |
| [`rebase-after-squash`](skills/rebase-after-squash) | Resolve rebase-after-squash-merge conflicts cleanly |
| [`rum-review`](skills/rum-review) | Query Datadog RUM data, synthesize findings into categorized issues |
| [`scoutmail`](skills/scoutmail) | Monitor all Spark email accounts and surface only new messages that genuinely require attention |
| [`instruction-profile-maintenance`](skills/instruction-profile-maintenance) | Maintain instruction recipes and loading behavior while preserving profile boundaries, hook contracts, and runtime isolation |
| [`session-hardening`](skills/session-hardening) | Session retrospective: turn friction and gaps hit during a session (wrong docs, misfiring hooks, missing skills, repeated manual steps, newly-dead mechanisms) into concrete tooling fixes |
| [`screenshot-pr`](skills/screenshot-pr) | Capture one signature screenshot (deploy preview, or localhost for a sub-PR), upload it via Claude-in-Chrome into the PR description |
| [`slack-gif-creator`](skills/slack-gif-creator) | Build animated GIFs optimized for Slack |
| [`superset-config`](skills/superset-config) | Configure superset.sh (agentic IDE) project scripts — setup/run/teardown |
| [`tab-triage`](skills/tab-triage) | Flush open Chrome tabs through chrome-tab-org, then triage the JSON log into vault notes and ClickUp tasks |
| [`unblock-roadmap`](skills/unblock-roadmap) | Interactive sweep of the repos `advance-roadmap` is blocked on — asks how to resolve each, records answers as directives for a later run |
| [`use-spark`](skills/use-spark) | Query the Spark email client CLI — emails, calendar, contacts, scheduling |
| [`weekly-product-changelog-and-announcement`](skills/weekly-product-changelog-and-announcement) | Generate + post a weekly per-engineer changelog (internal) and a customer-facing product announcement (external) from cross-repo git history |
| [`wrapup-repos`](skills/wrapup-repos) | Wrap up in-progress work in one repo unattended — commit + record decisions in `.claude/IN_PROGRESS.md` |

Each skill is a directory with a `SKILL.md` (required) plus optional `scripts/`, `references/`, `assets/`.

### Public plugin

**[Developer Workflow Kit](plugins/unblock-roadmap/README.md)** generalizes eight of these
workflows for other developers: roadmap unblocking and implementation, moving diffs,
rebasing after squash merges, stale-doc repairs, session close-out/resumption, and session
hardening. Public editions live at `skills/<name>/public/SKILL.md`; the personal entrypoints
above keep their existing behavior. Build the skills-only archive with
`python3 bin/build-developer-workflow-kit /tmp/developer-workflow-kit.zip`.
Public submission requirements and excluded candidates are tracked with the plugin source.

### Ticket retention and hygiene data

Personal DEV tickets represent explicitly requested tickets or concrete future work outside the
current branch; PRs do not require retrospective tickets. Work CON PR backfill is unchanged.
`bin/linear-pr-archive` exports completed tickets to verified comments on their merged PRs,
with private local snapshots and repeat-safe markers. It retains planning tickets and only
deletes proven legacy create-pr backfills when explicitly run with `--delete` and reviewed
`backfill_evidence`. Issues without PRs or unexported documents/assets are retained.
`bin/hygiene-log` records structured observations in the personal memory directory's
`hygiene.jsonl`; no automatic hygiene tickets are filed.

### Slash commands (`commands/`)

| Command | Purpose |
|---|---|
| [`/resolve-conflict`](commands/resolve-conflict.md) | Guide Claude through git merge/rebase conflict resolution with step-by-step reporting |

### Agents (`agents/`)

| Agent | Purpose |
|---|---|
| [`codex-reviewer`](agents/codex-reviewer.md) | Run one Codex review pass and return faithful normalized findings for caller triage |
| [`claude-reviewer`](agents/claude-reviewer.md) | Independently review Codex-authored changes or a named review angle with code evidence |
| [`implementer`](agents/implementer.md) | Implement one scoped task with Codex-first routing, Claude fallback, checks, and local checkpoints |
| [`researcher`](agents/researcher.md) | Investigate code, prior decisions, documentation, branches, and PRs read-only with source evidence |
| [`ops-investigator`](agents/ops-investigator.md) | Investigate Datadog, Koyeb, and GitHub Actions read-only with reproducible evidence and observability gaps |
| [`prod-reader`](agents/prod-reader.md) | Answer read-only production tenant-data questions through the audited api query script — aggregate/anonymized results, exact SQL in the report, never raw PII |

### Hooks (`hooks/`)

| Hook | When | Purpose |
|---|---|---|
| [`ai-tools-sync.sh`](hooks/ai-tools-sync.sh) | SessionStart | Keep the deploy clone (`~/.ai-tools`) synced with `origin/main`, re-link on change, and nudge about a dirty deploy clone or leftover `--dev` links |
| [`ai-tools-deploy-guard.sh`](hooks/ai-tools-deploy-guard.sh) | PreToolUse (Edit\|Write\|MultiEdit\|NotebookEdit) | Block edits into the deploy clone, including through installed symlinks; allow gitignored runtime files; wired by `install.sh` |
| [`post-yesterdays-ccusage.sh`](hooks/post-yesterdays-ccusage.sh) | SessionStart | Post daily Claude + Codex token-usage summaries to Slack, catching up missed days |
| [`pick-up-nudge.sh`](hooks/pick-up-nudge.sh) | SessionStart | Nudge to run `/pick-up` when the repo has an `IN_PROGRESS.md` with open checklist items; asks for a background `/pick-up sweep` once per ISO week; resets a file with nothing open to the empty template |
| [`block-push-to-main.sh`](hooks/block-push-to-main.sh) | PreToolUse (Bash) | Block a Claude-issued `git push` that would target `main`, with a per-repo allowlist |
| [`readonly-ops-guard.sh`](hooks/readonly-ops-guard.sh) | PreToolUse (ops-investigator frontmatter) | Block known mutating ops commands with a deliberately text-based, fail-open guardrail |
| [`zsh-word-split-guard.sh`](hooks/zsh-word-split-guard.sh) | PreToolUse (Bash) | Block Bash commands that hit zsh traps: unquoted `$VAR` in `set --`/`for … in` (no word-split) and bare `===` words (`=` expansion); bypass with `# zsh-ok` |
| [`enforce-claude-symlinks.sh`](hooks/enforce-claude-symlinks.sh) | git `pre-commit` hook (not a `settings.json` hook — runs only from the dev checkout) | Reject a commit that adds an unsymlinked regular file under `~/.claude/` |
| [`claude-symlink-hygiene.sh`](hooks/claude-symlink-hygiene.sh) | SessionStart | Nudge about new unsymlinked files in `~/.claude/{hooks,commands,agents}/` before they become a blocker |
| [`personal-repo-hygiene-check.sh`](hooks/personal-repo-hygiene-check.sh) | SessionStart | Nudge personal-repo conventions — misplaced plan files, malformed ADRs, missing `PRODUCT.md`/`DESIGN.md`, missing `.superset/config.json` |
| [`session-doc-start.sh`](hooks/session-doc-start.sh) | SessionStart | Create/refresh the live `.claude/.sessions/<id>.md` presence doc for the worktree, plus its durable global mirror |
| [`session-doc-stop.sh`](hooks/session-doc-stop.sh) | Stop | Mechanically refresh the session doc's timestamp and `git diff --stat` summary |
| [`session-doc-end.sh`](hooks/session-doc-end.sh) | SessionEnd | Delete the local session doc on clean exit; mark its global mirror `ended_cleanly` |
| [`hygiene-scan-all.sh`](hooks/hygiene-scan-all.sh) | SessionStart (async) | Regenerate the cross-repo `~/.claude/HYGIENE.md` hygiene report cache |

Hooks need extra wiring in `~/.claude/settings.json` — see [`hooks/README.md`](hooks/README.md).

### Automations (`automations/`)

| Automation | Schedule | Purpose |
|---|---|---|
| [`advance-roadmap`](automations/advance-roadmap) | Every 2 hours at :45 on odd hours (launchd; `ADVANCE_ROADMAP_CADENCE_HOURS` in the plist) | Headlessly ship planned `ROADMAP.md` items via the `advance-roadmap` skill — keeps shipping one after another until a run doesn't ship cleanly or 40 min pass |
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
| [`session-retrospectives.md`](docs/session-retrospectives.md) | The session-retrospective class — field schema, action tiers, self-start rules, and the registry of retros that live here or as `~/.claude/CLAUDE.md` sections |

### Tools (`bin/`)

| Tool | Purpose |
|---|---|
| [`ai-tools-clone-lock`](bin/ai-tools-clone-lock) | Keep the deploy working tree read-only; `run --` opens a brief update window and re-locks afterward |

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
- `~/.claude/output-styles/<name>.md` → `~/.ai-tools/output-styles/<name>.md` (per output style; select with `outputStyle` in settings.json)
- `~/.claude/agents/<name>.md` → `~/.ai-tools/agents/<name>.md` (per agent)
- `~/.claude/hooks/<name>` → `~/.ai-tools/hooks/<name>` (per hook)
- `~/.claude/automations/<name>/<script>` → `~/.ai-tools/automations/<name>/<script>` (per automation script; scheduling itself is separate, see [`automations/README.md`](automations/README.md))
- `~/.claude/awesome-statusline.sh` → `~/.ai-tools/statusline/awesome-statusline.sh`, plus `~/.claude/settings.json`'s `statusLine.command` is set to run it

It also prunes dangling `~/.claude/*` symlinks left behind by renamed or removed ai-tools content — only links whose target was inside an ai-tools checkout are touched; anything else is left alone.

It also wires a `SessionStart` hook (`ai-tools-sync.sh`) into `~/.claude/settings.json` that keeps `~/.ai-tools` synced with `origin/main` automatically on future sessions — see [`hooks/README.md`](hooks/README.md#ai-tools-syncsh) — and a `PreToolUse` hook (`ai-tools-deploy-guard.sh`) that blocks Claude's file tools from editing the deploy clone while allowing gitignored runtime files.

Safety: it **refuses to overwrite** existing non-symlink files/directories — manually `rm -rf` the conflicting path first if you want this repo's version. If `~/.ai-tools` has been edited in place (uncommitted changes), the update step warns and skips the fetch/merge rather than resetting it — no local edit is ever silently dropped.

`~/.ai-tools` is **read-only on purpose**: `EACCES` / permission denied there (including through installed symlinks) means an edit targeted the deploy clone. The fix is always to edit `~/code/ai-tools`. Updates use `bin/ai-tools-clone-lock run -- <command>` and re-lock even on failure; `.git/` stays writable for fetches and hooks. Existing gitignored runtime files stay owner-writable, including session-hardening's `memory.md` (created on first lock) and existing Python cache directories; their parents stay locked.

For an emergency repair, run `~/.ai-tools/bin/ai-tools-clone-lock unlock`, perform the repair, then `~/.ai-tools/bin/ai-tools-clone-lock lock`. Check with `~/.ai-tools/bin/ai-tools-clone-lock status`. Removing the clone with `rm -rf ~/.ai-tools` also needs `unlock` first. These commands honor `AI_TOOLS_HOME` when the clone lives elsewhere; `install.sh --dev` leaves the dev checkout writable.

### Cloud environments

Any environment without a persistent `~/code/ai-tools` checkout (Claude Code on the web, Cursor cloud agents, Superset workspaces, CI) just needs the one-liner bootstrap run as its setup step:

```bash
curl -fsSL https://raw.githubusercontent.com/jnelken/ai-tools/main/install.sh | bash
```

- **Claude Code on the web**: put it in the environment's setup script.
- **Cursor cloud agents**: put it in `.cursor/environment.json`'s `install` command.
- **Superset workspaces**: put it in `.superset/config.json`'s `setup` command.

What the bootstrap does **not** carry: `~/.claude/settings.json` is machine-local and not versioned, so beyond `statusLine`, the `ai-tools-sync.sh` `SessionStart` entry, and the `ai-tools-deploy-guard.sh` `PreToolUse` entry that `install.sh` writes, every other hook still needs wiring per [`hooks/README.md`](hooks/README.md) on each new environment. Secrets (webhook URLs, API keys) arrive via environment variables set on the environment itself — never from the repo.

### Windows

`install.sh` runs under Git Bash (it sets `MSYS=winsymlinks:nativestrict` so links are real NTFS symlinks, never silent copies). One-time prerequisites:

- **Developer Mode** on (Settings → System → For developers) so symlinks work without elevation — `install.sh` checks this first and exits with instructions if not.
- `jq` on `PATH` (`scoop install jq`).
- `CLAUDE_CODE_GIT_BASH_PATH` pointing at Git Bash's `bash.exe` (e.g. `~\scoop\apps\git\current\bin\bash.exe`) when Claude Code can't find it on its own — hooks and the statusline run through it.

Run from PowerShell:

```powershell
& "$env:USERPROFILE\scoop\apps\git\current\bin\bash.exe" -lc '~/.ai-tools/install.sh'
```

Launchd automations don't apply on Windows.

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

**The trap:** editing through `~/.claude/...` (instead of `~/code/ai-tools/...`) targets the *deployed copy* and fails with permission denied while it's locked. The fix is always to edit `~/code/ai-tools`, commit, and push. Edits left after an emergency unlock still trigger the dirty-clone checks and block updates.

## Sharing with coworkers

These are personal tools and may reference my specific workflows/repos. The one-liner installs *my* config into your `~/.claude` and keeps tracking my `origin/main` automatically via the `ai-tools-sync` hook — running it as-is means my future edits land in your config too. If that's not what you want, fork the repo and point `AI_TOOLS_REPO` at your fork before bootstrapping, or just copy the individual skills/commands you find useful and adapt them locally.

## Instruction profiles

[Profile selection, runtime isolation, installation and rollback](docs/instruction-profiles.md). [ADR 0001](adr/0001-compose-instructions-by-role-and-repository.md) records the intent, alternatives, and adopted implementation plan. Maintained recipes live in `instructions/`; `agent-profile` explains selection and `agent-run` composes CLI launches.

### Installer self-updates and deploy drift

A successful clone or fast-forward restarts into the deployed installer once, preserving flags and skipping another update. This avoids mixing old parsed functions with newly deployed scripts. `--dev`, `--no-update`, and unchanged revisions do not restart.

Dirty deploy clones still skip updates. Executable-bit drift gets a specific diagnostic, a command to preserve tracked changes in a patch, and shell-escaped permission repair commands through `ai-tools-clone-lock`. Repairs remain explicit; untracked files require separate preservation, and staged changes require separate review. No local edits are automatically discarded.

Regression checks (temporary local Git remotes; no network): `python3 -m unittest discover -s lib -p test_installer.py`.
