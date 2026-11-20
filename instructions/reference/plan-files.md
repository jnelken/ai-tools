Loaded on demand from `instructions/development/shared.md` — read when starting a planning or investigation session, writing a plan file, or leaving harness plan mode.

## Plan Files

### Running planning docs

- **Open it early.** After orientation, any planning or investigation session opens a running doc: `docs/plans/<slug>.md` in the target repo, or the harness plan file in plan mode, copied to `docs/plans/` on exit.
- **Use these sections:** Status, Decisions, Findings (with evidence), Open threads (checkbox list), Approach.
- **Keep it current:** update it after each finding or agent result.
- **Close threads visibly:** answer and tick with an annotation, or move the thread to a ticket or `IN_PROGRESS.md` and note where. Never delete one silently.
- **Commit:** in personal repos, commit and push it under the existing commit rules.
- **Scratch plans live in the root checkout.** A repo's gitignored `docs/plans/tmp/` belongs to its root checkout (the first entry of `git worktree list`, e.g. `~/code/api`), never to a worktree: deleting a worktree deletes its untracked plans, and agents in other worktrees can't see them. In a worktree, make `docs/plans/tmp` a symlink to the root's: `root=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)"); mkdir -p "$root/docs/plans/tmp"`, move any files from a real `docs/plans/tmp` there first, then `ln -s "$root/docs/plans/tmp" docs/plans/tmp`. A symlink isn't matched by the `tmp/` ignore rule, so make sure the repo's shared `.git/info/exclude` lists `docs/plans/tmp`. In briefs, handoffs and tickets, reference scratch plans by their root-checkout path.

### Suggested execution header

Every plan file you write (under `docs/plans/` in a project repo, or anywhere else when asked to create a plan) must begin with a **Suggested execution** block right after the title, recommending which Claude model and reasoning effort would best execute the plan, and why. Format:

```markdown
# Plan Title

> **Suggested execution:** <model> with <reasoning effort> reasoning — <one-sentence rationale>. Step up to Opus for <when>; step down to Haiku for <when>.
```

Pick a tier: Opus, Sonnet or Haiku. Name the tier, not a version: it means the latest model of that tier, so the header doesn't go stale when a new version ships. Reasoning effort: low / medium (auto) / high. Base the recommendation on the task's code volume, design/visual judgment needs, algorithmic complexity, and how ambiguous the plan itself is (a detailed plan lets a cheaper model execute fine; a vague brief needs a smarter model to fill gaps).

### Claude Code plan mode

Outside harness plan mode, write the running plan directly to `docs/plans/<slug>.md` in the project repo (for team visibility and version control). During harness plan mode, the harness plan file **is** the running plan; do not also write `docs/plans/` while plan mode is active. As the first action after exiting plan mode, copy that plan file to `docs/plans/<slug>.md` in the target repo, then continue the work.
