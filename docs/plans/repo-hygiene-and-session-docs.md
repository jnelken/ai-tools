# Repo hygiene checks + per-session handoff docs

> **Suggested execution:** Sonnet 4.6 with medium reasoning — mostly mechanical bash/jq hook scripting against a well-specified design below, no algorithmic complexity. Step up to Opus 4.7 only if the real Claude Code hook JSON schema (field names for `session_id`, `hook_event_name`, `cwd`) turns out to differ from what's assumed here and needs live experimentation to pin down; step down to Haiku 4.5 for nothing here — the scripts are short but the correctness bar (don't corrupt `~/.claude/settings.json`, don't leak state across sessions) warrants Sonnet throughout.

## Context

Two independent but co-located conventions, both delivered as `SessionStart`/`Stop`/`SessionEnd` hooks in this repo (`hooks/`), following the existing pattern (`post-yesterdays-ccusage.sh`, `set-process-name.sh`) — symlinked into `~/.claude/hooks/` by `install.sh`, wired manually into `~/.claude/settings.json` per `hooks/README.md`. Nothing here is a slash-command skill (`skills/`); both pieces are passive/automatic, so `hooks/` is the right home, consistent with this repo's existing split.

1. **Repo hygiene nudges** — at session start, in any directory with git history, check: plan docs live under `docs/plans/` or `docs/plans/archive/`; if the repo already has an ADR directory, its entries follow the convention; if `PRODUCT.md` and `DESIGN.md` are both missing, nudge `/impeccable`.
2. **Session handoff docs** — every session running inside a git worktree gets a small, live "what's happening here right now" doc, so another device or agent glancing at the directory can see active work in flight without asking.

## Design decisions (resolved with Jake)

- **Location, no git tracking**: everything lives at the worktree root, gitignored via a **global** git `core.excludesFile` (not each repo's tracked `.gitignore` — avoids touching files inside every repo). Cross-device pickup rides on Dropbox sync for repos under `~/Dropbox/code`; root checkouts (`~/code/woodrow`, `~/code/api`) and sswt workspaces won't sync automatically — accepted limitation, not solved here.
- **Cleanup is staleness-based, not liveness-based**: no cross-check against `~/.claude/session-env/<id>` aliveness. A file is stale (and safe to GC) purely by `updated_at` age, so crashed/`kill -9` sessions self-heal on the next hygiene sweep without needing to detect "is this session still running."
- **Session ID is always embedded** in the doc (frontmatter), so any file found on disk is traceable back to the session that wrote it.
- **One file per session** (session-scoped filename) — no single shared `SESSION.md` to clobber when two sessions run in the same directory (interactive + a background/cron agent, or two terminal tabs).
- **Hygiene nudges fire on `SessionStart` only** — no cron sweep. Simple, contextual, no extra scheduling infra to maintain.

## Component 1 — `hooks/personal-repo-hygiene-check.sh` (SessionStart)

Bails immediately (exit 0, silent) unless `git rev-parse --is-inside-work-tree` succeeds. Then, four independent checks, each best-effort / never blocking:

1. **Plan docs location** — scan the repo root (not recursive into `.git`, `node_modules`, `vendor`) for `*.md` files that look like plan docs: filename matches `/plan/i`, or content's first heading is followed by a `> **Suggested execution:**` line (this repo's own plan-file convention). Any match not already under `docs/plans/` or `docs/plans/archive/` → nudge to relocate it.
2. **ADR practice** — look for `docs/adr/`, `docs/decisions/`, or `adr/`. If none exists, say nothing (adopting ADRs isn't being forced on every repo — "respected" implies checking a practice that's already in use). If one exists, check entries loosely follow `NNNN-title.md` with a status field; flag stragglers.
3. **PRODUCT/DESIGN nudge** — if **both** `PRODUCT.md` and `DESIGN.md` are absent from the repo root, suggest `/impeccable` can establish them. (Interpreting "no PRODUCT and DESIGN.md" as both-missing, not either-missing — a repo with one of the two isn't nudged.)
4. **superset-config** — if `<repo-root>/.superset/config.json` is absent, nudge `/superset-config` to set up setup/run/teardown scripts. Unconditional (unlike check 2/3, no "already in use" gate) — per your existing `CLAUDE.md` rule, feature work always happens in a Superset workspace, so every repo you'd feature-branch in is expected to carry a committed config eventually. A hook can only cheaply check file existence, not "did superset-config actually run and produce a sane config" — that judgment stays with the skill itself when invoked.

Output goes through the hook's `additionalContext` JSON field so it lands as context for the assistant at session start, not a blocking prompt. To avoid nagging every single session in a repo you open ten times a day, each check is gated by a small per-repo cooldown file under `~/.claude/state/hygiene-nudges/<repo-hash>.json` (default: once per 24h per repo), same state-dir pattern `post-yesterdays-ccusage.sh` already uses.

Validated against real repos: `dubsketch` has `PRODUCT.md` but no `DESIGN.md` → no nudge (has one of two). No repo currently has an ADR directory → check 2 stays silent everywhere until one is adopted deliberately.

## Component 2 — session handoff docs

Directory: `.claude-sessions/` at the worktree root (`git rev-parse --show-toplevel`), globally gitignored. File: `.claude-sessions/<session_id>.md`.

Frontmatter (mechanical, hook-written):
```yaml
session_id: <uuid>
started_at: <ISO8601>
updated_at: <ISO8601>
branch: <git branch>
host: <hostname -s>
cwd: <worktree root>
```
Body: free text — "active work" + "handoff" notes, meant to be kept current by the assistant itself (only Claude knows the narrative), not fabricated by a hook. `SessionStart`'s `additionalContext` output will carry a short standing instruction: keep this file's body updated with 2–4 sentences on current work and next steps, especially before pausing or ending.

Three hooks:
- **`hooks/session-doc-start.sh`** (`SessionStart`) — bails if not inside a git worktree. Creates `.claude-sessions/<session_id>.md` with frontmatter + empty body stub. Also runs the staleness sweep: any `*.md` in `.claude-sessions/` (this worktree only) with `updated_at` older than `SESSION_DOC_STALE_HOURS` (default 6, env-overridable) gets deleted.
- **`hooks/session-doc-stop.sh`** (`Stop`, fires after each assistant turn) — mechanical-only, no LLM call: bumps `updated_at` and refreshes a one-line heuristic status (e.g. `git diff --stat` summary) so freshness stays accurate for the staleness sweep without per-turn cost.
- **`hooks/session-doc-end.sh`** (`SessionEnd`) — deletes this session's own file (best-effort; if it's missed on a crash, the next `session-doc-start.sh` sweep GCs it once stale).

