---
name: unresolved-thread-scan
description: Claude Tag (Claude in Slack) routine — scans this channel's recent threads for work Claude already investigated that is either genuinely stuck waiting on a human decision, or still open with no Linear ticket recording it, and posts the findings back as a reply in its own routine thread (escalating PR-approval blockers to #pr-review instead). Runs automatically once attached as a plugin and triggered by a channel's scheduled routine; not something a user invokes by name mid-conversation. Applies whenever the routine fires, regardless of what else is happening in the channel that day.
---

# Unresolved Thread Scan

## Overview

Claude posts analysis, investigations, and PRs into engineering channels all the time. Two things then go wrong quietly. Some of that work reaches a dead end only a human can clear — a product call, a missing access grant, an ambiguous requirement — and it sits in the channel, easy to lose in the scroll. And some of it is never written down anywhere durable: a real proposed fix or an offered PR that exists only as a Slack message, with no Linear ticket behind it. This skill is a standing sweep that surfaces both back into this same channel, in a thread of its own, without re-reporting the same thread every time it fires.

This skill is attached as a plugin to whichever channels want the sweep, so its instructions are already loaded when each channel's own routine fires — the routine only needs to trigger it on schedule, not restate the steps.

The sweep is **durably authorized**: once a channel's routine is set up, run it in full every time it fires. Don't ask for permission to scan, to post, or to write the log on each firing.

## Route it into a thread, don't run it in the channel

Post one short top-level message — "Scanning for unresolved threads..." — then do all of the work below, findings included, as replies in the thread under that message. The scan reads 30 days of history, cross-checks Linear and GitHub, and reasons about each candidate — that's a lot of noise for the channel's main timeline, and the channel only ever needs the findings.

**Never write anything inside the threads being scanned.** All output from this routine — questions, notes, findings — belongs in this routine's own thread, never appended to the original session you're reading.

## Steps

### 1. Find the threads worth surfacing

Scan this channel's last 30 days of messages for threads where Claude posted analysis, an investigation writeup, or opened a PR. For each candidate, cross-check any referenced Linear ticket and/or GitHub PR (via their MCP tools) to see what actually happened next.

Surface a thread if **either** of these is true:

**(a) It's genuinely stuck on a human.** A real open question nobody has answered, a decision between options that only product/eng leadership can make, or access/credentials Claude doesn't have and can't get itself. For this branch the bar is: *if no human acts, does this stay stuck forever?* Threads that are merely quiet, waiting on CI, waiting on review, or tagged "needs more info" do **not** qualify under (a) — those move on their own, and "Claude could go gather that itself" is never a blocker.

**(b) It's real still-open work, with or without a Linear ticket.** Analysis, a proposed fix, or an offered PR. This branch has a different bar — the risk isn't that the work is blocked, it's that it gets **lost**. So the (a) exclusions above don't apply here: a quiet, unblocked, perfectly healthy piece of work still qualifies under (b) if the only record of it is a Slack message.

**A missing Linear ticket is never a reason to skip a thread — it's the reason to report it.** Undocumented work is the most likely to disappear. Never treat "I couldn't find a ticket for this" as a precondition failure, a lookup error, or something to leave out because the line would look incomplete. It is itself the finding.

### 2. Check for a human resolution reaction

Before reporting any candidate, read the reactions on that thread's root message (often an alert from a monitoring tool, or whatever kicked the thread off) explicitly — reactions aren't always in the wake feed, so don't assume there are none just because you didn't see one go by.

If the root message carries a ✅ from a **human**, treat the thread as resolved and skip it, regardless of ticket status or how open the work still looks. A ✅ added by Claude's own bot user does **not** count — otherwise the scan would silence its own reporting.

### 3. Check the log before reporting anything

This channel keeps its own memory of every thread already surfaced this way, at `team/channel/stuck-threads-log.md`. Read it first, and drop anything already there.

**Match on `thread_ts`, not on the ticket reference.** Items surfaced under branch (b) have no ticket to match on, so a ticket-keyed check would re-report every no-ticket item, every time the routine fires. The `thread_ts` is the identity of the item; the ticket ref is just an attribute of it.

