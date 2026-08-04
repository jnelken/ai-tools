---
name: close-out
description: Use at the end of a long working session to confirm nothing was left orphaned before closing the thread — sweeps the conversation for unanswered questions, unfiled findings, retracted claims and "I'll do that later" promises, sweeps the machine for stray background processes, temp scripts, uncommitted/unpushed worktrees, un-announced PRs and stale sswt workspaces, files every real follow-up into Linear assigned to the user, writes anything only a human can decide into a running IN_PROGRESS.md so a future session can pick it up, then prints a large ASCII confirmation banner once the machine is genuinely clean (open decisions no longer block the close — they just have to be durably captured first). Trigger phrases include "wrap up this session", "can I close this thread", "close out", "close this out", "file the follow-ups", "am I safe to close this", "session wrap-up", "anything left hanging", "did we leave anything unfinished", "safe to end the session".
---

# Close Out

## Overview

A long session accumulates debris: a question you asked that never got answered, a bug you found in passing and never filed, a `nohup`'d job still burning CPU, a `tmp-*.ts` prod-DB script sitting untracked in a repo, a PR that never made it to `#pr-review`. None of that is visible from the last few messages — it's spread across hours of transcript and across the filesystem.

This skill is the closing checklist. Five steps, in order: sweep the conversation (1), sweep the machine (2), file follow-ups into Linear (3), write what only the human can decide into a running `IN_PROGRESS.md` (4), and — **only once everything is either resolved or durably captured** — print a confirmation banner (5).

Two different outcomes call for two different responses, and conflating them is the bug this redesign fixes:

- **Things this session left behind that it could still fix itself** — a background job it started, a temp script with a live secret, unpushed work in a worktree it touched. These are **dangerous, not just undecided**, and the skill must not paper over them. They still hard-block the banner.
- **Things only the user can decide** — an unanswered question, a PR awaiting someone else's approval, a prod change awaiting authorization. Blocking the thread on these just to keep them visible was the wrong trade: the user loses nothing by closing as long as the decision is written somewhere durable. `IN_PROGRESS.md` is that durable place — a future session (or the user, next week) reads it and picks up exactly where this one left off.

The single worst outcome this skill can produce is a **false all-clear**: a banner that says "nothing orphaned" while a background job is still running, a finding is unfiled, a temp credential script is sitting in a repo, or a decision exists only in this transcript and nowhere else. Every gate in step 5 exists to make that impossible — when in doubt, print the NOT CLEAN block instead.

## Report shape

Open the response with a start marker, before step 1's findings print — a long session's transcript makes the sweep hard to relocate later, and this is the anchor to scroll back to:

```
────────────────────────────  CLOSE-OUT SWEEP  ────────────────────────────
```

Everything from step 1 through step 3 — every path, PID, SHA, ticket id, exactly as those steps produce it — is printed below that marker, in order. **Step 4's output does not get reprinted in the chat.** Its content lives in `IN_PROGRESS.md`; the transcript gets only a one-line pointer (file path + item count) — see step 4. Restating the full list of open questions in the response is exactly the failure mode this redesign removes: it's ephemeral the moment the thread closes, while the file persists. The step 5 banner (success or NOT CLEAN) is the **last** thing in the response, after all of it. It points back at both the detail above the marker and at `IN_PROGRESS.md`, it doesn't repeat either. This gives the response three fixed landmarks regardless of outcome: the start marker, the full detail, and the verdict at the very bottom.

## When NOT to use

- The user wants in-progress *code* tidied, committed, and a NEXT-STEPS file written across repos under `~/Dropbox/code` — that's [[wrapup-repos]]. This skill closes out a *conversation*; it does not finish anyone's half-written feature. (If both skills have run against the same repo, you may find both `NEXT-STEPS.md` and `IN_PROGRESS.md` at its root — `NEXT-STEPS.md` is one wrapup-repos run's snapshot of what it did and the decisions it skipped; `IN_PROGRESS.md` is close-out's running log of open questions across sessions. Don't merge them into one file without the user asking — they're owned by different skills with different update semantics.)
- The user only wants to know which PRs haven't been announced in `#pr-review` — that's [[pr-review-gaps]], much cheaper.
- The user only wants stray dev servers killed — that's [[reap-dev-servers]].
- The session was short and single-purpose (one file edited, one question answered). Say so and skip; a five-step sweep on a ten-message session is noise.

