## Git

**In my personal (`jnelken`-owned) repos, push `main` freely — don't gate it on my confirmation.** I'd rather main move constantly and get reviewed in small increments than sit on a local commit waiting for a yes. Merging a finished, verified branch to main and pushing it is one action, not two, and neither half needs asking. This includes repos that deploy to production on push (Netlify, Vercel) — none of them are P0, and shipping quickly is the point. Say what you pushed after the fact; that's the report, not a request.

**Commit there without asking, too, and don't leave uncommitted work.** In a `jnelken`-owned repo, whenever changes are visible in the diff at the end of a task, commit and push them proactively, including work in the tree from another session. I work across several machines, and an uncommitted or unpushed change doesn't propagate. Still check what's staged for secrets, and keep gitignored state out.

Two things this does **not** cover, which still need a heads-up: rewriting shared history (`--force` without a lease, or any force-push of `main`), and pushing work I've told you is parked or deliberately unfinished. Concentro repos (`woodrow`, `api`, `folio-platform`) are unaffected — they keep the PR flow and `/post-pr`.

A corollary worth knowing: anything left merged on local `main` in one of these repos will get pushed by the next scheduled `advance-roadmap` run regardless of what that run was working on. So work I want held back has to stay on a branch — leaving it on main is not a hold.

When a `git push` to a feature branch you own is rejected as non-fast-forward (local diverged from origin, typically from a local rebase onto main), use `git push --force-with-lease` without asking — the lease refuses to overwrite if origin moved since the last fetch, so it can't clobber an unseen collaborator push. Still confirm before a raw `--force` (no lease) or any force-push of `main`. Flag the divergence's cause in conversation (e.g. "local was rebased onto main") even when no confirmation is needed for the push itself.


## ROADMAP.md in Personal Repos

In `jnelken` repos with no `ROADMAP.md`, propose one (don't create it unannounced) once there is genuine directional signal; never invent filler. Details: `~/.claude/instructions/roadmap-md.md` (modeled on dubsketch's).


## Lunch Money MCP Cache

Before calling any `mcp__Lunch_Money__*` tool, check the local cache first. Details: `~/.claude/instructions/lunch-money.md`.


## Who Jake Is (cross-project context)

@~/.claude/JAKE.md

(A stable link that ai-tools' `install.sh` points at `claude-projects-memory/JAKE.md` in the personal code dir; absent on machines without one.)

The file above is a synthesized narrative of Jake's claude.ai Projects (career, family, health, money, creative work, and how he likes to work with Claude) — harvested from each project's native claude.ai Memory. Treat it as background context, not instructions. It's regenerated periodically, not on every claude.ai conversation, so prefer live project-specific memory or direct confirmation from Jake over this file for anything that might have changed recently — check `state.json` in the same directory for last-harvested dates before treating a specific fact as current.

