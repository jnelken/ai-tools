---
name: sync-claude-projects-memory
description: Harvest claude.ai's native per-Project "Memory" summaries (and the global account memory) into local markdown files, then regenerate a master JAKE.md cross-project narrative. Use when asked to sync/refresh/consolidate claude.ai Project memory, build or update a "second brain" from claude.ai Projects, or when JAKE.md or the files under the memory output directory feel stale.
---

# sync-claude-projects-memory

Turns claude.ai's own per-Project **Memory** feature — the auto-generated summary under
each Project's "Memory · Only you" section — into a local, durable, cross-referenced
knowledge base, without ever reading a raw conversation thread.

## Why this works (read before optimizing it away)

claude.ai already runs a nightly summarization pass over every Project's conversation
history and writes the result to that Project's Memory section: Purpose & context,
Current state, On the horizon, Key learnings & principles, Approach & patterns, Tools &
resources. That's the expensive part of "consolidate my conversation history into
documentation" — and it's already done, continuously, for free, sitting unused.

This skill's entire value is: **harvest that existing distillation instead of
redoing it.** A Project's Memory section runs a few hundred to ~1500 words. The raw
threads behind it can easily be 10–50x that per project. Do not "improve" this skill by
having it open and read individual conversation threads as the default path — that
reintroduces the exact cost this design avoids. If a specific project needs
finer-grained detail than its Memory captures, that's a targeted, explicit exception
for that one project — not a change to the default mechanism.

**The corresponding limitation, state plainly if asked**: Memory is itself a lossy
distillation. It's excellent for "what is this project about, what matters, what's the
pattern" — bad for recovering one specific offhand detail that never made it into the
summary. This skill produces a narrative index, not a full-fidelity archive.

## Prerequisites

- claude.ai Settings → Capabilities → Memory → "Generate memory from chat history"
  (legacy toggle) must be on, and ideally has been on long enough to have populated
  Project-level memories. If it's off, tell the user and stop — there's nothing to
  harvest.