## Steps

### 1. Sweep the conversation for unfinished business

Re-read the **whole** session, start to finish — not the last few exchanges. Build a list, one row per item:

| Category | What to look for | Where it ends up |
|---|---|---|
| **Pending decision** | A question you asked the user that never got a direct answer. Scrolling past it is not an answer. | `IN_PROGRESS.md` (step 4) |
| **Unfiled finding** | A bug, perf problem, security smell, or observability gap you discovered and only ever mentioned in chat. | Linear (step 3) |
| **Retracted claim** | Anything you asserted in a *durable artifact* (Linear comment, PR body, ticket description, commit message, doc) that you later corrected or walked back. | Fixed in place — verified, not filed |
| **Blocked work** | Something you started and stopped. Record *what* it's blocked on, by name. | `IN_PROGRESS.md` (step 4) |
| **Deferred promise** | Any "I'll do that after…", "worth doing later", "next step is…" you wrote and never came back to. | `IN_PROGRESS.md` (step 4) |

A **pending decision**, **blocked work**, or **deferred promise** is not an item you keep holding until step 5 — hand it straight to step 4's `IN_PROGRESS.md` write. An **unfiled finding** is concrete, actionable work with no decision attached; it goes to Linear, same as before. The distinction is "does closing this out require someone's judgment?" — if yes, `IN_PROGRESS.md`; if no, file it and move on.

**Retracted claims get verified, not remembered.** If you corrected yourself mid-session, the correction is only real if it landed in the artifact. Go read the artifact — `gh pr view <n> --json body`, `mcp__claude_ai_Linear__get_issue`, `mcp__claude_ai_Linear__list_comments` — and confirm the wrong claim is actually gone or annotated. A correction that exists only in chat is an **outstanding item**: the durable record still says the wrong thing, and whoever reads it next has no idea.

This applies more broadly: **prefer the durable system over the conversation's own narrative.** The transcript is a record of what you believed at the time, and parts of it were superseded. Linear, GitHub, and the filesystem are what's actually true now.

### 2. Sweep for orphaned state

Run these. Report findings as evidence (paths, PIDs, SHAs), not as conclusions.

**a. Background processes and live tasks.**
```bash
ps aux | grep -Ei 'nohup|codex review|remote-exec|tsx watch|vite|pg_dump' | grep -v grep
```
Also enumerate in-harness background work with `TaskList` and any Monitor/background Bash still live. Anything this session started and no longer needs gets stopped with `TaskStop` (or reported with its PID if it's a detached shell process — do not kill processes you can't attribute to this session; see [[reap-dev-servers]] for the orphan-vs-live classification).

**b. Temp/scratch scripts left inside a repo.** These must never be committed, and the dangerous ones are the ones that reach prod.
```bash
git status --porcelain | grep -Ei 'tmp-|scratch|scripts/tmp|\.bak$|debug-.*\.(ts|js|sh)$'
```
For each hit, check whether it touches credentials or prod:
```bash
grep -lE 'PROD_MGMT|DATABASE_URL|decryptConnectionUrl|API_KEY|SECRET|xoxp-' <paths>
```
**Report the path and why it's dangerous — do not print the file's contents into the transcript, and never `git add` it.** Recommend moving it to the session scratchpad or deleting it, and let the user choose.

**c. Uncommitted and unpushed work across every worktree touched this session.** `git worktree list` alone is not enough — it only covers the current repo's set. Take the union of:
- Superset `workspaces_list` (the authoritative sswt set), plus
- `git worktree list` run from each root checkout touched this session (e.g. `/Users/jake/code/api`, `/Users/jake/code/woodrow`).

