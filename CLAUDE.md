# ai-tools

Personal AI coding tools repo for jake — Claude Code and Cursor skills, hooks, commands, and automations. Claude pieces get symlinked into `~/.claude/` via `install.sh`.

Canonical URL: https://github.com/jnelken/ai-tools

## Skills sync rule (standing)

**All skills must be written or edited in this repo under `skills/<name>/`, then committed and pushed.** They are installed by symlink (`./install.sh` → `~/.claude/skills/<name>`). Do not leave a skill only in a one-off project, Cursor cloud workspace, or unpushed branch — copy/update it here before considering the work done.

When authoring a skill elsewhere first (spike, cloud agent, another repo):

1. Copy the skill directory into `skills/<name>/` here (self-contained: `SKILL.md` + any `scripts/`, `reference(s)/`, `templates/`, assets).
2. Add a row to the skills table in `README.md`.
3. Commit and push to `main`.
4. On each machine: `git pull` in this repo (symlinks already point here after `install.sh`).

## Agent permissions

Agents may **commit and push** in this repo without asking first. This is a deliberate standing exception to the default "commit or push only when the user asks" rule — it applies to this repo only. Keep commits small and descriptive; push to `main` is fine.

**Whenever changes are visible in the diff at the end of a task in this repo, commit and push them proactively — don't wait to be asked.** This is important because I work across multiple laptops and rely on this repo (skills, hooks, commands) staying continuously in sync between them; an uncommitted or unpushed change here doesn't propagate. This proactive-push rule applies only to this repo, not to other repos (woodrow, api, etc.), where the normal explicit-commit-only policy still holds.
