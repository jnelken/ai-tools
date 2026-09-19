---
name: datadog-tool-selection
description: Choose between the pup CLI, the Datadog MCP connector, and the Datadog REST API for a given Datadog task — logs, metrics, monitors, traces, RUM, dashboards, incidents, notebooks. Use when starting a Datadog lookup or investigation and the access path is unclear, or when the chosen path does not expose a needed operation and you need the documented fallback order.
---

# Datadog Tool Selection Skill

Use this skill to choose between Datadog MCP tools, direct Datadog REST API calls, and `pup` CLI.

## Goal

Pick the lowest-maintenance path that can reliably complete the task with verification.

## Decision Order

1. Start with Datadog MCP for search, analysis, and investigation workflows.
2. Use Datadog REST API when MCP does not expose the needed write operation.
3. Use `pup` only for operations it explicitly supports in this environment.

## What To Use

### Use Datadog MCP When

- You need to inspect logs, metrics, spans, services, incidents, dashboards, monitors, or notebooks.
- You need SQL-style log analysis (`analyze_datadog_logs`) for grouping/counting.
- You need fast exploratory work with structured output.

Preferred MCP tools:

- `search_datadog_logs` for raw events and field discovery.
- `analyze_datadog_logs` for aggregations and distinct value extraction.
- `search_datadog_monitors`, `search_datadog_dashboards`, `search_datadog_services` for inventory.
- `get_datadog_metric`, `search_datadog_spans`, `get_datadog_trace` for performance debugging.

### Use Datadog REST API When

- You need to create/update log pipelines.
- You need endpoint coverage not available in MCP tools.
- You need precise control over full resource payloads.

Common patterns:

- List pipelines: `GET /api/v1/logs/config/pipelines`
- Read one pipeline: `GET /api/v1/logs/config/pipelines/{id}`
- Update one pipeline: `PUT /api/v1/logs/config/pipelines/{id}`

Environment requirements:

- `DD_SITE` (for this workspace: `us5.datadoghq.com`)
- `DD_API_KEY`
- `DD_APP_KEY`

### Use `pup` When

- You need supported `pup` flows that are faster than hand-written `curl` (for example many query/reporting tasks).
- The exact subcommand exists in your installed version and returns valid JSON for your task.

## Known Findings From Latest Use (2026-03-27)

- `pup` is installed (`0.19.1`) but does not expose a logs custom-pipeline CRUD command in this environment.
- `pup logs custom-pipeline list` is not a valid command here.
- Datadog REST API is required for pipeline updates.
- For Neon logs in this workspace, host is a shared collector host; endpoint identity is best derived from:
  - `endpoint_id`
  - `endpoint_type`
  - `compute_role`

## Practical Workflow

1. Discover fields with MCP:
   - `search_datadog_logs` with `extra_fields: ["*"]`
   - `analyze_datadog_logs` with explicit `extra_columns`
2. Confirm target resource shape with API `GET`.
3. Patch with API `PUT`.
4. Re-read resource with API `GET` to verify applied processors/filters.
5. Validate new data path with MCP logs query after fresh events arrive.

## Validation Checklist

- Resource update response is successful and includes expected changes.
- Follow-up `GET` matches intended filter/processors.
- At least one post-change event contains the newly added fields.
- Any assumptions are documented in the run log/PR notes.

## Required Maintenance Note

After every use of this skill, update this file with:

- Date of run.
- Which tool path succeeded (`MCP`, `API`, `pup`, or mixed).
- Any newly discovered capability gaps or behavior changes.
- Any revised recommendation for future runs.
