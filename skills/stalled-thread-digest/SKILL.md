---
name: stalled-thread-digest
description: Daily Claude Tag (Claude in Slack) routine — scans this channel's recent threads for work Claude already investigated that is now genuinely stuck waiting on a human decision, and cross-posts new ones to #eng-claudio-chat. Runs automatically once attached as a plugin and triggered by a channel's scheduled routine; not something a user invokes by name mid-conversation. Applies whenever the routine fires, regardless of what else is happening in the channel that day.
---

# Stalled Thread Digest

## Overview

Claude posts analysis, investigations, and PRs into engineering channels all the time. Some of that work reaches a dead end that only a human can clear — a product call, a missing access grant, an ambiguous requirement — and then it just sits in the channel, easy to lose in the scroll. This skill is a standing daily sweep that surfaces exactly those dead ends to a central channel (`#eng-claudio-chat`) so nothing genuinely blocked stays invisible, without re-reporting the same thread every day.

This skill is attached as a plugin to whichever channels want the sweep, so its instructions are already loaded when each channel's own routine fires — the routine only needs to trigger it on schedule (9am ET daily), not restate the steps.

## Steps

### 1. Find threads that are genuinely stuck

Scan this channel's last 30 days of messages for threads where Claude posted analysis, an investigation writeup, or a PR. For each one, cross-check the referenced Linear ticket and/or GitHub PR to see what actually happened next.

Only surface a thread if it is truly stuck on a human — a real open question nobody has answered, a decision between options that only product/eng leadership can make, or access/credentials Claude doesn't have and can't get itself. Do **not** surface a thread just because it's quiet, waiting on CI, waiting on review, or tagged "needs more info" — those move on their own. The bar is: if no human acts, does this stay stuck forever? If yes, it belongs in the digest.

### 2. Check the log before reporting anything

This channel keeps its own memory of every thread already surfaced this way — one entry per thread with its `thread_ts` and the ticket/PR reference it maps to. Before posting anything from step 1, drop everything that's already in that log. The digest should only ever contain genuinely new items; a thread doesn't get reported a second time just because it's still stuck the next day.

### 3. Post new items to #eng-claudio-chat

For each thread that passed steps 1 and 2, post one line to `#eng-claudio-chat` via this webhook:

```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text": "<message>"}' \
  <ENG_CLAUDIO_WEBHOOK_URL>
```

`<ENG_CLAUDIO_WEBHOOK_URL>` is a placeholder — the real webhook URL is never written into this skill file, because this skill lives in a public repo. It's supplied only inside Slack, as part of the routine that triggers this skill in each channel (see "Deploying this skill" below).

Each line should carry: the ticket/PR ID, a few words on what it's actually blocked on, who the right person to ask is (if determinable from the ticket/thread), and a link to this channel's thread. Prefix the line with this channel's name — `#eng-claudio-chat` receives this same digest from multiple channels, so the source has to be unambiguous at a glance. One item per line; batch everything from this run into as few messages as makes sense rather than one webhook call per item.

### 4. If the webhook is blocked

If the `curl` fails on a permission error, that means Claude in Slack's auto-mode allow rules haven't approved this webhook yet — don't keep retrying it, that won't change until an admin acts. Instead, post the same digest as a new top-level message in this channel, and say once (not on every future run) that the webhook needs an admin to approve it before it can post to `#eng-claudio-chat` directly.

### 5. Update the log

Add every item posted this run (whether it went out via the webhook or the in-channel fallback) to the log from step 2, so tomorrow's run knows not to repeat it.

## Deploying this skill

This is a Claude Tag plugin skill, not a Claude Code slash-command skill — it isn't invoked by name, it's auto-loaded into every session in a channel it's attached to. To put it to work in a channel:

1. An admin attaches this skill's plugin to the channel (or workspace-wide, if every channel should run the sweep).
2. In that channel, set up (or replace) the daily routine by messaging `@Claude`: run the stalled-thread digest every weekday at 9am ET, and include the real `#eng-claudio-chat` webhook URL in that message so it's available at run time — that's the only place the live webhook value should ever be written down.

Replacing an existing routine (e.g. one that currently pastes the full instructions inline) works the same way: mention `@Claude` in the channel and describe the replacement, or run `@Claude !routines` first to see and edit what's there.
