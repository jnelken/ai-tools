---
name: kosha-triage
description: Migrate stale ClickUp captures into the markdown vault at ~/Dropbox/code/knowledge-vault and delete the originals. Use when triaging, migrating, consolidating, or distilling ClickUp tasks — especially Memos, Backlog, or Someday — into vault records, or when Jake says "clean up my inbox", "what's in Memos", "distill these", "migrate the backlog", or asks where an old capture should live. Also use for any end-of-session distill that needs routing into bija / manana / pariksha / smriti / shruti. Read this before reading any ClickUp task you intend to consolidate or delete — partial metadata reads lose data silently.
---

# kosha-triage

Move stale ClickUp captures into the vault, then delete the originals. The
ordering is the whole design: **write first, verify, delete last.** ClickUp
deletion isn't reversible through the API, so a delete that runs before a
verified write loses the capture permanently.

Sibling to [[tab-triage]] — same shape (read a pile, classify, file, clean up),
different source. Where tab-triage never closes a tab, this skill never deletes
a task until the vault file has been read back.

| Who | Does |
|---|---|
| **`clickup_dump.py`** | Complete reads. Pages, re-fetches, flags incomplete records |
| **This skill** | Classifies, writes vault files, proposes deletions |
| **Jake** | Approves the plan and the delete list. Both, separately |

## The vault

`~/Dropbox/code/knowledge-vault/kosha/`

```
bija/        बीज — seeds. Raw jots, not yet routed
manana/      मनन — topics to sit with until he knows more
pariksha/    परीक्षा — proposals, verdict pending
smriti/      स्मृति — his own thinking
shruti/      श्रुति — received material
```

Filename `YYYY-MM-DD-slug.md`, dated by **original capture date**, not migration
date. How long a thought has been sitting is the useful signal.

**Paths never change.** Links, `graduated_to`, `from_manana`, and ClickUp
`Source Note` fields all point at paths. See `knowledge-vault/RENAMES.md`.

## Routing

**shruti vs smriti** — his thinking or someone else's? A link, an article, a
creator's channel is shruti. Received material in smriti dilutes his own notes
with other people's ideas.

**manana vs pariksha** — manana holds topics, pariksha holds questions. If it
answers yes/no or A/B, it was never manana. `blog publisher` is a topic.
"Should the blog live on Substack?" is a question with a verdict pending.

**manana vs a ClickUp task** — a topic is something to *write about* and never
becomes a task. An idea is something to *build* and stays in ClickUp. Filing a
topic as a task is what killed the `H` list: a question can't be marked done, so
it sat for a year.

**pariksha resolves in place** via `outcome`, and never moves. Six months later
he wants the reasoning next to the verdict.

## Frontmatter

```yaml
title: Declarative sentence, not a topic
domain: <closed vocabulary>
captured: YYYY-MM-DD        # original capture date
private: true               # default. false only for explicit creator output
karma: []                   # ClickUp task URLs
links: []                   # other vault paths
graduated_to: []            # manana only
from_manana: null           # smriti born from a manana
outcome: null               # pariksha only
```

Domains: `Music` `Health` `Career` `Engineering` `Finance` `Tax`
`Relationships` `Bucket List` `Creator Business` `Household` `PKM`
`Knowledge Graph` `ADHD Center` `Spiritual`

Closed list. Nothing fits, **ask** — inventing values is how the original tag
drift happened. Adding a domain is an edit to `knowledge-vault/README.md`, never
an in-session decision.

## 1. Query — completely, never partially

`clickup_filter_tasks` returns a thin slice: name, status, tags, priority, due
date, list, assignee. Not descriptions, not custom field values, not checklists.
Large sections collapse to counts like `custom_fields_count: 1`.

A task titled "Blog platform like Substack" held a **Ghost** signup URL in a
custom field. Reading title and tags would have recorded the opposite of what it
meant.

For a handful of tasks, `clickup_get_task` with every section:

```
include: ["description", "custom_fields", "attachments", "checklists",
          "dependencies", "linked_tasks", "subtasks", "watchers"]
```

For more, use the script — it pages properly, re-fetches each task individually
because list endpoints omit custom field values entirely, and refuses to return
a record whose section counts don't match its contents:

```bash
export CLICKUP_TOKEN=pk_...
python3 ~/Dropbox/code/knowledge-vault/scripts/clickup_dump.py \
  --list 900501279116 --md -o /tmp/memos.md
```

Known: `Memos` is `900501279116`. Tasks space is `90100240600`.

Two pre-existing signals in the data:

- **`URL` custom field** `bf3208ea-e2dd-4005-a4a2-938b736a2d3f` — often the only
  place the actual content lives. Jake thought he'd deleted this field; he
  hadn't
- **`task_type: "Resource"`** — he was already flagging shruti two years ago.
  Strong hint, not a rule

## 2. Distill — propose, never write

Cluster related captures. Recapture frequency is signal: the same thought caught
six times over ten weeks matters more than one caught once. But verify it *is*
the same thought — four "blog platform" captures named four different platforms.

Present the plan in this shape, then **stop**:

```
VAULT
1. [folder/domain] Title — one-line summary

KARMA (ClickUp)
1. [List] Title
   Urgent: y/n · Sankalpa: y/n · Kind: X
   Source: vault #N

CLEANUP
- task ids to delete once written
```

No write tool, no delete tool, before he approves.

When consolidating, **carry every tag from every source task**. A `marketing`
tag once nearly vanished because it sat on a task slated for deletion rather
than the one being kept.

## 3. Write

Create the file. Read it back before touching ClickUp.

Every record ends with provenance — source ids, original capture dates, carried
tags:

```
Origin: six ClickUp tasks in `Memos`, captured Jan 12 – Mar 26 2024.
Consolidated 2026-08-30. Source ids: 86a1xz9u8, ... Carried tag: `marketing`.
```

**Quote verbatim.** Don't tidy grammar, soften phrasing, or rewrite for flow. If
a capture holds difficult self-directed material, preserve it exactly and add no
commentary or framing around it. The record is his voice, not a summary of it.

**Don't fill in a manana.** A title and whatever fragments exist is correct and
complete. Scaffolding an empty outline is the friction that makes him stop using
the queue.

**Don't resolve a pariksha.** Record the options and the current state; supply
no recommendation and no comparison. A proposal with a verdict you wrote isn't a
parked question — it's you deciding for him.

## 4. Delete — last, after separate confirmation

Table every task id and name. Get an explicit yes — the approval in step 2 was
for the plan, not for the deletions. Then delete one at a time with
`clickup_delete_task`, confirming each.

Never delete on the same turn as the write. That gap is where he catches a
misroute.

## What goes wrong

**Reading titles instead of records.** The largest failure. Titles lie.

**Noticing a gap without closing it.** Seeing `custom_fields_count: 1`, saying
so out loud, and not fetching the field is the same as never noticing. Flag and
fetch in the same turn.

**Stopping at page 0.** `filter_tasks` caps at 100 and sets `has_more`.

**Assuming a cluster is one thought.** Check dates and content.

**Routing a question into manana.** Yes/no answer means pariksha.

**Deleting before verifying the write.** Irreversible. Read the file back.

## Notes

- Jake's ClickUp reorganization is in flight — three lists, custom fields,
  Urgent/Sankalpa. Don't assume the current list structure is final, and don't
  create tasks in lists this skill didn't read.
- The nightly enforcement script doesn't exist yet, by design. Manual operation
  first so it encodes tested rules rather than guessed ones.