Then, per worktree:
```bash
git -C "$WT" status --porcelain
git -C "$WT" log --oneline @{u}..HEAD 2>/dev/null   # unpushed commits; error = no upstream, also a finding
```

**Split the results into two buckets — this matters for step 5.** A worktree counts as **touched this session** if you ran a command in it, edited a file in it, or acted on its branch/PR. Only touched worktrees gate the banner. Everything else — parked sswt workspaces, long-lived branches the session never went near — is **reported as context and does not block the close**. Without this split the gate latches NOT CLEAN forever on work that was never this session's to finish, and a banner that can never print teaches the user to ignore it.

**d. PRs opened this session that never got reviewed or announced.** Per the user's `CLAUDE.md` post-PR workflow, an open PR needs a clean `/loop /peer-review` and a `#pr-review` Slack post.
```bash
gh pr list --repo <repo> --head "$BRANCH" --state open --json number,title,url,reviewDecision
```
Cross-check `~/.claude/state/pr-review-posted.jsonl` for the URL. Missing → outstanding. Do **not** post to Slack from this skill without asking; see [[pr-review-gaps]] for the gated posting flow.

**e. sswt workspaces that are now empty or whose PR merged.** Verify emptiness *before* proposing anything:
```bash
git -C "$WT" status --porcelain          # must be empty
git -C "$WT" log --oneline origin/main..HEAD   # must be empty
```
Only if both are empty (and any PR is merged/closed) may you *propose* deletion. `workspaces_delete` is marked destructive by the MCP server for good reason — **never call it without explicit user confirmation**, and always show the two command outputs as the evidence for why you think it's safe.

**f. Stale `pr-review-posted.jsonl` entries.** The user's `CLAUDE.md` says to remove an entry once the PR's branch/worktree is gone. For each line in `~/.claude/state/pr-review-posted.jsonl`, check whether the branch still resolves and whether a workspace still exists; list the dead ones for removal (ask first — it's user state).

**g. Durable learnings not yet written to memory.** Derive the project memory directory rather than hardcoding it (this skill runs in any repo):
```bash
MEM="$HOME/.claude/projects/$(pwd | sed 's:/:-:g')/memory"
ls "$MEM"
```
If the session produced a durable, reusable fact — a gotcha, a corrected assumption, a convention — and it isn't in there, that's an outstanding item.

### 3. File follow-ups in Linear

Follow [[linear-ticket-gen]] for access, ID resolution, and the state/cycle defaults (including the Triage trap). If Linear MCP tools aren't authenticated, that skill owns the fallback — don't re-derive it here.

**Duplicate-check FIRST, before creating anything.** Run `mcp__claude_ai_Linear__list_issues` with a keyword query built from the finding's core terms, and read descriptions, not just titles. If an overlapping ticket exists, **comment on it** (`mcp__claude_ai_Linear__save_comment`) instead of filing a near-duplicate. Creating a second ticket for work someone already tracked is a worse outcome than a slightly-off comment.

Rules for what you file:

- **Assign every ticket to the user.** `jake@concentro.io`, assignee id `20d3377a-786b-41e2-8308-3c7e1c07df2e`.
- **Bundle by change, not by symptom.** Three findings that all get fixed by one edit and one deploy are **one ticket** — filing three means three restarts of the same work. Genuinely independent work gets its own ticket. Ask yourself: "would fixing these ship together?"
- **Bodies carry the evidence, not a summary of it.** `file.ts:142` with the offending lines quoted; the verbatim error string; the measured number ("p99 3.4s across 812 requests, 2026-07-28"); the log excerpt; the PR/commit SHA. Someone picking this up cold in three weeks must not have to re-derive anything you already knew. "Investigate the slow query in the files route" is a failed ticket; the query, its plan, and its timing is a real one.
- **Observability gaps get the `telemetry` label**, id `df76fea0-b81f-4c48-8068-c0f3a50271d0`. The separate `datadog` label exists for a different purpose — do not reach for it because the finding came from Datadog. Confirm label ids with `mcp__claude_ai_Linear__list_issue_labels` if anything looks off.
- **If a ticket corrects an earlier claim, say so explicitly and date it.** e.g. "Correction (2026-07-30): the 2026-07-28 comment on CON-3271 said the rows were nulled by the migration. They were not — verified against prod, 19 rows still carry the old value."
- **Never fabricate ticket content.** If you can't produce the evidence for a finding — the line number is gone, the log has rotated, the number was a guess — write *"evidence not captured; re-derive by <specific step>"* in the ticket. Inventing a plausible file:line is worse than admitting the gap, because it reads as verified.

Create/update via `mcp__claude_ai_Linear__save_issue`. Record every identifier and URL you touched — step 5 reports them.

### 4. Write what still needs the human to IN_PROGRESS.md

Collect what this skill **cannot** close out itself:

- **Decisions only the user can make** — the unanswered questions from step 1, restated as decisions with the options.
- **Prod changes awaiting authorisation** — anything you deliberately did not run.
- **PRs awaiting approval** — open, reviewed or not, waiting on a human.
- **Destructive cleanups you proposed but did not perform** — workspace deletions, temp-file removals, state-file edits.
- **Blocked work and deferred promises from step 1's table.**

None of this blocks the close on its own anymore — it gets written to `IN_PROGRESS.md` instead, at the root of whichever repo it's about (usually just the current repo; if step 2's worktree sweep touched others with their own open items, each gets its own file). Writing it down is what makes closing safe: nothing is lost, because a future session — or you, next week — opens the repo and finds exactly where things stood.

