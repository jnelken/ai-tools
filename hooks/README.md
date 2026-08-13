# Hooks

Symlinked into `~/.claude/hooks/` by `install.sh`. Each hook needs to be **wired up** in `~/.claude/settings.json` to actually fire — symlinking the script alone does nothing.

## post-yesterdays-ccusage.sh

Posts daily ccusage summaries (Claude + Codex token usage) to a Slack channel via incoming webhook. Triggered on every Claude Code session start; catches up any days missed since the last successful post.

### Requirements

- `$SLACK_CCUSAGE_WEBHOOK_URL` exported in your shell env (incoming webhook for the target Slack channel)
- `npx`, `jq`, `curl` on `PATH`
- `ccusage >= 20` for Codex usage data (older versions still post Claude-only data)
- State dir created on first run: `~/.claude/.ccusage-state/` (gitignored from anywhere)

### settings.json wiring

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.claude/hooks/post-yesterdays-ccusage.sh\"",
            "timeout": 300,
            "async": true
          }
        ]
      }
    ]
  }
}
```

Why these options:
- `async: true` — runs in the background so session start isn't blocked while ccusage runs + the Slack POST happens.
- `timeout: 300` — caps at 5 minutes so a stuck request can't linger.
- `bash "$HOME/..."` — more portable than relying on the executable bit + shebang.

If you already have a `hooks.SessionStart` array, append the new entry rather than replacing.

### Behavior

- Reads `~/.claude/.ccusage-state/last-posted` (one line, `YYYY-MM-DD`) to track the last successfully-posted day.
- Posts one Slack message per missed day, from `last+1` through yesterday.
- Marker advances only after a successful POST, so transient Slack failures resume cleanly on the next session.
- Skips silently if any dependency (`npx` / `jq` / `curl`) is missing, or if the webhook env var is unset — never blocks a session.

### Disabling

Remove the entry from `settings.json` OR `unset SLACK_CCUSAGE_WEBHOOK_URL` to no-op.

## set-process-name.sh + set-process-title.cjs

A pair that labels every agent-spawned node process for Activity Monitor — `c-eslint-my-feature` instead of `node`. The PreToolUse hook rewrites Bash commands to export `PROCESS_NAME={agent}-{script}-{branch}`; the `.cjs` injector (loaded via `NODE_OPTIONS=--require`) applies it as the process title, falling back to the script basename so MCP servers self-label too.

Full setup, optional MCP/Codex coverage, and gotchas: [`named-node-processes.md`](named-node-processes.md).

## block-push-to-main.sh

Prevents accidental `git push` to `main` from Claude Code without breaking terminal-driven pushes. Wired into `.claude/settings.json` as a `PreToolUse` hook on Bash, it blocks any `git push` command that would target the main branch. Repos can be allowlisted (one per line) in `~/.claude/hooks/block-push-to-main.allowlist` to skip the check — `ai-tools` is included by default since agents may push to main there freely.

See the script header for the full logic (handles `--all`, `--mirror`, explicit refspecs, and implicit main-branch pushes from the current branch).

## enforce-claude-symlinks.sh

A **pre-commit hook** (runs via `git hook-path` / `.git/hooks/pre-commit`) that enforces the symlink policy at commit time: all version-controlled files in `~/.claude/` must either be symlinks pointing to `ai-tools/`, or be on a sensitive-data allowlist.

**Rationale:** `~/.claude/` should be a thin symlink layer into version-controlled content, not a source of truth. This keeps hooks, commands, and agent definitions in sync across devices while keeping sensitive local config (allowlists, tokens, session state) safely out of the repo.

To activate:
```bash
cd /path/to/ai-tools
mkdir -p .git/hooks
ln -s ../../hooks/enforce-claude-symlinks.sh .git/hooks/pre-commit
```

The hook will reject a commit from `ai-tools` if any regular files (not symlinks) exist under `~/.claude/hooks/`, `~/.claude/commands/`, or `~/.claude/agents/` **except** those on the sensitive allowlist (currently: `block-push-to-main.allowlist` and `*.json` settings files).

## pick-up-nudge.sh

A **SessionStart hook** that surfaces unresolved `IN_PROGRESS.md` content at the top of a fresh session, so it isn't missed until someone remembers to check. The counterpart to the `pick-up` skill's own lookup order (lives in `~/.claude/skills/pick-up`, not this repo).

"Unresolved" means at least one open checklist item (`- [ ]`), not just file presence — `close-out` clears resolved entries in place, so a file left with only a header/close-out-log has nothing to pick up and stays silent.

### Requirements

- `git`, `jq` on `PATH`

### settings.json wiring

Add to `~/.claude/settings.json` (alongside any existing `SessionStart` entries — append, don't replace):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/pick-up-nudge.sh\"" }
        ]
      }
    ]
  }
}
```

