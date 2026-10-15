---
name: prod-query
description: Answer read-only questions about production customer-tenant data (counts, per-user or per-entity-type aggregates, feature adoption joined with Datadog RUM user ids) through the api repo's audited query:prod-readonly script. Use when the answer lives in the tenant or management database rather than in telemetry, e.g. "how many favorites do these two users have", "entities per type for tenant X", "which of these RUM users adopted feature Y". Never for writes or raw PII.
---

# prod-query

Read-only production data questions, asked through one audited door: `pnpm run query:prod-readonly` in the **api** repo. The script (not this doc) is the source of truth for flags and enforcement; this skill documents the contract and how to use it safely. For the hands-off version, delegate to the `prod-reader` agent, which carries these rules.

> **Availability:** the script lands with api PR `jake/prod-readonly-query`. Run it from the **api checkout** once that PR is merged (`~/code/api`); until then it exists only in that branch's worktree. If `pnpm run query:prod-readonly` is missing, stop and say so. Do not fall back to `psql`/`prisma`.

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
  --tenant <subscriberId|slug> \
  [--db tenant|mgmt]        # default: tenant
  --sql "<single SELECT or WITH..SELECT>" \
  [--max-rows 200] \
  [--format json|table]
```

- `--reason` is required and audited. Make it specific: the question and who wants it (`"favorites per entity type for 2 pilot users on tenant acme, CS check-in"`).
- `--tenant` takes a subscriber id (UUID) or slug.
- `--db tenant` is the customer's own database; `--db mgmt` is the management database (subscribers, users-to-tenant mapping, flags).
- Exactly one `SELECT` or `WITH ... SELECT`. Writes, DDL and multi-statement SQL are rejected.
- Connection config comes from `PROD_MGMT` and friends in `api/.env`. **Never print, echo or cat them, and never read `.env`.**
- Every run is appended to the audit log `~/.claude/state/prod-query-log.jsonl`.
- Open question to check against `pnpm run query:prod-readonly -- --help` after the merge: whether `--db mgmt` still needs a `--tenant`.

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

Other mgmt tables for tenant lookup: `"Subscribers"` (`id`, `title`, `deletedAt`) and `"SubscriberNeonProjects"` (`subscriberId`, `neonProjectId`, `region`). **Never select `databaseUrl` or `poolerUrl` from `"SubscriberNeonProjects"`, and never touch `account`, `session`, `Verification`, or OAuth/2FA tables** (credentials and tokens).

## Cookbook (verified against the schemas)

Table names are quoted PascalCase because Prisma maps them: tenant `"Favorite"` (no `@@map`), `"Entities"`, `"EntityTypes"`, `"Users"`, `"Subscribers"`. Check `~/code/api/prisma/tenant/schema.prisma` or `prisma/management/schema.prisma` before writing anything new; don't guess columns.

`--db tenant` unless noted.

```sql
-- Total favorites per user (2 users)
SELECT "userId", count(*) AS favorites
FROM "Favorite"
WHERE "userId" IN ('<uuid1>', '<uuid2>')
GROUP BY "userId"

-- Favorites per user per entity type. "Favorite" has no type column: go through
-- Entities -> EntityTypes. LEFT JOINs keep favorites whose entityId is NULL or points at a deleted entity.
SELECT f."userId", et."title" AS entity_type, count(*) AS favorites
FROM "Favorite" f
LEFT JOIN "Entities" e ON e.id = f."entityId"
LEFT JOIN "EntityTypes" et ON et.id = e."entityTypeId"
WHERE f."userId" IN ('<uuid1>', '<uuid2>')
GROUP BY f."userId", et."title"
ORDER BY f."userId", favorites DESC

-- Live entity counts per type (exclude soft-deleted and archived)
SELECT et."title" AS entity_type, count(*) AS entities
FROM "Entities" e
JOIN "EntityTypes" et ON et.id = e."entityTypeId"
WHERE e."deletedAt" IS NULL AND e."archivedAt" IS NULL
GROUP BY et."title"
ORDER BY entities DESC

-- Feature adoption: how many of these users have at least one favorite, out of how many exist
SELECT
  (SELECT count(*) FROM "Users" WHERE id IN ('<uuid1>', '<uuid2>', '<uuid3>') AND "deletedAt" IS NULL) AS active_users,
  (SELECT count(DISTINCT "userId") FROM "Favorite" WHERE "userId" IN ('<uuid1>', '<uuid2>', '<uuid3>')) AS users_with_favorites

-- Active-user headcount for the tenant
SELECT count(*) FILTER (WHERE "deletedAt" IS NULL) AS active_users, count(*) AS all_users
FROM "Users"
```

Notes: `"Favorite"` has no `deletedAt`; `"Entities"` and `"Users"` do. `EntityTypes.title` is the tenant-configured type label, which is fine to report. Adapt the same patterns (join through `"Entities"` and `"EntityTypes"`, aggregate with `count`) for other per-type questions.

## PII rules

- **Aggregate or ids-only by default.** Counts, ids, timestamps, enum/type labels.
- **Never select** `email`, `name`, `image`, entity `title`/`slug`/`externalId`, `metadata`, `additionalDetails`, document/file/comment/chat content, or anything that identifies a person or a client's deal. `Subscribers.title` is only OK to resolve a tenant, not to decorate a report.
- **Anonymize people in answers**: `U1`, `U2`, ... Give real ids only when the caller needs them for a RUM join, and never next to an email or name.
- **Refuse and hand to Jake**: any write, any request for raw PII or content, "dump everything", raising the row cap to get around aggregation, or a tenant nobody named.

## How to report

1. **Answer**: the number(s) first, with anonymized labels.
2. **Scope**: tenant (id/slug), db (`tenant` or `mgmt`), environment (prod), when it ran.
3. **SQL run**: each statement verbatim plus its `--reason`, so it's reproducible.
4. **Caveats**: soft-deleted rows included or not, row cap hit, unverified assumptions (e.g. mgmt/tenant id match).
5. Mention the audit log (`~/.claude/state/prod-query-log.jsonl`) when the caller asks what was run.
