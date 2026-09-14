# ai-tools

Personal AI coding tools repo for jake — Claude Code and Cursor skills, hooks, commands, and automations.

Canonical URL: https://github.com/jnelken/ai-tools

## Deploy model (important — read before editing anything under `~/.claude`)

**This checkout (`~/code/ai-tools`) is dev-only.** `~/.claude/*` is symlinked from a separate deploy clone at `~/.ai-tools`, which tracks `origin/main` and is managed entirely by `install.sh` — never hand-edited. Concretely:

- `git push` to `main` from this checkout **is** the deploy step — it doesn't reach any machine until something updates `~/.ai-tools` from origin.
- After pushing, run `~/.ai-tools/install.sh` to make *this* machine pick the change up right away, instead of waiting for the next session's `ai-tools-sync` SessionStart hook to fetch/merge/re-link it automatically.
- Editing through `~/.claude/...` instead of `~/code/ai-tools/...` edits the *deployed copy*, not the source of truth — it'll get flagged as dirty (by `ai-tools-sync.sh` and `claude-symlink-hygiene.sh`) and overwritten on the next clean update. The fix is always: move the edit back here, commit, push.

See `README.md`'s Install / Dev workflow sections for the full mechanics (`install.sh --dev` to test an unpushed change, invocation modes, env vars).

## Skills sync rule (standing)

**All skills must be written or edited in this repo under `skills/<name>/`, then committed and pushed.** They are installed by symlink (`~/.ai-tools/install.sh` → `~/.claude/skills/<name>`, sourced from the deploy clone — see Deploy model above). Do not leave a skill only in a one-off project, Cursor cloud workspace, or unpushed branch — copy/update it here before considering the work done.

When authoring a skill elsewhere first (spike, cloud agent, another repo):

1. Copy the skill directory into `skills/<name>/` here (self-contained: `SKILL.md` + any `scripts/`, `reference(s)/`, `templates/`, assets).
2. Add a row to the skills table in `README.md`.
3. Commit and push to `main`.
4. On each machine: run `~/.ai-tools/install.sh` (or wait for the next session's `ai-tools-sync` hook) to pull the push into the deploy clone.

## Agent permissions

Agents may **commit and push** in this repo without asking first. This is a deliberate standing exception to the default "commit or push only when the user asks" rule — it applies to this repo only. Keep commits small and descriptive; push to `main` is fine.

**Whenever changes are visible in the diff at the end of a task in this repo, commit and push them proactively — don't wait to be asked.** This is important because I work across multiple laptops and rely on this repo (skills, hooks, commands) staying continuously in sync between them; an uncommitted or unpushed change here doesn't propagate. This proactive-push rule applies only to this repo, not to other repos (woodrow, api, etc.), where the normal explicit-commit-only policy still holds.
