# ai-tools

Personal AI coding tools repo for jake — Claude Code and Cursor skills, hooks, commands, and automations. Claude pieces get symlinked into `~/.claude/` via `install.sh`.

## Agent permissions

Agents may **commit and push** in this repo without asking first. This is a deliberate standing exception to the default "commit or push only when the user asks" rule — it applies to this repo only. Keep commits small and descriptive; push to `main` is fine.

**Whenever changes are visible in the diff at the end of a task in this repo, commit and push them proactively — don't wait to be asked.** This is important because I work across multiple laptops and rely on this repo (skills, hooks, commands) staying continuously in sync between them; an uncommitted or unpushed change here doesn't propagate. This proactive-push rule applies only to this repo, not to other repos (woodrow, api, etc.), where the normal explicit-commit-only policy still holds.
