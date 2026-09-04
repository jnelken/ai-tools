---
name: archived-flag-cleanup
description: Delete the gate code for archived (fully GA) Folio feature flags across api and woodrow — read the archived set from the management DB, compare it across environments, check polarity per call site, prune the orphans, and regenerate both usage manifests. Use when asked to "delete archived flag code", "clean up GA'd feature flags", "retire a feature flag", "remove a flag gate", or after archiving flags in the management portal. Also use when deciding whether a flag is safe to delete.
---

# Archived Feature Flag Cleanup

## Goal

Remove the gate code for flags that have finished rolling out, without changing
behaviour and without reviving dead code paths.

**Never delete `FeatureFlagDefs` rows.** Code PRs delete code; Jake retires the
rows in the management portal. Deleting the row is also how you'd break the
`GET /` resolution contract for any client still asking for the key.

## Why archived means safe to delete

`adminArchivedAt` on `FeatureFlagDefs` means *rollout won*. Archiving:

- forces `defaultValue = true`, and
- **hard-deletes every `FeatureFlagAssignment` override row** (not recoverable —
  unarchive does not bring them back).

So an archived flag resolves `true` for every subscriber. A gate reading it can
only ever return `true`, which is what makes deleting the gate
behaviour-preserving *in the environment where it is archived*. That caveat is
the whole of step 2.

## Step 1 — Get the archived set from the database

Archived state is **not in git**. There is no code-side flag registry in api at
all (the key set is DB-owned), and woodrow's `FEATURE_FLAGS` registry carries no
lifecycle data. You must query.

Feature flags live in the **management** DB, so no tenant-URL decryption is
needed — `PROD_MGMT` from api's `.env` is enough.

```sh
cd /Users/jake/code/api
set -a; . ./.env >/dev/null 2>&1; set +a
psql "$PROD_MGMT" -P pager=off -c '
SELECT key, label, "groupName", "defaultValue",
       to_char("adminArchivedAt", '"'"'YYYY-MM-DD'"'"') AS archived,
       (SELECT count(*) FROM "FeatureFlagAssignments" a WHERE a."flagId" = f.id) AS assignments
FROM "FeatureFlagDefs" f
WHERE "adminArchivedAt" IS NOT NULL AND "adminDeletedAt" IS NULL
ORDER BY "adminArchivedAt", key;'
```

Needs `dangerouslyDisableSandbox` (network egress to Neon).

Sanity check: every archived row should come back `defaultValue = t` with
`assignments = 0`. Anything else means someone wrote to the row outside the
archive path — stop and investigate rather than deleting code.

### The naming trap — do not "fix" these column names

The lifecycle columns are deliberately `adminHiddenAt`, `adminDeletedAt`,
`adminArchivedAt`. Renaming any of them to `archivedAt`/`deletedAt` opts
`FeatureFlagDef` into the automatic lifecycle filter in `prisma/index.ts`, which
would silently drop flags out of **resolution** and rewrite unrelated `where`
clauses. The schema comments say so; believe them.

Their meanings are not interchangeable:

| Column | Still listed? | Still resolves? |
|---|---|---|
| `adminHiddenAt` | no (admin list only) | **yes, unchanged** |
| `adminArchivedAt` | yes | yes, pinned `true` for everyone |
| `adminDeletedAt` | no | **no — resolves `false`** |

## Step 2 — Compare across environments before deleting anything

**This is the step that catches the surprise.** Archive state is per-environment,
but code deletion is global. A flag archived in prod can still be mid-rollout —
or off entirely — in stage, and deleting its gate silently turns it on there.

```sh
for E in PROD_MGMT STAGE_MGMT DEMO_MGMT; do
  URL="${(P)E}"   # zsh indirect expansion
  echo "===== $E ====="
  psql "$URL" -P pager=off -c '
  SELECT key, "defaultValue" AS def,
         to_char("adminArchivedAt", '"'"'YYYY-MM-DD'"'"') AS archived
  FROM "FeatureFlagDefs" WHERE key IN (<the keys from step 1>) ORDER BY key;' 2>&1
done
```

For each flag, classify:

- **archived, or `defaultValue = true` with no false overrides** → deletion is
  behaviour-preserving there. Fine.
