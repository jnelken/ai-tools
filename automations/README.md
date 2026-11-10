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

Two finite batches every day at **10:30 and 15:30 local time** (Eastern on Jake's Mac), starting
with `daily_batch.py`. It queues direct-child personal repos, excluding `.noroadmap`,
non-`jnelken` origins, aliases, and repos with a worker still in flight. Each repo gets
its own read-only planner, scoped to **one actionable ticket/roadmap item**, with one thread
per repo by default. Extra threads require an explicit override.
The existing clean-tree, live-session, directive, dependency, and review gates still apply.
When a repo has no actionable item, its planner reports that and stops (Step 2d). There is no
code-health or hygiene fallback; `run.sh` refuses a `code_health` plan.

All planners finish before implementation begins. Ready plans are persisted and wait at
a disk barrier; the controller releases workers together, up to the concurrency limit.
Each worker uses the existing Superset workspace, verification, cross-model review, and
PR merge flow. A repo's finished slot never queues another ticket from that repo.

### Durable recovery and dynamic scheduling rollout

`lib/workqueue.py` keeps stable work identities, attempts and bounded phase checkpoints in
`~/.claude/automations/advance-roadmap/workqueue.sqlite3` (SQLite WAL, full synchronous commits).
The existing pending-plan files remain compatibility projections and migration sources. Only
existing pending assignments authorize recovery during migration; an old failed log does not
revive deliberately parked work. Historical attempts retain assigned and reported repo separately.

Saved work no longer disappears after 36 hours or two retries. Those thresholds request revalidation
of the ticket, branch and existing PR/merge instead. Unsafe repos are held without losing their plan.
Checkpoints preserve next actions, session IDs and artifact pointers across providers; a changed
HEAD marks test/review evidence invalid while retaining findings for rechecking. Workers currently
use checkpoint-based continuation; native CLI session reattachment is not assumed to work.

The dashboard's **Work queue and continuation** panel shows phases, waiting reasons, eligibility
decisions and unrouted Linear issues. Relevant human replies/directives reopen a held assignment
for revalidation; an explicit release is also available without an agent:

```sh
python3 ~/.claude/automations/advance-roadmap/lib/workqueue.py status
python3 ~/.claude/automations/advance-roadmap/lib/workqueue.py unblock <work-id> --reason 'What changed'
```

`com.jake.advance-roadmap-shadow.plist` invokes `scheduler.py` every 15 minutes. **Default mode is
shadow:** it makes no model calls and dispatches no workers. It imports completed records,
reconciles surviving workers using PID birth times, refreshes a cached Linear snapshot at most
once per 15 minutes and records proposed eligibility decisions. An idle tick never launches a model.
No-work caches depend on launch-time inputs, so late completions cannot make newer inputs look
already triaged. Unrouted tickets remain on the dashboard instead of invalidating every repo;
explicit repo directives and cross-repo dependencies are preserved.

The active 10:30 / 15:30 job retains live authority during the seven-day shadow period. Trial and
expansion commands are gated by actual saved observations, not just a configuration switch:

```sh
python3 ~/.claude/automations/advance-roadmap/scheduler.py --status
# After seven days of fresh shadow observations without false skips/errors:
python3 ~/.claude/automations/advance-roadmap/scheduler.py --promote <repo-one> [repo-two]
# After a measured seven-day trial meets the efficiency/completion checks:
python3 ~/.claude/automations/advance-roadmap/scheduler.py --expand
# Restore legacy dispatch while preserving queue/checkpoints:
python3 ~/.claude/automations/advance-roadmap/scheduler.py --rollback
# Begin a new observation period after fixing a shadow failure:
python3 ~/.claude/automations/advance-roadmap/scheduler.py --restart-shadow
```

In trial/dynamic mode, the scheduled job excludes those repos. The new controller shares its kernel
lock and budget leases; it never runs beside the legacy controller. It streams one item per repo
without a global planning barrier, caps all active phases at five, reserves full implementation plus
review capacity before planning, and admits at most two fresh planning attempts per repo per day.
Quota waits have a `not_before`; unchanged no-work inputs wait for a change or the 72-hour safety
audit. Checkpoints are updated at phase transitions and worker milestones. Dynamic attempts are
folded into the existing scheduled summary, not posted per tick. Rollback preserves all work history.

The approved rollout is enabled with `scheduler.py --enable-rollout <ai-tools-dev-checkout>`.
It automatically selects up to two eligible pilot repos with the largest measured no-work cost
after the shadow gate passes. Expansion requires a full measured trial week with token
coverage, at least 50% fewer no-work planning tokens, no lost ships and no increased errors.
`--status` reports the baseline/trial totals and limitations; token totals are not quota percentages
or cross-provider dollar costs. Do not report the rollout complete before these observation periods
have actually passed. The execution plan stays active until then. After rollout completion, the controller moves it to
`docs/plans/archive/`, commits and pushes the move from the configured dev checkout. A dirty tree,
non-main branch or unpublished commits defer archival without touching other work. A failed push
is retried using the recorded archive commit. Rollback disables automatic promotion.

Install the shadow agent by symlinking the maintained deploy-clone plist into `~/Library/LaunchAgents/`,
then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.advance-roadmap-shadow.plist`.
It has `RunAtLoad=false`; a bootstrap does not start a model or force a roadmap batch.

### Schedule, concurrency, and recovery

`com.jake.advance-roadmap.plist` has two `StartCalendarInterval` entries: **10:30 AM and
3:30 PM local time**, with `RunAtLoad=false`. macOS may deliver a missed tick after waking;
a slot-specific manifest prevents starting the same slot twice. A running batch can finish
past the next scheduled time; the shared kernel lock prevents overlapping schedulers.

Carried dispatch/resume plans run first, without planning new work or expanding to extra
threads. New work starts only after those workers ship and their pending plans are cleared.
Interrupted work is resumed automatically. If a worker explicitly needs an owner decision or
access, it is parked with one question while unrelated repos continue. Test failures and merge
conflicts remain agent-owned. Existing repo/in-flight locks and worker freshness checks remain.

`ADVANCE_ROADMAP_LANES=5` caps the total number of active agents in a batch. Planning, approval,
and implementation run in sequence, so no more than **five agents at once** can be active across
those phases, including worker reviews. The controller enforces the cap for command-line and
environment overrides; use 1–5 to lower concurrency (`0`/unlimited and values over five are
rejected). Ready planners consume no model resources and free their planning slot, so a queue
larger than five still reaches the barrier. Run records are cumulative across the batch and can
contain more than five calls.
Planning times out after 30 minutes (`ADVANCE_ROADMAP_PLAN_TIMEOUT_S`); an individual
failed or skipped planner does not block the rest. Worker timeouts remain in `run.sh`.

The shared kernel lock prevents overlapping schedulers. The durable queue and selected
ticket IDs are in `~/.claude/automations/advance-roadmap/batches/YYYY-MM-DD-HHMM/batch.json`;
that directory also holds the batch's immutable Linear snapshot. Logs and lane run records
remain in the usual `logs/`, `runs/`, and `runs.jsonl` paths. Plans not implemented remain
in the existing per-repo pending-plan files for the next scheduled batch.

Quota is checked before each implementation wave. Exhaustion defers the remaining plans
and finishes the batch; there is **no 30-minute quota retry loop, refilling, chaining, or
second batch in the same scheduled slot**. A crashed controller is not automatically rerun in the same slot either:
waiting lanes exit when it disappears, while already dispatched workers can finish and
remain protected by the existing repo/in-flight locks. A failed preflight does not claim a slot; the next invocation can retry it. Legacy cadence/backoff calculations apply
only to manual `run.sh` use, not this twice-daily schedule.

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
2. Launches a **read-only orchestrator**: **Codex Sol high** by default, Claude Sonnet medium if Sol
   is over its gate. Sandbox / tool deny-list — triage only, no edits. A planner that prints a
   limit banner instead of a plan is recorded and the tick fails over to the next planner still
   under its gate.
3. Parses `ORCHESTRATOR_RESULT_JSON`, then dispatches `worker.sh` along
   **Codex Luna (high) → Claude Sonnet (high)**, skipping any provider over its usage
   gate and failing over on a limit banner within the same tick. Cursor remains in usage telemetry
   and reviewer routing, but is no longer routed as a worker. Worker models and efforts are
   `*_WORKER_*_DEFAULT` in `lib/models.sh`; override with `ADVANCE_ROADMAP_{CLAUDE,CODEX}_WORKER_MODEL`
   and `_WORKER_EFFORT`.
4. Records provider limits so the **next** run routes differently.

**A failed plan dispatches nothing.** When no planner returns a valid `ORCHESTRATOR_RESULT_JSON`
(every one limited, or output that isn't a plan), `run.sh` writes a `planner_error` into the
orchestrator result — `kind: quota` with the provider, its limit text and reset, or
`kind: invalid_output` with the parse error — and stops: no worker, no batch-ready signal, no
bookkeeping, no pending plan and no carried-forward verdict. A valid `blocked-no-item` /
`nothing-qualified` plan still keeps its bookkeeping.

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
orchestrator's and the worker's, which the agent writes to `runs/<stamp>/worker-result.json`. Both
are read only from a provider's final answer — Codex's `exec -o` file, or the `result` field of
Claude's or Cursor's JSON — never from the transcript, which echoes the prompt and the docs' valid
example fences. A planner with no final answer produced no plan, even when it printed a fence. If the
worker skips its file, `worker.sh` recovers it from a fence in that final answer and labels it
`stdout-fence`. An EXIT trap records aborts. **Nothing reads the run log to decide an outcome**; the dashboard,
`state.json` and the Slack summary all read the record, and fall back to log parsing only for
runs from before 2026-09-24.

Two consecutive `error` / `failed` / `incomplete` runs post a separate `<!here>` Slack alert and a
macOS notification, then again every 4 failures. A recovery notice requires a shipped worker
with observed exit 0 for the same repository and item (ticket, branch, or item name), and names
that work specifically. Unrelated/no-work planner results never confirm recovery; other
outstanding failures are called out. (A routine "error" summary line went unnoticed for 4.5 days
in Sep 2026.)

A run stopped by usage — the planner hit its limit, or every worker provider was over its gate or
limited — is recorded as **`quota-paused`**, not `error`. Its row carries a `quota` field (provider,
reset and cause); it neither counts toward nor breaks an error streak, and the batch supervisor
pauses that lane instead of alerting. A run that never started because its providers were already
gated (`skipped-quota`: no planner under its gate, the daily batch deferring saved plans for lack
of worker quota, or a plan approver that ran out) carries the same field. Every one of them —
planner, worker, approver, skip — comes from `usage.py quota-record --role R`, which lists every
provider in the role's full preference list with its gate state and reset, not only the providers
that lane tried, so a provider filtered out of a quota-filtered chain still shows up. The daily
batch's quota summary counts every provider a row lists as over its gate or limited, so a limit
one lane hit is reported for every repo it held back.

**Carried-forward verdicts are checked for contamination.** `lib/verdict.py` refuses to save a
no-work verdict from a run whose planner failed or whose worker hit a limit, and `verdict.py check`
discards an existing one whose source run shows a planner error, a limit banner, or an
`error` / `incomplete` / `quota-paused` outcome in `runs.jsonl`, forcing full triage. Bad verdicts
written before this check heal on the next check of that repo.

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

The 10:30 and 15:30 batches stay separate from the Sunday 02:45 `wrapup-repos` schedule.

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
provider with no reading at all is available with gate state `unknown` until it hits a limit.

The same gate applies to **every role**: the planner (`run.sh`, `planapproval.py`), the worker
(`worker.sh` checks each provider before trying it, an explicit `--providers` list included, and
exits 3 when none remain) and the reviewer (`review.sh`). Check one by hand with
`usage.py gate --role orchestrator|worker|reviewer --provider P` (exit 0 available, 3 gated; any other code is a usage.py failure, which never blocks).

| Provider | Source (in order) | Gate |
| --- | --- | --- |
| Claude | `~/.claude/state/claude-usage.json` (statusline) | 5h ≥ 70% or 7d ≥ 90% while that window is unexpired, or an observed limit hit until its reset |
| Codex | 1. short-lived `codex app-server` → `account/rateLimits/read` (fresh, ~1s) · 2. newest `token_count.rate_limits` in `~/.codex/sessions/**/rollout-*.jsonl` (as fresh as the last Codex turn; never overwrites a newer stored reading) | 5h ≥ 70%, 7d ≥ 90%, or `rateLimitReachedType` set while a window is unexpired |
| Cursor | `POST api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage` with the Agent CLI's Keychain token (`cursor-access-token` / `cursor-user`) — the RPC behind the CLI's own `/usage` | the pool the role draws from ≥ 100%: `autoPercentUsed` when that role's model (`ADVANCE_ROADMAP_CURSOR_WORKER_MODEL` / `ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL`) is `auto` (default), `apiPercentUsed` otherwise; clears at `billingCycleEnd` |

Thresholds are env-overridable: `ADVANCE_ROADMAP_MAX_FIVE_HOUR_PCT` (70),
`ADVANCE_ROADMAP_MAX_WEEKLY_PCT` (90) and `ADVANCE_ROADMAP_MAX_CURSOR_PCT` (100).

Only the `codex` limit id is read (the separate `base_model_inference` / gpt-reserve pool is
ignored). A window whose reset time has passed counts as 0% even without a fresh probe.

Limit banners carry their reset as relative time ("in 2h 13m") or a clock time ("5pm",
"11:50 AM (America/New_York)"); a clock time already past rolls over to the next day.

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

## `machine-status`

A read-only page of this Mac's health at `http://127.0.0.1:8423/`, mounted at `/status` on the
tailnet. Each card is one check, with its own timeout, run in parallel and cached 30 s:

- **Daemons:** `claude-rc-daemon`, `superset-host-daemon` and `crash-session-check` `status`
  (from `~/dotfiles/bin`), parsed to ok / problem / not installed, raw output collapsed.
- **Tailscale** (online, name, addresses, served pages) and **Superset** (CLI and host answering).
- **This Mac:** disk free (warns under 10%), uptime, load, power and battery, last sleep and wake,
  and what's holding the Mac awake.
- **AI providers:** each gate and usage window from advance-roadmap's `providers-usage.json`.

```sh
ln -sf ~/.ai-tools/automations/machine-status/com.jake.machine-status.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.machine-status.plist
tailscale serve --bg --set-path /status http://127.0.0.1:8423
```

Tests: `python3 -m unittest discover -s automations/machine-status/tests`.

## `paradev`

Jake's standing on Paradev's (formerly Superset's) Production Run leaderboard, on its own page:
all-time / 7-day best / today / rolling-7-day ranks, personal records (best single-day rank,
busiest day), the top days, and every recorded day. Served at `http://127.0.0.1:8422/` and, through
Tailscale Serve, at `/paradev` on the tailnet.

- `leaderboard.py`: `snapshot` reads today's ranks from `leaderboard.me` (the Superset CLI's login)
  and fills in recent days' single-day ranks from the public `leaderboard.public.standings`;
  `backfill --top N | --all | --days …` records past days. A day stays provisional until 48 h
  after it ends; ranks below about #1,100 are out of the API's reach and show as "below".
- `gen-page.py` builds `paradev.html`; `serve.py` serves it with a ↻ refresh.
- Data lives in `~/.claude/automations/paradev/` (`leaderboard.json`, `leaderboard-daily.jsonl`,
  `leaderboard-history.jsonl`). Files left in the old `advance-roadmap` folder are merged in on the
  first run and renamed `*.migrated`. The server builds the page on start if it doesn't exist yet.

```sh
for p in com.jake.paradev com.jake.paradev-snapshot; do
  ln -sf ~/.ai-tools/automations/paradev/$p.plist ~/Library/LaunchAgents/$p.plist
  launchctl bootout gui/$(id -u)/$p 2>/dev/null
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$p.plist
done
tailscale serve --bg --set-path /paradev http://127.0.0.1:8422
python3 ~/.claude/automations/paradev/leaderboard.py backfill --top 10   # first time only
python3 ~/.claude/automations/paradev/gen-page.py                         # build the page now
```

`com.jake.paradev` keeps the server up; `com.jake.paradev-snapshot` records the day at 18:50
local, just before the UTC day ends. Tests: `python3 -m unittest discover -s automations/paradev/tests`.

## `reap-sswts`

Every 10 minutes, `reap.py` looks at this machine's Superset tabs (local host only) and acts
on Claude tabs whose transcript ends on a ⛏️ sign-off at least an hour old
(`REAP_SSWTS_GRACE_MIN`), with the agent idle and nothing typed since. Replying to the
session, even after the glyph, cancels it. No model calls; it is plain checks plus the CLI.

- **sswt (worktree workspace):** runs [`self-destruct`](../skills/self-destruct/SKILL.md)'s
  `teardown.sh` (archive the workspace, prune, `git branch -D` in the root clone, recovery
  command in `~/.claude/state/self-destruct.log`) only if every other tab in it is also a
  reapable ⛏️ Claude tab or an idle shell prompt, the tree is clean, the branch has a
  MERGED PR and no OPEN one, `HEAD` is a commit that PR carried, and no gitignored
  `IN_PROGRESS.md` would go with it. It also drops the PR's `pr-review-posted.jsonl` entry.
- **Root checkout** (non-worktree type, the project's own path, or `main`/`master`): closes
  the ⛏️ tab's terminal only, like `/self-destruct` in a root checkout. The workspace and clone are never touched.

Every skip is logged with its reason to `~/.claude/automations/reap-sswts/logs/reap.log`.
Preview with `~/.claude/automations/reap-sswts/run.sh --dry-run` (prints to the terminal).
Tests: `python3 -m unittest discover -s automations/reap-sswts/tests`.

```sh
ln -s ~/.ai-tools/automations/reap-sswts/com.jake.reap-sswts.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.reap-sswts.plist
```

## `tailnet-index`

The tailnet's front page: every page this Mac serves over Tailscale Serve, with each page's title
and whether it's answering. It reads `tailscale serve status --json` on each visit (cached 20 s),
so a newly mounted page appears on its own. Served at `http://127.0.0.1:8420/` and mounted at `/`.

```sh
ln -sf ~/.ai-tools/automations/tailnet-index/com.jake.tailnet-index.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.tailnet-index.plist
tailscale serve --bg --set-path / http://127.0.0.1:8420
```

Below the live pages it lists a **library** from `~/.claude/automations/tailnet-index/library.json`
(kept local: it holds private claude.ai artifact links and personal paths; copy
`library.example.json` to start): **Reports** (markdown, rendered), **On claude.ai** (links to
published artifacts) and, at the bottom, the **Sketchpad** (logo-gestalt boards, design mocks,
prototypes, handoffs). Only files the config's globs find are served, at `/lib/<id>/`, plus files
beside them so a board's images load.

Tests: `python3 -m unittest discover -s automations/tailnet-index/tests`.

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
