---
name: monthly-email-update
description: Generate a monthly engineering update for non-technical internal employees from team commit history across woodrow and api. Use when asked for a monthly recap, internal newsletter-style update, or company-facing summary of what the team shipped, fixed, and improved behind the scenes.
---

# Monthly Email Update Skill

Use this skill when asked to generate a monthly team email update for internal, non-technical readers.

## Source of truth

- `/Users/jake/code/woodrow`
- `/Users/jake/code/api`
- `fp weekly:commits` (this repo's `scripts/weekly-slack-commits.sh`) for author discovery and commit collection across both repos

## Required behavior

1. Cover the full requested month across both repos.
2. Treat the audience as internal employees who do not need implementation detail.
3. Write in a team-first voice. Do not attribute work to individuals unless explicitly requested.
4. Include broad team work:
   - shipped product changes
   - reliability and correctness work, reported as improvements rather than as a defect list (see the section rules)
   - platform, data-integrity and permission work — always reported, in `Foundations`
   - delivery tooling and process changes only when they clearly improved speed, quality, or operational safety
5. Group commits into business-readable themes, not raw commit-by-commit bullets.
6. Keep the final draft concise: roughly 450-580 words, usually 7-9 narrative bullets total before the numbers, including 2-3 in `Foundations`. (The pre-2026-08 target of 380-450 predates the `Foundations` section and is no longer reachable without dropping real work.)
7. Add inline markdown links to the most representative 1-3 PRs for each theme when the landed work can be tied to merged PRs.
8. Avoid ticket IDs, repo names, internal module paths, and low-level implementation detail. Feature names, product-surface names, and named technologies (e.g. DataGrid, embeddings, vector search) are allowed and encouraged when paired with a brief plain-English gloss per the writing rules.

## Collection workflow

1. Pick the month window:
   - `since`: first day of the month at `00:00`
   - `until`: **actual** last day of the month at `23:59` — use the real DD (28/29/30/31). Do not template `YYYY-MM-31`; `git log --until` silently coerces out-of-range dates and will sweep extra hours from the next month into the window.
2. Discover authors across both repos for that window (April 2026 example):

```bash
fp weekly:commits --repo /Users/jake/code/woodrow --since "2026-04-01 00:00" --until "2026-04-30 23:59" --list-authors
```

   Drop `--list-authors` to print the grouped per-author commit listing for the same
   window; pass `--authors "A,B"` to restrict it to specific people.

   **Invoke through `fp`, not the script directly.** `bin/fp` owns the `--repo`
   entrypoint convention: it consumes `--repo`, resolves the repo root, and calls
   `scripts/weekly-slack-commits.sh` with `--woodrow` / `--api` built for it. The
   script itself therefore rejects `--repo` — that is correct, not a bug. Running
   `./scripts/weekly-slack-commits.sh --repo ...` fails with `Unknown option:
   --repo`; running `fp weekly:commits --repo ...` works. Either way both repos are
   read; `--woodrow` / `--api` only override non-default checkout paths.

3. Collect commits across both repos for the full team. Omit merge commits. Drop
   genuinely external bots (Dependabot, Renovate, `github-actions[bot]`) — the
   script only filters `github-actions[bot]` itself.

   **Do not drop Claude-authored work.** `Claude` / `claude[bot]` commits and
   `app/claude` PRs are AI-generated but human-directed, and they are a large and
   growing share of output (43 of 252 PRs and 46 of 295 commits in August 2026).
   Attribute them to a human and count them normally — see the attribution rules
   below.

   Merge split git identities before counting. One person can appear under several
   names/emails (e.g. `Tom` and `Tom Stilwell`), and a commit may carry a different
   person's email entirely. Run `git log --format='%an <%ae>' | sort -u` over the
   window and reconcile before reporting any per-author number.
4. Group related commits into named themes based on shared feature area, bug cluster, or operational improvement.
5. Resolve representative merged PRs for each theme. Most commit subjects carry a `(#NNN)` suffix from the squash-merge — use those as the inline link source. Sub-PRs merged into a trunk branch keep their individual commits and have **no** suffix (38% of August 2026's commits); for those, fall back to `gh pr list --state merged --search "<subject>"` in the relevant repo.

   Verify the repo before writing a link. A `(#NNN)` is only unique within one
   repo, and a feature area can move between repos month to month — check the
   commit's own repo rather than reusing last month's mapping.

6. Count merged PRs with `gh`, not by counting `(#NNN)` suffixes:

```bash
gh pr list --repo Concentro-Inc/<repo> --state merged --limit 500 \
  --search "merged:YYYY-MM-01..YYYY-MM-DD" --json number,author
```

   Suffix-counting undercounts every trunk-flow month and the error grows as the
   team uses trunks more, so month-over-month deltas computed that way are not
   comparable. (Updates before 2026-08 used suffix-counting: July 2026 published
   254 when `gh` reports 306.) If a number in this month's draft is not comparable
   to the previous month's published figure, say so in the terminal report — never
   silently propagate the old convention.
7. Translate each theme into plain-English employee-facing copy.

If the monthly window has very little activity, say so plainly and keep the update short rather than padding it.

## Attributing Claude-generated work

Claude opens PRs under the `app/claude` author and authors commits as `Claude` /
`claude[bot]`. That work is real and belongs to a human. Resolve the owner with this
cascade, stopping at the first rule that applies:

1. **Branch prefix.** A branch starting with a person's handle is theirs —
   `jake/...`, `jake-agent/...` → Jake; `tom/...` → Tom. This wins even when
   somebody else merged it.
2. **Merger.** Any other branch prefix (`fix/`, `claude/`, `ci/`, `con-...`, or no
   prefix) belongs to the human who merged it.
3. **Reviewer.** If the merge was programmatic (`mergedBy` is `app/claude` or a bot,
   i.e. auto-merge), fall back to the human who reviewed it.

```bash
gh pr list --repo Concentro-Inc/<repo> --state merged --limit 400 \
  --search "merged:YYYY-MM-01..YYYY-MM-DD author:app/claude" \
  --json number,headRefName,mergedBy
# then, only for programmatic merges:
gh pr view <n> --repo Concentro-Inc/<repo> --json reviews
```

Bot-authored **commits** map to a human the same way: match the commit's `(#NNN)`
suffix to its PR and inherit that PR's owner. Trunk sub-commits carry no suffix —
find the merge commit that introduced them (`git log --merges` over the window,
matched on ticket ID) and attribute via that PR.

A PR can merge in a later month than the commit landed in `main`; widen the `gh`
search window if a lookup comes back empty.

Because Claude-authored work is counted, the Numbers row is **`Commits landed`**,
not "Human commits landed" — the label would misdescribe what is being counted.

## Writing rules

- Use an executive-style internal update: clear, plainspoken, and grounded in both what shipped and why it matters.
- Focus on what changed for the company, the product, support burden, delivery speed, or reliability.
- Keep most bullets to one sentence. Use a second sentence only when it meaningfully improves clarity.
- Prefer concrete language:
  - good: `Improved account setup so new customers hit fewer edge-case failures`
  - bad: `Refactored signup validation and request plumbing`
- Name the feature, then teach it. When a bullet references a real product surface, technology, or concept, say what it is in one short plain-English clause so a non-technical reader — and eventually a client — learns something. Do not leave raw jargon standing alone, and do not replace the name with pure outcome language that hides what actually shipped.
  - good: `Rolled out DataGrid — a faster, more scannable table view — across dashboards so dense data is easier to compare at a glance`
  - bad (raw jargon): `Migrated dashboards to DataGrid and fixed chip overflow`
  - bad (too abstract): `Made tables easier to read`
- Assume the reader is smart but new. A one-line gloss ("X, which is Y") is almost always enough; do not lecture.
- Internal work belongs only when you can explain why it mattered:
  - good: `Made test coverage more reliable so issues are caught before they reach customers`
  - bad: `Migrated E2E specs and CI helpers`
- Do not overclaim roadmap certainty in the close.

## Output delivery rule

- Write the draft to `/Users/jake/code/folio-platform/docs/monthly-email-updates/YYYY-MM.md`. Use the absolute path — this convention lives in the folio-platform repo regardless of which working directory the skill is invoked from.
- After writing, tell the user the file path.
- Do not paste the full email into the chat unless asked.

## Output format

```markdown
# [Month] [Year] Team Update

[Two-sentence summary of the month in plain English.]

## What We Shipped

- **[Theme title]**: [1-2 sentence explanation of what changed and why it matters to internal readers, with inline PR links.]

## Foundations

- **[Project name]**: [1-2 sentences on what was standardized, hardened, or made reliable, and what that means for someone using Folio, with inline PR links.]

## Behind The Scenes

- **[Theme title]**: [1-2 sentence explanation of how this helped the team move faster, reduced risk, or improved quality, with inline PR links.]

## Numbers

- **Active developers**: [N]
- **Commits landed**: [N]
- **PRs merged**: [N]
- **Reliability improvements**: [N]
- **Avg commits per developer**: [N]

## MVPs

- **[Developer]** — MVP: [best stat label] ([N] or [short count phrase])
- **[Developer]** — MVP: [best stat label] ([N] or [short count phrase])

[One short closing paragraph on what the team is focused on next month.]
```

## Section rules

- Section order is fixed:
  1. `What We Shipped`
  2. `Foundations`
  3. `Behind The Scenes`
  4. `Numbers`
  5. `MVPs`
- Omit empty sections.
- `What We Shipped` is for visible product work and meaningful workflow improvements employees would recognize as progress.
- `Foundations` is for substantial engineering work with no demoable surface: data standardization and correctness projects, platform and job-system reliability, access-control and permission hardening, environment isolation, performance work. **Always include this section** — platform reliability matters increasingly to the company, and because this work has nothing to show on screen, a format organized around features drops it even in months where it was the single largest investment. Omit it only when the month genuinely contains none of it, never because it was hard to phrase.
- `Behind The Scenes` is narrower: delivery tooling, CI, testing infrastructure, and process changes that made the team faster or safer. Platform and data work belongs in `Foundations`, not here.

- **There is deliberately no "fixes" section.** A section organized around defects narrates the month as a list of mistakes and oversights, which misrepresents work that was mostly deliberate investment. This is a rule about *framing*, not omission — reliability work is still reported, in the section that fits:
  - A fix that gives users a capability or a safeguard they can notice → `What We Shipped`.
  - A fix that hardens the platform, corrects data, or closes a permission gap → `Foundations`.
  - Describe the resulting behavior, not the defect. Good: `Same-named files in different folders stay distinct.` Bad: `Fixed a bug where same-named files overwrote each other.`
  - Same rule in `Numbers`: the row is `Reliability improvements`, never "bugs fixed" or "defects resolved".

- Name the project, not the category. A `Foundations` bullet titled **Percentage standardization** reads as a piece of work with a beginning and an end; one titled "Platform and data integrity" reads as routine maintenance and buries the thing that actually happened.
- `Foundations` bullets follow the same writing rules as every other: name the system, then say in one plain clause what it means for someone using Folio. Never let it become a list of internal components. Good: `Some values were stored as "45" and others as "0.45", so the same figure could read differently depending on where you looked.` Bad: `Enforced the percentage decimal-fraction contract at extraction time.`
- `Numbers` should show 4-6 simple headline metrics. Prefer counts and averages that can be explained quickly.
- `Reliability improvements` is a heuristic count of business-readable reliability clusters, not every tiny patch commit. Count across the **full** commit set including AI-authored work — that work skews toward small correctness fixes, so counting only human-authored commits undercounts it badly (8 vs 12 for August 2026).
- `MVPs` should give each active developer one short, numeric stat they can credibly claim for the month. The stat does not need to use the same category for every person.
- **Derive both the count and the named areas from the full attributed set**, AI-authored work included. It is easy to update the number and forget the areas, because the areas usually come from reading that person's own-authored commits — but AI-authored work often concentrates in *different* areas than a person's hand-written commits (in August 2026 it was mostly interface correctness and CI automation, which appeared nowhere in the hand-written totals). A stat whose number counts 131 PRs while its areas describe only 101 of them is wrong in the part a reader actually reads.

## Style examples

| Too technical | Better |
|---|---|
| Extracted shared form schema validation and normalized API error mapping | Reduced the number of edge-case form failures and made validation behavior more consistent, which should mean fewer avoidable support issues and cleaner launches for new workflow changes. |
| Split Playwright coverage into package-level specs and CI jobs | Made automated checks more dependable, so the team gets earlier warning when important flows break before changes reach customers. |
| Migrated dashboards to DataGrid and fixed chip overflow | Rolled out DataGrid — a new table component that renders dense data faster and handles long tag lists cleanly — across several dashboards, so comparing records side by side is quicker and less cluttered. |
| Added backend groundwork for embeddings and vector search | Started building the search plumbing that lets AI features find the most relevant documents by meaning, not just keywords (called embeddings and vector search). It is early, but it is the foundation for smarter chat, retrieval, and file-context features later this year. |

## Defaults

- Audience: non-technical internal employees (internal-only — not for clients)
- Tone: executive status note
- Format: markdown email draft
- Attribution: team-first
- Links: inline PR links in bullets
- Metrics: simple headline counts and averages
- Include a short forward-looking closing paragraph
