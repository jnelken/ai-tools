---
name: prod-query
description: Answer read-only questions about production customer-tenant data (counts, per-user or per-entity-type aggregates, feature adoption joined with Datadog RUM user ids) through the api repo's audited query:prod-readonly script. Use when the answer lives in the tenant or management database rather than in telemetry, e.g. "how many favorites do these two users have", "entities per type for tenant X", "which of these RUM users adopted feature Y". Never for writes or raw PII.
---

# prod-query

Read-only production data questions, asked through one audited door: `pnpm run query:prod-readonly` in the **api** repo. The script (not this doc) is the source of truth for flags and enforcement; this skill documents the contract and how to use it safely. For the hands-off version, delegate to the `prod-reader` agent, which carries these rules.

> **Availability:** the script is api PR #1542 (branch `jake/prod-readonly-query`, CON-4086). Run it from the **api checkout** once that PR is merged (`~/code/api`); until then it exists only in that branch's worktree. If `pnpm run query:prod-readonly` is missing, stop and say so. Do not fall back to `psql`/`prisma`. The full guardrail doc is `docs/reference/prod-readonly-query.md` in that repo.

## Which tool for which question

| The question is about | Use |
|---|---|
| Rows/counts in a customer's database (favorites, entities, users, files, flags) | **this skill / `prod-reader`** |
| Errors, latency, logs, traces, RUM sessions, monitors, deploys, CI | `ops-investigator` (Datadog via `pup`, Koyeb, GitHub Actions) |
| How the code works, what a column means, prior decisions | `researcher` |
| Who used a feature in the UI (the RUM side of an adoption join) | `pup` / `rum-review` (see `datadog-tool-selection`) to get ids, then this skill to count against the DB |

Adoption questions are usually two halves: RUM tells you *who did something in the UI* (ids), the database tells you *what state they ended up in*. Get the ids from Datadog first, then query by id here.

## CLI contract

Run from the api checkout:

```
pnpm run query:prod-readonly -- \
  --reason "<why, required>" \
  --tenant <subscriberId|title|ep-endpoint> \
  [--db tenant|mgmt]        # default: tenant
  --sql "<single SELECT or WITH..SELECT>" \
  [--max-rows 200] \
  [--format json|table]
```

- `--reason` is required (8+ characters) and audited. Make it specific: the question and who wants it (`"favorites per entity type for 2 pilot users on tenant acme, CS check-in"`). Never put a secret in `--reason` or `--sql`: pnpm echoes the command line to the terminal before the script starts (`pnpm --silent run …` suppresses that).
- `--tenant` takes a subscriber id (UUID), the exact subscriber title (case-insensitive), or the Neon endpoint id (`ep-…`). Prefer the UUID: an ambiguous title is refused. **There is no `slug` column.**
- `--db tenant` is the customer's own database; `--db mgmt` is the management database (subscribers, users-to-tenant mapping, flags). `--tenant` is required for `--db tenant` and **ignored for `--db mgmt`**.
- `--max-rows` is 1–5000 (default 200). Exit codes: `0` ok · `1` runtime error · `2` usage error · `3` refused by a guard.
- Exactly one `SELECT` or `WITH ... SELECT`. Writes, DDL, multi-statement SQL, side-effecting functions and **column alias lists** (`FROM x AS t(a, b)`, `WITH t(a) AS`) are rejected. Alias columns inside the SELECT instead (`SELECT n AS total`).
- Connection config comes from `PROD_MGMT` and friends in `api/.env`. **Never print, echo or cat them, and never read `.env`.**
- Every run, including refused and failed ones, is appended to the audit log `~/.claude/state/prod-query-log.jsonl`. Output is withheld if that line cannot be written.
- **`--allow-pii` is human-only.** It needs an interactive terminal and a typed confirmation, so an agent cannot use it. Never pass it, and never try to reword a refused query to get around a guard: report the refusal.

## Map a RUM user or account to a tenant

The web app sets RUM identity in woodrow's `use-sync-datadog-user.ts`:

- `@usr.id` is the user's id (the `User.id` UUID).
- `@account.id` is the **subscriber id**, which is exactly what `--tenant` takes. If you have `@account.id`, you're done.
- If you only have `@usr.id`, resolve it through the management DB:

```sql
-- --db mgmt: which tenant(s) do these user ids belong to? (ids only, no email/name)
SELECT "subscriberId", count(*) AS matched_users
FROM "Users"
WHERE id IN ('<uuid1>', '<uuid2>')
GROUP BY "subscriberId"
```

Then confirm in the tenant DB that the same ids exist (`SELECT count(*) FROM "Users" WHERE id IN (...)`) before trusting a mgmt-to-tenant id match. The mgmt and tenant `User` tables are separate.

Other mgmt tables for tenant lookup: `"Subscribers"` (`id`, `deletedAt`; `title` is refused by the PII guard, and `--tenant` already resolves a title for you) and `"SubscriberNeonProjects"` (`subscriberId`, `neonProjectId`; `region` is free text and is refused). **Never select `databaseUrl` or `poolerUrl` from `"SubscriberNeonProjects"`, and never touch `account`, `session`, `Verification`, or OAuth/2FA tables** (credentials and tokens).

