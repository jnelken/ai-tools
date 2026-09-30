---
name: datadog-tofu-sync
description: Reconcile `infra/datadog/` (OpenTofu) with live Datadog state — import existing monitors and Software Catalog entities so they come under tofu management, detect stale IDs in `infra/datadog/README.md`, and scaffold `.tf` blocks from existing monitors. Use when asked to "import datadog monitors", "sync datadog state", "adopt this monitor into tofu", "fix stale ids in the datadog readme", or after creating a monitor in the Datadog UI that should be managed in code.
---

# Datadog ↔ OpenTofu Sync

## Goal

Adopt resources that already exist in Datadog into the OpenTofu state tracked at `infra/datadog/terraform.tfstate` so a future `tofu apply` does not create duplicates. Never modify Datadog autonomously — this skill only mutates local state and (with permission) `.tf` / docs.

## Where this runs

The stack is in **folio-platform** (`~/code/folio-platform/infra/datadog/`). It isn't in api or woodrow any more, and older briefs that still say `api/infra/datadog` are stale. Its state is local and gitignored, so:

- Work in the **root checkout** on the open batch branch, not a worktree. Follow folio-platform's `CLAUDE.md` workflow.
- **Before you edit or apply, confirm the tree is yours.** Check `git status --porcelain` and `git log @{u}..HEAD`. Other sessions share this checkout, and `apply` takes the whole working tree, including their uncommitted `.tf` and unpushed commits. If anything isn't yours, coordinate with its session before touching the tree.
- **Apply before you push.** The pre-push hook blocks any push that still has unapplied changes.

## Auth

Always invoke through the npm wrapper scripts. They handle credentials via the `DD_API_KEY` / `DD_APP_KEY` / `DD_SITE` chain (env → `.env` → `~/.zsh_secrets`) and `cd` into `infra/datadog/` before running `tofu`. Do not call `tofu` directly from the repo root — the state file lives in the subdirectory.

```
npm run datadog:tofu:init
npm run datadog:tofu:plan
npm run datadog:tofu:import -- '<address>' '<id>'
npm run datadog:tofu:apply
```

The `--` is required so npm passes args through to the wrapper.

## Decision Order

1. **MCP first** for monitor lookup: `search_datadog_monitors`. Prefer searching by a unique tag (`tag:"monitor:<unique-key>"`) over title — titles often contain `{{env.name}}` template literals that defeat exact match. Tag conventions live in each `.tf` file under the `tags = [...]` block.
2. **Datadog REST API** for Software Catalog entities (no MCP coverage). Endpoint:
   ```
   GET https://api.${DD_SITE}/api/v2/catalog/entity?filter[ref]=<kind>:<namespace>/<name>
   ```
   The `id` field in the response is the entity UUID for import.
3. **Direct API** as fallback for any resource type the MCP doesn't expose.

## Workflow

### 1. Find what's pending

```bash
npm run datadog:tofu:plan 2>&1 | grep -E "will be (created|updated|destroyed)"
```

Each "will be created" line is a candidate for import. "will be updated in-place" indicates drift between live state and `.tf` — that's a separate decision (accept drift via apply, or amend `.tf` to match live).

### 2. Look up the live ID for each candidate

**For monitors** (`datadog_monitor.<name>` or `module.<name>.datadog_monitor.this`):

- Read the corresponding `.tf` file in `infra/datadog/` to find a unique tag from the `tags = [...]` block — typically `monitor:<something>` (e.g. `monitor:auth-stderr-regression`, `monitor:ai-chat-stream-latency`).
- Search:
  ```
  search_datadog_monitors(query: 'tag:"monitor:<unique-key>"')
  ```
- Verify the returned monitor's `query` field matches the `query` in the `.tf` file before trusting the ID. Title alone is not enough — multiple monitors can share a title prefix.

**For Software Catalog entities** (`datadog_software_catalog.<name>`):

- The `.tf` defines the ref via `metadata.kind`, `metadata.namespace` (defaults to `default`), and `metadata.name`.
- Query:
  ```bash
  curl -sS -G "https://api.${DD_SITE}/api/v2/catalog/entity" \
    --data-urlencode "filter[ref]=<kind>:default/<name>" \
    -H "DD-API-KEY: $DD_API_KEY" \
    -H "DD-APPLICATION-KEY: $DD_APP_KEY"
  ```
