---
name: tab-triage
description: Flush every open Chrome tab through the chrome-tab-org extension, then triage the resulting JSON log — drop the throwaways, capture the keepers as a committed markdown doc, and file ClickUp tasks for the tabs that still need work. Use when the user wants to clear out accumulated tabs, triage a tab dump, process a tab log, or asks "close all my tabs and tell me what was in them", "triage my tabs", "clean up my browser".
---

# tab-triage

The AI half of [[chrome-tab-org]]. The extension closes tabs and writes a JSON log; **this skill
is the "separate, later AI pass" that log was always designed to feed** — see
`~/Dropbox/code/chrome-tab-org/docs/plans/chrome-tab-organizer.md` for why the analysis
deliberately lives out here instead of inside the extension (short version: doing it in-extension
meant a pasted API key, and neither Anthropic's nor Notion's OAuth actually solves that).

The division of labour is the whole design:

| Who | Does |
|---|---|
| **The extension** | Enumerates and closes tabs. Only it can — it holds `tabs`/`tabGroups`. |
| **Jake** | Clicks Close & Log. Only he can — no agent loads an unpacked extension or clicks a side panel. |
| **This skill** | Reads the log, classifies, writes the capture doc. Never touches Chrome. |
| **The triage subagent** | Turns the actionable slice into deduped ClickUp tasks. |

**This skill never closes a tab.** Every irreversible act is Jake's click. If you find yourself
reaching for a browser tool to close something, you have misread this skill.

## 1. Make sure a log can be produced

Check `~/Downloads/tab-organizer-logs/`.

**If it doesn't exist**, the extension has probably never been loaded. Walk him through it — don't
try to do it yourself, `chrome://extensions` is not agent-reachable:

1. `chrome://extensions` → toggle **Developer mode** (top right)
2. **Load unpacked** → `/Users/jake/Dropbox/code/chrome-tab-org`
3. Click the toolbar icon to open the side panel

**Then, either way**, ask him to flush:

> Open the side panel, leave every box checked, optionally type a note about what this batch was
> (it lands in the log and gives me context), and click **Close & Log**. Tell me when it's done.

Say plainly what that costs before he clicks, because it is not undoable from the browser:

- **Every `http(s)` tab in every window closes** — all groups, all windows, pinned tabs included.
- **URLs survive, page state does not.** In-progress forms, unsaved drafts, anything mid-checkout
  is gone.
- **`chrome://`, `file://`, and other extensions' pages survive untouched** — `isActionableUrl` in
  `lib/tab-capture.js` filters to `http(s)`, so they are neither logged nor closed.

Then **wait**. Do not open any browser tab before he confirms — a tab you created would render in
the panel default-checked and get closed with the batch. All browsing you do in this skill happens
*after* the flush.

## 2. Read the newest log

```bash
ls -t ~/Downloads/tab-organizer-logs/*/tab-log-*.json | head -1
```

**The guard is idempotence, not recency.** Grep the capture-doc directory
(`~/Dropbox/code/chrome-tab-org/docs/captures/`) for that filename. If it's already recorded there,
this batch has been triaged — say so and stop, rather than re-filing every task a second time.

Do *not* reject a log for being older than this session. "I flushed twenty minutes ago, triage it
now" is the normal case. Instead, show the timestamp and confirm: *"Newest log is from 14:32,
41 tabs across 6 groups — that the batch?"*

If the download landed flat in `~/Downloads/` instead of the `tab-organizer-logs/<profile>/`
subfolder, or Chrome prompted for a save location, **that is a bug in the extension, not a
formatting quirk** — record it and report it rather than working around it silently.

The log's shape (`lib/log-writer.js`):

```json
{ "capturedAt": "ISO", "profile": "...", "note": "user's note or null",
  "groups": [ { "name": "Mobile", "tabs": [ { "title": "...", "url": "...", "domain": "..." } ] } ] }
```

## 3. Classify every tab

Three buckets. **Read the group name and the note as context** — the same URL means different
things under "Mobile" than under "Job Search", and the note is Jake telling you what this batch
was for. Use them.

### Drop — throwaway, not worth a line

- **Login and OAuth residue** — `accounts.google.com`, `/auth/callback`, `/oauth/`, `/signin`,
  `/login/success`, SSO landing pages, "you may now close this window" confirmations.
- **Bare roots of sites he already knows** — `amazon.com`, `youtube.com`, `reddit.com`,
  `github.com` with no meaningful path. Nothing was being read there.
- **Duplicates** — exact repeats, and URLs identical after stripping `utm_*`, `fbclid`, `gclid`,
  `?ref=`, and trailing `#` fragments. Keep the one with the richest title.
