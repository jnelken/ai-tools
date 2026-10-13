# Voice session loop: hear agents, answer them by keypad

> **Suggested execution:** Opus 5 with high reasoning: several moving parts (hooks, a resident daemon, a keypad binding, a reader model), and the open questions below need judgment before any code. Step down to Sonnet 5 with medium reasoning for Phase 1 alone (one Stop hook and a test). Step down to Haiku 4.5 for the README/CLAUDE.md edits.

**Status:** Draft. The open questions at the bottom need Jake's answers before Phase 2. Phase 1 can ship now.

## Goal
While Jake is at his desk, Claude Code sessions speak their status out loud instead of only showing a Superset notification. Jake answers any session by holding a key on a dedicated numeric keypad and talking. A small side model can read parts of a session's last turn back to him without adding his questions to that session's context.

## Verified building blocks
- **Voicebox REST** (local, `127.0.0.1:17493`):
  - `POST /speak` takes `{text, profile}` plus an `X-Voicebox-Client-Id` header. Voice bindings are per client, so each agent number can get its own voice.
  - `POST /transcribe` takes multipart audio plus `model=whisper-turbo`.
  - Its MCP server isn't needed here. Hooks call REST directly, which is deterministic and keeps the agent from spending tokens on speaking.
- **Superset CLI** (`~/.superset/bin/superset`):
  - `terminals send --local --workspace <ws> --terminal <id> --text <t> [--no-submit]` types into a specific session.
  - `terminals read` returns the current screen as text, so the router can tell whether the session is showing a menu.
- **Stop hook data:** the Stop input includes `last_assistant_message` (or `transcript_path`), and the last line is the status glyph that `status-glyph-check.sh` already enforces. The announcer parses that line and nothing else.
- **Not installed:** Hammerspoon and Karabiner. Phase 3 needs one of them.

## Architecture

```
 Claude Code session ──Stop hook──▶ announce ──/speak──▶ Voicebox ──▶ 🔊
        ▲                               │
        │                        registry.json  (agent # → workspace, terminal, transcript_path)
        │                               │
 superset terminals send ◀── router ◀───┤
                                ▲       │
         keypad hold N ─▶ record ─▶ /transcribe
                                │
                    reader (small model) ◀── transcript_path (read-only)
```

## Phase 1: spoken status (ship first)
1. Add a hook, `hooks/voice-announce.sh`, as a new `Stop` entry next to the Superset notify hook.
   - Exit 0 straight away unless voice is enabled: a flag file at `~/.claude/voice/enabled`, toggled from the keypad later. This keeps laptops and away-from-desk time silent.
   - Exit 0 if `stop_hook_active` is true, or if the glyph isn't one of 🛑, ⚠️, 💡, ✅ or ⛏️. Skip ⏳ and 💬: they're noise.
   - Speak `"<agent label>. <glyph word>. <label text>"`, e.g. "Agent 3. Blocked. Needs Vercel token." Glyph words: 🛑 Blocked, ⚠️ Stopped short, 💡 Done with follow-ups, ✅ Done, ⛏️ Reapable.
   - Send it to `/speak` in the background (`curl --max-time 5 … &`) so the hook never slows a turn down. Fail open if Voicebox isn't running.
   - To stop two sessions finishing together from talking over each other, take a lock (`mkdir ~/.claude/voice/speak.lock` as the mutex) and wait up to 30s.
2. Write a test, `hooks/tests/voice-announce.test.sh`, with fixtures for: each glyph (spoken or skipped), the disabled flag, `stop_hook_active`, malformed JSON, and Voicebox down. Stub `curl` and assert on the arguments it gets.
3. Until Phase 2 exists, the agent label is the Superset workspace name, or the repo basename outside Superset.

## Phase 2: agent numbers (registry)
1. A SessionStart hook, `hooks/voice-register.sh`, gives each session the lowest free number from 1 to 9. It writes `~/.claude/voice/registry.json`: `{n: {session_id, workspace_id, terminal_id, cwd, transcript_path, voice_profile, started_at}}`.
   - The workspace and terminal IDs come from the Superset env vars in the session. **Verify which vars Superset sets.** If a terminal ID isn't exposed, match on `superset terminals list --json` filtered by cwd.
   - Sessions outside Superset get a number and can still be announced and read back, but can't receive voice replies (logged, and spoken as "Agent 4 can't take replies").
