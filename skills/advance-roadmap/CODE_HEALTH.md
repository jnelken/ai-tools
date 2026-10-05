# Code health — rubric, retrospective, and the code-health pass

Three things share this file because they share one definition of "unhealthy":

- **The rubric** — what counts as a finding, how bad it is, and when it's worth a ticket.
- **The retrospective** — after every clean ship, the worker looks at the repo it just worked in and
  files what it saw as `code-health` tickets. It reports; it never fixes.
- **The code-health pass** — when the orchestrator finds nothing else to ship, it dispatches one
  bounded, behavior-preserving refactor instead of stopping (ORCHESTRATOR.md Step 2d).

The principles are not news to any model. This file exists because three different providers run
this unattended, and they need the **same thresholds, the same severity order, and the same
definition of done** — otherwise the retrospective files a different list every run and the dedup
never converges.

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

**Severity sets the order of work, not whether to file.** S1 always comes first: a red baseline
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
or block a ship. It applies to feature runs and code-health runs alike. Budget: a few minutes,
read-only on the code.

1. **Look at two scopes.** The area this run touched (you just read it closely — that's where your
   judgment is best), plus the repo-wide cheap checks above. Don't audit the whole repo by hand.
2. **Dedupe against what's already filed.** List the repo's open `code-health` issues (the
   `repo/<dir>` label's own name is just `<dir>`):

   ```sh
   linear api '{ issues(filter:{ and:[ { labels:{ some:{ name:{ eq:"code-health" } } } },
     { labels:{ some:{ name:{ eq:"<dir>" } } } } ],
     state:{ type:{ nin:["completed","canceled"] } } }){ nodes{ identifier title description } } }'
   ```

   A finding about the same file and the same problem is a duplicate even if worded differently —
   skip it, or comment new evidence on the existing ticket if the problem got measurably worse (the
   900-line file is now 1,300).
3. **File at most 2 new tickets per run**, highest severity first. More than that is noise Jake has
   to triage; the next run will see what's left. File through `jnelken-linear` — never
   `linear issue create`, which skips the repo label:

   ```sh
   python3 ~/.claude/skills/jnelken-linear/jlin.py new --repo <dir> --label code-health \
     --priority <3 for S1, else 4> --title "Code health: <imperative fix>" --description-file <f>
   ```

   Body:

   ```markdown
   **Finding:** <rubric row> (S<n>)
   **Evidence:** <paths with line counts / grep hits / the failing command and its first error line>
   **Proposed change:** <the smallest behavior-preserving change that resolves it>
   **Acceptance:** <checkable: "no file in src/editor over 400 lines", "madge reports no cycles", tests/build/lint green>
   **Risk:** <what could break; which tests cover it, or which tests must be added first>

   _Filed by advance-roadmap's code-health retrospective._
   ```

   Never paste secrets or env values. Don't file style nits a formatter would settle.
4. **Report it.** Name each ticket filed (or "nothing new") in the worker `summary`. Leave it out of
   `slack_summary` unless it's S1.

If Linear is unreachable, list the findings in the `summary` and move on — never block a run on it.

## The code-health pass (worker, when the orchestrator dispatches `work_kind: code_health`)

This is a refactor run: **the product must behave exactly as it did before.** It follows WORKER.md
Steps 3–8 like any item, with these differences.

**What to work on.** If the request names a `code-health` ticket (`linear_id`), that ticket's
Proposed change and Acceptance are the scope. If not, audit the repo with the rubric, take the
single highest-ranked finding that fits the caps below, and file up to 2 of the rest per the
retrospective (that *is* this run's retrospective — don't run a second one).

**Establish the baseline first.** Run tests, build and lint on the fresh branch before changing
anything, and keep the output. If the baseline is red, fixing it *is* the finding — S1 outranks the
brief. If it's red for a reason you can't fix (missing env, external service), stop: there is no
way to prove a refactor preserved behavior. Report it as a Step 2b blocker on the ticket (or in the
summary for an ad-hoc pass).

**Caps.**

- **One finding, or one tight theme** (e.g. "dedupe the three date formatters"). Not a sweep.
- **About 400 changed lines**, counting a pure move (`git diff -M --stat`) as zero. Bigger than that
  means the finding needs splitting: do the first slice, and file or update a ticket for the rest.
- **Behavior-preserving only.** No changed UI, copy, URLs, public APIs, storage formats, env vars, or
  dependency majors. No new runtime dependencies.
- **Tests before restructuring.** Code with no tests gets characterization tests first — pin what it
  does today, then move it. If you can't write a meaningful test for it, don't restructure it.
- **Moves stay inside one feature area per run.** Co-locating `editor/` is fine; reorganizing the
  whole `src/` tree is a ticket for Jake to look at, not a run.
- **No new escape hatches** (`as any`, `@ts-ignore`) to make a refactor compile.

**Verification is stricter than a feature's.** Everything Step 5 requires, plus: no test removed or
weakened, lint warnings not increased, and the build output not meaningfully larger. Step 5b's
cross-model review applies unchanged; tell the reviewer in your commit messages that the change is
meant to be behavior-preserving, so it reviews for that.

**Commits.** `refactor:` / `test:` / `chore:` in the repo's convention, and every commit of the run
carries this trailer — it's how the orchestrator's per-repo cooldown sees this run in `git log`:

```
Advance-Roadmap: code-health
```

**Bookkeeping.** Skip Step 6's roadmap edits — a refactor isn't a roadmap item. A ticket-backed pass
closes its ticket in Step 7b's close-out like any other item. `slack_summary` says what got simpler,
in a sentence ("Split the 1,400-line editor store into three modules; no behavior change.").
