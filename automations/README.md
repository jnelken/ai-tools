# Automations

Scheduled headless `claude -p` runs, launched by macOS `launchd` on my machines.
`install.sh` symlinks each automation's script(s) into `~/.claude/automations/<name>/`,
but **the launchd plist is not symlinked or auto-loaded** — paths/usernames can differ
per machine, and you don't want a stray automation running immediately on a fresh
checkout. Wire it up manually per machine:

```bash
cp ~/.ai-tools/automations/<name>/<plist>.plist ~/Library/LaunchAgents/
# edit the plist if paths/username differ from this machine
launchctl load ~/Library/LaunchAgents/<plist>.plist
```

Each automation keeps its own `logs/` locally under `~/.claude/automations/<name>/logs/`
— a real directory outside the deploy clone, which is why it's runtime output and never
makes the deploy clone dirty.

## `advance-roadmap`

Every 6 hours (4:45am / 10:45am / 4:45pm / 10:45pm local time — see **Cadence backoff** below),
picks a personal repo under
`~/Dropbox/code` whose `ROADMAP.md` has real planned work, ships exactly ONE item on a
branch, verifies with the repo's own `npm test` / `npm run build`, moves the item to Shipped,
then merges to `main` locally and **pushes**. No PR.

### Cadence backoff

launchd always fires four times a day; the effective cadence is enforced inside `run.sh`, so the
schedule never has to be rewritten. `gen-dashboard.py` already classifies every run, so it owns
the arithmetic and writes `cadence_hours` to `state.json`:

| Consecutive unproductive runs | Cadence | Slots that run |
|---|---|---|
| 0–3 | 6h | 04:45, 10:45, 16:45, 22:45 |
| 4–7 | 12h | 04:45, 16:45 |
| 8+ | 24h | 04:45 |

"Unproductive" means `blocked-no-item` **or** `nothing-qualified` — a run that found no qualifying
repo at all counts too, since an empty queue is exactly when backing off is worth the most. Four
of them is a full day of finding nothing. **Any run that ships resets it.** Quota, lock
and backoff skips don't count toward the streak — they're not evidence either way. A missing or
corrupt `state.json` falls back to 6h, so a bad read can never wedge the job off.

### Slack summary

Each completed run posts one line — outcome · repo · duration, plus the cadence when backed off —
to `$ADVANCE_ROADMAP_SLACK_WEBHOOK`, falling back to `$SLACK_CCUSAGE_WEBHOOK_URL`. With neither
set the step is a silent no-op, and a failed post never fails the run.

**Where the webhook goes:** `~/.claude/automations/secrets.env`, sourced by `run.sh`.

```bash
install -m 600 /dev/null ~/.claude/automations/secrets.env
echo 'export ADVANCE_ROADMAP_SLACK_WEBHOOK="https://hooks.slack.com/services/..."' \
  >> ~/.claude/automations/secrets.env
```

Not the plist — that's committed to this repo, and a webhook URL is a credential. Not `~/.zshrc`
either: launchd hands a scheduled job a bare environment and never reads a login shell's rc files,
which is why this file exists at all. It sits outside the repo, so there's nothing to gitignore.
Skipped runs never reach the Slack step, so only completed runs post.

### Orchestrator / worker split

`run.sh` no longer runs a single write-capable Claude Opus session. It:

1. Refreshes `providers-usage.json` (`lib/usage.py` — Claude statusline probe ∪ prior limit hits).
2. Launches a **read-only orchestrator**: **Codex Sol high** by default, Claude Opus high if Sol
   is exhausted. Sandbox / tool deny-list — triage only, no edits.
3. Parses `ORCHESTRATOR_RESULT_JSON`, then dispatches `worker.sh` along
   **Cursor Auto → Codex Sol → Claude Opus**, failing over on `limit_hit` within the same tick.
4. Records provider limits so the **next** run routes differently.

Skill sources of truth: [`SKILL.md`](../skills/advance-roadmap/SKILL.md) (router),
[`ORCHESTRATOR.md`](../skills/advance-roadmap/ORCHESTRATOR.md),
[`WORKER.md`](../skills/advance-roadmap/WORKER.md),
[`SAFETY.md`](../skills/advance-roadmap/SAFETY.md).

Skip the tick only when **no orchestrator** remains (both Codex and Claude hot). Worker
exhaustion alone still lets the orchestrator report `blocked-no-item` / `nothing-qualified`.

Guardrails (personal `jnelken` repos only, clean tree, no force, all-or-nothing verification)
live in `SAFETY.md`. `run.sh` adds the single-instance lock.

`run.sh` regenerates `dashboard.html` after every run (including skips). The quota card shows
Claude windows plus the multi-provider routing line from `providers-usage.json`.

The `:45` slots stay offset from `wrapup-repos` so this job never starts on a tree that job
just dirtied.

## `wrapup-repos`

Off-peak, weekly (Sunday 2:45am local time), picks the dirtiest/most-recently
touched repo under `~/Dropbox/code`, finishes obvious low-risk loose ends, commits a WIP,
and writes `NEXT-STEPS.md`. Single source of truth for the actual workflow is the
[`wrapup-repos`](../skills/wrapup-repos/SKILL.md) skill — `run.sh` just invokes
`claude -p` headlessly against it, so editing the skill changes both the on-demand
`/wrapup-repos` and this scheduled job.

After each run, `run.sh` also regenerates `~/.claude/automations/wrapup-repos/dashboard.html`
via `gen-dashboard.py` — a self-contained (file://-safe) status page covering run history,
`NEXT-STEPS.md` decision lists, `(auto)` commits, and disabled repos across `~/Dropbox/code`.
Open it directly in a browser to check the automation's state without digging through logs.
