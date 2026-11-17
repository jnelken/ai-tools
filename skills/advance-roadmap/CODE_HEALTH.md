# Code health — rubric and retrospective

Two things share this file because they share one definition of "unhealthy":

- **The rubric** — what counts as a finding and how bad it is.
- **The retrospective** — after every clean ship, the worker looks at the repo it just worked in and
  records structured observations in the hygiene log. It reports; it never fixes.

There is no code-health pass any more. advance-roadmap used to dispatch a behavior-preserving
refactor when it found nothing to ship; that fallback is retired, and a run with nothing actionable
reports that and stops (`../conductor/ORCHESTRATOR.md` Step 2d).

The principles are not news to any model. This file exists because three different providers run
this unattended, and they need the **same thresholds, the same severity order, and the same
definition of done**. Stable categories and evidence make observations comparable across runs.

**Repo conventions win.** A repo's `CLAUDE.md` / `AGENTS.md` and the parent
`$CODE_DIR/CLAUDE.md` (e.g. its "React component over 150 lines → split it" rule) override every
threshold below. Where they say nothing, use these.

## The rubric

Measure source you own. Exclude generated output, lockfiles, vendored code, fixtures, snapshot
files, migrations, and data/content files (a 2,000-line word list is not a design problem).

| Sev | Finding | Threshold / signal |
|---|---|---|
| **S1** | Broken baseline | Failing tests, build or type errors, or lint **errors** on `origin/main`. |
| **S2** | Circular imports | Any cycle between modules you own. |
| **S2** | Type escape hatches | `as any`, `: any` on a boundary, `@ts-ignore`, `@ts-expect-error` / `eslint-disable` with no stated reason. |
| **S2** | Stale or lying types | A type that no longer matches what the code returns or stores; a hand-written type duplicating one the source of truth (schema, API client, DB) can generate. |
| **S2** | Diverged duplicates | The same concept defined twice (constant, type, helper, validation rule) where the copies already disagree. |
| **S3** | Oversized files | Over **1,000 lines** always. Over **400** when the file mixes unrelated responsibilities. |
| **S3** | Oversized units | A function over ~80 lines, or a component over the repo's limit (150 in this repo family). |
| **S3** | DRY violations | Three or more copies of the same logic, or two copies of anything over ~15 lines. |
| **S3** | Untested logic | A utility/module with real branching logic and no test file — the parent `CLAUDE.md` asks for tests on every utility in its own file. |
| **S3** | Dead code | Unused exports, files nothing imports, flags that are always on. |
| **S4** | No co-location | Files that change together living far apart (a component, its hook, its test and its styles in four top-level folders). |
| **S4** | Unnavigable tree | A directory of 15+ files mixing concerns; misleading names; a `utils/` or `helpers/` junk drawer; the same kind of file placed by three different conventions. |
| **S4** | Poor composability | Prop/arg lists that grow a boolean per caller; if/else on "which caller am I"; logic welded to a UI component that should be a hook or pure function. |

**Severity sets the order of work.** S1 always comes first: a red baseline
blocks every future ship in that repo. Within a severity, prefer the finding in code that changes
most often (`git log --format= --name-only -50 | sort | uniq -c | sort -rn`) — cleaning code nobody
touches buys little.

**Cheap ways to look** (read-only, never commit their output):

- size: `git ls-files | grep -E '\.(ts|tsx|js|jsx|vue|py|swift|go|rs)$' | xargs wc -l | sort -rn | head -20`
- escape hatches: `git grep -nE 'as any|: any\b|@ts-ignore|@ts-expect-error|eslint-disable' -- ':!*.d.ts'`
- cycles (JS/TS): the repo's own `import/no-cycle` lint rule or `madge` if it has one; otherwise
  `npx -y madge --circular --extensions ts,tsx,js,jsx,vue <src>` (a cache download, not a repo install)
- baseline: the repo's real `test`, `build`, `lint` / `typecheck` scripts — Step 5 already ran them

## The retrospective (worker, after a clean ship)

Run it after WORKER.md Step 7b's close-out, only when the outcome is `shipped` — never let it delay
or block a ship. Budget: a few minutes,
read-only on the code.

1. **Look at two scopes.** The area this run touched (you just read it closely — that's where your
   judgment is best), plus the repo-wide cheap checks above. Don't audit the whole repo by hand.
2. **Record the observations as data.** For each substantive finding, append one structured
   record (no secrets or env values):

   ```sh
   ~/.ai-tools/bin/hygiene-log --repo <dir> --category "<rubric row>" \
     --finding "<concise finding>" --evidence "<paths, counts, measured error>" \
     --disposition deferred --reason "<why not addressed>" --run <stamp> --pr <url>
   ```

   The log is JSONL under the personal memory directory, with timestamp, repo, category,
   evidence, disposition/reason, PR/commit, run and a stable finding fingerprint. Log fixed and
   dismissed observations too when useful; commit/PR history holds the implementation detail.
3. **Report briefly.** Mention important findings and the log location in the worker summary.
   Do not create Linear hygiene tickets or retroactively ticket the work just shipped. Only
   Jake's explicit request authorizes a hygiene ticket. Concrete product/bug work being planned
   for a later branch may still become a future-work ticket under the shared DEV policy.

If logging fails, include the observation in the run summary and continue; never block a ship.
