# Monthly Retrospective Skill

Use this skill when asked to generate a monthly retrospective for a single team member.

## Source of truth

- `/Users/jake/code/woodrow` — frontend repo
- `/Users/jake/code/api` — backend repo
- `fp retro:commits` — commit + line-stat fetch command

## Standard run command

```bash
fp retro:commits --repo /Users/jake/code/woodrow --author "Jake Nelken" --since "YYYY-MM-01" --until "YYYY-MM-31"
```

The script outputs TSV sorted by lines changed descending:
```
lines_changed  repo  date  subject
```

## Required behavior

1. Run the script for the requested author and month.
2. **Group commits semantically** into named themes — one theme per shipped feature, fix cluster, or ops improvement. A theme is a natural unit of work (e.g. "DataGrid migration", "E2E test suite", "Dashboard tweaks"). Commits that clearly belong together (same ticket prefix, same subsystem, consecutive dates) should be merged into one theme.
3. **Calculate effort per theme**: sum `lines_changed` across all commits in the theme. Compute each theme's share of the total month lines as a percentage.
4. **Sort themes by effort descending** — highest line count first. This is the primary sort; no category grouping.
5. For each theme, write one nested sub-bullet **from a stakeholder's perspective**. The reader may not be technical — they see "Dialog → Modal migration" and have no idea why it consumed 12% of the month. The description must answer: *what problem did this solve for users or the team, why did it take the time it did, and what does it unlock going forward?* Do not describe implementation mechanics unless they directly explain the business impact.
6. Use "What Went Well", "Not So Well", and "What Could Have Gone Better" as the three top-level sections. Within each section, themes are sorted by effort descending.
   - **What Went Well**: features, refactors, infra/ops, tooling — anything net-positive.
   - **Not So Well**: bugs, regressions, hotfixes, flag reversals.
   - **What Could Have Gone Better**: reflective paragraphs, not bullets (see format below).
7. Omit merge commits, lockfile bumps, and env-only changes from theme grouping (they will naturally have near-zero line counts and should be absorbed into adjacent themes or dropped).

## Writing the sub-bullet (stakeholder voice)

Each sub-bullet should read like an answer to "why did this matter?" — not "what was done." Examples of the contrast:

| Too technical | Stakeholder-readable |
|---|---|
| Migrated all legacy `<Dialog>` usages to the new `Modal` primitive and added a lint boundary | Every popup and confirmation box in the app had been built on an old component that couldn't support keyboard trapping, scroll-locking, or consistent sizing. This work replaced all of them with a single system, so future UI work doesn't have to carry that debt — and users get more consistent, accessible modals everywhere. |
| Extracted Playwright into a standalone `e2e` package with co-located specs | Before this, automated browser tests were fragile, hard to run locally, and not connected to CI. Now tests live next to the features they cover, run reliably on every PR, and alert on failures in Slack — giving the team earlier warning when something breaks. |

Keep descriptions to 2–4 sentences. Avoid jargon. Prefer concrete outcomes over process details.

## Output delivery rule

- **Write the retrospective to a file**: `docs/retros/YYYY-MM-[author-slug].md` (e.g. `docs/retros/2026-03-jake.md`).
- After writing, tell the user the file path so they can open it.
- Do not paste the full retrospective content into the chat.

## Output format (written to file)

```markdown
# [Month] [Year] Retrospective — [Name]

---

## What Went Well

- **[Theme title]** · ~[N]% of month
  - [Stakeholder-readable description: what problem it solved, why it took the time it did, what it unlocks.]

- **[Theme title]** · ~[N]%
  - [Description.]

...

---

## Not So Well

- **[Bug / regression title]** · ~[N]%
  - [What broke, the user impact, and what process gap it reveals — in plain language.]

...

---

## What Could Have Gone Better

[3–5 paragraphs. Bold the theme of each paragraph. Cite specific features or bug clusters by name.
Look for: repeated follow-up fix commits on the same feature, flag reversals, hotfixes that arrived
shortly after a feature merged, boundary validation gaps, or the same subsystem breaking multiple times.
End with a forward-looking observation about what infrastructure or habits are now in place to do better.]
```

## Effort display rule

- Show percentages rounded to the nearest whole number.
- Themes under ~1% of total lines can be grouped as a single "Miscellaneous polish" bullet at the bottom of their section.
- Do not show raw line counts in the output — percentages only.

## Date display rule

- Use full month name in the header (e.g. `March 2026`).
