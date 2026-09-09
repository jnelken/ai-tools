---
name: stalled-thread-digest
description: Daily Claude Tag (Claude in Slack) routine — scans this channel's recent threads for work Claude already investigated that is either genuinely stuck waiting on a human decision, or still open with no Linear ticket recording it, and cross-posts new ones to #eng-claudio-chat. Runs automatically once attached as a plugin and triggered by a channel's scheduled routine; not something a user invokes by name mid-conversation. Applies whenever the routine fires, regardless of what else is happening in the channel that day.
---

# Stalled Thread Digest

## Overview

Claude posts analysis, investigations, and PRs into engineering channels all the time. Two things then go wrong quietly. Some of that work reaches a dead end only a human can clear — a product call, a missing access grant, an ambiguous requirement — and it sits in the channel, easy to lose in the scroll. And some of it is never written down anywhere durable: a real proposed fix or an offered PR that exists only as a Slack message, with no Linear ticket behind it. This skill is a standing daily sweep that surfaces both to a central channel (`#eng-claudio-chat`) so neither stays invisible, without re-reporting the same thread every day.

This skill is attached as a plugin to whichever channels want the sweep, so its instructions are already loaded when each channel's own routine fires — the routine only needs to trigger it on schedule (9am ET daily), not restate the steps.

The sweep is **durably authorized**: once a channel's routine is set up, run it in full every time it fires. Don't ask for permission to scan, to post, or to write the log on each firing.

## Route it into a thread, don't run it in the channel

Run the sweep the way a router does: post one short top-level message in the channel saying the daily scan is starting, then start a **thread session** off that message and do all of the work below inside it. The scan reads 30 days of history, cross-checks Linear and GitHub, and reasons about each candidate — that's a lot of noise to dump into a channel's main timeline, and the channel only ever needs the outcome. Keep the top-level message to a line; everything else lives in the thread.

## Steps

### 1. Find the threads worth surfacing

Scan this channel's last 30 days of messages for threads where Claude posted analysis, an investigation writeup, or a PR. For each one, cross-check any referenced Linear ticket and/or GitHub PR (via their MCP tools) to see what actually happened next.

Surface a thread if **either** of these is true:

**(a) It's genuinely stuck on a human.** A real open question nobody has answered, a decision between options that only product/eng leadership can make, or access/credentials Claude doesn't have and can't get itself. For this branch the bar is: *if no human acts, does this stay stuck forever?* Threads that are merely quiet, waiting on CI, waiting on review, or tagged "needs more info" do **not** qualify under (a) — those move on their own, and "Claude could go gather that itself" is never a blocker.

**(b) It's real still-open work with no Linear ticket at all.** Analysis, a proposed fix, or an offered PR that nobody has recorded anywhere durable. This branch has a different bar — the risk isn't that the work is blocked, it's that it gets **lost**. So the (a) exclusions above don't apply here: a quiet, unblocked, perfectly healthy piece of work still qualifies under (b) if the only record of it is a Slack message.

**A missing Linear ticket is never a reason to skip a thread — it's the reason to report it.** Undocumented work is the most likely to disappear. Never treat "I couldn't find a ticket for this" as a precondition failure, a lookup error, or something to leave out because the line would look incomplete. It is itself the finding.

### 2. Check the log before reporting anything

This channel keeps its own memory of every thread already surfaced this way, at `team/channel/stuck-threads-log.md`. Read it first, and drop anything already there.

**Match on `thread_ts`, not on the ticket reference.** Items surfaced under branch (b) have no ticket to match on, so a ticket-keyed check would re-report every no-ticket item, every day, forever. The `thread_ts` is the identity of the item; the ticket ref is just an attribute of it.

The digest should only ever contain genuinely new items. A thread doesn't get reported a second time just because it's still stuck, or still undocumented, the next day.

### 3. Post new items to #eng-claudio-chat

For each thread that passed steps 1 and 2, post one line to `#eng-claudio-chat` via this webhook:

```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text": "<message>"}' \
  <ENG_CLAUDIO_WEBHOOK_URL>
```

`<ENG_CLAUDIO_WEBHOOK_URL>` is a placeholder — the real webhook URL is never written into this skill file, because this skill lives in a public repo. It's supplied only inside Slack, as part of the routine that triggers this skill in each channel (see "Deploying this skill" below).

Each line carries, in order:

- **the Linear ticket ID — or the literal words "no Linear ticket"** when none exists. Say it explicitly; never drop the item just because this field would be empty.
- a few words on what it's actually blocked on, or on what the undocumented work is
- who the right person to ask is, if determinable from the ticket or thread
- a permalink to this channel's thread

Prefix every line with this channel's name — `#eng-claudio-chat` receives this same digest from multiple channels, so the source has to be unambiguous at a glance. One item per line; batch everything from this run into as few messages as makes sense rather than one webhook call per item.

### 4. If the webhook is blocked

If the `curl` fails, don't keep retrying it — nothing about the failure changes until an Owner
changes a setting. Post the same digest as a new top-level message in this channel instead, and say
once (not on every future run) which setting is missing:

- **`hooks.slack.com` on the Access bundle's Domains tab.** This is the one that's definitely
  required. Claude Tag runs channel work in a sandbox behind an egress proxy, so a request to a host
  no allow layer covers is *blocked, not sent*. Symptom: Claude reports the host was "blocked by the
  network egress proxy."
- **An auto mode allow rule on the scope**, if the permission checker still stops the POST after the
  host is reachable. One plain sentence, e.g. "Posting the stalled-thread digest to the
  #eng-claudio-chat Slack webhook from a session in this channel is a normal, approved workflow."

Both live at `claude.ai/admin-settings/claude-tag`. Egress changes take about a minute to apply, and
apply to threads already running — so a retry in the same thread is worth one attempt after an Owner
confirms the domain is added.

### 5. Update the log

Add every item posted this run — whether it went out via the webhook or the in-channel fallback — to `team/channel/stuck-threads-log.md`. One entry per item:

- `thread_ts` (the match key for step 2)
- the ticket reference, or `none` when there wasn't one
- the date it was surfaced

### 6. If nothing new was found, end quietly

No post, no "all clear" message, no log update. A silent run is the expected outcome most days, and a daily "nothing to report" gets the digest muted.

## Deploying this skill

This is a Claude Tag plugin skill, not a Claude Code slash-command skill — it isn't invoked by name, it's auto-loaded into every session in a channel it's attached to. To put it to work in a channel:

1. An admin attaches this skill's plugin to the channel (or workspace-wide, if every channel should run the sweep).
2. In that channel, set up (or replace) the daily routine by messaging `@Claude`: run the stalled-thread digest every weekday at 9am ET, and include the real `#eng-claudio-chat` webhook URL in that message so it's available at run time — that's the only place the live webhook value should ever be written down.

Replacing an existing routine (e.g. one that currently pastes the full instructions inline) works the same way: mention `@Claude` in the channel and describe the replacement, or run `@Claude !routines` first to see and edit what's there.

**A routine that still pastes the full instructions inline overrides this file.** Until such a routine is shortened to a trigger plus the webhook URL, edits here change nothing about what actually fires — the inline text is what runs.
