---
name: rum-review
description: Query Datadog RUM data, synthesize findings into categorized issues, and interactively file them as Linear tickets. Use when investigating user experience problems or doing a periodic RUM audit.
---

# RUM Review Skill

Use this skill when asked to review Datadog RUM data for UX insights and file actionable findings as Linear issues.


**Personal workspace (`jnelken`, team Dev, DEV-\*)?** Don't create the issue here — use [[jnelken-linear]] (`jlin.py new`), which refuses to file one without its `repo/*` label.

## Invocation

```
/rum-review          # defaults to 30d window
/rum-review 7d
/rum-review 30d
```

If a `--team=` argument is provided (e.g. `--team=CON`), use it as the Linear team identifier when creating issues. Otherwise ask the user which team to file under after findings are presented.

---

## Step 1 — Query RUM in Parallel

Use the `datadog-api-claude-plugin:rum` agent. Default time window: **last 30 days** unless the user specifies otherwise.

Run all of the following queries in a single agent invocation:

1. **Views** — unique view names with counts (all distinct route patterns seen in the window)
2. **Custom actions** — unique action names with counts
3. **Errors** — top 15 most frequent errors (JS errors + network errors), with counts and representative messages
4. **Sessions** — total unique session count for the window
5. **Rage clicks** — all rage click events with: target element description, view name, count

---

## Step 2 — Synthesize Findings

Analyze the query results and produce a numbered list of findings. Categorize each finding:

- **[ERROR]** — A recurring error worth fixing. Include if: >50 total occurrences, OR the error message suggests a user-visible failure (permission denied, entity not found, failed to fetch). Exclude browser extension fingerprints (e.g. `Object Not Found Matching Id:N, MethodName:update`) and mark them as noise.
- **[FRUSTRATION]** — Rage click or dead click signal. Group by surface (e.g. "Checklists search fields") when multiple targets share the same underlying cause.
- **[INSTRUMENTATION GAP]** — A meaningful product flow with no custom RUM actions. Identify by comparing the known product areas (inferred from view names) against the custom actions list. Upload flows, creation flows, and core navigation with zero custom actions are good candidates.
- **[PERF]** — A performance outlier if Core Web Vitals data is available (LCP >3s or CLS >0.1 on a specific route).
- **[NOISE]** — Items that inflate error counts but are not app bugs (browser extensions, stage-env URLs leaking into production RUM, known infrastructure flakiness). Call these out explicitly so they can be filtered — but do NOT file them as issues.

For each finding (except NOISE), include:
- Category badge: `[ERROR]`, `[FRUSTRATION]`, `[INSTRUMENTATION GAP]`, or `[PERF]`
- **Summary**: 1-2 sentences describing the finding in plain language
- **Data**: the specific counts, view names, or error messages backing it
- **Suggested issue title**: concise, actionable (e.g. "Silence Comments:read 403 for users without Comments scope")
- **Suggested priority**: Urgent / High / Medium / Low, with a one-line rationale

Format example:

```
1. [ERROR] Missing Comments scope not handled gracefully
   727 occurrences of "Missing scope: Comments:read" across two error paths. Users
   without Comments:read see unhandled exceptions when visiting checklist or document views.
   → "Gate /comments/unresolved call on Comments:read capability"   Priority: High
   Rationale: Affects every user without Comments access; error is user-visible.

2. [FRUSTRATION] Checklist search fields unresponsive
   50 of 87 total rage clicks (57%) hit search inputs on /checklists — three distinct
   fields: "Search…", "Search classifications…", "Search variables…".
   → "Investigate checklist popover focus and open latency"   Priority: High
   Rationale: Majority of all recorded frustration signals in the app.
```

---

## Step 3 — Interactive Review

After presenting all findings, ask:

> **Which findings should I file as Linear issues?**
> Enter numbers (e.g. `1 3 4`), `all`, or `none`.

Wait for the user's response before proceeding.

---

## Step 4 — Create Linear Issues

For each approved finding:

1. Use `mcp__claude_ai_Linear__list_teams` to resolve the team if not provided via argument.
2. Use `mcp__claude_ai_Linear__save_issue` to create the issue with:
   - **title**: the suggested issue title (or a refined version if the user indicated changes)
   - **description** (markdown):
     ```
     ## Finding
     [1-2 sentence summary from Step 2]

     ## RUM Data
     [Specific counts, view names, error messages, time window]

     ## Investigation Steps
     [2-4 concrete next steps tailored to the finding type — see guidance below]

     ---
     *Filed via /rum-review — [date] — [time window] window*
     ```
   - **priority**: map Urgent→1, High→2, Medium→3, Low→4
   - **teamId**: resolved from list_teams

3. After each issue is created, output its URL.

### Investigation step guidance by category

**[ERROR]**: Check where the error is caught; look for a missing capability guard before the API call; verify whether a `beforeSend` filter could suppress known-noise variants.

**[FRUSTRATION]**: Check whether the popover/input auto-focuses on open; measure popover open latency; confirm the trigger element has a visible loading or active state.

**[INSTRUMENTATION GAP]**: Identify the top-level component for the flow; add `datadogRum.addAction(name, context)` calls at initiation and completion; check if errors during the flow are already caught or need a custom error action.

**[PERF]**: Identify the largest asset or slowest API call on the route; check for render-blocking requests; look for unnecessary waterfall fetches that could be parallelized.

---

## Maintenance Note

After each run of this skill, update the "Last Run" section below.

### Last Run

_(not yet run)_
