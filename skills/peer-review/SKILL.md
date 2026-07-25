---
name: peer-review
description: Use when the user wants a Codex code review run against the current branch (or uncommitted changes), mechanical findings applied as edits, and — by default — the branch pushed and the PR notified with the round count once review comes back clean; pass "local" for the original no-push, no-PR-contact behavior. Trigger phrases include "peer review", "run codex on this", "self-review before I push", "codex my changes", "do a local review first", "keep reviewing until clean", "cycle codex locally", "review and push when clean", "local review only".
---

# Peer Review

## Overview

Run `codex review` against the current branch's diff, triage its findings, apply the mechanical fixes, and commit. Two modes, chosen by the optional `local` argument (step 1):

- **Default (push mode):** once the review comes back clean (or the `/loop` ceiling is hit) with no unresolved P1/P2 findings, push the branch and — if a PR already exists for it — post a comment reporting how many local review rounds ran before this push, plus the model and reasoning effort that ran them. Gives human reviewers a signal for how much ground Codex already covered before they open the diff, so the GitHub Codex bot on the PR (and any human) has less to flag.
- **`local` mode:** the original behavior — review → fix → commit, never push, never touch `gh`. Use this for a review pass with zero PR-side effects (e.g. reviewing someone else's branch, or you're not ready for this to be visible).

Composes with `/loop` for iterative convergence in either mode.

For addressing comments **already posted** on a GitHub PR by a bot reviewer, see [[babysit-pr]] instead — this skill only runs fresh local Codex passes. For driving Codex on GitHub itself (not a local pass), see [[cycle-review-pr]].

## How to invoke

Single pass, default (push) mode:
```
/peer-review
```

Single pass, local-only (no push, no `gh` calls):
```
/peer-review local
```

Iterative until clean (recommended for non-trivial diffs) — **this is how you cycle** through multiple review→fix→review rounds; step 7 owns the loop-continue decision and the anti-loop ceiling. Works in either mode:
```
/loop /peer-review
/loop /peer-review local
```

Scope hints combine with the mode keyword in any order, e.g. `/peer-review local uncommitted` or `/peer-review base=develop`. Auto-detect scope otherwise (see step 1).

## When NOT to use

- Diff is empty (nothing changed vs base, no uncommitted work) — exit immediately, tell the user there's nothing to review.
- User wants to address comments **already posted** on a GitHub PR — that's [[babysit-pr]], not this.
- User explicitly asked for a different reviewer (Claude itself, CodeRabbit, etc.) — this skill is Codex-specific.
- `codex` CLI not installed (`command -v codex` empty) — surface that to the user with the install path; don't try to substitute.

## One iteration

### 0. Check for a round already in progress

A full round (`codex review` → triage → apply → commit) can take 5–10+ minutes. If another `/peer-review` invocation — a different session, another agent, or the user working the same branch by hand — is mid-round, starting a second review before that round's fixes are committed reviews a moving target and produces stale, duplicate findings (a finding gets reported that the other round already fixed). Reviews must not begin until the previous round is fully committed or concluded. Guard with a lock file scoped to this worktree:

```bash
GIT_DIR=$(git rev-parse --git-dir)
LOCK="$GIT_DIR/peer-review.lock"
if [ -f "$LOCK" ]; then
  age=$(( $(date +%s) - $(cut -d' ' -f1 "$LOCK") ))
  if [ "$age" -lt 1200 ]; then
    cat "$LOCK"   # a live round holds the lock — do not start a new review
  else
    rm -f "$LOCK"  # stale (>20min) — previous round crashed; self-heal
  fi
fi
```

