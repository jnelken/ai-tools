## Response Sign-off Glyphs

End every final message of a turn with one status line — `<glyph> <label, ≤8 words>` — so I can tell where the session stands without reading it (e.g. `⏳ watching CI on #1629, ~8m`). A Stop hook (`status-glyph-check.sh` in ai-tools) sends the turn back once if the last line doesn't start with a glyph. Mid-turn text and subagent output don't need one.

| Glyph | Status | Use when |
|---|---|---|
| 🛑 | Blocked | A decision, credential, permission or action from me is needed before anything moves. Label says what. |
| ⚠️ | Stopped short | Work stopped with something I should know but needn't decide: failed gate, red test, unverified or skipped step. |
| ⏳ | Watching | Idle now, but a Monitor, ScheduleWakeup, background agent or CI watch is armed and the session resumes on its own. Label names what and a rough ETA. |
| 💡 | Follow-ups | Asked-for work done and verified; recommended follow-ups or open questions worth a look. |
| ✅ | Done | Everything asked for is complete and verified; nothing pending. |
| ⛏️ | Reapable | ✅ plus nothing keeps this session alive: no open questions, no in-progress work, no pending follow-up that needs this context, and any sswt it owns is merged (or already reaped). Applies with or without an sswt. Its checks and the "reap" authorization live in the ⛏️ memory note. |
| 💬 | Answer only | Informational reply; no task was requested. |

- One primary glyph, by priority: 🛑 > ⚠️ > ⏳ > 💡 > ⛏️ > ✅ > 💬. At most one secondary when both are true (`⏳💡`). Never pair ⛏️ with 💡, 🛑 or ⚠️ — ⛏️ already means nothing is open.
- Write ⚠️ and ⛏️ in emoji form (`U+26A0 U+FE0F`, `U+26CF U+FE0F`). The bare text forms render monochrome and the hook rejects them.
- Pick honestly: a turn that armed a watch is ⏳, not ✅; unverified work is ⚠️, not ✅.
- The same Stop hook also shows a non-blocking `⏰` warning while the session holds a pending `CronCreate` / `ScheduleWakeup` job: those are session-only and die if the session closes. Don't repeat the warning in the status line; a ⏳ label should still say what is armed.


## Context Usage: Recommend /handoff

Once conversation context passes roughly 50% of the window, proactively recommend running `/handoff` — don't wait for me to notice the session is getting heavy. Mention it once, briefly, at a natural break in the work (not mid-tool-call-chain), and let me decide; don't invoke it unprompted. Skip the reminder if a handoff was already offered or run earlier in the same session.


## Parallelize with Subagents

Whenever a task breaks into independent pieces, size the fan-out with `bin/agent-budget allow --provider P --role R --want N`, then launch each piece through `bin/fanout spawn --label L --provider claude|codex --role R --prompt-file F`. Fan-outs run in Superset terminals: headless by default, or `--interactive` for an agent TUI. Each terminal waits on the shared budget without using a model. Read completion through `bin/fanout status` / `collect`; the exit file is authoritative. The in-process Agent / Workflow gate denies over-budget calls and points here.

Independent means: no piece edits a file another piece touches, and no piece needs another's output as input. Research questions, isolated file/codebase lookups, per-repo or per-target checks (e.g. auditing several PRs, checking several services), and non-overlapping edits are common cases. Launch the independent remainder concurrently; sequence work with real dependencies.

**Parallel is the default across repos, PRs and workspaces.** Work in different repos or worktrees (`/post-pr` runs, per-repo checks, sswt dispatches, reviews) goes out concurrently within the shared budget unless one piece needs another's output or edits the same files or state. A sequencing hint ("run the API one first") is a preference; flag any ordering risk, and serialize when explicitly required or when the dependency is real.

An agent given its own worktree starts with no dependencies installed. Tell it to install them first (`mise exec -- pnpm install --frozen-lockfile` in woodrow/api), and never symlink another worktree's `node_modules`: pnpm will try to purge the shared copy.

For suitable in-process sub-work, default to `subagent_type: "fork"` when it does not need a fresh perspective and its raw tool output is not worth keeping in context. Forks inherit full conversation context and share the prompt cache. Use a fresh agent type (or explicit `model` override) when it needs a clean start or a different model.

This default applies to Fable, Sonnet and Opus. It stacks with the Model Router for `jnelken` repos (a SessionStart hook points at it there), or the Fable-specific Sonnet/Opus split below.

## Above-Sonnet Models (Opus, Fable): Prefer Sub-Agents

This section is the fallback path when the Model Router (`~/.claude/MODEL_ROUTER.md`) isn't in play — outside the personal code dir, or its preconditions fail. When the active model is above Sonnet (Opus or Fable), always use sub-agents (via the Agent tool) for one-off tasks and investigations — one-shot lookups, exploratory research, isolated file searches, and similar — or whenever otherwise suitable. Delegate to a sub-agent rather than doing this work directly in the main loop.

Delegate through the preset agents, which carry the model and rules: `researcher` (read/think), `ops-investigator` (Datadog/Koyeb/CI, read-only), `implementer` (writing — Codex first, does it itself only if Codex fails), and `codex-reviewer` / `claude-reviewer` (one review lens each). Any other sub-agent must never use Fable — pass an explicit `model`. Codex spec: `~/.claude/instructions/codex-implementation.md`.


## Cross-Session Continuity

Every Claude Code session's full transcript is retained automatically and is searchable via `search_session_transcripts` / `list_events` (ccd_session_mgmt MCP). When a task references prior work, a past decision, or feels like a continuation of something already discussed, search past transcripts before assuming the context is gone or asking me to re-explain — don't wait to be told to. This is for detail too specific to belong in the curated memory system (`$(~/.ai-tools/bin/personal-code-dir --memory-dir)`); that memory system still owns durable facts, preferences, and project state — use it as normal, and don't duplicate its content into transcript-search habits.


## Plan Files
Every plan file you write (under `docs/plans/` in a project repo, or anywhere else when asked to create a plan) must begin with a **Suggested execution** block right after the title, recommending which Claude model and reasoning effort would best execute the plan, and why. Format:

```markdown
# Plan Title

> **Suggested execution:** <model> with <reasoning effort> reasoning — <one-sentence rationale>. Step up to Opus for <when>; step down to Haiku for <when>.
```

Pick a tier: Opus, Sonnet or Haiku. Name the tier, not a version: it means the latest model of that tier, so the header doesn't go stale when a new version ships. Reasoning effort: low / medium (auto) / high. Base the recommendation on the task's code volume, design/visual judgment needs, algorithmic complexity, and how ambiguous the plan itself is (a detailed plan lets a cheaper model execute fine; a vague brief needs a smarter model to fill gaps).

Write the real plan to `docs/plans/` in the project repo (for team visibility and version control), even when the harness also creates its own plan file elsewhere.
