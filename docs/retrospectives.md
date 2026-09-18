# Retrospectives

A **retrospective** is a pass over work that is already finished, looking for one named class of residue it left behind. This doc holds the field schema, the shared action tiers, and the registry of current members. `~/.claude/CLAUDE.md`'s `## Retrospectives` section points here rather than restating any of it.

## What counts as a retrospective

Three things together:

1. **It runs after the fact.** The work is done. A retro never changes what the work should have been — it only looks at what the work left.
2. **It hunts one named class of residue.** Not "review this." A retro that can't name what it's looking for produces noise, and the noise is what kills the habit.
3. **It produces a durable artifact**, or explicitly declines to. A note that dies with the transcript isn't a retro output; a ticket, a doc, a memory, or a committed change is.

**In-flight judgment heuristics are not retrospectives.** CLAUDE.md's `## Refactoring vs Patching: Feature Complexity Threshold` fires *during* work, produces a decision rather than an artifact, and has no destination. It is deliberately not a member of this class and should not be reshaped into one.

## The six fields

Every retro declares all six. The first answers *when to retro*; the fourth answers *when to make changes*.

| Field | What it fixes |
|---|---|
| **Trigger** | The event or cadence that starts it. An event (`session used Datadog tools`) or a schedule (`weekly`), never "when it seems useful." |
| **Corpus** | What it reads. Bound this tightly — an unbounded corpus is how a retro turns into a general audit. |
| **Detection** | What counts as a finding, including the evidence required and the noise threshold that suppresses the rest. |
| **Action** | What it may do with a finding, as one of the three tiers below. |
| **Destination** | Where output lands — session note, Linear ticket, repo doc, agent memory, a committed change, or a run log. |
| **Skip when** | The explicit suppression clause. Every existing member has one; a retro without one eventually fires on nothing and gets ignored. |

## Action tiers

Shared vocabulary, so a new retro picks a posture instead of inventing one.

| Tier | Condition | Behavior |
|---|---|---|
| **1 — Apply** | provably dead (an exhaustive reference search returns zero) **and** trivially reversible (recoverable from git or a recorded SHA) **and** local to this machine or repo **and** has no human-invocable surface | do it, report after |
| **2 — Offer** | judgment-dependent, changes behavior, or touches shared config or instruction files | numbered list, then `Enter numbers (e.g. 1 4), a category name, all, or none` — and never treat "here's the report" as implicit approval |
| **3 — Report only** | outward-facing or hard to reverse — remote branches, published artifacts, live DB writes, anything needing a service restart, shared-history rewrites | name the finding, name the remediation, stop |

An item moves **up** a tier when evidence is incomplete. It never moves down.

**The fourth Tier 1 condition is the one that keeps the table honest.** An alias, shell function, slash command, or skill is never Tier 1 no matter how many references a search fails to find — its call sites live in muscle memory and shell history, and no grep can see those. The worked example: `gpstack` in `~/dotfiles/zsh/lib/30-git.zsh` had zero references across dotfiles, `~/.claude/`, skills, docs and bin, and was fully recoverable from git — Tier 1 by every other test — yet removing it was correctly a decision Jake made, not one a sweep should have made for him.

## Registry

| Retro | Home | Trigger | Corpus | Action | Destination |
|---|---|---|---|---|---|
| **Datadog Debugging Follow-ups** | CLAUDE.md § | session used Datadog tools | the investigation just performed | 1 | session note + one bundled Linear ticket (`telemetry` label) |
| **Gotcha Documentation Follow-ups** | CLAUDE.md § | session established non-obvious, verified, undocumented facts | the subsystem investigated | 1 | the code > comment > existing doc > memory cascade; new repo doc at ~3+ related facts |
| **ROADMAP.md in Personal Repos** | CLAUDE.md § | a `jnelken` repo with no `ROADMAP.md` and real directional signal | the repo's stubs, half-wired integrations, open questions | 2 — offer, don't create unannounced | committed `ROADMAP.md` |
| **`monthly-retro`** | skill | a calendar month, per author | that author's commit history | 1 | `docs/retros/YYYY-MM-<author>.md` |
| **`simplification-retro`** | skill | work completed — a merged PR, a finished task | everything that work touched, plus what it orphaned elsewhere | tiered per finding | applied changes + a numbered offer list + `memory.md` |

## Adding one

1. Fill in the six fields. If you can't state **Detection** and **Skip when** concretely, the retro isn't ready — those two are what separate a retro from "have a look around."
2. Pick an action tier per finding *kind*, not for the retro as a whole. Most useful retros span tiers.
3. Decide the home:
   - **A CLAUDE.md section** when it should fire on ordinary sessions without being invoked. Costs context on every session, so keep it short and point back here.
   - **A skill** when it's deliberate, long, or needs a run log. Author it in `~/code/ai-tools/skills/<name>/`, add a README row per the skills-sync rule in `CLAUDE.md`, and give it a `memory.md` if runs should compare against each other.
4. Add a row to the registry above.
5. If runs should be comparable over time, copy `skills/folio-datadog-hygiene/memory.md`: append-only, committed, read before the run, appended after, findings framed *persisted / new / resolved since last run*. Never rewrite prior entries. The anti-pattern to avoid is `rum-review`'s `### Last Run` stub — in-file state nobody maintains is worse than none.
