# Session status sign-off glyphs

> **Suggested execution:** Sonnet 5 with medium reasoning — one small bash hook, one test, one CLAUDE.md section; the design is settled. Step up to Opus 5 if transcript parsing misbehaves (e.g. the last message isn't visible when Stop fires); step down to Haiku 4.5 for only the CLAUDE.md/memory edits.

## Context
Jake wants every response's last line to show at a glance where the session stands: done, follow-ups, blocked, watching, etc. ⛏ already exists and keeps its meaning: sswt can be reaped, PR merged, no open questions (see memory `feedback_pickaxe_signoff_when_reapable.md`). Decisions made: semantic emoji, with ⛏ shown as the emoji form ⛏️. A Stop hook enforces the sign-off by blocking and retrying.

## Glyph set
The last line of every final message is `<glyph> <label of 8 words or fewer>`, e.g. `⏳ watching CI on #1629, ~8m`.

| Glyph | Status | Use when |
|---|---|---|
| 🛑 | Blocked | A decision, credential, permission or action from Jake is required before anything can move. The label says what's needed. |
| ⚠️ | Stopped short | Work stopped with something Jake should know but doesn't have to decide: a gate failed, a test is red, a step was unverified or skipped. |
| ⏳ | Watching | Idle right now, but a Monitor, ScheduleWakeup, background agent or CI watch is armed and the session will resume on its own. The label names what it's watching and a rough ETA. |
| 💡 | Follow-ups | The asked-for work is done and verified, but there are recommended follow-ups or open questions worth a look. |
| ✅ | Done | Everything asked for is complete and verified, with nothing pending. |
| ⛏️ | Reapable | This is ✅ plus the sswt is merged, clean and pushed, with no open questions. The existing checks and the "reap" authorization are unchanged. |
| 💬 | Answer only | The response was informational and no task was requested. |

**Rules**
- ⚠️ and ⛏️ must always be written in their emoji form: `U+26A0 U+FE0F` and `U+26CF U+FE0F`. The bare text forms, `⚠` (U+26A0) and `⛏` (U+26CF), render as monochrome text and don't count.
- Pick one primary glyph using this priority: 🛑 > ⚠️ > ⏳ > 💡 > ⛏️ > ✅ > 💬.
- A single secondary glyph is allowed when both statuses are true, e.g. `⏳💡`. Never combine ⛏️ with 💡, 🛑 or ⚠️, because ⛏️ already means nothing is open.
- Mid-turn text doesn't need a glyph, since focus mode hides it anyway. Subagent output doesn't need one either.

## Implementation
1. **Hook**: create `~/code/ai-tools/hooks/status-glyph-check.sh` (the source repo; `~/.ai-tools` is the deploy clone) and symlink it at `~/.claude/hooks/status-glyph-check.sh`, matching the existing hooks.
   - Read the Stop input JSON with `jq`. If `stop_hook_active` is true, exit 0 so the hook can't loop.
   - Get the final text from `last_assistant_message` if the input has it. Otherwise read `transcript_path` and take the last `type=="assistant"` entry that has a text block.
   - If there's no text at all, exit 0. That covers a turn that ended on AskUserQuestion or ExitPlanMode, or plan mode generally.
   - Check that the last non-blank line starts with one of the glyphs. Require U+FE0F after ⚠ and ⛏, and block on the bare text forms so the emoji rendering is enforced. Tolerate a leading `—`.
   - If the glyph is missing, emit `{"decision":"block","reason":"<one-line legend + 'append a status line'>"}`.
   - Fail open (exit 0) on any parse error. Follow the `set -u` / no `-e` style of `session-doc-stop.sh`.
2. **Register** the hook as a new `Stop` entry in `~/.claude/settings.json`, next to the Superset notify hook.
3. **Test**: add `~/code/ai-tools/hooks/tests/status-glyph-check.test.sh` with fixtures covering: glyph present, glyph missing (blocks), `stop_hook_active` (passes), a tool-use-only final message (passes), ⛏️ and ⚠️ with FE0F (pass), bare ⛏ and ⚠ (block), and malformed input (passes). Mutation-check it by stubbing the matcher to always succeed and confirming the missing-glyph case fails.
4. **Instruction**: add a "Response Sign-off Glyphs" section with the table and rules to `/Users/jake/dotfiles/claude/CLAUDE.md` (the real path, not the symlink).
5. **Memory**: update `feedback_pickaxe_signoff_when_reapable.md`. Switch its sign-off from bare ⛏ to ⛏️ (U+26CF U+FE0F), and say ⛏️ is one tier of the glyph system in CLAUDE.md, and link it. Leave the reap mechanics as they are.
6. **Plan doc**: copy this plan to `~/code/ai-tools/docs/plans/session-status-glyphs.md`. Commit ai-tools (standing rule: commit, don't push). The dotfiles repo auto-commits and pushes.

## Verification
- Run the test script. Every case should pass, and the mutation check should fail as expected.
- Live check: in a fresh session, ask a trivial question. The reply should end in 💬. Then ask it to reply without a glyph, and confirm the hook forces one retry that adds the line, with no loop.
- Check that plan mode and AskUserQuestion turns are never blocked.
- Run `jq . ~/.claude/settings.json` to confirm the settings file is valid JSON after the edit.