## New files in this repo

- `hooks/personal-repo-hygiene-check.sh`
- `hooks/session-doc-start.sh`
- `hooks/session-doc-stop.sh`
- `hooks/session-doc-end.sh`
- `hooks/README.md` — new sections documenting all four, matching the existing write-up style (requirements, settings.json wiring, behavior, disabling)

## Global config touched (outside this repo)

- `~/.claude/settings.json` — add hook entries under `SessionStart`, `Stop`, `SessionEnd` (currently unset for these four scripts; existing entries for other hooks stay untouched, append not replace).
- Global git `core.excludesFile` — currently **unset** (checked: `git config --get core.excludesFile` returns nothing). Needs creating (e.g. `~/.gitignore_global`) and configuring, adding `.claude-sessions/` to it. This is a global git config change — flagging before applying since it's a shared setting, not scoped to one repo.

## Open limitations (accepted, not solved here)

- Non-Dropbox repos (`~/code/woodrow`, `~/code/api`, sswt workspaces) get session docs and hygiene checks locally, but they won't sync to another device by themselves.
- Staleness threshold (6h default) is a guess; may need tuning after real usage.
- The narrative body quality depends on the assistant actually following the standing instruction — nothing enforces it mechanically beyond the frontmatter timestamps.

## Rollout order

1. `hooks/personal-repo-hygiene-check.sh` + README section (self-contained, easiest to validate).
2. `.gitignore_global` setup + `core.excludesFile` config.
3. `session-doc-start.sh` / `session-doc-stop.sh` / `session-doc-end.sh` + README section.
4. Wire all four into `~/.claude/settings.json`.
5. Dogfood in this repo first (already created `docs/plans/` as part of writing this doc — check 1 now has a home to point to).
