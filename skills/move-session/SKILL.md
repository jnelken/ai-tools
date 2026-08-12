---
name: move-session
description: Find a lost Claude Code session by remembered keywords and resume or re-home it in the right project directory. Use when a past session is missing from the `claude --resume` picker — typically because the repo was moved/renamed on disk so old sessions are stranded under a stale encoded project path, or because the session ran from an unexpected cwd — or when the user asks "find the session where I was discussing X", "I can't find the session I did X in", "move this session to this project", "relocate a claude session", "why isn't my old session in the resume picker". Covers keyword search across transcripts and prompt history, global resume by session ID, and re-homing with /cd.
---

# Move Session

Find a Claude Code session the user has lost track of, hand them the resume
command, and re-home it into the right project directory so it shows up in
that directory's `--resume` picker going forward.

## How sessions are stored

Each session is one JSONL transcript at:

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
```

`<encoded-cwd>` is the session's working directory with `/` replaced by `-`
(e.g. `/Users/jake/code/ai-tools` → `-Users-jake-code-ai-tools`). This is why
moving or renaming a repo on disk strands its sessions: they stay filed under
the old encoded path and stop appearing in the new location's picker.

There are no sidecar files or indexes tied to the transcript — but the JSONL
format is internal and changes between releases, so prefer the built-in
mechanisms below over moving files by hand.

## Finding a lost session by keyword

Delegate this search to a sub-agent when it's more than one quick grep — the
transcripts are large and the matching lines are noisy.

1. **Start with `~/.claude/history.jsonl`.** It indexes every prompt the user
   ever typed, with the project directory each one ran in. Grepping it for
   topic keywords is the fastest way to enumerate candidate sessions, and it
   was the decisive move the last time this hunt was run:

   ```bash
   rg -i 'keyword' ~/.claude/history.jsonl
   ```

2. **Grep transcripts across ALL projects** — not just the current one, since
   the whole reason the session is lost is usually that it's filed elsewhere:

   ```bash
   rg -li 'keyword' ~/.claude/projects/ --glob '*.jsonl'
   ```

3. **Assume the remembered phrasing is wrong.** Search loose substrings and
   variants, case-insensitive, before concluding a session doesn't exist.
   (Real example: the user remembered "weekly-slack-changelog-and-announcements";
   the session actually said "weekly-product-changelog-and-announcement" — an
   exact-phrase grep found nothing.)

4. **Rank by mtime** (`ls -lt` the matches) — the wanted session is usually
   recent. Exclude the currently running session (its ID is on the statusline
   after 🪪).

5. **Verify before reporting**: peek at the matching lines and a few user
   messages to confirm it's the actual discussion, not an incidental mention.
   Report the session ID (filename minus `.jsonl`), the decoded working
   directory, last-modified time, and a short proving excerpt.

## Resuming and re-homing (the supported path)

Two built-ins make manual file moves unnecessary:

- **`claude --resume <session-id>` works globally** (v2.1.223+): it searches
  the current project and its worktrees first, then every other project on
  the machine. A stranded session is resumable from anywhere by ID.
- **`/cd <dir>` re-homes the session** (v2.1.169+): run inside the resumed
  session, it relocates the transcript to `<dir>`'s project storage, so it
  appears in that directory's picker afterward and leaves the old one.

So the full recipe after a repo move is:

```bash
cd /new/repo/location
claude --resume <session-id>   # global lookup finds it under the stale path
# then inside the session:
/cd /new/repo/location         # re-homes it permanently
```

## Manual move (fallback only)

If the installed version predates global resume, moving the file works:

```bash
mkdir -p ~/.claude/projects/<new-encoded-cwd>
mv ~/.claude/projects/<old-encoded-cwd>/<session-id>.jsonl \
   ~/.claude/projects/<new-encoded-cwd>/
rmdir ~/.claude/projects/<old-encoded-cwd>   # only if now empty
```

Gotchas:

- The transcript's internal `cwd` fields still record the old path. Harmless —
  a resumed session adopts the directory it's launched from — but files the
  old session referenced must exist at the same *relative* paths in the new
  location for the context to make sense.
- Don't script bulk moves against the format; it's version-unstable. One file
  for one recovery is fine.

## What NOT to do

- Don't edit or rewrite transcript contents (e.g. to "fix" the stale `cwd`
  fields) — the format is internal.
- Don't assume an empty grep means the session is gone; try `history.jsonl`
  and looser keywords first. Transcripts are only deleted by explicit cleanup,
  not by repo moves.