- **`defaultValue = false`** → deletion **turns the feature on** in that
  environment. Still usually correct (prod is the GA truth), but say so
  explicitly in the PR description. Stage is where QA happens, so an unannounced
  flip there generates "why did this change?".

Known state as of 2026-09: the demo management DB is behind on migrations and
has no `adminArchivedAt`/`adminDeletedAt`/`groupName` columns at all — that
query will error against it. Pre-existing drift; not something this cleanup
fixes.

## Step 3 — Find every reference, in both repos

A flag can live in either repo or both. `activity_hub` is woodrow-only;
`chat_claude_models` was api-only; most `ff_*` flags are woodrow-only.

```sh
# api — no registry; grep the key and check the generated manifest
cd /Users/jake/code/api
grep -rn "<key>" src scripts utils types --include='*.ts' --include='*.mdx' | grep -v server-consumed-flags.json
jq '.flags["<key>"]' src/services/feature-flags/server-consumed-flags.json

# woodrow — registry + call sites
cd /Users/jake/code/woodrow
grep -rn "<key>" app apps e2e --include='*.ts' --include='*.tsx'
```

Quote the `--include` globs — zsh expands them otherwise and the grep silently
finds nothing.

In api, every DB flag read funnels through `getResolvedFlagsForSubscriber` /
`resolveFlagsForSubscriber`, so grepping those two names enumerates the read
sites provably. Watch for the one exception:
`src/routes/chat/get-change-history.ts` queries `managementDb` directly and
reimplements resolution.

### Wrapper-dead is not key-live

A per-flag wrapper module (`src/services/ai/<name>-flag.ts`) can have **zero
production callers while the key is still read elsewhere**. `chat_claude_models`
was exactly this: the wrapper was dead, but the live read was an entry in
`PROVIDER_SELECTION_FLAGS` in `chat-model-availability.ts`. Deleting only the
wrapper would have left the flag fully functional and the task half-done.

Check the wrapper and the key separately.

## Step 4 — Polarity, per call site, never per flag

**This is where a mechanical sweep ships a silent bug.** Do not conclude "these
are all positive guards" from a sample. Read every site. Real examples, all from
one flag:

| Shape | Reading | Correct action |
|---|---|---|
| `if (!enabled) return null;` | positive guard | drop the guard, keep the body |
| `if (enabled) return;` | **inverted** | delete everything *after* the guard |
| `{!enabled && x ? (…) : null}` | **negative** | delete the whole block |
| `if (!enabled \|\| other) return <Legacy/>` | **compound** | drop only the flag half |
| `enabled ? <New/> : <Legacy/>` | ternary | keep the true branch, delete the else |

The inverted early-return is the dangerous one. With the flag archived, `if
(enabled) return;` **already always fires**, so the code below it is *already
dead*. "Drop the guard, keep the body" resurrects it. In the real case this was
a legacy `createNotification` fallback sitting under a server-side notification
— reviving it would have double-notified every document mention.

Sweep for the non-positive shapes explicitly:

```sh
grep -rnE "if \(<varName>\) return|!<varName> &&|<varName> \? " app apps --include='*.tsx' --include='*.ts'
```

## Step 5 — Prune orphans, then verify they are actually orphaned

Deleting a branch orphans whatever only that branch used. Let the linter find
them rather than guessing:

```sh
cd /Users/jake/code/woodrow
npx prettier --write <file>
npx eslint <file> --config eslint.config.precommit.js   # no-unused-vars warnings
```

Then for each orphan, **check whether it is dead at its source too** — the
linter only tells you this file stopped using it:

```sh
grep -rn "<Symbol>" app apps --include='*.ts' --include='*.tsx' | grep -v "<its own definition file>"
```

Do not trust a report (or your own earlier note) that says "this constant stays,
it's used elsewhere". Verify per symbol. In the `ff_condensed_header` cleanup,
`LEGACY_TAB_GROUP_CLASS` genuinely had another consumer but
`LEGACY_TAB_LIST_CLASS` did not — same import block, opposite answers.

