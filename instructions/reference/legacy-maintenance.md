## File Location

This file lives at `/Users/jake/dotfiles/claude/CLAUDE.md` and is symlinked from `/Users/jake/.claude/CLAUDE.md`. Edit tools must target the real dotfiles path directly — writing through the symlink is refused.


## Keeping CLAUDE.md Lean

This file is loaded into every session, so every line costs context everywhere. When adding guidance, keep only what applies to most sessions inline (hard rules, triggers, one-line summaries). Put situational detail — mechanics, tiers, examples, per-tool procedures — in `~/.claude/instructions/<topic>.md` (real path `/Users/jake/dotfiles/claude/instructions/`), and leave a short section here: a one- or two-sentence rule plus `Details: ~/.claude/instructions/<topic>.md`. Read the file only when the situation arises. Length triggers: a section that runs past ~6 lines, or holds steps or edge cases most sessions never need, moves out the same way; and once this file passes ~200 lines, move out the least-critical sections until it is back under ~150 before adding anything new. Start each instructions file with a one-line "Loaded on demand from `~/.claude/CLAUDE.md` — read when …" so the trigger is clear.