**This is a running document, not a snapshot — reconcile, don't overwrite.** Before writing:
1. Read the existing `IN_PROGRESS.md` if one is present, and note its `_Last updated:_` date — that becomes the "previous entry" date in the resolved-since line.
2. Check off or remove anything it lists that got resolved this session (say so in the chat pointer — "resolved 2 items from a prior IN_PROGRESS.md").
3. Keep anything still open that this session didn't touch — a previous session's unresolved item is not yours to drop just because you didn't get to it.
4. Append this session's new items.
5. Get today's actual date from the system clock (e.g. `date +%F`) — never reuse the file's previous date and never guess or infer it from conversation context. Set the `_Last updated:_` line to that date on every write, even if nothing else changed.
6. Get this session's id from the `$CLAUDE_CODE_SESSION_ID` env var (e.g. `echo $CLAUDE_CODE_SESSION_ID`) and append a line to `## Close-out sessions` — date + session id. Append, never overwrite: this list is the full history of every session that has reconciled this file, and it's how a future session finds the `claude --resume <id>` (or Superset agent) that did the work described above.
7. Write the merged result back.

Suggested shape:

```markdown
# In Progress

_Last updated: 2026-08-03 (close-out)_

## Decisions needed
- [ ] Retention: keep 30-day default or match the customer's ask of 90? (asked 2026-08-01, still open)

## Blocked
- [ ] Sync script — blocked on a third-party credential rotation, see #1142

## Awaiting external action
- [ ] PR #1121 awaiting review approval — https://github.com/...
- [ ] Prod migration for TICKET-1234 awaiting authorisation to run

## Deferred
- [ ] Revisit the shared write-path guard once TICKET-1200 lands

## Close-out sessions
- 2026-08-01 — 3f9a1c2e-...
- 2026-08-03 — 939449f5-...
```

In the chat, do **not** reprint this list — point at it: *"N items written to `IN_PROGRESS.md` — see the file for details."* Restating the full text in the transcript defeats the purpose; the file is the durable copy, the chat is not.

### 5. The confirmation banner

**The gate is now about danger and mechanics, not decisions.** Print the success banner only if **every one** of these is true:

