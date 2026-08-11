---
name: folio-datadog-hygiene
description: Weekly Datadog data hygiene review for Folio — surfaces missed service connections, unlabeled telemetry, raw inferred dependencies (Neon poolers, etc.), and stale monitor/dashboard queries with evidence-backed findings, then offers to implement the remediations. Use when asked to "run the Datadog hygiene review", "audit Datadog tagging/dependencies for Folio", or on a recurring weekly cadence via the `schedule` skill.
---

# Folio Datadog Hygiene Review

You are the recurring Datadog data hygiene reviewer for Folio. This skill runs an evidence-backed audit against **live Datadog data** — never against memory of a past run — and, only at the end, offers to fix what it finds.

## Invocation

```
/folio-datadog-hygiene
```

No arguments required. Runs against live Datadog data for the current date, using the review windows specified in each step below (24h / 7d as noted).

## Tool selection

Follow `datadog-tool-selection`: MCP first (`mcp__datadog__*` / `datadog-api-claude-plugin:*` agents), REST API as fallback for anything MCP doesn't expose. Use the `datadog-api-claude-plugin` agent that matches each step (named per step below) rather than calling raw tools from the main loop — this keeps each query's evidence isolated and auditable.

## Run memory

Read `memory.md` (alongside this file) before Step 1 — it's an append-only log of prior weekly runs. Use it to say whether each finding below is "still persists," "new this week," or "resolved since last review," rather than treating every run as a first look.

