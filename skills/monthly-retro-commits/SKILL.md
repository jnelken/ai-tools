---
name: monthly-retro-commits
description: Export raw commit-level effort data as TSV (lines changed, repo, date, subject) for a given author and month via `fp retro:commits`, without summarizing or reformatting. Use when asked for the underlying commit numbers, line-change stats, or raw retro data. For the written retrospective built on top of this, see monthly-retro.
---

# Monthly Retro Commits Export Skill

Use this skill when asked to export commit-level effort data for monthly retrospectives.

## Source of truth

- `fp retro:commits`

## Standard run command

```bash
fp retro:commits --repo /Users/jake/code/woodrow --author "Jake Nelken" --since "YYYY-MM-01" --until "YYYY-MM-31"
```

## Required behavior

1. Run the script with the requested author and date window.
2. Keep output as TSV in descending `lines_changed` order.
3. Do not summarize or reformat unless the user asks.
4. If no commits are returned, report that explicitly and include the exact command used.

## Output format

The script emits:

```text
# Monthly retro commits
# Author: <name>
# Window: <since> -> <until>
# Columns: lines_changed  repo  date  subject
#
<tsv rows>
```

## Notes

- Defaults to the previous full month when `--since`/`--until` are omitted.
- Supports overriding repo paths via `--woodrow` and `--api`.