## Cookbook (verified against the schemas)

Table names are quoted PascalCase because Prisma maps them: tenant `"Favorite"` (no `@@map`), `"Entities"`, `"EntityTypes"`, `"Users"`, `"Subscribers"`. Check `~/code/api/prisma/tenant/schema.prisma` or `prisma/management/schema.prisma` before writing anything new; don't guess columns.

`--db tenant` unless noted. These pass the guard as written: they return counts, `uuid` ids, enums, timestamps and flags only.

```sql
-- Total favorites per user (2 users)
SELECT "userId", count(*) AS favorites
FROM "Favorite"
WHERE "userId" IN ('<uuid1>', '<uuid2>')
GROUP BY "userId"

-- Favorites per user per entity type. "Favorite" has no type column: go through
-- Entities. The type is reported as its id: EntityTypes.title is refused by the PII guard
-- (see "What the guard refuses"), so give the caller ids and let them map labels.
SELECT f."userId", e."entityTypeId", count(*) AS favorites
FROM "Favorite" f
LEFT JOIN "Entities" e ON e.id = f."entityId"
WHERE f."userId" IN ('<uuid1>', '<uuid2>')
GROUP BY f."userId", e."entityTypeId"
ORDER BY f."userId", favorites DESC

-- Live entity counts per type (exclude soft-deleted and archived), by type id
SELECT e."entityTypeId", count(*) AS entities
FROM "Entities" e
WHERE e."deletedAt" IS NULL AND e."archivedAt" IS NULL
GROUP BY e."entityTypeId"
ORDER BY entities DESC

-- Feature adoption: how many of these users have at least one favorite, out of how many exist
SELECT
  (SELECT count(*) FROM "Users" WHERE id IN ('<uuid1>', '<uuid2>', '<uuid3>') AND "deletedAt" IS NULL) AS active_users,
  (SELECT count(DISTINCT "userId") FROM "Favorite" WHERE "userId" IN ('<uuid1>', '<uuid2>', '<uuid3>')) AS users_with_favorites

-- Active-user headcount for the tenant
SELECT count(*) FILTER (WHERE "deletedAt" IS NULL) AS active_users, count(*) AS all_users
FROM "Users"

-- Which tables/columns exist (pg_catalog works; information_schema is refused)
SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'
```

Notes: `"Favorite"` has no `deletedAt`; `"Entities"` and `"Users"` do. To label entity types, get the `entityTypeId`s from the query and ask the caller (or Jake) to map them to type names in the app; do not try to select `EntityTypes.title`.

## What the guard refuses (exit 3)

The script's PII guard is a backstop, not permission to push on it. It refuses:

- **Text that is not an identifier or classifier.** A text column read straight from a table must have a source name like `*Id`, `status`, `kind`, `type`, `role`, `provider`, `mode`, `category`, … Everything else (`title`, `name`, `email`, `slug`, `valueRaw`, `snippet`, …) is refused, even aliased, wrapped, or renamed. `slug` is refused on purpose: entity slugs are slugified titles.
- **Computed text.** `upper(x)`, `x || y`, `max(title)`, `string_agg(...)`, `id::text`, literals like `'x' AS label`. Keep ids as `uuid`, counts as numbers; do not cast ids to text.
- **json/jsonb/xml/record/composite columns**, whole-row references (`to_json(t)`, `row_to_json`), and character-code/hex helpers (`ascii`, `chr`, `encode`, `md5`).
- **Personal-looking identifiers anywhere in the SQL** (`email`, `name`, `phone`, `address`, `token`, `url`, `content`, …), including in `WHERE`.
- **Values that look personal** (emails, JSON-looking text, strings over 512 characters) and any result containing a protected secret.
- `pg_stats`-family and credential catalogs (`pg_shadow`, `pg_authid`).

If a query is refused, report it as an escalation. Do not rewrite it into a cleverer shape to slip past a guard.

## PII rules

- **Aggregate or ids-only by default.** Counts, ids, timestamps, enum/type labels.
- **Never select** `email`, `name`, `image`, entity `title`/`slug`/`externalId`, `metadata`, `additionalDetails`, document/file/comment/chat content, or anything that identifies a person or a client's deal. The guard enforces most of this, but it is heuristic: you are still responsible. `Subscribers.title` may be used in `--tenant` to resolve a tenant, not selected to decorate a report.
- **Anonymize people in answers**: `U1`, `U2`, ... Give real ids only when the caller needs them for a RUM join, and never next to an email or name.
- **Refuse and hand to Jake**: any write, any request for raw PII or content, "dump everything", raising the row cap to get around aggregation, or a tenant nobody named.

## How to report

1. **Answer**: the number(s) first, with anonymized labels.
2. **Scope**: tenant (subscriber id), db (`tenant` or `mgmt`), environment (prod), when it ran.
3. **SQL run**: each statement verbatim plus its `--reason`, so it's reproducible.
4. **Caveats**: soft-deleted rows included or not, row cap hit, unverified assumptions (e.g. mgmt/tenant id match).
5. Mention the audit log (`~/.claude/state/prod-query-log.jsonl`) when the caller asks what was run.
