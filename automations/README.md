# Automations

Scheduled headless `claude -p` runs, launched by macOS `launchd` on my machines.
`install.sh` symlinks each automation's script(s) into `~/.claude/automations/<name>/`,
but **launchd plists are not auto-loaded** — paths/usernames can differ
per machine, and you don't want a stray automation running immediately on a fresh
checkout. Wire it up manually per machine:

```bash
ln -s ~/.ai-tools/automations/<name>/<plist>.plist ~/Library/LaunchAgents/
# Preserve an existing local plist before replacing it; customize paths in maintained source.
launchctl load ~/Library/LaunchAgents/<plist>.plist
```

Each automation keeps its own `logs/` locally under `~/.claude/automations/<name>/logs/`
— a real directory outside the deploy clone, which is why it's runtime output and never
makes the deploy clone dirty.

## `advance-roadmap`

One finite batch every day at **10:30 local time** (Eastern on Jake's Mac), starting
with `daily_batch.py`. It queues direct-child personal repos, excluding `.noroadmap`,
non-`jnelken` origins, aliases, and repos with a worker still in flight. Each repo gets
its own read-only planner, scoped to **one actionable ticket/roadmap item or one hygiene pass**.
The existing clean-tree, live-session, directive, dependency, and review gates still apply.
When a repo has no actionable item, its planner uses the existing Step 2d code-health fallback:
prefer a code-health ticket, otherwise queue one bounded, behavior-preserving hygiene pass.
This fallback is evaluated per repo, even when other repos have feature work. All existing
hygiene eligibility rules apply, including opt-in, a clean tree, verification, and the 24-hour
cooldown. An ineligible repo is skipped. Each repo gets one feature/resume **or** hygiene run,
never both in the same daily batch.

All planners finish before implementation begins. Ready plans are persisted and wait at
a disk barrier; the controller releases workers together, up to the concurrency limit.
Each worker uses the existing Superset workspace, verification, cross-model review, and
PR merge flow. A repo's finished slot never queues another ticket from that repo.

### Schedule, concurrency, and recovery

`com.jake.advance-roadmap.plist` has one `StartCalendarInterval`: 10:30 daily, with
`RunAtLoad=false`. It replaces the two continuously refilled sprint windows and their
hourly watchdog ticks. macOS may deliver a missed tick after waking; the dated manifest
prevents a second batch that day. A batch already in progress can finish after 10:30.

`ADVANCE_ROADMAP_LANES=5` caps active planners and workers independently. The controller
enforces a maximum of **five repos at once**, including command-line/environment overrides;
use 1–5 to lower concurrency (`0`/unlimited and values over five are rejected). Ready planners
consume no model resources and free their planning slot, so a queue larger than five
still reaches the barrier.
Planning times out after 30 minutes (`ADVANCE_ROADMAP_PLAN_TIMEOUT_S`); an individual
failed or skipped planner does not block the rest. Worker timeouts remain in `run.sh`.

The shared kernel lock prevents overlapping schedulers. The durable queue and selected
ticket IDs are in `~/.claude/automations/advance-roadmap/batches/YYYY-MM-DD/batch.json`;
that directory also holds the batch's immutable Linear snapshot. Logs and lane run records
remain in the usual `logs/`, `runs/`, and `runs.jsonl` paths. Plans not implemented remain
in the existing per-repo pending-plan files for the next daily batch.

Quota is checked before each implementation wave. Exhaustion defers the remaining plans
and finishes the batch; there is **no 30-minute quota retry loop, refilling, chaining, or
second batch that day**. A crashed controller is not automatically rerun that day either:
waiting lanes exit when it disappears, while already dispatched workers can finish and
remain protected by the existing repo/in-flight locks. A failed preflight is recorded in
the manifest and waits for the next daily tick. Legacy cadence/backoff calculations apply
only to manual `run.sh` use, not this daily schedule.

Install the maintained source after committing/pushing and running `~/.ai-tools/install.sh`.
Preserve any existing local plist before replacing it, then symlink the maintained plist
and reload launchd (do this after an active legacy supervisor has finished):

```sh
ln -s ~/.ai-tools/automations/advance-roadmap/com.jake.advance-roadmap.plist ~/Library/LaunchAgents/com.jake.advance-roadmap.plist
launchctl bootout gui/$(id -u)/com.jake.advance-roadmap
launchctl enable gui/$(id -u)/com.jake.advance-roadmap
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.advance-roadmap.plist
python3 ~/.claude/automations/advance-roadmap/daily_batch.py --dry-run
```

`--dry-run` only lists candidate repos and the cap; it does not create a batch, call a
provider, or launch implementation. Test with fake providers:
`python3 -m unittest discover -s automations/advance-roadmap/tests` and
`zsh automations/advance-roadmap/tests/test-run-record.sh`.

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

1. Refreshes `providers-usage.json` (`lib/usage.py` — Claude, Codex and Cursor quota probes ∪
   prior limit hits; see [Provider usage sources](#provider-usage-sources)).
2. Launches a **read-only orchestrator**: **Codex Sol high** by default, Claude Opus high if Sol
   is exhausted. Sandbox / tool deny-list — triage only, no edits.
3. Parses `ORCHESTRATOR_RESULT_JSON`, then dispatches `worker.sh` along
   **Cursor Auto → Codex Sol → Claude Opus**, failing over on `limit_hit` within the same tick.
4. Records provider limits so the **next** run routes differently.

Skill sources of truth: [`SKILL.md`](../skills/advance-roadmap/SKILL.md) (router),
[`ORCHESTRATOR.md`](../skills/conductor/ORCHESTRATOR.md),
[`WORKER.md`](../skills/advance-roadmap/WORKER.md),
[`SAFETY.md`](../skills/advance-roadmap/SAFETY.md).

Skip the tick only when **no orchestrator** remains (both Codex and Claude hot). Worker
exhaustion alone still lets the orchestrator report `blocked-no-item` / `nothing-qualified`.

Guardrails (personal `jnelken` repos only, clean tree, no force, all-or-nothing verification)
live in `SAFETY.md`. `daily_batch.py` and `run.sh` share the single-instance lock.

### Run memory

One file per repo in the Claude memory dir's `advance-roadmap-runs/` (ledger of each repo's last 10 runs
plus notes), written only through [`lib/runmemory.py`](advance-roadmap/lib/runmemory.py), which locks the
directory and rewrites atomically so concurrent runs can't lose each other's rows. `has-stamp` is what
Step 0 uses to tell a finished run from a killed one.

### Run record and failure alert

Every run — shipped, blocked, skipped, crashed — appends exactly one row to `runs.jsonl`. `run.sh`
writes it (`lib/runrecord.py append`) from exit codes it observed and two result files: the
orchestrator's (Codex's `exec -o` final message, so echoed prompts can't be mistaken for it) and
the worker's, which the agent writes to `runs/<stamp>/worker-result.json`. If the agent skips that
file, `worker.sh` recovers it from the provider's own stdout fence and labels it `stdout-fence`.
An EXIT trap records aborts. **Nothing reads the run log to decide an outcome**; the dashboard,
`state.json` and the Slack summary all read the record, and fall back to log parsing only for
runs from before 2026-09-24.

Two consecutive `error` / `failed` / `incomplete` runs post a separate `<!here>` Slack alert and a
macOS notification, then again every 4 failures; the first success afterwards posts a recovery
line. (A routine "error" summary line went unnoticed for 4.5 days in Sep 2026.)

The live folder links each file individually, so a new `lib/` module needs its own link:

```sh
ln -s ~/.ai-tools/automations/advance-roadmap/lib/runrecord.py ~/.claude/automations/advance-roadmap/lib/
```

End-to-end test with fake providers: `zsh automations/advance-roadmap/tests/test-run-record.sh`.

`run.sh` regenerates `dashboard.html` after every run (including skips). The quota card shows
Claude windows plus the multi-provider routing line from `providers-usage.json`.

**Live dashboard with one-click refresh.** `serve.py` serves the page at
<http://127.0.0.1:8421/> (localhost only). Its **↻ refresh** button runs `usage.py refresh`,
takes a fresh Linear snapshot into `live-snapshot.json`, and rebuilds the page — about 15s. It
skips the usage and Linear steps while a run holds `run.lock`, since the run refreshes both
itself. Opened as `file://`, the button just points you at the server. Install it once:

```sh
cp ~/.ai-tools/automations/advance-roadmap/com.jake.advance-roadmap-dashboard.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.advance-roadmap-dashboard.plist
```

Server log: `~/.claude/automations/advance-roadmap/logs/dashboard-server.log`.

**Phone access over Tailscale.** The dashboard is reachable from any device on this Mac's
tailnet at `https://<this-mac>.<tailnet>.ts.net/roadmap` while the Mac is awake — private to
the tailnet; do **not** use Tailscale Funnel. `serve.py` stays bound to loopback and Tailscale
Serve proxies to it, so the server is never itself exposed:

```sh
tailscale serve --bg --set-path /roadmap http://127.0.0.1:8421
```

↻ refresh works from the phone too. Two things make that true, and both look removable until
you try it on a phone: `_allowed()` accepts a `*.ts.net` Host/Origin (loopback-only would 403
every tailnet request), and the button resolves `refresh` against `document.baseURI` rather
than posting to an absolute `/refresh`, which the `/roadmap` prefix would otherwise miss.

Turn tailnet access off with `tailscale serve reset`. That leaves the local server running on
`127.0.0.1:8421`.

The 10:30 batch stays separate from the Sunday 02:45 `wrapup-repos` schedule.

### Token accounting

Every model call a run makes appends a line to `runs/<stamp>/model-usage.jsonl` (`lib/tokens.py`):
the orchestrator (`run.sh`), the worker (`worker.sh`) and each reviewer (`review.sh`, tagged with its
review round). `runrecord.py` folds them into the `tokens` field of the run's `runs.jsonl` row — one
entry per (step, review round, model) — and the dashboard shows them per run (drawer) and per model
over 24h / 7d / all tracked runs. Nothing here gates routing.

| Provider | Source |
| --- | --- |
| Claude | `--output-format json` → `modelUsage` (per model, subagents included, plus list-price cost); `tokens.py` turns the JSON back into plain text so the limit-banner grep and fence extraction are unchanged |
| Codex | the rollout under `~/.codex/sessions` matching the `session id:` header (full in / cached / out split); falls back to the closing `tokens used` total |
| Cursor | `--output-format json` → `usage`. Reports the Auto pool, not the model it routed to |

`total` = new input + cache writes + output; cache reads are kept apart (they dwarf the rest and bill far
lower). Runs before this was added have no token data. Codex subagent rollouts are not summed.

### Provider usage sources

`lib/usage.py refresh` reads live quota for every provider before routing, so a hot provider is
skipped *before* a run hits its limit. Every probe is best-effort and returns nothing on failure;
the observed-limit-hit path (`record-limit`, parsed from CLI output) stays as the fallback, and a
provider with no reading at all is assumed available until it hits a limit.

| Provider | Source (in order) | Gate |
| --- | --- | --- |
| Claude | `~/.claude/state/claude-usage.json` (statusline) | none by percentage — shown on the dashboard only; gated solely by an observed limit hit |
| Codex | 1. short-lived `codex app-server` → `account/rateLimits/read` (fresh, ~1s) · 2. newest `token_count.rate_limits` in `~/.codex/sessions/**/rollout-*.jsonl` (as fresh as the last Codex turn; never overwrites a newer stored reading) | 5h ≥ 70%, 7d ≥ 80%, or `rateLimitReachedType` set while a window is unexpired |
| Cursor | `POST api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage` with the Agent CLI's Keychain token (`cursor-access-token` / `cursor-user`) — the RPC behind the CLI's own `/usage` | the pool the worker draws from ≥ 95%: `autoPercentUsed` when `ADVANCE_ROADMAP_CURSOR_WORKER_MODEL` is `auto` (default), `apiPercentUsed` otherwise; clears at `billingCycleEnd` |

Only the `codex` limit id is read (the separate `base_model_inference` / gpt-reserve pool is
ignored). A window whose reset time has passed counts as 0% even without a fresh probe.

Reproduce (read-only, writes nothing, exit 1 if any provider returned no data):

```sh
python3 ~/.claude/automations/advance-roadmap/lib/usage.py probe --provider all
python3 ~/.claude/automations/advance-roadmap/lib/usage.py show   # stored state + history
ADVANCE_ROADMAP_USAGE_PROBES=0 …/usage.py refresh                 # limit-hit fallback only
```

Tests: `python3 -m unittest discover -s automations/advance-roadmap/tests`.

Nothing sensitive is persisted: the Cursor token lives only in memory for one request, and only
whitelisted numeric/timestamp fields reach `providers-usage.json` (no email, no reset-credit ids).

Tried and rejected: `codex /status` (interactive TUI only); `agent about|status --format json`
(plan tier and auth state, no usage).

Limitations:

- **The Cursor RPC is internal and undocumented.** It can change with any Agent CLI update; the
  probe then returns nothing and routing falls back to limit hits.
- **Ignore Cursor's dollar fields.** `includedSpend`/`limit`, `noUsageBasedAllowed` and a
  `displayMessage` of "You've hit your usage limit" describe the extra-usage (on-demand) budget,
  which is deliberately set to $0 with no balance. These runs live only on the subscription quota,
  which is what the `*PercentUsed` fields measure. The dollar fields are neither stored nor gated on.
- **Keychain under launchd.** The `security` lookup has a 5s timeout, so an access prompt can't
  hang a scheduled run; it just skips the Cursor probe.
- The Keychain token is the Agent CLI's; if it expires the probe gets a 401 until the next `agent`
  run refreshes it.

## `wrapup-repos`

Off-peak, weekly (Sunday 2:45am local time), picks the dirtiest/most-recently
touched repo under the personal code dir, finishes obvious low-risk loose ends, commits a WIP,
and reconciles its decisions, ticket candidates, and code-state notes into that repo's
`.claude/IN_PROGRESS.md`. Single source of truth for the actual workflow is the
[`wrapup-repos`](../skills/wrapup-repos/SKILL.md) skill — `run.sh` just invokes
`claude -p` headlessly against it, so editing the skill changes both the on-demand
`/wrapup-repos` and this scheduled job.

After each run, `run.sh` also regenerates `~/.claude/automations/wrapup-repos/dashboard.html`
via `gen-dashboard.py` — a self-contained (file://-safe) status page covering run history,
open `.claude/IN_PROGRESS.md` items, `(auto)` commits, and disabled repos across the personal code dir.
Open it directly in a browser to check the automation's state without digging through logs.