### Behavior

- Looks for `.claude/IN_PROGRESS.md` first, then repo-root `IN_PROGRESS.md` — same order `pick-up` uses.
- Bails silently outside a git repo, if neither file exists, or if `jq` is missing.
- Counts open (`- [ ]`) checklist lines; if zero, stays silent even when the file exists.
- Emits via `hookSpecificOutput.additionalContext` telling the assistant to mention the open-item count and offer `/pick-up` — explicitly instructed not to read the file or act on its items itself, since `pick-up` owns that flow (plan-mode read, verification against current state, then user approval).
- No cooldown, unlike the hygiene nudges below — every session start in a repo with real open work should surface it, not just once a day.

### Disabling

Remove the entry from `settings.json`.

## claude-symlink-hygiene.sh

A **SessionStart hook** (runs at the top of every Claude Code session) that nudges about unsymlinked files in `~/.claude/` before they become a blocker.

When it detects new regular files in `~/.claude/{hooks,commands,agents}/`, it emits a friendly message with fix instructions. Uses a 24-hour cooldown per file-set so it only nudges once a day even if you open the same repo multiple times.

### settings.json wiring

Already wired in by default (added during initial setup). If missing, add:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.claude/hooks/claude-symlink-hygiene.sh\""
          }
        ]
      }
    ]
  }
}
```

### Allowlist

Files that are OK to be local (not symlinked) are listed in the `allowlist` array at the top of the script. Add entries there if you have legitimate local-only files in `~/.claude/`.

## Personal-repo gate

`personal-repo-hygiene-check.sh` and all three `session-doc-*.sh` hooks share the same gate: they only run inside repos whose `origin` remote is owned by an allowed GitHub owner (default `jnelken`, override with `PERSONAL_REPO_OWNERS`, comma-separated). Concentro-Inc repos (`woodrow`, `api`, and anything cloned from that org) — or any other org — are silently skipped. A repo with **no** configured `origin` remote is treated as personal (assumed local-only scratch work, not a cloned company repo).

## personal-repo-hygiene-check.sh

Nudges repo conventions at session start, inside any directory with git history. Four best-effort checks, never blocking:

1. Plan-looking `*.md` files (filename matches `/plan/i`, or opens with a `**Suggested execution:**` line) sitting outside `docs/plans/`/`docs/plans/archive/` — nudge to relocate.
2. If a `docs/adr/`, `docs/decisions/`, or `adr/` dir already exists, flag entries that don't follow `NNNN-title.md` + a `Status:` field. Silent if no ADR dir exists at all — this doesn't force ADR adoption on every repo.
3. `PRODUCT.md` **and** `DESIGN.md` both missing from the repo root — suggest `/impeccable`.
4. `.superset/config.json` missing — suggest `/superset-config`.

### Requirements

- `jq`, `shasum` on `PATH` (both standard on macOS)

### settings.json wiring

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.claude/hooks/personal-repo-hygiene-check.sh\""
          }
        ]
      }
    ]
  }
}
```

If you already have a `hooks.SessionStart` array, append the new entry rather than replacing.

### Behavior

