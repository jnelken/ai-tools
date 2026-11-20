# Instruction migration index

Original source: dotfiles/claude/CLAUDE.md. Section text is preserved at migration; environment grants apply only to their classified targets.

| Original section | Destination |
|---|---|
| PR Descriptions | development/shared.md |
| Post-PR Workflow | development/work.md |
| Superset Workspaces (sswt) | machine/superset.md (Mac machines) |
| Datadog | development/work.md |
| Linear | development/shared.md |
| Session Retrospectives and Gotcha Docs | development/shared.md |
| Committing Work | development/shared.md |
| Golden Paths | development/shared.md |
| Refactoring vs Patching | development/shared.md |
| Git | development/personal.md |
| Response Sign-off Glyphs | providers/claude-development.md |
| Context Usage: Recommend /handoff | providers/claude-development.md |
| ROADMAP.md in Personal Repos | development/personal.md |
| File Location | reference/legacy-maintenance.md |
| Keeping CLAUDE.md Lean | reference/legacy-maintenance.md |
| Parallelize with Subagents | providers/claude-development.md |
| Budgeted Fan-out (budget/fanout routing split out of Parallelize with Subagents) | providers/claude-personal.md |
| Above-Sonnet Models (Opus, Fable): Prefer Sub-Agents | providers/claude-development.md |
| Lunch Money MCP Cache | machine/personal-mac.md |
| Cross-Session Continuity | providers/claude-development.md |
| Who Jake Is (cross-project context) | development/personal.md |
| Plan Files | development/shared.md (pointer), reference/plan-files.md (2026-11-20) |

The previous parent code-CLAUDE.md is preserved in development/frontend.md and loaded on demand. Global Codex installation preferences are preserved in shared.md. Legacy maintenance guidance is retained as reference; the new installer owns file locations.

Later relocations (2026-11-20, from the monthly instruction-overhead audit): *Playwright: one browser per machine* detail moved to reference/playwright-slot.md, *Secrets and `.env` files` detail to reference/secrets-env.md, the *Invariants* procedure to skills/peer-review/templates/invariants/README.md, and *Plan Files* to reference/plan-files.md. Each source fragment keeps the hard rule and a one-line pointer that says when to read the file. *Post-PR Workflow* now keeps only the standing authorizations and the `#product-updates` question; mechanics live in the post-pr and create-pr skills and post-pr-trunk-flow.md.