- The `data[].id` is the UUID to pass to import. Note: tofu accepts UUID for import but the resulting state ID is the **ref** (e.g. `system:default/example-service`), not the UUID — so a follow-up `tofu state show` will look different from the value you imported with. This is normal.

### 3. Determine the tofu address

- **Bare resource** (`resource "datadog_monitor" "<local>" { ... }`): address is `datadog_monitor.<local>`.
- **Module instance** (`module "<name>" { source = "./modules/log_error_monitor" ... }`): address is `module.<name>.datadog_monitor.this`. The `this` suffix comes from the resource label inside the module — verify by reading `infra/datadog/modules/<module>/main.tf`.
- Software Catalog: `datadog_software_catalog.<local>`.

### 4. Run the import

```bash
npm run datadog:tofu:import -- '<address>' '<id>'
```

Common errors:

- **"Resource already managed by OpenTofu"** — that address is already in state. Don't re-run; instead `tofu state show '<address>'` to check the stored ID matches the live one. If they differ, the prior import targeted a different remote object — `tofu state rm '<address>'` then re-import. If they match, the import was already done.
- **"Cannot import non-existent remote object"** — the ID is wrong (often stale, e.g. monitor was recreated and got a new ID). Re-search by tag.
- **Plain shell error** — quote the address in single quotes; module addresses contain `.` which some shells interpret. Avoid shell markers like `===` between chained imports (zsh interprets `=` specially).

### 5. Verify

```bash
npm run datadog:tofu:plan 2>&1 | grep -E "will be|^Plan:"
```

After importing, the resource should drop out of the "will be created" list. If it still appears, check that the address typed into `tofu import` matches the address declared in `.tf` exactly (including module prefix).

### 6. Refresh stale documentation

After every successful import session, sweep `infra/datadog/README.md` for the "Import Existing Monitors" and "Import Existing UI-Created Software Catalog Entities" sections. Compare each documented `<id>` (and any literal IDs) against the IDs now in state via:

```bash
cd infra/datadog && tofu state list | grep datadog_ \
  | xargs -I{} sh -c 'echo "{}"; tofu state show "{}" | grep -E "^\s+id\s+="'
```

Update README entries where the documented ID differs from the live ID, and add entries for newly imported addresses that weren't previously listed. Documenting current IDs is allowed — these are not secrets, and stale IDs cause confusion.

## Reverse Direction: Scaffold `.tf` From An Existing Monitor

When a monitor is created in the Datadog UI and should be brought under tofu management:

1. `search_datadog_monitors(query: 'id:<numeric_id>')` to retrieve the full definition.
2. If the monitor follows the log-alert pattern (filter query → log alert), use the shared `./modules/log_error_monitor` — instantiate as `module "<snake_case_name>" { source = "./modules/log_error_monitor" ... }`. Look at an existing `<service>-<symptom>.tf` module instantiation in `infra/datadog/` as the reference pattern.
3. If it's a metric or query alert, use a bare `resource "datadog_monitor" "<name>"` block — look at existing `.tf` files in `infra/datadog/` that follow the bare-resource pattern (e.g. response-time or latency monitors).
4. Always include `managed-by:opentofu` in the `tags` list.
5. After writing the `.tf`, run the import workflow above so the live monitor is adopted instead of recreated.

## What This Skill Does NOT Do

- Never runs `tofu apply` — that touches Datadog. Apply is a human decision.
- Never runs `tofu state rm` without confirming the stored ID actually mismatches live.
- Never modifies `.tf` files to match live drift without surfacing the diff first — drift can mean "live is correct" (someone tweaked in UI for a reason) or "code is correct" (live drifted accidentally), and only a human can pick.

## State Persistence Caveat

`infra/datadog/terraform.tfstate` is **gitignored** and there is no remote backend configured in `providers.tf`. Imports persist only on the machine that ran them — a fresh checkout will see every resource as "will be created" again until imports are re-run. Treat this skill's output as ephemeral until a shared backend is in place. When running `tofu apply` for the first time on a new machine, do a full import sweep beforehand to avoid duplicating live resources.

## Validation Checklist

- After all imports: `tofu plan` shows zero "will be created" entries except for genuinely-new resources (those that don't exist in Datadog yet).
- `tofu state list` (run from `infra/datadog/`) matches the resource set declared across `infra/datadog/*.tf`.
- README import sections reflect current live IDs; no entries reference resources that are already in state without noting they're imported.

## Run Log

Keep repository-specific import notes in a private project log, not in this public skill.
