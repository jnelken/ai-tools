---
name: datadog-tool-selection
description: Choose between the pup CLI, the Datadog REST API, and the Datadog MCP connector for a given Datadog task — logs, metrics, monitors, traces, RUM, dashboards, incidents, notebooks. Use when starting a Datadog lookup or investigation and the access path is unclear, or when pup does not expose a needed operation and you need the documented fallback order.
---

# Datadog Tool Selection Skill

Use this skill to choose between the `pup` CLI, direct Datadog REST API calls, and the
Datadog MCP connector.

## Goal

Pick the lowest-maintenance path that can reliably complete the task with verification.

## Decision Order

1. **Start with `pup`.** It covers most Datadog work — monitors, logs, metrics, traces, RUM,
   dashboards, LLM Obs spans and evaluators, downtimes, audit logs. It is a direct
   authenticated CLI, so it is faster than MCP and costs no context loading tool schemas.
2. **Use the REST API through `pup api <path>`** when `pup` has no subcommand for the
   operation (see Known Gaps). It reuses pup's auth and site, so prefer it to hand-written
   `curl`.
3. **Use the Datadog MCP connector (`mcp__claude_ai_Datadog__*`) last**, only for product
   surfaces with no `pup` equivalent.

## Folio's Datadog config: use `datadog-researcher`

In a folio-platform session, delegate read-only Datadog research (what a field or query
does, file vs live drift, whether a query matches data) to the repo's
`datadog-researcher` agent (`folio-platform/.claude/agents/datadog-researcher.md`). Its
"Known traps" section is the maintained list of pup/API/MCP gotchas hit so far — rate
limits, syntax, silent no-ops, validation blind spots. From another repo, read that
section before a non-trivial Datadog lookup rather than re-learning it.

## What To Use

### Use `pup` When

- Default for any lookup or investigation.
- `DD_API_KEY`, `DD_APP_KEY`, and `DD_SITE` (`us5.datadoghq.com`) are already set in the
  environment, so no setup is needed.
- Always pass `--no-agent` when scripting, so output is the raw payload rather than the
  agent envelope, and use `pup --no-agent <cmd> --help` (agent mode prints a JSON schema
  instead of usage).
- Pass `--read-only` for lookups: pup then refuses create/update/delete. It does **not**
  cover `pup api -X POST|PUT|PATCH|DELETE` (verified 2026-10-12).
- `pup docs ask "<question>"` queries Datadog's docs AI. Treat answers as leads and confirm
  against `docs.datadoghq.com`.

### Use `pup api` (REST) When

- Log pipeline CRUD and ordering: `pup api v1/logs/config/pipelines[/<id>]`,
  `v1/logs/config/pipeline-order` (`-X POST|PUT --input -` to write).
- Per-group monitor state: `pup api "v1/monitor/<id>?group_states=all"`.
- Monitor definition validation: `pup api -X POST v1/monitor/validate --input - < file.json`
  (returns `{}` when valid; checks schema, not notification handles).
- Metric existence: `pup api v2/metrics/<m>/all-tags` (`/tags` 404s on metrics with no tag
  configuration).
- Any other endpoint pup lacks, or when you need the full resource payload.

### Use Datadog MCP When

Only when the task needs a product surface `pup` does not cover, for example:

- Workflow Automation
- App Builder
- LLM Observability datasets/experiments
- CSPM/SBOM findings

Known MCP pitfalls: `create_datadog_monitor` creates **draft** monitors that never notify;
dashboard `validate` passes layouts the real API rejects; `ask_widget_expert` has given
wrong answers.

Note: the `datadog-api-claude-plugin` marketplace plugin is deliberately disabled — it only
wraps the same API behind an extra sub-agent hop that `pup` already covers directly. Do not
re-enable it for routine Datadog work.

## Known Gaps (pup 1.10.3, 2026-10-12)

- No create/update for logs indexes, logs metrics (`get|list|delete` only), or synthetics
  tests (`get|list|search|run`). folio-platform's `npm run stuffy-pup` wraps the REST API for
  those three.
- No log pipeline subcommand — use `pup api` (above).
- No `pup monitors validate` — use `pup api` (above).
- `pup integrations slack list` returns 400 regardless of config.
- `pup llm-obs spans search` **does** exist (needs `--ml-app`; output is `.spans`, not
  `.data`).
- For Neon logs in this workspace, host is a shared collector host; endpoint identity is
  best derived from `endpoint_id`, `endpoint_type`, `compute_role`.

## Practical Workflow (config changes)

1. Discover fields and explore the data with `pup` (`--read-only`).
2. Confirm target resource shape with a GET.
3. Write with the pup subcommand, or `pup api -X PUT` when there is none.
4. Re-read the resource with a GET to verify what was stored.
5. Validate the data path with a `pup` query after fresh events arrive — many Datadog
   writes succeed while changing nothing (an absent remapper source, stale metric points).

## Validation Checklist

- Resource update response is successful and includes expected changes.
- Follow-up `GET` matches intended filter/processors.
- At least one post-change event contains the newly added fields.
- Any assumptions are documented in the run log/PR notes.

## Required Maintenance Note

When a run finds a pup capability gap, a behavior change, or a new trap, update **Known
Gaps** here (tool-path facts) and add the trap to
`folio-platform/.claude/agents/datadog-researcher.md` (behavior facts), with the date.