1. Every item from step 1 is accounted for: filed in Linear, fixed in place (retracted claims), or written into `IN_PROGRESS.md`. None exists **only** in this transcript.
2. Every retracted claim's correction was **verified in the durable artifact**, not just in chat.
3. Step 2 found nothing **this session** left behind that it could have resolved itself: no background process it started and should have stopped, no temp script it wrote into a repo, no uncommitted or unpushed work in a worktree it touched, no PR it opened that's missing its review/announcement, no state entry it made stale. Pre-existing debris the session never touched is reported, not gated on.
4. Every follow-up from step 3 is actually filed or commented — you have the identifiers.
5. Step 4's items are all **written into `IN_PROGRESS.md` and the file is saved** — not just described in chat. The list no longer has to be *empty* to pass; it has to be *durable*.

**What still hard-blocks:** condition 3 — things this session itself caused and left in a state that costs money, leaks a secret, or would strand work if the thread closes right now. Those are fixable *tonight*, by you, and closing without fixing them is the false all-clear this skill exists to prevent.

**What no longer blocks:** an unmade decision, an unauthorized prod change, a PR awaiting someone else's approval — anything that was never yours to resolve. Those move from "block the thread" to "write it down" once step 4 runs. A ticket filed in Linear was already treated this way (future work tracked there isn't an orphan); `IN_PROGRESS.md` extends the same logic to the human-judgment items that don't fit a ticket.

If all five hold, print the full findings from steps 1–3 first, in order, below the start marker, then a one-line pointer to `IN_PROGRESS.md` (path + item count) — then close with this banner **last**, after all of it, not instead of it:

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║        ███  SESSION WRAPPED — SAFE TO CLOSE  ███                 ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

  Scroll up to the CLOSE-OUT SWEEP marker for the full rundown. Recap:

  Tickets filed/updated : CON-1234 (new), CON-1200 (commented)
  PRs                   : #1118 merged, #1121 open (awaiting approval — see below)
  Worktrees             : 4 checked, all clean and pushed
  Background tasks      : 2 stopped, 0 running
  Memory                : 1 learning written (project_foo_gotcha.md)
  Temp files            : none left in any repo
  Open items            : 3 written to IN_PROGRESS.md (api/IN_PROGRESS.md) — nothing lost on close
```

If **anything** in condition 3 (or 2 or 4) fails, report the full findings from steps 1–3 first, in the body of the response, in order, below the start marker — every path, PID, SHA, and ticket id, exactly as those steps produced them. The box goes **last, after that detail, not instead of it** — it's a closing marker, not a substitute summary:

```
████████████████████████████████████████████████████████████████
█                                                              █
█             NOT CLEAN — DO NOT CLOSE THIS THREAD             █
█                                                              █
████████████████████████████████████████████████████████████████

  Scroll up to the CLOSE-OUT SWEEP marker for the full evidence. This session left something behind it can still fix:

  1. <one line: the dangerous or mechanical thing itself, not the evidence behind it>
  2. ...