- Bails silently (no output) outside a git repo, or if `jq`/`shasum` are missing.
- Findings are emitted via `hookSpecificOutput.additionalContext`, so they land as session-start context rather than a blocking prompt.
- State: `~/.claude/state/hygiene-nudges/<repo-hash>.json` tracks the last nudge time per repo. Re-nudges at most once every `HYGIENE_NUDGE_COOLDOWN_HOURS` (default `24`) so a repo you open repeatedly in a day isn't renudged every session.

### Disabling

Remove the entry from `settings.json`, or `export HYGIENE_NUDGE_COOLDOWN_HOURS=999999` to effectively silence it.

## session-doc-start.sh + session-doc-stop.sh + session-doc-end.sh

Gives every Claude Code session running inside a git worktree a small, live "what's happening here right now" doc at `.claude-sessions/<session_id>.md` (worktree root) — so another device or agent glancing at the directory can see active work in flight, including a session ID, branch, host, and a handoff-oriented body. Meant as a **live presence indicator**, not a durable log: it's created at session start, kept fresh through the session, and removed on clean exit.

- **`session-doc-start.sh`** (`SessionStart`) — creates/refreshes `.claude-sessions/<session_id>.md` (preserving `started_at` and the body across a resume), and sweeps every *other* doc in the same dir whose `updated_at` is older than `SESSION_DOC_STALE_HOURS` (default `6`). Injects a standing instruction via `additionalContext` telling the assistant to keep the body updated with 2–4 sentences on current work + next steps.
- **`session-doc-stop.sh`** (`Stop`, fires after every assistant turn) — mechanical-only refresh (no LLM call, since this fires every turn): bumps `updated_at` and records a one-line `git diff --stat` summary. Keeps the staleness signal accurate between narrative updates.
- **`session-doc-end.sh`** (`SessionEnd`) — deletes this session's own doc on clean exit.

Cleanup is **staleness-based, not liveness-based** — a doc is swept purely by its `updated_at` age, never by checking whether the session that wrote it is still running. A crashed or `kill -9`'d session's doc just ages out on the next `SessionStart` sweep in that worktree; nothing needs to detect the crash.

### Requirements

- `jq` on `PATH`
- A global git exclude for `.claude-sessions/` (see `~/.gitignore_global` / `git config --global core.excludesFile`) — these docs are never meant to be committed.

### settings.json wiring

Add to `~/.claude/settings.json` (alongside any existing `SessionStart` entries — append, don't replace):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/session-doc-start.sh\"" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/session-doc-stop.sh\"" }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/session-doc-end.sh\"" }
        ]
      }
    ]
  }
}
```

### Known limitations

- Cross-device pickup rides on whatever syncs the worktree itself (e.g. Dropbox for repos under `~/Dropbox/code`). Root checkouts (`~/code/woodrow`, `~/code/api`) and sswt workspaces won't sync a doc to another device on their own.
- The narrative body is only as good as the assistant keeping it updated — nothing mechanically enforces that beyond the frontmatter timestamps.

### Relationship to NEXT-STEPS.md and IN_PROGRESS.md

Three different repo-root files can legitimately coexist under `~/Dropbox/code`, each owned by a different mechanism with different update semantics — don't merge them:

- **`.claude-sessions/<id>.md`** (this hook family) — live, per-session, ephemeral. Gone the moment a session exits cleanly; only lingers past that if the session crashed, until the next staleness sweep.
- **`NEXT-STEPS.md`** ([[wrapup-repos]]) — one wrap-up run's snapshot, overwritten wholesale each time it runs. `wrapup-repos` also **reads** `.claude-sessions/*.md` as one of its signal sources: a live doc makes it skip that repo entirely (same tier as a mid-rebase repo); a crashed session's doc feeds its "understand the direction" step, since it's the only record of what that session was doing.
- **`IN_PROGRESS.md`** ([[close-out]]) — a durable, reconciled log of open questions across many sessions, appended-to rather than overwritten.

### Disabling

Remove all three entries from `settings.json`.
