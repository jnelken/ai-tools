You are implementing slice E1 ("Running planning docs") of the quota-attuned fan-out plan in Jake's ai-tools repo. This Superset workspace's worktree is your checkout. Never edit ~/.ai-tools (the read-only deploy clone).

Read first: docs/plans/quota-attuned-fanout.md (section E, the E1 bullet, and the Decisions), CLAUDE.md, instructions/development/shared.md (the *Plan Files* section), skills/pick-up/SKILL.md and skills/close-out/SKILL.md. That doc is itself the model of a running doc: Status, Decisions, Findings with evidence, Open threads, Approach. Use it as the worked example.

Build:
1. **Rule.** In instructions/development/shared.md, under *Plan Files*, add a short "Running planning docs" rule:
   - **Open it early.** Any planning or investigation session opens a running doc right after orientation: `docs/plans/<slug>.md` in the target repo, or, while in harness plan mode, the harness plan file, which is copied to docs/plans/ on exit.
   - **Sections:** Status, Decisions, Findings (with evidence), Open threads (checkbox list), Approach.
   - **Keep it current:** update it after each finding or agent result.
   - **Closing open threads:** only with an answer (tick and annotate), or by moving the thread to a ticket or IN_PROGRESS.md (note where). Never delete silently.
   - **Commit:** in personal repos it is committed and pushed, per the existing commit rules.
   
   Keep it tight and skimmable, in the same voice as the surrounding file. Don't duplicate the existing Suggested-execution rule; extend it.
2. **pick-up skill.** It also sweeps `docs/plans/*.md` (not archive/) for unchecked `- [ ]` open threads. In interactive mode it presents them alongside IN_PROGRESS.md items. In `sweep` mode it only flags items that hard evidence shows are done or tracked, matching its existing evidence bar.
3. **close-out skill.** Its sweep includes `docs/plans/*.md` open threads touched in this session. Each must be answered, moved to a ticket or IN_PROGRESS.md, or explicitly left open with a reason, before the session counts as closed.
4. Update README.md's skills table rows if their descriptions change.

Rules:
- Docs and skills only. Do not touch automations/, lib/, hooks/ or install.sh; other slices own them.
- Tick E1 in the plan doc's Open threads / Approach with what you did.
- Commit coherently, `git push -u origin <branch>`, and open a ready PR with `gh pr create`, body `## Summary` / `## Changes` / `## Validation` (manual QA steps only). Do not merge. End with the PR URL.
