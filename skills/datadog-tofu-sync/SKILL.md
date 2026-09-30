---
name: datadog-tofu-sync
description: Reconcile `infra/datadog/` (OpenTofu) with live Datadog state for the resources pup cannot manage — logs metrics, the logs index, and synthetics tests — plus detect stale IDs in `infra/datadog/README.md`. Monitors and Software Catalog entities are NOT tofu-managed any more (they are `monitors/*.json` / `catalog/*.json` applied with `pup`). Use when asked to "import a logs metric", "adopt this synthetics test into tofu", "sync datadog state", or "fix stale ids in the datadog readme".
---

# Datadog ↔ OpenTofu Sync

## Scope change: monitors and catalog entities left tofu

Monitors and Software Catalog entities are **no longer managed by OpenTofu**. They are JSON files managed with the `pup` CLI, and this skill does not apply to them:

- Monitors: `monitors/*.json` — `pup monitors create --file <f>`, `pup monitors diff <id> <f>`, `pup monitors update <id> --file <f>`; the file → monitor-ID table is `monitors/README.md`. To adopt a monitor created in the UI, `pup monitors get <id>`, strip server-owned fields (id, created, modified, creator, overall_state, org_id…) into `monitors/<name>.json`, confirm with `pup monitors diff`, and record the ID.
- Catalog entities: `catalog/*.json` — `pup software-catalog entities upsert --file <f>`.

What this skill still covers is what pup 1.10.3 can't create or update: `datadog_logs_metric`, `datadog_logs_index` / `datadog_logs_index_order`, and `datadog_synthetics_test`. Ignore any monitor, module, or catalog steps below that predate the move; they are kept only as history.

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

1. **`pup` / MCP first** for lookups (`pup logs metrics get <name>`, `pup synthetics tests get <public_id>`, or the MCP equivalents).
2. **Direct API** as fallback for any resource type the MCP doesn't expose.

## Workflow

### 1. Find what's pending

```bash
npm run datadog:tofu:plan 2>&1 | grep -E "will be (created|updated|destroyed)"
```

Each "will be created" line is a candidate for import. "will be updated in-place" indicates drift between live state and `.tf` — that's a separate decision (accept drift via apply, or amend `.tf` to match live).

### 2. Look up the live ID for each candidate

**Logs metrics** import by metric name (e.g. `folio.api.extend.credits`); **logs index** by `main`; **synthetics tests** by bare public ID (see below). No search step needed.

### 3. Determine the tofu address

- Address is `<resource_type>.<local_name>` exactly as declared in the `.tf` (e.g. `datadog_logs_metric.folio_api_extend_credits`, `datadog_logs_index.main`, `datadog_synthetics_test.<name>`).

### 4. Run the import

```bash
npm run datadog:tofu:import -- '<address>' '<id>'
```

Common errors:

- **"Resource already managed by OpenTofu"** — that address is already in state. Don't re-run; instead `tofu state show '<address>'` to check the stored ID matches the live one. If they differ, the prior import targeted a different remote object — `tofu state rm '<address>'` then re-import. If they match, the import was already done.
- **"Cannot import non-existent remote object"** — the ID is wrong (stale, or the resource was recreated). Re-check it with `pup`.
- **Plain shell error** — quote the address in single quotes; module addresses contain `.` which some shells interpret. Avoid shell markers like `===` between chained imports (zsh interprets `=` specially).

### 5. Verify

```bash
npm run datadog:tofu:plan 2>&1 | grep -E "will be|^Plan:"
```

After importing, the resource should drop out of the "will be created" list. If it still appears, check that the address typed into `tofu import` matches the address declared in `.tf` exactly (including module prefix).

### 6. Refresh stale documentation

After every successful import session, sweep `infra/datadog/README.md` for the "Import Existing Resources" section. Compare each documented `<id>` (and any literal IDs) against the IDs now in state via:

```bash
cd infra/datadog && tofu state list | grep datadog_ \
  | xargs -I{} sh -c 'echo "{}"; tofu state show "{}" | grep -E "^\s+id\s+="'
```

Update README entries where the documented ID differs from the live ID, and add entries for newly imported addresses that weren't previously listed. Documenting current IDs is allowed — these are not secrets, and stale IDs cause confusion.

## Reverse Direction: new monitor created in the UI

Not a tofu job any more. Export it to `monitors/<name>.json` with `pup monitors get <id>` (see **Scope change** above), verify with `pup monitors diff <id> monitors/<name>.json`, and add the ID to `monitors/README.md`. Tag it `managed-by:pup`, not `managed-by:opentofu`.

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