```

This box now holds **only** condition-3-style items — things the assistant caused and can still resolve, plus any retracted claim not yet verified or step-3 follow-up not yet filed. Pending decisions and other human-only items do **not** belong in this box anymore; they belong in `IN_PROGRESS.md` regardless of whether the banner is success or NOT CLEAN. Each line must be phrased as something actionable right now ("stop process 41213 or confirm it should keep running", "delete the temp script at scripts/tmp/dump-prod.ts or move it out of the repo", "push the 3 unpushed commits in the woodrow worktree") — if a finding can't be compressed to that, it isn't ready for the box; go resolve it further.

Never soften a partial result into the success banner, never print both, and never print the success banner "except for one small thing." One small thing left in condition 3 is a NOT CLEAN — but a long `IN_PROGRESS.md` is not "one small thing," it's the mechanism working as designed.

## Common mistakes

- **Printing the banner because the sweep was tidy rather than because every gate condition holds.** Condition 3 (session-caused, still-dangerous debris) has no partial credit. Walk it literally.
- **Dumping the full open-questions list into the chat instead of `IN_PROGRESS.md`.** That's the exact failure this redesign fixes — a list that only exists in the transcript is gone the moment the thread closes. Write it to the file; point at it in chat.
- **Overwriting `IN_PROGRESS.md` wholesale.** It's a running document across sessions, not a snapshot from this one. Read it first, keep what's still open, note what got resolved, then merge in the new. Blind-overwriting silently drops another session's unresolved item.
- **Putting a pending decision or an awaiting-approval PR into the NOT CLEAN box.** Those aren't condition-3 material anymore — they belong in `IN_PROGRESS.md` regardless of which banner prints.
- **Treating a long `IN_PROGRESS.md` as reason to hold the banner back.** The banner's job is to confirm the machine is safe and nothing's lost, not that there's nothing left to think about. A well-populated file with real, actionable entries is success, not partial credit.
- **Reading only the recent context in step 1.** The items most likely to be orphaned are the ones from hours ago that got buried under later work — that's precisely why they're orphaned.
- **Trusting a mid-session correction.** You said "actually that's wrong" in chat; the Linear comment still says the wrong thing. Open the artifact and look.
- **Filing five tickets for one deploy.** Bundle by change. Five tickets means five context reloads for the same fix.
- **Writing summary-grade tickets.** "Look into the flaky test" costs the future reader everything you already knew. Paste the failure output.
- **Inventing evidence to make a ticket look complete.** A fabricated `file.ts:88` will be trusted and will waste someone's afternoon. Write "evidence not captured" instead.
- **Deleting a workspace, branch, or file because it "looked empty."** Verify with the two commands, show the output, then ask. `workspaces_delete` is irreversible.
- **`cat`-ing a temp script that touches prod credentials into the transcript** to decide whether it matters. The path and a `grep -l` hit are enough to know it matters.
- **Hardcoding the memory path.** It's derived from cwd — this skill runs in any repo.
- **Only running `git worktree list` from the current repo.** sswt worktrees and the other root checkout both get missed. Take the union.
- **Gating the banner on a parked worktree the session never touched.** That's someone's deliberately-parked work, not this session's debris — sweep it wide, report it, but gate narrow. A NOT CLEAN that can never clear is as useless as a false all-clear.
- **Auto-posting to `#pr-review` during the sweep.** Report the gap; the posting flow is gated on confirmation ([[pr-review-gaps]]).
- **Creating a duplicate Linear ticket because the search was title-only.** Read descriptions; comment on the existing issue when it overlaps.
- **Re-pasting evidence into the closing box (either outcome).** The box is a marker at the bottom pointing back at the detail above, not a second copy of it — condense to one line per item.
- **Skipping the start marker, or burying it after some findings already printed.** It only works as a scroll-back anchor if it's the very first thing in the report, before step 1's output.

## Why this exists

The cost of an orphaned item isn't the item — it's that nobody knows it exists. A bug found and not filed is worse than a bug never found, because the session's cost was paid and the value was thrown away on close. A background job left running burns money invisibly. A temp script with a prod connection string sitting untracked in a repo is one `git add -A` away from being a real incident.

Closing a long thread is exactly when all of that gets dropped, because the interesting work is over and the remaining work is bookkeeping. This skill makes the bookkeeping mechanical and — crucially — makes "everything is fine" something that has to be *earned* against a checklist rather than *felt* at the end of a productive day.

The earlier version of this skill earned that trust by blocking the close on anything unresolved, including things only the user could ever decide — which meant a pile of open questions could hold a thread open indefinitely for no reason other than that nobody had re-typed them somewhere durable. The fix isn't to lower the bar; it's to recognize that a decision written into `IN_PROGRESS.md` is exactly as safe as a ticket filed in Linear — durable, findable, not lost when the thread closes. What still has to be *resolved* before closing is narrower and sharper now: only the things this session itself broke and could still fix tonight.
