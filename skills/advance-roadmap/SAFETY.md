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
- **A `human-only` label means this skill never implements it.** That label marks work no
  unattended run can finish — a GUI installer or wizard, a vendor sign-in, a purchase, a physical
  device, a credential only Jake holds. Drop those issues from the candidate batch entirely, and
  do it **silently**: a `human-only` issue is not *blocked* on anything this skill could resolve,
  it simply isn't this skill's work, so Step 2b does not apply and an `@jnelks` ping every six
  hours is pure noise. Record it in run memory as `skipped-human-only` with its `DEV-N` id and
  move on. It's a flat workspace label, not a child of the `repo` group, so an issue carries both
  it and its own `repo/*` label.
- **Clean tree or skip — unless a directive says otherwise.** `git -C <repo> status --porcelain`
  must be empty. Uncommitted work means the user is mid-thought there; branching, merging and
  pushing around it tangles their diff. Pick a different repo — never stash, reset, or commit
  their WIP to get started **on your own initiative**. The one exception: a pending directive from
  [[prepare-roadmap]] — a `## Directive` section in the description of a ticket labelled
  `roadmap-directive` for this repo, which means Jake was already asked and already answered. See
  Step 1a (orchestrator, decides) and Step 2c (worker, acts) for exactly how far that authorization
  extends and no further. **The label only marks a directive as pending; it authorizes nothing by
  itself.** What authorizes a destructive instruction is the recorded `git status` snapshot matching
  the live tree as an exact set — re-checked by the worker at execution time, never assumed from
  triage. Resolve the ticket you intended to attempt before this
  check; when the tree blocks it, comment on that ticket per Step 2b before moving on.
- **Linear goes through the `linear` CLI, and nothing else.** It authenticates with the API key
  it keeps in the macOS keychain (service `linear-cli`, workspace `jnelken`) — the only credential
  that survives an unattended run. Never use Linear MCP tools and never attempt OAuth: that path
  expires silently and cost every run on 2026-09-24 its Linear view. The **orchestrator** never
  calls Linear at all — its sandbox can't read the keychain — and reads the snapshot `run.sh`
  wrote before launching it (`linear-snapshot.json`, path in the prompt; `"ok": false` means the
  fetch failed and `error` says why). The **worker** is unsandboxed and calls the CLI directly:
  - read an issue: `linear issue view DEV-N` · raw GraphQL: `linear api '<query>'`
  - comments: `linear issue comment list DEV-N` · `linear issue comment add DEV-N --body-file <f>`
  - close out: `linear issue update DEV-N --state Done`
  - labels: `linear issue update DEV-N --add-label <l>` / `--remove-label <l>` (never `--label`,
    which replaces the whole set)
  - descriptions: `linear issue update DEV-N --description-file <f>`
  If a CLI call fails, record the error and continue per the "never block a run on Linear" rule.
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


