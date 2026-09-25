---
name: wrapup-repos
description: Wrap up in-progress work in one local repo under ~/Dropbox/code — pick the dirtiest/most-recently-touched repo, finish obvious low-risk loose ends, run the quick verify, commit on the current branch, and record decisions, ticket candidates, and code-state notes in the repo's `.claude/IN_PROGRESS.md`. Use on-demand ("/wrapup-repos", "wrap up my repos", "tidy my in-progress work") or via the scheduled off-peak launchd job. Safe by design: never pushes, never force/destructive, never commits secrets or build junk.
---

# Wrap up in-progress repo work

Advance ONE local repo's in-progress work to a clean, committed, well-documented state, and leave
the user a short list of decisions to make when they return. The user's time is the scarce
resource: do the obvious, low-risk implementation work; hand back only the judgment calls.

Candidate repos are the direct children of `/Users/jake/Dropbox/code`.

## Two ways this runs
- **On-demand (interactive):** the user invoked you directly. If they named a specific repo or
  asked for something particular, honor that over the auto-selection below. Otherwise auto-select.
  You may briefly confirm your pick if the user is clearly present.
- **Scheduled (headless / unattended):** you were launched by the off-peak launchd job with no
  human watching. Be conservative, follow the safety rules to the letter, and leave great notes.

## Hard safety rules (never violate)

- NEVER `git push`, open a PR, or contact any remote — Linear included. All work stays local;
  larger work is written down as a ticket candidate for a later interactive session to file.
- NEVER use `--force`, `git reset --hard`, `git clean -fd`, or `git checkout -- <file>` to discard
  the user's uncommitted work, or delete branches/worktrees/files the user created.
- NEVER commit secrets or junk: skip anything that looks like credentials (`.env`, `*.pem`,
  tokens, API keys) or build output / large generated files (`node_modules/`, `.next/`, `dist/`,
  `build/`, `*.log`, caches). Respect `.gitignore`. If dirty files look like they should NOT be
  committed, leave them unstaged and note it under `## Code state notes` in `.claude/IN_PROGRESS.md`.
- NEVER `git add` or commit `.claude/IN_PROGRESS.md` — it's local session-continuity state. If the
  repo's `.gitignore` doesn't already exclude it (a `.claude/` or `.claude/*` entry also counts),
  add a `.claude/IN_PROGRESS.md` line; that `.gitignore` edit may ride in the Step 4 commit.
- Touch ONLY the single repo you select. Never modify files outside it.
- SKIP any repo that fails the shared preflight: run `~/dotfiles/bin/git-safe-to-autocommit <repo>`
  and pick a different repo on a non-zero exit. It refuses repos mid-rebase/merge/cherry-pick/revert/
  bisect and repos with a detached HEAD (committing there orphans the work onto no branch). This
  script is the single source of truth, shared with the dotfiles auto-sync shell hook — don't
  reimplement the check inline here; a divergent inlined copy is what let the hook commit on top of
  a stuck rebase for two weeks unnoticed.