When you delete a symbol whose doc comment other comments defer to ("`h-auto`
for the same reason as the legacy strip"), move the reasoning to the surviving
site instead of letting the reference dangle.

For a flag that gates a whole directory, `pnpm knip` (woodrow, entries
`app/root.tsx` + `app/routes.ts`) is more reliable than eyeballing the import
graph. `pnpm run delete-feature` exists for whole-feature removals.

## Step 6 — Expect test fixtures to be load-bearing

Long-lived flags get used as convenient example keys throughout test suites, and
that is usually the largest part of the diff. Before estimating, count:

```sh
grep -rc "<key>" app apps --include='*.test.ts' --include='*.test.tsx' --include='*.spec.ts' | grep -v ':0$'
```

Traps seen in practice:

- **`ff_new_dashboards` was the only `default: true` flag in woodrow's
  registry**, and a hook test used it to assert the frontend-default fallback.
  Deleting it leaves that assertion no substitute — rewrite the test to derive
  the expectation from the registry rather than hardcoding a key.
- **`ff_feedback` / `ff_new_dashboards` are baked into the management suite's
  mock helper as named parameters** (`feedbackDefaultValue`, `dashboardsHiddenAt`,
  …). Removing them from the registry also flips `isRegistryBacked` /
  `canRemoveConfiguration`, changing page behaviour in those tests. That is a
  wide rename, not a registry edit — give it its own PR.
- **A flag can be the fixture for "registry-only, no DB row"**. Swap it to
  another registry-only flag that is staying (`ff_view_tabs` worked), not to one
  you are also deleting.
- Watch for **search-term strings** that matched the old flag's *label*. Renaming
  the fixture key without the search term leaves a test that finds nothing.
- api's `scripts/__tests__/generate-server-consumed-flags.test.ts` has an
  `it.each` list asserting the scanner finds each key **in the real repo**.
  Remove the retired key from that list or the test fails on a missing key. Its
  synthetic `scanSources` fixtures are inline source strings and need no change.

### Two PRs that merge cleanly can still break each other

**A clean merge is not a passing merge, and this is the single easiest way to
ship a red `main` from this kind of cleanup.** Splitting by blast radius (below)
means several PRs edit the same test file. Git merges by *hunk*, so two PRs that
touch different regions merge with zero conflicts — while one PR renames the
fixture the other's new tests are keyed to.

Real case: a fixture-rename PR renamed `ff_new_dashboards` → `ff_data_studio`
(label `"New Dashboards"` → `"Data Studio"`) throughout
`FeatureFlagsPage.test.tsx`. A sibling PR added four tests calling
`getRowFor("New Dashboards")` ~800 lines away. Both PRs green. Merge: no
conflict, no warning, and four tests looking up a fixture that no longer exists.

Check every pair that touches a shared test file, and check by *running*, not by
reading:

```sh
git merge --no-commit --no-ff <other-branch>
git diff --name-only --diff-filter=U          # often empty — that is the trap
pnpm exec vitest run <the shared test dir>    # this is the real check
git merge --abort
```

If a pair fails, **stack them** rather than writing a merge-order note: base the
renaming PR on the other so its rename covers the new tests, verify the combined
state green, and set the PR base on the host (`gh pr edit <n> --base <branch>`)
so the ordering is structural instead of tribal knowledge in a description.

Note which direction to stack: put the **wide mechanical rename on top**, since
it is the change that can absorb the other's new call sites. Stacking the
feature PR on the rename instead leaves the same orphans.

### Adjacent registry entries conflict, and both stock resolutions are wrong

Splitting by flag means several PRs each delete a different entry from the same
`FEATURE_FLAGS` object. When two deleted entries are **adjacent**, they share the
`default: false,\n},` lines between them, so git cannot merge the two deletions —
whichever lands second conflicts. The conflict is shaped so each side looks like
it is *keeping the other's* flag:

```
<<<<<<< HEAD
  ff_ag_entities_table: {
    ...
=======
  ff_upload_center: {
    ...
>>>>>>> other-branch
    default: false,
  },
```

`--ours` and `--theirs` both leave one live registry entry for a flag whose code
is gone — a clean-looking resolution that silently defeats the cleanup. **The
correct resolution is to delete both entries**, then regenerate the manifest.

Say this in the PR descriptions, with the exact resolution, because the person
merging is usually not the person who split the work.

### Prove the whole set composes before calling it done

Individually-green PRs are not a green `main`. Merge them all onto `main` in a
throwaway worktree and run the real checks:

```sh
git worktree add -f -b _integration_check /tmp/integ origin/main
cd /tmp/integ
for B in <branches in merge order>; do git merge --no-ff -m "merge $B" "$B"; done
# resolve: regenerate manifests; delete BOTH sides of an adjacent-entry conflict
pnpm run typecheck && pnpm exec vitest run <the flag test dirs>
grep -c '^<<<<<<<' app/lib/feature-flags.ts   # must be 0
cd - && git worktree remove --force /tmp/integ && git branch -D _integration_check
```

Do **not** resolve such a run with a blind `git add -u` — that stages conflict
markers, and the tree then "passes" merging while being syntactically broken.
Grep for markers explicitly before trusting any result.

This integration pass is what catches the adjacent-entry conflict, the shared
fixture rename, and any registry entry a per-flag PR forgot.

### Check worktree base drift before stacking anything

Superset workspaces created at different times branch from different `main`
commits, and a stale `origin/main` in the root checkout hides it. Rebasing one
onto another then replays *main's* commits as if they were yours — a 1-commit
rebase reports `Rebasing (1/12)` and the PR diff grows by a dozen unrelated
files.

Always `git fetch origin` first, then read both numbers:

```sh
for B in <branches>; do
  printf '%-40s ahead=%s behind=%s\n' "$B" \
    "$(git rev-list --count origin/main..$B)" "$(git rev-list --count $B..origin/main)"
done
```

Rebase every branch involved onto current `origin/main` **before** stacking, so
the stack shares one base. The same drift also inflates a stacked PR's file list
without touching its content: GitHub diffs against the base *branch*, so if the
base is behind, everything `main` gained in between shows up as the stacked PR's
diff. `git diff origin/main...<branch>` is the honest number — if that is clean
and the PR page is not, rebase the base branch, don't go hunting the files.

When rebuilding a branch that has a merge commit in it, `rebase --onto` will try
to replay whatever came in through the merge. Cherry-pick the branch's own
commits onto the new base instead, and prove the result:

```sh
git diff <backup-ref> HEAD    # must be empty — same tree, new history
```

## Step 7 — Regenerate both manifests and commit them

```sh
cd /Users/jake/code/api     && pnpm run generate:server-consumed-flags
cd /Users/jake/code/woodrow && pnpm run client-flags:generate
```

api's pre-commit hook runs the generator and stages it, but **the manifest only
rewrites when the key set changes** — which a flag deletion does, so it must be
in the commit or `pnpm run lint:invariants` fails CI.

## Step 8 — Verify

```sh
# api: do not run `pnpm run typecheck`/`build` — a Stop hook does it. Just stop and read.
cd /Users/jake/code/api && pnpm run lint:invariants

cd /Users/jake/code/woodrow && pnpm run typecheck
pnpm exec vitest run <touched test files>

# every deleted key gone; every kept key still present
grep -rn "<deleted key>" app apps src scripts
grep -rn "<kept key>" app apps        # must still return its sites
```

**Check the registry separately from the call sites.** Deleting a gate and
deleting the `FEATURE_FLAGS` entry are two edits, and finishing the interesting
one makes it easy to forget the boring one. A flag whose gate is gone but whose
registry entry survives is dead config that still shows as registry-backed in the
admin portal:

```sh
for K in <every deleted key>; do
  git grep -q "$K" <branch> -- app/lib/feature-flags.ts \
    && echo "$K STILL IN REGISTRY"
done
```

The generated manifest will **not** catch this — it indexes *call sites*, so a
flag with no consumers is correctly absent from it while the registry entry sits
there untouched. In the CON-3722 run `ff_condensed_header` was exactly this:
~210 lines of legacy header deleted, manifest clean, registry entry still
present.

Two zsh traps in these verification loops specifically:

- `git show "$B:app/lib/..."` **silently mangles the path** — zsh reads `$B:a`
  as its `:a` (absolute-path) modifier and eats the `a`, so you get
  `.../branchnamepp/lib/...` and a `fatal: ambiguous argument` for every
  iteration. Use `git grep <pat> "$B" -- <path>` instead, which takes the
  revision as its own argument.
- Quote every `--include` glob (`--include='*.ts'`), and expand file lists as
  `"${arr[@]}"` — zsh does not word-split unquoted parameters.

Note that `app/woodrow/entity.tsx` and the tab-strip styles have **no unit test
coverage at all**, so for changes there typecheck plus manual QA is the only
safety net. Say so in the PR rather than implying tests cover it.

## Keeping a flag deliberately

Callers often want one archived flag kept (this skill was written for a run that
kept `ff_cross_navigator`). Two things to get right:

1. **Exclude it everywhere**, including from the greps you use to confirm
   cleanliness — a kept key *should* still return sites.
2. **Check whether the kept flag depends on a branch you are deleting.** Keeping
   `ff_cross_navigator` while deleting `ff_condensed_header` only worked because
   all three cross-navigator sites live inside condensed-header-only artifacts,
   and the surviving branch is the condensed one. Had the legacy branch survived,
   the kept flag would have had no reachable UI. Trace the kept flag's sites into
   the branch you are keeping before you delete the other.

## PR shape

One PR per repo minimum, and split further by blast radius rather than lumping:

- A registry-only flag with no gating code is a one-line change.
- A flag whose gate wraps a large legacy branch is its own PR.
- A flag that is a test fixture is a test-refactor PR.
- Flags with an existing groomed ticket (e.g. CON-3682 for `ff_ag_entities_table`
  + ConfigurableTable) belong to that ticket, not a new one.

Commit once per flag, and state in the message which branch survived and why —
especially for a non-positive gate, where "kept the true branch" is the reviewable
claim.

Splitting is the right call, but it creates work the split itself hides. Before
calling the set done:

- **Trial-merge and run every pair that touches a shared test file** (see "Two
  PRs that merge cleanly can still break each other"). Skipping this is how a
  set of individually-green PRs reds `main`.
- **The generated manifests conflict on every merge after the first**, because
  each branch regenerated them from its own key set. Resolution is always
  *regenerate* (`pnpm run client-flags:generate` /
  `pnpm run generate:server-consumed-flags`), never hand-merge or trust a
  `rerere` auto-resolution — and `git rerere` will silently reuse a stale one, so
  re-run the generator and re-`git add` even when the file no longer shows
  markers.
- **A stacked PR needs a rebase, not a merge, once its base squash-merges.**
  Squashing rewrites the base's commits, so merging `main` in afterwards replays
  them against their own squashed form. Say this in the stacked PR's description
  — the person merging is usually not the person who built the stack.
- **Never force-push a branch that is the BASE of another open PR.** This is the
  most expensive mistake available here, and `--force-with-lease` does not stop
  it: the lease only guards against pushes you have not *seen*, and a `git fetch`
  makes the dangerous one "seen". If the stacked PR merged into its base branch
  and you then rebase-and-force-push that base, you revert the merge — and the
  stacked PR goes on showing **MERGED** while its changes reach no branch at all.
  Nothing warns you; the only tell is the push output naming an old SHA you do
  not recognize. Check first, every time:

  ```sh
  gh pr list --repo <owner/repo> --state all --base "$BRANCH" \
    --json number,state,mergedAt      # anything merged here?
  git ls-remote --heads origin "$BRANCH"
  ```

  If something merged in, cherry-pick that PR's own commits onto the rebased tip
  and prove it with `git diff <its-merge-commit> HEAD` (only the new base's
  content should differ). Recovering costs a fresh re-land PR.

  Corollary: when someone has already merged `main` into a PR branch (GitHub's
  *Update branch* button, leaving a `Merge branch 'main' into …` commit), **adopt
  their merge** instead of force-pushing a tidier rebase over it. Verify their
  resolution and move on — a linear history is not worth a second overwrite of
  someone else's work.
- **Stacking costs you CI, silently.** In woodrow, `unit-tests.yml` and the e2e
  workflow trigger on `pull_request: branches: [main]`. A PR based on another
  *branch* therefore runs **no** unit tests, lint, or typecheck — the only check
  that appears is `label`, and it passes. The check list looks green because it
  is nearly empty, which is indistinguishable at a glance from a green suite.
  So when you stack, verify locally (typecheck + the affected suites) and say in
  the PR body that CI's suite did not run and why. It starts running once the
  base merges and the PR retargets `main`, so that is the moment to re-check.
  Confirm the trigger before assuming it applies elsewhere:

  ```sh
  sed -n '1,8p' .github/workflows/unit-tests.yml   # look for `branches: [main]`
  ```

Search Linear before filing: retirement tickets often already exist, sometimes
blocked on exactly the rollout that just completed.
