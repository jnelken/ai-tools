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

1. **Start with `pup`.** It covers roughly 80% of Datadog work — monitors, logs, metrics,
   traces, RUM, dashboards. It is a direct authenticated CLI, so it is faster than MCP and
   costs no context loading tool schemas.
2. **Use the Datadog REST API** when the operation is a write or config change `pup` does not
   expose — log pipeline CRUD is the known case (see Known Findings).
3. **Use the Datadog MCP connector (`mcp__claude_ai_Datadog__*`) last**, only for product
   surfaces with no `pup` equivalent.

## What To Use

### Use `pup` When

- Default for any lookup or investigation: logs, metrics, monitors, traces, RUM, dashboards,
  incidents, services.
- `DD_API_KEY`, `DD_APP_KEY`, and `DD_SITE` are already set in the environment, so no setup
  is needed.
- Prefer it over hand-written `curl` whenever a subcommand exists for the task.

### Use Datadog REST API When

- You need to create/update log pipelines (`pup` has no custom-pipeline CRUD — see below).
- You need endpoint coverage not available in `pup`.
- You need precise control over full resource payloads.

Common patterns:

- List pipelines: `GET /api/v1/logs/config/pipelines`
- Read one pipeline: `GET /api/v1/logs/config/pipelines/{id}`
- Update one pipeline: `PUT /api/v1/logs/config/pipelines/{id}`

Environment requirements:

- `DD_SITE` (for this workspace: `us5.datadoghq.com`)
- `DD_API_KEY`
- `DD_APP_KEY`

### Use Datadog MCP When

Only when the task needs a product surface `pup` does not cover, for example:

- Workflow Automation
- App Builder
- LLM Observability datasets/experiments
- CSPM/SBOM findings

Note: the `datadog-api-claude-plugin` marketplace plugin is deliberately disabled — it only
wraps the same API behind an extra sub-agent hop that `pup` already covers directly. Do not
re-enable it for routine Datadog work.

## Known Findings From Latest Use (2026-03-27)

- `pup` is installed (`0.19.1`) but does not expose a logs custom-pipeline CRUD command in
  this environment.
- `pup logs custom-pipeline list` is not a valid command here.
- Datadog REST API is required for pipeline updates.
- For Neon logs in this workspace, host is a shared collector host; endpoint identity is best
  derived from:
  - `endpoint_id`
  - `endpoint_type`
  - `compute_role`

## Practical Workflow

1. Discover fields and explore the data with `pup`.
2. Confirm target resource shape with API `GET`.
3. Patch with API `PUT`.
4. Re-read resource with API `GET` to verify applied processors/filters.
5. Validate the new data path with a `pup` logs query after fresh events arrive.

## Validation Checklist

- Resource update response is successful and includes expected changes.
- Follow-up `GET` matches intended filter/processors.
- At least one post-change event contains the newly added fields.
- Any assumptions are documented in the run log/PR notes.

## Required Maintenance Note

After every use of this skill, update this file with:

- Date of run.
- Which tool path succeeded (`pup`, `API`, `MCP`, or mixed).
- Any newly discovered capability gaps or behavior changes.
- Any revised recommendation for future runs.