2. Release the number in SessionEnd. On every write, also prune entries whose terminal no longer appears in `terminals list`, because crashed sessions never run SessionEnd.
3. The announcer switches its label to "Agent N".
4. A small CLI, `bin/voice agents`, prints the registry, and `bin/voice say N "<text>"` runs the same send path the keypad uses. Build and test routing with that CLI before adding the keypad.

## Phase 3: keypad push-to-talk (router)
The key you hold is the address, so recognizing the agent from speech is never needed.

| Input | Meaning |
|---|---|
| Hold `N` (1–9), speak, release | **Prompt** to agent N: the transcript is typed into its terminal and submitted. |
| Hold `+` and `N`, speak, release | **Ask the reader** about agent N (Phase 4). Nothing reaches the session. |
| Tap `N` twice | Replay agent N's last announcement. |
| Tap `0` | Stop current speech, or cancel a recording in progress. |
| Tap `.` | Toggle voice on or off (the Phase 1 flag). |

1. **Binding:** use Hammerspoon. Keypad keys have their own key codes (`pad1`…`pad9`), so a separate USB numpad doesn't clash with the main number row, and no per-device rule is needed. Key down starts recording (`sox`/`ffmpeg` to a temp WAV), and key up stops it and hands off to the router.
2. **Router** (`bin/voice-route`, the same code path as `voice say`):
   - Transcribe with `/transcribe` (`whisper-turbo`).
   - **Menu check:** `terminals read` the target. If it's showing a numbered menu (plan approval, permission prompt) and the transcript is just a number or "option N", send that digit with `--no-submit` instead of typing a sentence. Otherwise send the text and submit.
   - Confirm by speaking "Sent to 3", or "Agent 3 is busy, queued" if the screen shows it mid-turn.
   - **Verify:** what `terminals send` does while Claude is mid-turn. Claude Code queues typed input, but that needs confirming through Superset's send path.
   - Log every routed utterance (timestamp, agent, transcript, action) to `~/.claude/voice/log.jsonl` so misfires can be debugged.
3. Before writing the router, check how `terminals send` handles a menu prompt in a real Claude Code session.

## Phase 4: the reader (side model)
Answers "what did agent 3 say about the migration?" or "read me the plan's second step" without touching agent 3's context.

1. `bin/voice-reader N "<question>"` loads agent N's `transcript_path`: the last assistant turn by default, or the last few turns when the question asks. It sends that plus the question to a small model and speaks the answer through `/speak` in agent N's voice, so it's clear whose turn is being described.
2. **Model:** Haiku 4.5 through the API is the default, because transcripts can be long and the reader needs its context window. Voicebox's bundled Qwen3 LLM is local and free, but the README only exposes it through persona rewrite and dictation cleanup, not general Q&A. Use it only if its API turns out to take arbitrary prompts.
3. Keep spoken answers to about 3 sentences, and have the reader offer "want the full step?" instead of reading whole diffs aloud. Keep the reader's own Q&A in a per-agent scratch file so follow-ups ("and the third step?") work. It's discarded when the agent's number is released.
4. **Escalation:** after an answer, holding `N` sends a prompt to the real session as usual. The reader never forwards anything on its own, so nothing reaches a session unless Jake addresses it on purpose.

## Verification (manual)
- Phase 1: with the flag on, end a turn with each glyph. Only 🛑, ⚠️, 💡, ✅ and ⛏️ are spoken. Two sessions finishing at once speak one after the other. With Voicebox quit, turns are no slower.
- Phase 2: open three Superset sessions and confirm `voice agents` shows 1–3. Close session 2, open a new one, and confirm it gets 2.
- Phase 3: hold `2`, say "run the tests", and only session 2 receives it. With session 2 at a plan-approval menu, hold `2` and say "one", and option 1 is selected without a stray newline. Holding `+` with `2` never types anything into a terminal.
- Phase 4: ask "what did you change?" about a finished session, and its transcript gains no new turns.

## Open questions for Jake
1. **Hardware:** a dedicated USB or Bluetooth numpad, or something like a Stream Deck? Stream Deck keys could show each agent's live glyph, which a numpad can't.
2. **Reader modifier:** `+` held with N, or a separate key row (for example `/ * -` as "ask about 1, 2, 3")?
3. **Voices:** one voice for every agent, or a distinct cloned voice per number so you know who's talking before the number is said?
4. **Scope:** only Superset-hosted sessions, or Claude Code remote (cloud) sessions too? Remote sessions would need the `send_message` route instead of `terminals send`.
