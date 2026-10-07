---
name: datadog-add-log-facets
description: Create Datadog Logs facets (string or measure) from a list of attribute paths by driving the Logs Explorer in Claude in Chrome, leaving every facet's display name empty so the label defaults to the path. Use when asked to "add facets", "create the Datadog facets", "make the @ai.* facets", or when a PR promotes log fields into a new namespace and the cut-over checklist says facets must exist before deploy. Needs a browser already signed in to Datadog; there is no public API for creating log facets.
---

# Datadog: add log facets

Facets are not in code and have no public API, so this skill creates them in the
Datadog UI through Claude in Chrome. A missing facet silently empties
log-based metrics and group-by monitors, which is why this step sits before a
log-field rename deploys.

## Hard rules

- **Never type into the display-name / label field.** Leave it empty so the
  facet is labelled with its path (`@ai.span_kind`). No descriptions, no groups,
  unless the user asked for them in this request.
- **Never enter credentials.** If Chrome lands on a Datadog login page (or SSO
  prompt), stop and tell the user to sign in in that tab, then continue.
- **Only create what was asked.** The input is an explicit list of paths and
  types. Don't add "while we're here" facets.
- **Don't delete or edit existing facets.** If one already exists with the
  right type, skip it. If it exists with the wrong type (string vs measure),
  stop and report it; changing the type is the user's call.

## Input

A list of `path → type`, where type is `string` (default) or `measure`
(numeric; you will also pick a unit of `none` unless told otherwise). Paths are
written with the leading `@` as in a search query: `@ai.skill.id`,
`@rollup.occurrences`.

Also resolve the Datadog site: the org's own site (check `DD_SITE` in the repo's
`.env`, or ask). Use `https://app.<site>/logs`; never assume `app.datadoghq.com`.

## Procedure

1. **Load tools** in one ToolSearch call (see the `claude-in-chrome` skill):
   `tabs_context_mcp`, `navigate`, `computer`, `read_page`, `find`,
   `form_input`, `tabs_create_mcp`, `tabs_close_mcp`. Call
   `tabs_context_mcp` first, then open **your own new tab**; close it when done.
2. **Open Logs Explorer** on a query that returns logs carrying the new
   fields, so the attributes appear in the side panel and autofill:
   `https://app.<site>/logs?query=<url-encoded query>&from_ts=now-1h`
   (e.g. `env:stage @logger:ai-span`). Take a screenshot. Login page → stop.
3. **Check what already exists** before creating anything: use the facet panel's
   filter box (left rail, "Filter facets") for each path's top-level namespace
   (`ai`, `rollup`) and note which paths already appear and with what type.
4. **Create each missing facet.** Two routes; prefer A.
   - **A. From a log (autofills path and type).** Click a log row to open its
     side panel, find the attribute under the log's attributes (expand the
     `ai` / `rollup` object), click the attribute value or its `⋮`/hover menu,
     and choose **Create facet for @<path>**.
   - **B. By path (no log has the field yet, e.g. `@ai.skill.id`,
     `@rollup.occurrences`).** At the bottom of the facet panel click
     **+ Add** (or the panel's "Add a facet" action), and enter the path in the
     field/path input.
   In the dialog:
   - Confirm the path is exactly the requested one, with the `@`.
   - Set **Type**: String, or Measure (Number) with unit `none`.
   - **Leave Display name / Label empty.** Don't touch Group or Description.
   - Click **Add**.
5. **Verify each facet** — don't trust the dialog closing. Reload the explorer
   and either (a) find the path in the facet panel, or (b) run
   `@<path>:*` in the search bar and confirm the facet autocompletes.
   For a measure, confirm it appears under measures, not as a string facet.
6. **Close your tab** and report per path: `created`, `already existed (ok)`,
   `wrong type (left alone)`, or `failed (why)`.

## Gotchas

- The explorer's time range matters for route A: the attribute only shows in the
  side panel if a log in range carries it. Widen to 4h or run the producing
  action again (e.g. dispatch the stage canary) rather than guessing.
- Facets are per Datadog org, not per environment or index, so creating them off
  a stage log covers prod.
- A measure facet needs the value to be numeric in the log; if the attribute is
  a string, route A offers only a string facet. Create it by path (route B) as a
  measure instead.
- Don't use browser alerts/dialog-triggering controls; if the page stops
  responding, stop and ask.
- This skill has not been run end to end against the live UI yet. The labels
  above ("Create facet for…", "+ Add", "Display name") are from memory of
  Datadog's UI; if a step doesn't match, take a screenshot, adapt, and update
  this file with what you saw.

## Example input (CON-3995 cut-over)

```
@ai.span_kind          string
@ai.span_name          string
@ai.feature            string
@ai.operation          string
@ai.skill.id           string
@ai.outcome            string
@ai.reasoning_effort   string
@rollup.occurrences    measure
```
