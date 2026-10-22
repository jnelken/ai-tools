# Preset subagents from session history

> **Status:** Implemented before 2026-10-22; archived by advance-roadmap. Evidence: agents/{codex-reviewer,claude-reviewer,implementer,researcher,ops-investigator}.md, install.sh section_agents, skills/peer-review/SKILL.md preset routing, and hooks/readonly-ops-guard.sh.

> **Suggested execution:** Sonnet 5 with medium reasoning: the plan pins down each agent's frontmatter and integration points, so most of the work is writing markdown and one hook script. Step up to Opus 5 for the peer-review SKILL.md rewrite (a 463-line skill with state and loop semantics that must not regress). Step down to Haiku 4.5 only for the CLAUDE.md trim.

## Context

You asked which recurring delegations should become preset subagents in `~/.claude/agents/`, using `/peer-review`'s reviewer as the example. Evidence from the last 60 days:

- **362 Agent calls across all sessions.** Opus was passed by hand 96 times and Sonnet 79 times.
- **The two existing presets were never used.** `react-pro` and `typescript-pro` had zero calls.
- **388 `codex review` runs and 22 `codex exec` implementation runs.**
- **8 corrective re-runs.** Each time a subagent backgrounded `codex review`, said "I'll wait", and its turn ended without the result.

Right now the same model choice, Codex flags and read-only rules get retyped into prompts. Presets put each of those in one file and fix the "I'll wait" failure in one place.

Docs facts that shaped the design:
- `model:` accepts aliases (`sonnet`, `opus`, `haiku`), and the Agent call's `model` parameter can still override it.
- Subagents can spawn subagents (3 levels deep) and can use the Skill tool.
- `tools:` takes bare tool names only. Restricting Bash to read-only needs a frontmatter `PreToolUse` hook.
- A background task inside a subagent is not guaranteed to block, and the Bash tool kills any single call at about 2 minutes. As built, agents detach Codex with `nohup` and poll in bounded foreground calls under 100s until it exits.

## Roster: five agents

Source: `~/.ai-tools/agents/<name>.md`. `install.sh` (`section_agents`) symlinks every `*.md` into `~/.claude/agents/`.

| Agent | model | tools | Replaces (history count) |
|---|---|---|---|
| `codex-reviewer` | haiku (thin wrapper; Codex does the reviewing) | Bash, Read, Grep | peer-review step 2 `nohup codex review … &` + Monitor; 388 runs; 37 review agents; 8 re-runs |
| `claude-reviewer` | opus | Read, Grep, Glob, Bash | peer-review's `claude -p` path when Codex wrote the last commit; ad-hoc "Angle B: removed-behavior auditor" forks |
| `implementer` | sonnet (so it can do the work itself when Codex fails) | all | about 55 worktree implementation agents and 19 "apply review findings" agents |
| `researcher` | opus | Read, Grep, Glob, Bash, WebFetch, WebSearch; `disallowedTools: Edit, Write, NotebookEdit` | about 90 Explore/general research calls (26 had Opus passed by hand), 16 vendor/compliance research forks, branch audits |
| `ops-investigator` | opus | Bash, Read, Grep, Skill; read-only Datadog MCP tools as fallback | about 30 Datadog/Koyeb/CI investigations; 876 `pup` calls, 619 `koyeb` calls, 290 `gh run view` calls |

### Shared contract: reviewers

**Input:**
- the base ref, or `--uncommitted`;
- one lens or focus prompt (one agent per lens, so peer-review fans out with several Agent calls in one message);
- the effort level;
- a "do not re-report" list.

**Output:** one fenced block, the same for both reviewers, so peer-review triage doesn't care which one ran:
- `status: ok | usage_limited | failed`;
- reviewer model and effort;
- the findings as a table: `P1/P2/P3 | file:line | claim | suggested fix`.

**Waiting rule:** the reviewer must wait for its review to finish before returning.
- `codex-reviewer` runs `codex review -c model_reasoning_effort=$EFFORT [--base $BASE | "<focus prompt with base inlined>"]` detached with `nohup` (never wrapped in `bash -c '…"$@"…'`, which background agents are denied even under bypassPermissions). Both `--base` and `--uncommitted` are mutually exclusive with a prompt, so scope goes inside the prompt when focus text is present. The transcript is saved to `$GIT_DIR/peer-review-<lens>.log`.
- It polls in bounded foreground calls (under 100s each) until Codex exits, giving up after 30 minutes. It never ends its turn while Codex is still running.
- Keep `codex review` rather than `codex exec`, since that subcommand is built for reviews. Keep the current "no `-m`" behavior so reviews don't change.
- On a usage limit it reports `usage_limited` and stops. The `/codex-action-review` fallback stays in the skill.
- Reviewers never edit files.

### `implementer`

**Input:**
- the absolute worktree path;
- a self-contained task;
- the files it owns;
- optionally `mode: direct`, to skip Codex for small mechanical fixes.

