# Session Retrospectives

A **session retrospective** is a pass over work a session just finished, looking for one named class of residue it left behind. This doc holds the field schema, the shared action tiers, the self-start rules, and the registry of current members. `~/.claude/CLAUDE.md`'s `## Session Retrospectives` section points here rather than restating any of it.

**"Session" is load-bearing.** These fire off the work a session just did, and their corpus is that work. `monthly-retro` is *not* a member: it is keyed to a calendar month and an author, reads commit history rather than a session, and produces a stakeholder narrative rather than a residue finding. Same word, different job — don't let the naming pull it in.

## What counts as a session retrospective

Three things together:

1. **It runs after the fact, on this session's work.** A retro never changes what the work should have been — it only looks at what the work left.
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

## Self-start

A retro that has to be remembered doesn't run. The Datadog Follow-ups retro fires reliably because it has five properties — copy all five, or it won't:

1. **It lives in `~/.claude/CLAUDE.md`**, so it's in context on every session without being loaded.
2. **It is phrased as an unconditional instruction**, not an offer — *"close with a short Follow-up note"*, not *"consider closing with"*.
3. **Its trigger is a fact about the session that needs no extra work to observe** — *"used Datadog tools"*. You already know the answer by the time the session ends. A trigger that requires a scan to evaluate will never fire, because nothing prompts the scan.
4. **Its output is additive and reversible** — a note and one ticket. Nothing is destroyed if the retro fires on a session that didn't really warrant it; the cost of a false positive is a paragraph.
5. **It has an explicit skip clause**, so the instruction stays honest instead of producing filler on every session.

**Property 4 is the constraint that governs the rest.** A self-started retro may only produce additive output — a note, a ticket, a doc, a memory. **Deletion is never self-started.** So when a retro fires on its own, it reports at Tier 3 posture regardless of how its findings tier: Tier 1 auto-apply is licensed by *explicit invocation only*. That keeps the trigger cheap to get wrong, which is what makes a standing instruction safe.

Writing a self-starting trigger: state it as a yes/no question about what the session did, answerable without new tool calls. Good — *"did this session use Datadog tools?"*, *"did this work remove or replace a mechanism?"*. Bad — *"is there unnecessary complexity anywhere?"*, which is the retro itself, not its trigger.

## Registry

| Retro | Home | Trigger | Corpus | Action | Destination |
|---|---|---|---|---|---|
| **Datadog Debugging Follow-ups** | CLAUDE.md § | session used Datadog tools | the investigation just performed | 1 (additive) | session note + one bundled Linear ticket (`telemetry` label) |
| **Gotcha Documentation Follow-ups** | CLAUDE.md § | session established non-obvious, verified, undocumented facts | the subsystem investigated | 1 (additive) | the code > comment > existing doc > memory cascade; new repo doc at ~3+ related facts |
| **ROADMAP.md in Personal Repos** | CLAUDE.md § | a `jnelken` repo with no `ROADMAP.md` and real directional signal | the repo's stubs, half-wired integrations, open questions | 2 — offer, don't create unannounced | committed `ROADMAP.md` |
| **`simplification-retro`** | skill, self-started by a CLAUDE.md § | the work removed or replaced a mechanism | everything that work touched, plus what it orphaned elsewhere | report-only when self-started; tiered per finding when invoked | findings note + `memory.md`; applied changes only on invocation |

Not a member: **`monthly-retro`** (calendar month + author, commit history, stakeholder narrative — see the note at the top).

## Adding one

1. Fill in the six fields. If you can't state **Detection** and **Skip when** concretely, the retro isn't ready — those two are what separate a retro from "have a look around."
2. Pick an action tier per finding *kind*, not for the retro as a whole. Most useful retros span tiers.
3. Decide the home. The two are not exclusive — the strongest shape is **both**: a short CLAUDE.md section that self-starts the detection and report, and a skill that holds the procedure and does the acting when invoked.
   - **A CLAUDE.md section** when it should fire without being invoked. Costs context on every session, so keep it short, point back here, and satisfy all five self-start properties above.
   - **A skill** when it's deliberate, long, or needs a run log. Author it in `~/code/ai-tools/skills/<name>/`, add a README row per the skills-sync rule in `CLAUDE.md`, and give it a `memory.md` if runs should compare against each other.
4. Add a row to the registry above.
5. If runs should be comparable over time, copy `skills/folio-datadog-hygiene/memory.md`: append-only, committed, read before the run, appended after, findings framed *persisted / new / resolved since last run*. Never rewrite prior entries. The anti-pattern to avoid is `rum-review`'s `### Last Run` stub — in-file state nobody maintains is worse than none.
