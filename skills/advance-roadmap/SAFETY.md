# advance-roadmap — hard safety rules

These bind **both** the read-only orchestrator and the write-capable worker. Never weaken them.

The orchestrator must not mutate repos (see ORCHESTRATOR.md). Only the worker may branch, edit,
commit, merge, or push (see WORKER.md).

## Hard safety rules (never violate)

- **Personal repos only.** Run `git -C <repo> remote get-url origin` and require the owner to be
  `jnelken`. `ROADMAP.md` is a personal-repo convention; Concentro repos (`woodrow`, `api`,
  `folio-platform`, anything under `Concentro-Inc`) track direction in Linear and must NEVER be
  touched by this skill. Pushing `main` on a work repo is the worst thing this skill could do.
- **A Linear issue's repo is its `repo/*` label, and nothing else.** No label, not actionable —
  see Step 2's *Also check Linear*. Never read it out of the title, body, or project name instead:
  a project called "Knowledge Base MVP" is not evidence for the `knowledge-graph` directory, and
  guessing is the same failure mode as the rule above — a feature pushed to `main` in the wrong
  repo.
- **Clean tree or skip — unless a directive says otherwise.** `git -C <repo> status --porcelain`
  must be empty. Uncommitted work means the user is mid-thought there; branching, merging and
  pushing around it tangles their diff. Pick a different repo — never stash, reset, or commit
  their WIP to get started **on your own initiative**. The one exception: a directive file from
  [[prepare-roadmap]] at `/Users/jake/.claude/automations/advance-roadmap/directives/<repo>.md`,
  which means Jake was already asked and already answered. See Step 1a for exactly how far that
  authorization extends and no further. Resolve the ticket you intended to attempt before this
  check; when the tree blocks it, comment on that ticket per Step 2b before moving on.
- **Shared preflight, not a reimplementation.** Run `~/dotfiles/bin/git-safe-to-autocommit <repo>`
  and skip the repo on a non-zero exit. It refuses repos mid-rebase/merge/cherry-pick/revert/bisect
  and repos with a detached HEAD. Do not inline your own version of this check — a divergent copy
  is exactly what let a sibling automation commit on top of a stuck rebase for two weeks unnoticed.
- **Skip live sessions.** If any `.claude-sessions/*.md` at the repo root has `updated_at_epoch`
  within the last 15 minutes, someone is working there right now. Pick a different repo.
- **Never force anything.** No `--force`, no `--force-with-lease`, no `git reset --hard`, no
  `git clean -fd`, no rewriting published history. If the final `git push` is rejected as
  non-fast-forward, STOP — leave the branch and its commits in place, and report it.
- **Never open a PR** and never delete a branch the user created.
- **Never commit secrets or build junk** (`.env`, `*.pem`, tokens, `node_modules/`, `dist/`,
  `.next/`, `build/`, `*.log`). Respect `.gitignore`.
- **Honor `.noroadmap`.** A repo with a `.noroadmap` file at its root has opted out. Never create,
  modify, or delete `.noroadmap` — it is the user's toggle.
- **All-or-nothing.** If verification fails and you can't fix it cleanly, do NOT merge or push.
  Leave the work on its branch, say so, and stop. A half-shipped roadmap item on `main` is worse
  than no run at all.