- **Lock held (age < 20min):** don't start a review this iteration.
  - Standalone (`/peer-review`): tell the user a round is already in progress in this worktree (report the lock's age and contents) and stop.
  - Under `/loop`: skip straight to `ScheduleWakeup(90s)` with reason `"waiting for in-progress peer-review round to finish"`, no review run. This does not count toward the anti-loop no-progress ceiling — you didn't run a review, so there's nothing to have converged or not.
- **Lock free (or just cleared as stale):** claim it before invoking codex, recording the HEAD you're reviewing against:
  ```bash
  echo "$(date +%s) pid=$$ head=$(git rev-parse --short HEAD)" > "$LOCK"
  ```
- **Release the lock as the last action of every iteration**, unconditionally, on every exit path — after step 5/6's commit-or-report, or immediately if you exit early (codex failed, nothing to review, lock was held by someone else). `rm -f "$LOCK"` before ending the turn. This only removes the lock — the round counter (step 2) is a separate file and survives.

### 1. Determine scope and mode

Parse the invocation for two independent things — order doesn't matter, either/both/neither may be present:

- **Mode:** `local` keyword present → local mode (never push, never call `gh`). Absent → default push mode. Carry this forward; it gates step 8.
- **Scope hint:** pick exactly one mode based on repo state (and any user hint):

| State | Scope | Command |
|---|---|---|
| User said `uncommitted` OR branch is `main`/`master` with dirty tree | uncommitted | `codex review --uncommitted` |
| User said `base=X` | branch vs X | `codex review --base X` |
| Current branch != main/master AND clean tree | branch vs main | `codex review --base main` (or `master` if that's the default) |
| Clean tree on main/master | nothing to review | exit |

Detect the default branch with `git symbolic-ref refs/remotes/origin/HEAD --short 2>/dev/null | sed 's@^origin/@@'`, falling back to `main`. Remember this value — step 8 needs it to refuse to push the default branch.

If both committed-branch-changes AND uncommitted changes exist, ask the user which scope they want — don't guess.

### 2. Run the review

**First round on a branch: build a focus prompt before invoking Codex.** A bare `codex review` reports its top-confidence findings, not an exhaustive sweep — each new diff re-rolls its attention, so on a cross-cutting change it peels one layer per round and a review that should converge in 1–2 rounds takes 5+. `codex review` accepts custom instructions as a positional `[PROMPT]` argument; use it. **Gotcha: `--base <branch>` cannot be combined with `[PROMPT]`** — despite the usage string advertising both, the CLI errors out (`the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`, verified on codex 2026-07). When passing a focus prompt, drop the `--base` flag and state the diff scope inside the prompt instead ("Review the changes on this branch relative to the merge-base with origin/<base>. …") — verified to scope the diff identically to `--base`. `--uncommitted` composes with `[PROMPT]` normally. Before the first round:

1. **Enumerate the change's interaction matrix from the diff.** Which axes does it cut across? Typical axes: data types / value formats (enum, array, date, percent…), component or editor variants that share the behavior, permission/visibility states (unauthorized, masked, loading, read-only), and host contexts (inside buttons, tables, dashboards). A change that mirrors display state or adds a new affordance to existing values usually spans several.
2. **Self-check the predictable cells yourself, first** — especially the security-shaped ones: *does the new affordance respect value masking / authorization in every state?* Fix what you find before spending a review round discovering it.
3. **Persist the matrix as a focus prompt** so every `/loop` round reuses it:
   ```bash
   echo "verify copy-vs-display parity for every data type; check masked/unauthorized/loading/read-only states; check all editor variants and host contexts" > "$GIT_DIR/peer-review-focus"
   ```
   (That's an example — write the actual axes you enumerated.) Like the rounds file, this persists across iterations and is never cleaned up per-round.

Then run every round with the focus appended — remember `--base` and a prompt are mutually exclusive, so the base goes inside the prompt:

```bash
FOCUS=$(cat "$GIT_DIR/peer-review-focus" 2>/dev/null)
if [ -n "$FOCUS" ]; then
  codex review "Review the changes on this branch relative to the merge-base with origin/$BASE. Focus especially on: $FOCUS. Also report anything else you find." 2>&1 | tee /tmp/peer-review-$$.txt
else
  codex review --base "$BASE" 2>&1 | tee /tmp/peer-review-$$.txt
fi
```

(Or `--uncommitted` per step 1.) Pipe through `tee` so you have a stable transcript to refer to while planning — Codex's output can be long and you don't want to re-read it from your scrollback.

**Gotcha — the Bash tool kills long `codex review` calls at ~2 minutes regardless of timeout settings.** Leaving the timeout at default, passing it explicitly (even up to 590000ms), or adding `run_in_background: true` does not protect the call — the tool invocation itself gets killed at the ~2-minute mark, even though the underlying `codex` process would otherwise keep running to completion. Reconfirmed twice in the same session on a larger diff, so don't re-litigate it by trying `timeout`/`run_in_background` again — go straight to detach-and-poll:

```bash
# Same --base/[PROMPT] exclusivity applies here: with a focus prompt, name
# the base inside the prompt; without one, use --base.
if [ -n "$FOCUS" ]; then
  nohup codex review "Review the changes on this branch relative to the merge-base with origin/$BASE. Focus especially on: $FOCUS. Also report anything else you find." > /tmp/peer-review-$$.txt 2>&1 &
else
  nohup codex review --base "$BASE" > /tmp/peer-review-$$.txt 2>&1 &
fi
CODEX_PID=$!
```

Then wait on it with the Monitor tool, not a blocking Bash polling loop — that loop also gets killed at 2 minutes even though the detached process underneath survives:
```bash
until ! ps -p $CODEX_PID >/dev/null 2>&1; do sleep 5; done; echo done
```
Once Monitor reports completion, read `/tmp/peer-review-$$.txt` for the review output and continue at step 3.

**Optional — parallel lenses for unusually wide diffs.** If the matrix spans 3+ axes, round 1 may run 2–3 *concurrent* `codex review` invocations, each with a different lens prompt (e.g. markup/a11y; permission and masking leaks; formatting/display parity), then triage the **union** of findings in step 3. This still counts as one round — one lock, one triage, one apply pass, one commit, one increment of the rounds file. It trades tokens for wall-clock rounds; use it when the alternative is predictably serial rounds each finding a different lens's issues.

If `codex` exits non-zero, release the lock (`rm -f "$LOCK"`), surface the stderr verbatim to the user, and stop. Common causes: not logged in (`codex login`), config error, network. Don't try to work around.

On a successful run (regardless of finding count), record the round:
```bash
GIT_DIR=$(git rev-parse --git-dir)
ROUNDS_FILE="$GIT_DIR/peer-review-rounds"
echo $(( $(cat "$ROUNDS_FILE" 2>/dev/null || echo 0) + 1 )) > "$ROUNDS_FILE"
```
This file is scoped to the worktree the same way the lock is, but it is never deleted between iterations — it's a running total for step 8, not a per-round marker.

### 3. Triage findings — DO NOT edit yet

Codex's review output is free-form prose, usually structured as numbered findings with severity hints (P1/P2/nit, or "Critical/Major/Minor", or just inline tone). Read the whole thing, then build a working list — one row per finding:

| # | severity | file:line | summary | action | reason |
|---|---|---|---|---|---|
| 1 | P1 | src/foo.ts:42 | null deref on `user.email` | apply | mechanical, clear fix |
| 2 | P2 | src/bar.ts:88 | rename `tmp` → `pendingUpload` | apply | naming nit, low risk |
| 3 | P1 | src/auth.ts:120 | session token TTL too long | **skip** | policy/security judgment — surface to user |
| 4 | nit | src/x.ts:5 | could use `?.` | **skip** | nitpick, not worth churn |
| 5 | P2 | docs/api.md:30 | stale example | apply | doc fix |

**Decision rules:**

| Situation | Action |
|---|---|
| Bug, null-safety, or correctness fix with a clear mechanical change | **apply** |
| Naming, formatting, or local refactor with clear intent and small blast radius | **apply** |
| Doc/comment fix that matches the code | **apply** |
| Suggestion requires architectural/security/policy judgment, or spans multiple files in non-obvious ways | **skip** — surface to user at end |
| Nit (style preference, ultra-minor) | **skip** by default; apply only if a cluster of nits exists in one file (batch cheap fixes) |
| Codex is wrong (you can verify by reading the file) | **skip**, note why |
| Finding is already fixed in the working tree (Codex reviewed a stale snapshot) | **skip**, note |

Treat Codex's confidence as a hint, not gospel. Read the actual file before applying anything — Codex sometimes hallucinates line numbers, variable names, or "current behavior" that's already different from what's on disk. Compare current `git rev-parse HEAD` against the `head=` value you recorded in the lock file when you claimed it (step 0) — if it moved, someone else committed while the review ran; some findings are likely already fixed under the "already fixed" rule above, so re-check every finding against the live file, not just the ones that look suspicious.

### 4. Apply the fixes

Apply every `apply` row from the working list, in file order (one file at a time, multiple edits per file batched). After each file, re-read it to confirm the edit landed cleanly. If an edit fails because the surrounding code doesn't match Codex's description, downgrade that row to `skip` and continue — don't force it.

**Do not** add comments referencing Codex, the review, or the finding number ("// per Codex review", "// addresses P1 #3"). The fix should be indistinguishable from any other commit; CLAUDE.md's no-noise-comments rule applies.

### 5. Commit (only if you applied fixes)

If at least one fix landed, commit. Follow the repo's commit conventions — check `CLAUDE.md` / `AGENTS.md` / recent `git log` first. Many repos require a ticket-ID suffix; reuse the ID from the branch name or the most recent commit on this branch.

```bash
git add -A && git commit -m "<short message following repo convention>"
```

Suggested message shape: `Address local Codex review feedback (CON-1234)` or similar, but defer to repo style.

If zero fixes were applied, do not create an empty commit.

Release the round lock now (`rm -f "$LOCK"`) — the round is committed or concluded, so the next invocation (this session's `/loop` re-entry or another session entirely) is clear to start.

### 6. Report

Always end with a short summary, even if no fixes landed:

```
Codex review summary
- Round: 3 (cumulative for this branch)
- Findings: 5 total (2 P1, 2 P2, 1 nit)
- Applied: 3 (src/foo.ts:42, src/bar.ts:88, docs/api.md:30)
- Skipped: 2
  - src/auth.ts:120 (P1, session TTL) — needs policy decision: <one-line summary>
  - src/x.ts:5 (nit) — too minor
- Committed: yes, <sha>
```

Surface skipped P1/P2 items as a bulleted list with one-line context each — they gate step 8 below.

### 7. Decide whether to continue (only under `/loop`)

If invoked under `/loop`:

| State | Next |
|---|---|
| Applied ≥1 fix this iteration | `ScheduleWakeup(60s)` — re-run review to confirm convergence or catch second-order issues |
| Zero fixes this iteration AND zero P1/P2 findings | omit `ScheduleWakeup` — clean, exit `/loop`, proceed to step 8 |
| Zero fixes this iteration AND skipped P1/P2 findings remain | omit `ScheduleWakeup` — the remaining work needs the user; exit `/loop`, **do not** proceed to step 8 |
| Three consecutive iterations with no progress (same findings keep coming back) | omit `ScheduleWakeup`, escalate to user — Codex disagrees with itself or you're misreading it; **do not** proceed to step 8 |
| Hit the 5-iteration hard ceiling | exit `/loop`; proceed to step 8 only if no unresolved P1/P2 findings remain, otherwise escalate to user |

State the phase in `ScheduleWakeup.reason`, e.g. `"re-running codex review to verify 3 fixes converged"`.

If not invoked under `/loop`, step 6's report is the end of this single pass — proceed straight to step 8 (same gating: only if no unresolved P1/P2 findings remain).

### 8. Push and notify the PR (skipped entirely in `local` mode)

Runs once, at the point this session actually ends — a standalone pass finishing step 6, or `/loop` exiting via step 7. Never runs mid-loop, and never runs at all if `local` was passed in step 1.

**Guards — skip this step (report why, stop) if any of these hold:**
- Unresolved P1/P2 findings remain from step 3/6. Those need the user's judgment before this branch goes near reviewers.
- The current branch is the repo's default branch (the one detected in step 1). Never auto-push `main`/`master`.

Otherwise:

1. Push:
   ```bash
   BRANCH=$(git branch --show-current)
   if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
     git push
   else
     git push -u origin "$BRANCH"
   fi
   ```
   If the push is rejected (diverged, no permissions, etc.), surface the error verbatim and stop. Don't force-push; don't proceed to the PR comment — nothing landed remotely, so there's nothing to notify.

2. Check whether a PR exists for this branch:
   ```bash
   PR_NUM=$(gh pr view --json number -q .number 2>/dev/null)
   ```
   Empty/error → no PR yet. Report that the push succeeded and stop; there's nothing to notify (per the user's "if it exists"). This isn't a permanent skip, though — if a PR for this branch gets opened later in the *same session* (e.g. the user follows up with "now open a PR"), post the comment at that point. The round already happened before that push; the PR simply didn't exist yet when step 8 first ran.

3. Gather what to report:
   ```bash
   ROUNDS=$(cat "$GIT_DIR/peer-review-rounds" 2>/dev/null || echo 0)
   EFFORT="${CLAUDE_EFFORT:-default}"
   ```
   State the model you're running as from your own system context (there's no env var for it — e.g. "Claude Sonnet 5").

4. Post one comment (a fresh comment each time this step runs — not an edit-in-place). The body is two parts, in this order: the round-count/model line, then a blank line, then a short prose **summary** — the same substance you'd give the user directly in chat, not the raw step-6 table. Reference the specific fix(es) (file:line or a one-line description of the bug) and the commit SHA(s); if a round found nothing, say so plainly ("No issues found."). If step 8 runs after multiple `/loop` rounds, the summary covers the cumulative set of fixes across *all* rounds since the last push/notify, not just the final one — synthesize from every round's step-6 report you generated this session, not only the last.

   ```bash
   gh pr comment "$PR_NUM" --body "$(cat <<EOF
Local peer review: $ROUNDS round(s) of \`codex review\` completed before this push (reviewed with <model>, reasoning effort: $EFFORT).

<summary>
EOF
)"
   ```

   Example:
   ```
   Local peer review: 1 round of `codex review` completed before this push (reviewed with Claude Sonnet 5, reasoning effort: high).

   Found one real bug: `groupKey` used `??`, which only falls back on null/undefined — so items with `label: ""` (rather than `null`) all collapsed into a single group and got hidden as spurious duplicates of one another. Fixed by falling back with `||` instead, added a regression test for the empty-string case, and re-ran the full suite clean (committed as `abc1234de`).
   ```

The round counter (`$GIT_DIR/peer-review-rounds`) is cumulative for the life of this worktree and this step never resets it. If the branch gets more local review rounds later (new commits, another `/peer-review` pass, another push), the next comment reports the new running total — reviewers see the full history of local review on this PR, not just the latest session.

## Anti-loop safeguards

- **Hard ceiling.** Five iterations max under `/loop`. Even if Codex still has findings, stop, report, and skip step 8 if P1/P2 findings remain.
- **Repeated finding.** If the *same* `file:line + summary` finding appears for 3 iterations after you've "fixed" it, stop touching it — your fix isn't what Codex wants. Surface to user.
- **Cost guard.** Each `codex review` invocation is non-trivial. If the diff hasn't changed since the last review (`git diff $BASE...HEAD` digest matches), don't re-run — exit immediately.
- **Round lock.** Never launch `codex review` while another round's lock (step 0) is held — wait it out instead of reviewing a moving target. Waiting on the lock doesn't count toward the hard ceiling or the no-progress escalation above; it's not an iteration, it's a pause before one.

## Common mistakes

- **Running bare generic rounds on a cross-cutting change** — without a focus prompt, Codex surfaces one layer per round and burns the loop ceiling on findings that were predictable cells of the change's interaction matrix (data type × variant × permission state). Enumerate the matrix, self-check the security-shaped cells, and seed every round with the focus prompt (step 2).
- **Auto-applying every finding** — Codex's job is to be thorough; yours is to be selective. Skipping is fine and often correct.
- **Editing during step 3** — step 3 is planning only. Edits in step 4. Otherwise you'll forget which findings were applied vs. skipped when you write the summary.
- **Trusting Codex's line numbers blindly** — read the file first. Codex reviews diffs and can be off by a few lines or refer to code that was moved/deleted.
- **Adding "// per Codex" comments** — noise; the commit message captures the why, the code shouldn't.
- **Creating empty commits** — if no fixes applied, don't commit. Just report and exit.
- **Running on `main` without `--uncommitted`** — `codex review` with no scope on `main` will fail or review against itself. Always pick a scope in step 1.
- **Letting step 8 push the default branch** — check against the branch detected in step 1, every time; never assume.
- **Pushing (or notifying) with unresolved P1/P2 findings still outstanding** — step 8's first guard exists specifically to stop this. Skipped-but-unresolved findings need a human's eyes before reviewers get told "this is clean."
- **Notifying a PR that doesn't exist** — `gh pr view` returning empty means there's genuinely nothing to notify yet; that's not an error, just report the push and stop. But don't forget about it permanently — if a PR opens later in the same session, go back and post the comment then.
- **Posting the round-count line with no substance** — the comment must include a prose summary of what was actually found/fixed (or "No issues found"), not just the round/model/effort line. A bare round-count line makes reviewers ask "okay, but what did it find?"
- **Resetting the round counter** — it's cumulative on purpose. Don't zero `$GIT_DIR/peer-review-rounds` after a successful notify.
- **Forgetting `local` when the user wants the old no-push, no-`gh` behavior** — default mode now pushes and comments; `local` is the opt-out, not the other way around.
- **Skipping the summary** — even on a clean review, report it. The user invoked the skill expecting output.
- **Starting a review without checking the lock** — a round in flight elsewhere means your review's findings go stale mid-run. Check step 0 first, every iteration.
- **Leaving the lock held on exit** — an orphaned lock blocks every future round in this worktree until it ages out (20min). Release it on every path out of an iteration, not just the happy one.

## Why this exists

Codex's GitHub bot review on a PR is the same engine as `codex review` locally. Running it pre-push collapses the feedback loop from "push → wait for bot → babysit-pr → push fixes → wait again" to "review locally → apply → push once." Pushing and notifying automatically (default mode) closes the loop further: human reviewers see up front how many local Codex rounds already ran and what ran them, so they can calibrate how much of their own scrutiny to spend re-checking mechanical stuff Codex already caught. `local` mode keeps the original zero-side-effects behavior for cases where auto-pushing isn't wanted.