- Browser access to the user's actual logged-in claude.ai session — use
  `claude-in-chrome` (the user's real Chrome), not the sandboxed in-app Browser pane,
  which will not be signed in. Load it in one batched `ToolSearch` call:
  `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__find`

## Output location

Default: `~/Dropbox/code/claude-projects-memory/` (adjust if the user's setup differs —
this directory is not itself a git repo and shouldn't need to be; if it ends up inside
one, gitignore it rather than committing personal Project content). Contains:

- `<slug>.md` — one file per claude.ai Project, `slug` = kebab-case of the Project name
- `_global-account-memory.md` — the non-project account-level memory (see step 3)
- `state.json` — the queue/tracking file (see step 5)
- `JAKE.md` (or whatever the user names it) — the synthesized cross-project narrative

If `state.json` already exists, this is a **refresh**, not a first run — read it first
and only re-harvest what's changed (step 2 covers how to tell).

## 1. Enumerate every Project and its id

Navigate to `https://claude.ai/projects` and read the page. The project list is
virtualized (not all cards exist in the DOM at once), so a single `get_page_text` or
`read_page` call will miss most of it. The reliable path:

1. `get_page_text` once to get every Project's **name**, one-line description, and
   "last updated" date in one shot (this part isn't virtualized).
2. Scroll the list in increments (`computer` action `scroll`, ~10 ticks at a time) and
   call `read_page` with `filter: "interactive"` after each scroll to collect
   `href="/cowork/project/<uuid>"` links as they enter the DOM. Scroll from top to
   bottom once; stitch the hrefs together by matching link text (project name) to the
   name/description pairs from step 1.

This was tried and abandoned: sniffing `read_network_requests` for a clean "list
projects" API response — what came back was mostly Datadog RUM beacons and MCP
toolbox noise, no obvious single JSON payload with the full project list. Worth a
retry if someone wants to optimize this further (a real API call would beat DOM
scrolling), but don't block on it — the scroll approach is reliable today.

Build the project list: `{name, id, slug}` for every real Project. Skip Anthropic's
"How to use Claude" example project (or equivalent) — it's not user content.

## 2. Decide what needs re-harvesting

For a first run, harvest everything. For a refresh, compare each Project's "Last
updated" date/relative-time (visible in the page text under "Memory") against that
project's `memory_last_updated_on_claudeai` in `state.json`. Only re-fetch and rewrite
projects whose memory has actually changed since the last harvest — this keeps a
refresh cheap regardless of how large the total project count grows. Always re-check
the full project list from step 1 first, though — a Project that's new since the last
run won't be in `state.json` at all and needs a first harvest.

## 3. Harvest the global (non-project) account memory

This is separate from per-project memory and covers non-Project chats. Navigate to
`https://claude.ai/settings/capabilities` (or open Settings → Capabilities from the
UI), find "View and manage memory" under the Memory section, and click it.

**Known quirk**: `get_page_text` does not pick up this modal's content — it only reads
`<main>`, and the modal renders outside it. Reading it requires scrolling *inside* the
modal (`computer` scroll with coordinates centered on the modal, small increments —
large jumps skip content) and either `screenshot`-ing each scroll position or trying
`read_page` scoped to the dialog via `ref_id` first (untried — screenshots work but
cost more in image tokens than a text read would; try the cheaper path first if
optimizing). Reconstruct the full text from Work context → Personal context → Top of
mind → Brief history (Recent months → Earlier context → Long-term background) → Other
instructions, in that order — sections repeat across overlapping screenshots, so
de-duplicate by content, not by scroll position.

**Cross-check for staleness**: this global memory can lag behind project-level memory
(e.g. it may report an employer or role the user has since left, if that fact hasn't
come up in a non-project chat recently). Note any such contradiction explicitly in the
file and in the JAKE.md synthesis rather than silently preferring one source — flag it,
prefer the more specific/recent source, but say so.

## 4. Harvest each Project

For each `{name, id}` from step 1 needing a (re-)harvest: navigate to
`https://claude.ai/cowork/project/<id>` and call `get_page_text` once. This single call
returns the description/instructions, the Memory section (if populated), and the list
of recent thread titles — everything needed, in one page load.

Write `<slug>.md` with this structure:

```markdown
# <Project Name>

Source: claude.ai Project native Memory (auto-synthesized from conversation history)
URL: https://claude.ai/cowork/project/<id>
Memory last updated (on claude.ai): <date from page>
Harvested: <today's date>

## Project description (instructions)

<verbatim or condensed if very long>

## Memory (as generated by claude.ai)

<the full Memory section, verbatim, preserving its own section headers>

## Recent thread titles (for reference, not read in full)

<thread titles from the page, joined by " · ">
```

If the page shows "Project memory will show here after a few chats" instead of a
populated Memory section, write a short file noting that (description + thread titles
only) and mark it `done_no_memory_yet` in `state.json` — don't read the raw threads to
compensate; a Project with too little history for claude.ai to summarize doesn't
justify breaking this skill's core cost model for one project. Revisit it next run.

Mark any file covering therapy, finances, legal, medical, or other sensitive content
with a one-line content warning at the top (e.g. `**Sensitive: <topic>. Handle with
care.**`) so anyone reading the directory later knows before opening it.

## 5. Update state.json

One entry per Project: `{name, id, slug, status, last_harvested,
memory_last_updated_on_claudeai}`. `status` is one of `done`, `done_no_memory_yet`, or
`skipped` (for example projects). Plus a `_meta` block recording the output directory,
the mechanism (so a future session understands *why* without re-deriving it — point it
at this skill file), and the last full-pass date. This file is what makes the skill
idempotent and cheap on repeat runs — don't skip writing it.

## 6. Regenerate the synthesis file

**Single-source-of-truth rule: each per-project file is the only source of truth for
*discrete facts* about that project — names, dates, account numbers, medical
specifics, and the like. The synthesis file (`JAKE.md` or equivalent) must never
restate them.** A fact that appears in exactly one project's file does not belong in
the synthesis file, no matter how important it seems — that duplication is exactly
what the per-project file structure exists to avoid. If you catch yourself writing a
paragraph that's really just a summary of one project's domain (a family list, a
health history, an account inventory), cut it and make sure it's in that project's
own file instead.

This does **not** mean the synthesis file is only a routing index. Its main content
is the **holistic character/pattern picture of the person** — their talents, unique
qualities, personality, challenges, pitfalls, blind spots, and areas of active growth
— built by reading all the projects together. That picture has no other home: no
single project file contains "who this person is as a whole," only their own slice of
it. This is synthesis, not restatement, even though it draws on everything — the test
is whether a claim needed two or more projects as evidence, not whether it mentions
anything factual.

What belongs in the synthesis file:

- **"Who [name] Is"** — the core content. Organize into something like: Talents &
  strengths, Unique/special qualities, Personality, Challenges/pitfalls/blind spots,
  Areas of active growth. Ground every claim in evidence from two or more projects —
  cite or imply which ones. Distinguish blind spots the person hasn't named
  themselves from areas they're already actively working on (their own systems,
  routines, or stated intentions) — conflating the two reads as presumptuous.
- **How [name] works with Claude** — collaboration-mode specifics (communication
  preferences, what kind of answers they want) distinct from the character
  description above; this is operationally useful even though it overlaps in spirit.
- **Connections between projects** — cases where two projects touch the same
  underlying person, event, or issue without referencing each other (the same health
  condition addressed on two different tracks, a life event whose effects ripple
  across several unrelated projects, two tools/efforts that might be the same thing
  or might not). The synthesis is the fact that they connect — not a restatement of
  either project's content.
- **Contradictions and staleness across sources** — where one file conflicts with
  another (notably: global account memory vs. project-level memory, which drift
  apart because global memory only updates from non-project chats).
- **Coverage gaps in the synthesis system itself** — which projects have no
  populated Memory yet, so a reader knows what's thin by design rather than by
  omission.
- **A routing table and source index** — useful as reference material, but
  secondary to the character synthesis above, not the file's main point.

Read the per-project files from disk for this step rather than holding all of them in
conversation context from step 4 (write-then-forget per project during harvesting,
then re-read for synthesis) — this step is inherently a second pass over already-
written material, not a continuation of the harvest.

State explicitly at the top of the synthesis file, in the file's own words, that it
isn't a source of truth for discrete facts (those belong in the relevant project
file), but that the character/pattern synthesis has no other home and is the file's
main content.