- **Dead ends** — search-result pages, expired share links, paginated feeds, `/404`.

A bare root is only a drop for a *familiar* site. `some-tool-i-have-never-heard-of.com` with no
path is a Keep — he opened it for a reason.

### Keep — worth returning to, implies no work

An article, a reference, a tool worth remembering. Preserved as a link in the capture doc.

### Actionable — implies a task

Finish reading this. Buy this. Reply to this. Sign up. Try this tool. Compare these three. If you
can write a verb-first sentence describing what he'd *do* with it, it's actionable.

**When a URL is genuinely ambiguous**, open it in your own tab and look — the browser is empty now,
so this is safe. Expect per-domain permission prompts. If it needs a login, don't sign in: file it
as Keep, flagged `not verified — needs your login`.

## 4. Write the capture doc

`~/Dropbox/code/chrome-tab-org/docs/captures/<YYYY-MM-DD>-<slug>.md`. The slug comes from Jake's
note, or the dominant group name, or just `flush`.

```markdown
# Tab capture — <date>

_Source: `tab-organizer-logs/<profile>/tab-log-<ts>.json` · <N> tabs in · <K> kept · <D> dropped_
_Note: "<his note, if any>"_

## Keepers

### <theme>
- [Title](url) — one line on why it's worth returning to.

## Filed as tasks
- [Title](url) → <ClickUp task link>

## Dropped (<D>)
Login/OAuth residue <n> · Bare roots <n> · Duplicates <n> · Dead ends <n>

Full record of every tab, including dropped ones, is in the source log above.
```

Dropped tabs are **collapsed to counts, never listed** — the JSON is the complete record, and
re-listing 60 junk URLs defeats the point of triaging. Commit the doc to `chrome-tab-org` on
`main`; it's a personal tooling repo, so no PR (see [[personal-tooling-repos-skip-pr]]).

## 5. Hand the actionable set to a triage subagent

Spawn one subagent with the actionable list. Its job: **dedupe first, then create.**

**Dedupe is not optional.** Jake reopens the same tabs and re-flushes; without this, one bookmarked
intention becomes five identical tasks. For each item, `clickup_search` the URL, then the title's
distinctive words. Near-match counts — if something plausibly covers it, **skip and report the
existing task** rather than creating a rival. Under-filing beats duplicate noise.

Each created task carries, in `markdown_description`: the URL, the source group name, and the
capture-doc path.

### Everything lands in one quarantined list, tagged

**Do not route tasks into his existing project lists.** Jake has a separate ClickUp
reorganization in flight, and tabs scattered across `Job Search` / `Music Creation` / `ToDo` would
be indistinguishable from work he filed deliberately. Two rules, both non-negotiable:

1. **One dedicated list — `Chrome Tabs`, in the `Tasks` space (`90100240600`).** Every task this
   skill creates goes there, whatever the tab is about. Resolve it by name with `clickup_get_list`;
   create it with `clickup_create_list` if it doesn't exist yet, and say so in the report.
2. **Every task is tagged `chrome-tab`.** That's the signature that lets him sweep, bulk-edit, or
   re-home the whole batch later regardless of where the list ends up.

ClickUp tags are space-level and `clickup_create_task` requires the tag to already exist in the
space. So on the first run: create one task, then `clickup_add_tag_to_task` it. If that errors
because the tag is unknown to the space, tell Jake it needs creating once in the ClickUp UI
(`Tasks` space → Tags → `chrome-tab`) rather than silently filing untagged tasks — an untagged
task is exactly the thing he asked to avoid.

Topical routing is deliberately **not** done here. If he later wants these dispersed into project
lists, that's a sweep over the `chrome-tab` tag — which is why the tag matters more than the
placement.

**If the actionable set is large (20+), report the count and the proposed task titles before
creating anything.** Task volume is Jake's call, not a surprise he discovers in ClickUp.

## 6. Report

- Counts per bucket, and the total in.
- The capture doc path.
- Every created task as a link, and confirmation they all carry the `chrome-tab` tag.
- Every dedupe skip, naming the existing task it deferred to.
- Anything you couldn't judge, and why.

## Notes

- **Re-running without a fresh flush is a no-op**, by design — step 2's idempotence guard catches
  it. That's the correct behaviour, not a failure.
- The log accumulates. Older logs stay in `~/Downloads/tab-organizer-logs/` as the permanent record
  behind every capture doc; nothing prunes them, and nothing should without Jake asking.
- If he wants a *bounded* flush rather than the whole browser, he unticks sections in the panel
  before clicking. The skill reads whatever the log contains — it doesn't care whether the batch
  was one group or everything.
