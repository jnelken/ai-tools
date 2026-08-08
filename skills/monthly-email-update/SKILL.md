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
   - meaningful bug and reliability fixes
   - internal improvements only when they clearly improved speed, quality, or operational safety
5. Group commits into business-readable themes, not raw commit-by-commit bullets.
6. Keep the final draft concise: roughly 380-450 words, usually 4-6 narrative bullets total before the numbers.
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

3. Collect commits across both repos for the full team. Omit merge commits and any bot authors. The script only filters `github-actions[bot]` during author discovery — drop Dependabot, Renovate, or any other bot authors yourself at the grouping step.
4. Group related commits into named themes based on shared feature area, bug cluster, or operational improvement.
5. Resolve representative merged PRs for each theme. The Concentro repos use squash-merge, so each commit subject already carries a `(#NNN)` suffix — use those as the inline link source. For any commit missing the suffix, fall back to `gh pr list --state merged --search "<subject>"` in the relevant repo.
6. Translate each theme into plain-English employee-facing copy.

If the monthly window has very little activity, say so plainly and keep the update short rather than padding it.

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

## Stability And Fixes

- **[Theme title]**: [1-2 sentence explanation of what problem was reduced or resolved, with inline PR links.]

## Behind The Scenes Improvements

- **[Theme title]**: [1-2 sentence explanation of how this helped the team move faster, reduced risk, or improved quality, with inline PR links.]

## Numbers

- **Active developers**: [N]
- **Human commits landed**: [N]
- **PRs merged**: [N]
- **Bug-fix themes resolved**: [N]
- **Avg commits per developer**: [N]

## MVPs

- **[Developer]** — MVP: [best stat label] ([N] or [short count phrase])
- **[Developer]** — MVP: [best stat label] ([N] or [short count phrase])

[One short closing paragraph on what the team is focused on next month.]
```

## Section rules

- Section order is fixed:
  1. `What We Shipped`
  2. `Stability And Fixes`
  3. `Behind The Scenes Improvements`
  4. `Numbers`
  5. `MVPs`
- Omit empty sections.
- `What We Shipped` is for visible product work and meaningful workflow improvements employees would recognize as progress.
- `Stability And Fixes` is for regressions, reliability issues, support-heavy problems, and quality improvements tied to broken behavior.
- `Behind The Scenes Improvements` is for tooling, infrastructure, testing, platform, or process changes that made delivery safer or faster.
- `Numbers` should show 4-6 simple headline metrics. Prefer counts and averages that can be explained quickly.
- `Bug-fix themes resolved` is a heuristic count of business-readable fix clusters or fix PR clusters, not every tiny patch commit.
- `MVPs` should give each active developer one short, numeric stat they can credibly claim for the month. The stat does not need to use the same category for every person.

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