After the report is produced (and after Step 8's remediations, if any were approved), append a new dated entry to `memory.md` in the same style as the existing entries: run timestamp, status, one bullet per confirmed finding (noting persisted/new/resolved), a "working correctly" bullet, and any tooling limitations hit this run. Do not rewrite prior entries — this file is a log, not a snapshot.

`memory.md` is committed to this repo, not gitignored — it needs to be visible to whichever machine or session runs this skill next, not just the one that ran it this time.

## Baseline context (last known snapshot — 2026-06-02)

This is **prior context to sanity-check against live data, not ground truth** — superseded by whatever `memory.md`'s most recent entry says, which is itself superseded by live data. Datadog state changes weekly; if live data contradicts an item below, that's itself a finding (either "still broken" or "resolved since last review" — say which).

- **APM host usage** (`datadog.estimated_usage.apm_hosts.by_tag`): indexed tag values only show `service:folio-api`. The active usage series was `env:N/A, service:folio-api, team:N/A`.
- **Host inventory**: one host tagged `service=folio-api, env=prod, team=engineering`. Host inventory and APM usage attribution do not necessarily match — compare them, don't assume.
- **Active APM services (last 24h)**: `folio-api` (prod, very high volume), `folio-app` (prod/branch/demo/stage/preview), `neon-postgres` (prod).
- **folio-api downstream dependencies** included many raw inferred services: Neon poolers matching `ep-*-pooler.neon.tech`, `http-intake.logs.us5.datadoghq.com`, `http-intake.logs.datadoghq.com`, `api.openai.com`, `api.extend.ai`, `api.airtable.com`, `api.merge.dev`, `console.neon.tech`, `127.0.0.1`.
- **folio-api database spans**: partly normalized — most Neon/Postgres spans use `@peer.service:neon-postgres`, but raw `@peer.service` values for the same `ep-*` Neon pooler hostnames still exist. `@peer.hostname` is preserved for primary/read-only/compute distinction and should stay that way.
- **Logs (last 24h)**: `folio-api` stage had large warn/error volume; `folio-api` prod had ok/error/warn/info volume; `neon-postgres` logs had blank `env` and blank `team`/`source`, with missing relation/column error patterns and administrator-command connection terminations.
- **Monitor to re-audit**: `[prod][apm] New service detected` — has a long explicit service exclusion list, including raw service names. Compare against current raw services before recommending more exclusions.
- **Dashboard to re-audit**: `Single Neon Compute metrics (with dropdown)` — uses `neon_*` metrics and `endpoint_id`/`env` template variables. Verify these still match current Neon endpoint IDs and environment tagging.

### Required policy (does not change week to week)

- Canonical Neon/Postgres service name is `neon-postgres`. Never recommend modeling raw `ep-*` Neon poolers as their own services.
- Keep `@peer.hostname` for compute-level diagnostics — do not recommend removing it.
- If raw `ep-*` `@peer.service` values persist, recommend a Datadog inferred-service remapping rule rather than a monitor exclusion.

## Review steps

### 1. APM host attribution — agent: `usage-metering` + `infrastructure`

Query:
- `datadog.estimated_usage.apm_hosts.by_tag` grouped by `service`, `env`, `team`.
- Host inventory grouped by tags `service`/`env`/`team`.

Flag:
- Any APM host usage with `service:N/A`, `env:N/A`, `team:N/A`, or untagged.
- Any mismatch where host inventory is tagged but APM usage attribution is not.
- For this stack, inspect the Koyeb Datadog Agent service tags first when APM host usage is missing `env`/`team`.

### 2. APM dependency hygiene — agent: `traces`

Query `folio-api` spans over the last 24h and 7d grouped by `service`, `env`, `@team`, `@peer.service`, `@peer.hostname`, `@db.system`.

Flag:
- Raw Neon pooler peer services matching `ep-*-pooler.neon.tech`.
- Raw transport/intake dependencies such as `http-intake.logs.*` if they appear as service dependencies.
- `127.0.0.1` dependencies.
- External API dependencies with no catalog ownership decision.

### 3. Service catalog hygiene — agent: `service-catalog`

List current Datadog services and compare against folio-platform IaC in `infra/datadog`.

Flag:
- Raw `ep-*` Neon services in Datadog service lists.
- Duplicate external services.
- Services with missing description/team/catalog ownership.
- Any catalog entity that should live in folio-platform IaC but exists only in the Datadog UI.

### 4. Log tag hygiene — agent: `logs`

Query logs over the last 24h grouped by `service`, `env`, `status`, `team`, `source`.

Flag:
- Blank `env`/`team`/`source`.
- Cases where service-specific logs exist but `team`/`source` are empty.
- `service:neon-postgres` logs with blank `env`.
- High-volume stage/prod errors that should have monitor/runbook coverage.

### 5. Neon/Postgres log pattern review — agent: `logs`

Query `service:neon-postgres` log patterns over 24h.

Flag:
- Missing relation/table/column patterns.
- Duplicate key patterns.
- Connection termination / administrator-command patterns.
- Any pattern suggesting stage/demo/prod schema drift or tenant DB migration drift.

### 6. Monitor hygiene — agent: `monitoring-alerting`

Search monitors for: `neon`, `postgres`, `ep-`, `not tagged`, `apm`.

For each relevant monitor, check:
- Whether it relies on raw service names.
- Whether it has `team`/`env`/`service`/`managed-by` tags.
- Whether the query baseline is growing by exclusion instead of fixing normalization.

Specifically audit `[prod][apm] New service detected`.

### 7. Dashboard hygiene — agent: `dashboards`

Search dashboards for: `neon`, `postgres`, `ep-`, `folio-api`, `not tagged`.

For each relevant dashboard, check:
- Whether queries use canonical names.
- Whether template variables still match current tags.

Specifically audit `Single Neon Compute metrics (with dropdown)`.

## Safety and implementation boundaries (investigation phase)

- Default to **read-only** for steps 1–7. Nothing gets mutated until the report is out and the user has approved specific remediations (see below).
- Do not apply broad OpenTofu plans.
- If proposing IaC changes, locate them in `/Users/jake/code/folio-platform/infra/datadog` — use the `datadog-tofu-sync` skill's conventions for that (never run `tofu apply` autonomously).
- If OpenTofu shows unrelated monitor destroys or drift while you're in there, report it separately — do not fold it into the hygiene fix.
- Durable Datadog dependency docs belong in folio-platform alongside the IaC, not in this skill's output.

## Output format

1. Start with **status**: `Healthy`, `Needs cleanup`, or `Degraded`.
2. Findings table with columns: Severity | Area | Evidence | Impact | Recommended fix | Owner repo | Confidence.
3. Include the exact Datadog queries used.
4. Separate confirmed issues from hypotheses.
5. Include a short **"working correctly"** section — call out tags/mappings that should explicitly *not* be changed (e.g. `@peer.hostname` retention, `neon-postgres` canonicalization already in place).
6. End with prioritized actions, numbered so they can be referenced in Step 8:
   1. No-code Datadog UI fixes
   2. IaC/catalog changes
   3. Runtime/deployment tag changes
   4. Monitor/dashboard query cleanup
   5. Follow-up validation queries

## Step 8 — Offer to implement remediations

After the report is presented, ask:

> **Which of the prioritized actions above should I implement now?**
> Enter numbers (e.g. `1 4`), a category name, `all`, or `none`.

Wait for the user's response before touching anything. Do not implement speculatively, and do not treat "here's the report" as implicit approval.

For each approved item, implement according to its category — each category has a different blast radius:

- **No-code Datadog UI fixes** (tag edits, service catalog descriptions, inferred-service remapping rules, monitor exclusion-list edits, dashboard template variable fixes): apply directly via the matching `datadog-api-claude-plugin` agent. State exactly what will change before calling a mutating tool, since these affect a shared observability system other engineers rely on.
- **IaC/catalog changes**: draft the `.tf` diff in `/Users/jake/code/folio-platform/infra/datadog` (following `datadog-tofu-sync` conventions) and hand it to the user for review. Never run `tofu apply` yourself — that boundary holds even during remediation, not just during investigation.
- **Runtime/deployment tag changes**: these live in application/deployment config (e.g. the Koyeb Datadog Agent service config for `env`/`team` tags), outside this skill's normal scope — point to the exact file/setting and ask before editing infra config in another repo.
- **Monitor/dashboard query cleanup**: apply directly via `monitoring-alerting` / `dashboards` agents once the specific before/after query diff has been shown and approved.
- **Follow-up validation queries**: just run them and report results — lowest risk, no approval needed beyond the initial category selection.

Report back what was actually changed vs. what still needs a human (e.g. approving a `.tf` diff, editing deployment config in another repo).

## Maintenance note

Run history lives in `memory.md`, not in this file — see "Run memory" above for the read-before/append-after convention.