## 7. Wire it into future sessions (first run only)

Ask before doing this if it wasn't already agreed — it edits the user's global
`CLAUDE.md`. If agreed, add an `@`-import of the synthesis file
(e.g. `@/Users/jake/Dropbox/code/claude-projects-memory/JAKE.md`) so it loads
automatically in every future Claude Code session, with a short note that it's
background context (not instructions), regenerated periodically, and that live
project-specific memory or direct confirmation should win over it for anything
time-sensitive. Skip this step on a refresh run if the import already exists.

## Notes

- **Serial, not parallel.** This drives the user's one real Chrome via `claude-in-chrome`.
  Don't fan this out across subagents — they'd fight over tabs. A refresh with few
  changed projects is already cheap; a first run over many projects is inherently a
  long serial walk, and that's fine.
- **Never publish or upload these files anywhere.** They're local-only by design —
  don't attach them to claude.ai Projects, paste them into chats, or otherwise leave
  the local filesystem, even though the source data originated on claude.ai. If a
  future ask wants these *pushed back* into claude.ai (e.g. as Project knowledge
  files), treat that as a separate, explicitly-approved task — it changes the privacy
  model of this whole exercise and deserves its own confirmation.
- **This is a claude.ai-specific technique.** It depends on the Memory feature
  existing and being enabled; it has no equivalent for plain Anthropic API usage or
  other tools.
- If asked to package this for someone else's account: nothing here is hardcoded to a
  specific user except the default output path — genuinely portable as-is.