The findings should only ever contain genuinely new items. A thread doesn't get reported a second time just because it's still stuck, or still undocumented, the next run.

### 4. Post findings in this routine's own thread

For each thread that passed steps 1–3, post one line as a reply in this routine's own thread (the one opened above) — not in a separate channel, and not appended to the original scanned thread.

Keep each line to one sentence if possible, carrying:

- a permalink to the original thread
- the open question needing resolution, or what the undocumented work is

Mention someone only if they actually need to act — use a real Slack mention for them; otherwise write their handle as `@.handle` so it doesn't ping. If someone was already working in the scanned thread, it's fine to mention them here.

**Exception — PR blocked on human approval.** If a thread is simply waiting on someone to approve or merge a PR, don't resurface it in the channel at all — send it to `#pr-review` instead, via the webhook (see below). Don't write a line like "no channel action needed — tracked via PR, not resurfaced as stuck" for a PR already shared to `#pr-review`; just leave it out of the channel post entirely.

> **Delegating the `#pr-review` cross-post:** the incoming webhook URL lives in the session configuration under Webhooks. A sub-agent can't see that section — if you delegate the cross-post, paste the full webhook URL into the worker's prompt, or make the POST from the main session yourself. Otherwise the worker will incorrectly report that no `#pr-review` webhook exists.

### 5. If the `#pr-review` webhook is blocked

If the `curl` to the `#pr-review` webhook fails, don't keep retrying it — nothing about the failure changes until an Owner changes a setting. Post the finding as a normal line in this routine's own thread instead, and say once (not on every future run) which setting is missing:

- **`hooks.slack.com` on the Access bundle's Domains tab.** This is the one that's definitely
  required. Claude Tag runs channel work in a sandbox behind an egress proxy, so a request to a host
  no allow layer covers is *blocked, not sent*. Symptom: Claude reports the host was "blocked by the
  network egress proxy."
- **An auto mode allow rule on the scope**, if the permission checker still stops the POST after the
  host is reachable. One plain sentence, e.g. "Posting an unresolved-PR notice to the #pr-review Slack
  webhook from a session in this channel is a normal, approved workflow."

Both live at `claude.ai/admin-settings/claude-tag`. Egress changes take about a minute to apply, and
apply to threads already running — so a retry in the same thread is worth one attempt after an Owner
confirms the domain is added.

### 6. Update the log

Add every item posted this run to `team/channel/stuck-threads-log.md`. One entry per item:

- `thread_ts` (the match key for step 3)
- the ticket reference, or `none` when there wasn't one
- the date it was surfaced
- `status` — `open` for a newly-logged item

Also update `status` to `resolved` on any already-logged item once:

- its root message gains a human ✅, or you otherwise confirm it closed, or
- its PR has been shared to `#pr-review` (this run or a prior one)

Serialize memory writes — no parallel workers on the same file.

### 7. Report the outcome

If nothing new was found, say so in one line in this routine's own thread and end. A quiet run still gets a line — that's how the routine reads as alive rather than possibly broken, since findings are no longer silent by default.

## Deploying this skill

This is a Claude Tag plugin skill, not a Claude Code slash-command skill — it isn't invoked by name, it's auto-loaded into every session in a channel it's attached to. To put it to work in a channel:

1. An admin attaches this skill's plugin to the channel (or workspace-wide, if every channel should run the sweep).
2. In that channel, set up (or replace) the routine by messaging `@Claude`: run the unresolved thread scan on the desired schedule, and include the real `#pr-review` webhook URL in that message so it's available at run time — that's the only place the live webhook value should ever be written down.

Replacing an existing routine (e.g. one that currently pastes the full instructions inline) works the same way: mention `@Claude` in the channel and describe the replacement, or run `@Claude !routines` first to see and edit what's there.

**A routine that still pastes the full instructions inline overrides this file.** Until such a routine is shortened to a trigger plus the webhook URL, edits here change nothing about what actually fires — the inline text is what runs.