- SKIP any repo with a **live** session doc: if any `.claude-sessions/*.md` at the repo root has
  `updated_at_epoch` within the last 15 minutes, someone is actively working there right now — do
  not touch it, pick a different repo. (These docs are written by this machine's session-doc hooks;
  see `hooks/README.md`. A doc that's present but NOT within that window is a different case — see
  Step 2, it's signal, not a skip.)
- If a change isn't OBVIOUS and LOW-RISK, DON'T make it — write it up as a decision instead.
- If there's nothing meaningful to do, say so briefly and stop. Never manufacture churn or make
  cosmetic commits just to have done something.

## Step 1 — Select ONE repo

First build the **eligible** set: every git repo directly under `/Users/jake/Dropbox/code`, MINUS
any repo the user has disabled. A repo is disabled (skip it entirely) if EITHER:
- its name matches an uncommented line in `/Users/jake/Dropbox/code/.wrapup-ignore` (one
  name or glob per line; `#` starts a comment; leading/trailing whitespace ignored), OR
- a `.nowrapup` file exists at the repo root (portable per-repo opt-out that travels with the repo).

If `.wrapup-ignore` doesn't exist, treat it as empty (nothing disabled). Never create, modify, or
commit `.wrapup-ignore` or `.nowrapup` files — they are the user's toggles.

Then, for each ELIGIBLE repo:
1. `dirty` = line count of `git -C <repo> status --porcelain`.
2. `recency` = the most recent of: last commit time (`git -C <repo> log -1 --format=%ct`) and the
   newest modification time among working-tree files (ignore `.git`, `node_modules`, other
   ignored paths, and — always, even where not gitignored — `.claude/IN_PROGRESS.md` and
   `.claude-sessions/`: this skill and the session hooks write those, so counting them would make
   every repo it touched look "recently worked on" forever). NOTE: a repo can have an old last-commit date but very recent file edits — the
   file mtimes are what matter for "recently worked on."

Candidate set = repos whose `recency` is within the last **14 days**. Among candidates:
- Prefer repos with uncommitted changes (`dirty > 0`), MOST DIRTY FIRST.
- If no candidate is dirty, pick the single most recently modified candidate.
- Ties may be broken arbitrarily — you do not need to be deterministic across runs.

Select exactly ONE repo. (You MAY do a second only if the first finishes fast and you have clear
time budget — otherwise stop at one.) If NO repo was modified in the last 14 days, append a
one-line timestamped note to `/Users/jake/Dropbox/code/.wrapup-idle.log` and stop.

## Step 2 — Understand the in-progress work AND the direction

In the selected repo:
- Read its own `CLAUDE.md`, `README*`, `package.json` scripts, and any existing
  `.claude/IN_PROGRESS.md` to load project-local conventions, intent, and what's already open. Project-local instructions OVERRIDE this skill's
  general guidance.
- Run `git log -n 20 --oneline`, `git status`, `git diff`, and `git diff --staged` to understand
  what was left mid-flight.
- Actively hunt for signal about where the project is headed, not just what's broken right now.
  Check, if present: `ROADMAP.md`, `PLAN.md`, `TODO.md`, `docs/*.md` (design docs, ADRs), a
  "Future work" / "Next steps" / "Roadmap" section in `README*` or `CLAUDE.md`, and `TODO`/`FIXME`
  comments touched by the recent diff. Also read the diff itself for architectural intent: a new
  abstraction introduced but not yet used elsewhere, a stubbed function, a half-wired
  integration — these imply a direction even when no doc states it.
- Check `.claude-sessions/*.md` at the repo root too. Since the hard safety rule above already
  skipped this repo if one was live, any file you find here belongs to a session that ended
  *without* a clean exit — a crash, a `kill -9`, a closed lid — so it's the only record of what
  that session was actually trying to do; a cleanly-closed session's doc is deleted immediately and
  leaves nothing to find. Read its body as directional signal, same tier as `ROADMAP.md`/`PLAN.md`,
  and note its `session_id` (from the frontmatter) if it meaningfully shaped Step 5's decisions.
- This scan feeds Step 5's decisions section — the goal is to leave the user forward-looking
  choices, not only a list of what stalled.

## Step 3 — Wrap up obvious, low-risk work only

Do the finishing work the user would find tedious but uncontroversial:
- Complete half-finished edits that have a single obvious intended endpoint.
- Fix clear errors (typos, obvious type/compile/lint errors, a broken import).
- Remove stray debug output / commented-out scratch code the user clearly left behind.
- If the project has a quick verify command, run it and fix what's trivially broken:
  - This repo family treats `npm run build` as the typecheck. Also try `npm run lint`, `npm test`
    if they exist and are fast. Prefer already-installed deps; don't add heavy new dependencies or
    start long-running dev servers.

Do NOT undertake large/ambiguous refactors, API redesigns, broad renames, or product/UX choices.
Those are DECISIONS — capture them in Step 5, don't act on them.

## Step 4 — Commit (on the current branch)

If you made changes and there is anything sensible to commit:
- Stage only the intentional source changes (honor the secret/junk exclusions above).
- Commit to the CURRENT branch — allowed even if it's `main`/`master`, per the user's explicit
  choice. Use a message that marks it automated, e.g. `wip(auto): <summary>` or
  `chore(auto): <summary>`.
- One focused commit is fine; split into a few if logically distinct. Do NOT push.

## Step 5 — Reconcile into `.claude/IN_PROGRESS.md` (the deliverable)

Write into the selected repo's `.claude/IN_PROGRESS.md` — the same running file [[close-out]]
owns, [[pick-up]] resumes from, the `pick-up-nudge` SessionStart hook surfaces, and
[[prepare-roadmap]] sweeps for open decisions. That is how this run's output reaches the user:
there is no separate report file.

**Follow close-out's step 4 procedure for the reconcile — don't reinvent it:** read the existing
file first; delete items that are resolved (remove the line, never leave `- [x]`); drop anything
now tracked in Linear or an open PR; keep every other session's still-open item; set
`_Last updated: <date +%F> (wrapup-repos)_` from the system clock; append
`- <date> — $CLAUDE_CODE_SESSION_ID (wrapup-repos)` to `## Close-out sessions`; create `.claude/`
if needed. Never overwrite the file wholesale.

What this skill contributes, by section:

- **`## Decisions needed`** — the most important section, and the heading [[prepare-roadmap]]
  parses, so keep it spelled exactly so. Each item is an unchecked `- [ ]` line framed as a
  concrete choice with options, never an open question, e.g. "Storage: (a) keep localStorage, or
  (b) move to cookie — left as (a)." Don't limit it to what was *necessary* to unblock what
  stalled; the point is to advance the project's thinking. Order:
  - **Architectural / roadmap decisions first.** If Step 2 found a roadmap, plan doc, or "Future
    work" section, formalize its loose bullets as sequenced, concrete choices with tradeoffs, not
    restated TODOs. When an item is about a specific `ROADMAP.md` / `docs/plans/` item, name that
    item so prepare-roadmap can record the answer (`**Decided:**`) in the right place. If no doc
    exists but the diff implies a direction (new abstraction, stub, half-wired integration),
    surface the choice that direction is heading toward. If there's no directional signal, add
    nothing — don't invent one.
  - **Then judgment calls skipped this run** — anything you deliberately didn't do because it
    needed judgment.
- **`## Ticket candidates`** — larger work that deserves a Linear ticket but isn't a decision.
  One item each: a title, a line or two of what/why, the suggested `repo/<dir>` label, and file
  pointers. Never file it yourself (see the safety rules); [[close-out]] files these and removes
  them from the file.
- **`## Code state notes`** — ad-hoc, temporary notes about the tree: dirty files left unstaged
  and why, a failing verify command with its key error, something half-wired you left alone.
  Free-form; prune any that are no longer true.

The run's own report — what you changed, commit hash(es), build result, why this repo was picked
(including any live-session skip or crashed session's `session_id` that informed the pick) —
goes in the final output below and the commit messages, **not** in the file. The one exception is
a failing verify, which stays visible as a code-state note until it's fixed.

## Final output

End with a 3–6 line plain-text summary: which repo you picked and why, what you changed, the commit
hash (if any), build status, and a pointer to `.claude/IN_PROGRESS.md` with item counts per
section (e.g. "2 decisions, 1 ticket candidate, 1 code-state note"). In a scheduled run this goes to the run log the user scans later.
