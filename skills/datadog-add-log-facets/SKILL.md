---
name: datadog-add-log-facets
description: Create Datadog Logs facets (string or measure) from a list of attribute paths by driving the Logs Explorer in a Superset workspace browser pane (or Claude in Chrome), leaving every facet's display name empty so the label defaults to the path. Use when asked to "add facets", "create the Datadog facets", "make the @ai.* facets", or when a PR promotes log fields into a new namespace and its cut-over checklist says facets must exist before deploy. There is no public API for creating log facets.
---

# Datadog: add log facets

Facets are not in code and have no public API, so this skill creates them in the
Datadog UI. A missing facet silently empties log-based metrics and group-by
monitors, which is why this step sits before a log-field rename deploys.

## Hard rules

- **Never type into the Display Name field.** In the Add facet dialog it is
  disabled and "Use path as display name" is checked by default: leave both
  alone, so the label is the path. No Group, no Description unless the user asked
  for them in this request (Datadog groups new facets by namespace on its own).
- **Never enter credentials.** If the pane lands on a login page, stop and tell
  the user to sign in there, then continue.
- **Only create what was asked.** Input is an explicit list of paths and types.
- **Don't delete or edit existing facets.** The dialog refuses duplicates
  ("A facet with path @x already exists."): report it as already existing and
  Cancel. A wrong type on an existing facet is the user's call.
- **Check before creating.** Another session or the user may have created some
  already; filter the facet panel first (step 3).

## Input

`path → type` per line, type `string` (default) or `measure`. Paths carry the
leading `@`: `@ai.skill.id`, `@rollup.occurrences`. Resolve the Datadog site
(`DD_SITE` in the repo `.env`, else ask) and use `https://<site>/logs`.

## Driver: Superset workspace browser pane (preferred)

The pane shares the user's signed-in Datadog session, and `superset browser`
exposes everything needed. Workspace id: `superset workspaces list --local -s
<slug> --json`.

```bash
superset browser open --workspace $W --show --target new-tab --url 'https://app.<site>/logs?query=...'
superset browser list --workspace $W            # pane id + current URL (the app redirects off app.<site>)
superset browser screenshot --workspace $W --pane $P --out shot.png   # then Read the PNG
superset browser eval --workspace $W --pane $P --code '<js expression>'   # read DOM, simple clicks
superset browser cdp --workspace $W --pane $P   # JSON {url: ws://...} for scripts/cdp.mjs
```

**Use real input for the form.** `eval` clicks and native-setter value writes are
flaky on Datadog's React select (the value reverts, the suggestion list never
mounts). `scripts/cdp.mjs` sends real events over the pane's CDP socket:

```bash
U=$(superset browser cdp --workspace $W --pane $P | python3 -c "import json,sys;print(json.load(sys.stdin)['url'])")
node scripts/cdp.mjs "$U" Input.insertText '{"text":"@ai.span_kind"}'        # type into the focused element
node scripts/cdp.mjs "$U" Input.dispatchMouseEvent '{"type":"mousePressed","x":300,"y":290,"button":"left","clickCount":1}'  # + mouseMoved before, mouseReleased after
```

Take a screenshot before every click: the pane can be resized by the user, so
coordinates are not stable; find elements with `eval` (`getBoundingClientRect`)
and click their centre.

Fallback driver: Claude in Chrome (`mcp__claude-in-chrome__*`, load per the
`claude-in-chrome` skill) in a tab you open yourself, closed when done.

## Procedure

1. **Open Logs Explorer** on a query whose logs carry the new fields (e.g.
   `env:stage @logger:ai-span`, range wide enough to include them). Screenshot.
   Login page → stop.
2. **Dismiss overlays** (e.g. a "Got It" popover) so they don't eat clicks.
3. **Check what exists.** Click the facet panel's "Search facets" input, type the
   namespace (`ai.`, `rollup`) with `Input.insertText`, screenshot. Each existing
   path shows as `namespace.field` under a group header. A measure facet expands
   to a Min/Max slider; a string facet to a value list. Skip existing ones.
4. **Create each missing facet.** Click **+ Add** beside "Showing N of M" in the
   panel. The **Add facet** dialog has tabs **Facet** and **Measure**, a **Path**
   select, and a collapsed **Advanced options** (Display Name, Type, Group,
   Description) you do not need to open.
   - Click the Path select, focus its combobox input (it renders outside the
     dialog element; id like `371`), `Input.insertText` the path.
   - A suggestion row **"New path: <path>"** (or an existing log attribute) appears
     under the input. Click it with a real mouse event. The select then shows the
     path.
   - **Facet tab** (string): nothing else to set. **Measure tab** (numeric): switch
     tab first. Its Path input is **prefilled with `@`**: select all (`cmd+a`)
     before typing, or you get `@@path`. Type defaults to **Double**; for counts
     (e.g. `@rollup.occurrences`) open Advanced options and pick **Integer**.
     Leave Unit at None unless told.
   - Optional **Group** (Advanced options) files the facet under a panel header.
     Typing a new name offers "New group: X"; an existing group matches
     case-insensitively and keeps its stored casing (`AI` → `Ai`).
   - If the dialog shows "already exists", **Cancel**. Otherwise click **Add**.
5. **Verify.** Re-filter the facet panel for the namespace and confirm each path
   is listed; for a measure, expand it and check for the Min/Max slider.
6. **Report** per path: `created`, `already existed`, `wrong type (left alone)`,
   or `failed (why)`. Close any tab you opened.

## Gotchas

- Facets are per Datadog org, not per environment or index: creating them off a
  stage log covers prod.
- Facet creation by path works with no matching logs in range ("New path:").
- Leftover open dialogs break later steps: always Cancel before moving on.
- **The Add facet dialog fades in over 3–6s, and input during the fade is lost
  silently** (the click lands on nothing, typed text goes nowhere, and a later
  click outside the still-small dialog closes it). Screenshot until the dialog is
  fully opaque before the first click. The first **+ Add** click right after a
  "successfully created" toast often doesn't open the dialog; screenshot-confirm
  rather than assume. With Claude in Chrome, keep `browser_batch` calls short:
  long batches with waits can time out mid-dialog. Confirm each create from the
  toast ("Facet @x has been successfully created").
- Don't trigger browser alert/confirm dialogs; if the pane stops responding, stop
  and ask.
- A concurrent human or session may be creating the same facets; "already exists"
  is the signal, not an error.

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