**Steps:**
1. Read the repo's `origin` owner.
2. **Concentro repos:** follow `~/.claude/instructions/codex-implementation.md` exactly. The prompt goes in a file, then `codex exec -m gpt-6.1-sol -c model_reasoning_effort=high -s workspace-write -C <wt> "$(cat …)" </dev/null` runs detached and polled, then the agent reads the diff and runs that repo's checks.
3. **`jnelken` repos:** follow `/Users/jake/dotfiles/claude/MODEL_ROUTER.md`.
4. **On a Codex failure** (as defined in codex-implementation.md: non-zero exit, a limit, the Luna fallback also unavailable, a hang, or bad output after one re-prompt), do the work itself.
5. Make a local checkpoint commit after each verified step, as the existing checkpoint-commit memory says. Never push.
6. Return the diff stat, the check results, and which path ran (Codex or self).
7. In a fresh worktree, install dependencies first (`mise exec -- pnpm install --frozen-lockfile`). Never symlink `node_modules`.

### `researcher`

- Returns conclusions with `file:line` references, never file dumps.
- Searches past transcripts when the task points at prior work.
- Read-only for Bash, enforced in the prompt (Edit and Write are already removed).

### `ops-investigator`

- Uses `pup` first. Loads the `datadog-tool-selection` skill when the access path is unclear.
- Knows the Koyeb rule that "a Koyeb env override beats a code default" and the `koyeb deployment get` recipe from `docs/koyeb-services.md`.
- Ends with a **Follow-up: observability gaps** section. The parent decides whether to file the `telemetry` ticket.
- **Read-only guard:** a frontmatter `PreToolUse` hook on Bash runs the new `~/.ai-tools/hooks/readonly-ops-guard.sh`. It exits 2 on mutating verbs:
  - `koyeb … update|redeploy|delete|pause|resume`, `koyeb secrets reveal`
  - `pup … create|update|delete|mute`
  - `gh run rerun|cancel`, `gh pr merge|comment|edit`
  - `git push`

## Not presets, and why

- **`/post-pr`.** It depends on `/loop` and ScheduleWakeup, and only the main session wakes from those. It was also the source of the "I'll wait" re-runs. Keep it in the main session; it uses the new reviewers indirectly through peer-review.
- **Planner.** The built-in `Plan` agent with `model: opus` already covers it (15 uses), so a preset adds nothing.
- **session-hardening.** It must be a `fork`, because it analyses the transcript.
- **Linear batch labelling.** 22 calls, all in one session. `linear-ticket-gen` already owns the conventions.
- **A main-thread default agent** (`"agent"` in settings, or `claude --agent`). It replaces the default system prompt for the whole session. Not what we want here.

## Integration edits

1. **`~/.ai-tools/skills/peer-review/SKILL.md`**
   - **Step 2:** replace the `nohup codex review` + Monitor block and the `claude -p` path with one Agent call per lens, all in one message:
     - `subagent_type: codex-reviewer`, or `claude-reviewer` when Codex wrote the last commit.
     - The step 1.5 reviewer choice, the effort ladder, the lens auto-promotion, `models.tsv` parsing (from the agent's output block now), and the `/codex-action-review` fallback on `usage_limited` all stay as they are.
   - **Step 4:** the "more than 3 disjoint fixes → Sonnet subagents" fan-out becomes `implementer` with `mode: direct`.
   - Steps 0, 1, 3 and 5–8 are unchanged.
2. **`/Users/jake/dotfiles/claude/CLAUDE.md`, "Above-Sonnet Models":** shrink to about 3 lines naming the presets. Keep "never Fable" and the pointer to codex-implementation.md, which becomes the spec the implementer cites. Edit the real dotfiles path, not the symlink.
3. **The plan doc:** commit a copy of this plan to `~/.ai-tools/docs/plans/` with the change. ai-tools is a `jnelken` repo, so it can be pushed straight to `main`.

## Verification

Agent definitions load at session start, so open a fresh session after installing.

1. **Installed:** `/agents` lists all five, and `ls -l ~/.claude/agents` shows the new symlinks.
2. **`codex-reviewer` waits:** run it on a tiny throwaway diff and on a real branch whose review takes more than 2 minutes. Both return the findings block in the same turn, with no "I'll wait" message.
3. **`claude-reviewer`:** run it on the same diff. The output block has the same shape, and no files change.
4. **`ops-investigator` guard:** `pup logs search …` succeeds. Asking it to run `koyeb service update` or `gh run rerun` is blocked by the hook. Also confirm the frontmatter hook fires from a user-level agent; the docs say it needs a trusted folder.
5. **`implementer` in a scratch worktree of internal-tools:**
   - With Codex, it produces a diff, makes a checkpoint commit and doesn't push.
   - Rerun with a bogus model to force a failure: it falls back to doing the work itself and reports `path: self`.
6. **peer-review end to end:** run `/peer-review local` on a real branch. The lenses run in parallel, the triage table has the same shape as before, `models.tsv` is filled, and the lock is released.

## Follow-ups (not in scope)

- **Codex model pin resolved:** best roles are pinned to `gpt-6.1-sol`; the fast tier is `gpt-6-luna`. Conductor's `review.sh` defaults to `low` effort, while peer-review never goes below medium.
- **Settings drift:** `~/.claude/settings.json` is a regular file, not a symlink to dotfiles, and the two differ. The `model-router-pointer.sh` hook can't fire because `~/.claude/MODEL_ROUTER.md` doesn't exist.
- **Unused presets:** retire `react-pro` and `typescript-pro`, which had zero uses in 60 days.
