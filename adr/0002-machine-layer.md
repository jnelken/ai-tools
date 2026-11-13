# ADR 0002: Machine layer for development instructions

- **Status:** Accepted; implemented
- **Decision date:** 2026-11-13

## Context

ADR 0001 selects instructions by role and target repository. It does not know which machine a session runs on, so Mac-only guidance (Superset/sswt, the Dropbox personal code dir, Lunch Money cache) loaded in cloud and PC sessions where it is wrong or dead weight.

## Decision

Add a machine layer, `instructions/machine/*.md`, composed into `development/*` profiles after the role fragment. Machines: `work-mac`, `personal-mac`, `personal-pc` (WSL Linux), `personal-cloud`.

- **Facts, not grants.** Machine fragments describe paths, hosts and tools. Push/merge permissions still come only from `classify()` on the target repository.
- **Identity precedence:** `AGENT_MACHINE`, then the `machine` key in `~/.config/agent-profiles/config.json`, then `CLAUDE_CODE_REMOTE=true` (`personal-cloud`), then unconfigured macOS (`mac`: Mac-wide fragments only, so a Mac without a key keeps the Superset rules). An invalid name fails closed.
- **Runtime profiles ignore the machine layer**, preserving ADR 0001 isolation.
- **Dotfiles unchanged:** `claude/instructions/*.md` stay linked everywhere; they load on demand and cost nothing unless read.
- Superset rules go to Work Mac and Personal Mac. Lunch Money cache guidance goes to Personal Mac.

## Consequences

Cloud and PC sessions no longer load Mac-only guidance. Each physical Mac needs one `machine` line to get its machine-specific fragment. Fresh sessions are required after a change; work-Mac rollout checks from ADR 0001 still apply.
