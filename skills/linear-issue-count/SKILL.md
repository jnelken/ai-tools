---
name: linear-issue-count
description: Count unarchived Linear issues in Jake's personal Linear workspace (`jnelken`, never Concentro) against the 250-issue plan cap, record it to a running history, and flag when it nears the cap. Use on demand ("/linear-issue-count", "how many Linear tickets do I have", "am I near the Linear limit") or via the weekly automation `linear-issue-count` (runs on Haiku through `skill-automation`).
allowed-tools: Bash(python3 ~/.claude/skills/linear-issue-count/scripts/count.py *), Bash(python3 ~/.claude/skills/linear-issue-count/scripts/count.py), Bash(python3 /Users/jake/.claude/skills/linear-issue-count/scripts/count.py *), Bash(tail *)
model: claude-haiku-5-5
---

# Linear issue count

Linear's cap counts every **unarchived** issue (done and canceled included until they auto-archive).
The script counts them per workspace, appends to `~/.claude/automations/linear-issue-count/state/history.jsonl`,
and fires a macOS notification at 80% of the cap (`LINEAR_ISSUE_CAP`, default 250).

1. Run exactly `python3 ~/.claude/skills/linear-issue-count/scripts/count.py` (no `cd`, no env prefix). It needs no arguments and makes read-only API calls.
2. Report its output as-is: count, percent of cap, change since the last record, state breakdown. Add the trend from
   `tail -n 8 ~/.claude/automations/linear-issue-count/state/history.jsonl` only if asked.
3. At 80%+, say that completed and canceled issues are the cheapest to archive; do not archive anything yourself.

## Unattended contract

When `$ARGUMENTS` is `unattended`, the last output line must be exactly one of:

```
AUTOMATION_RESULT: ok <workspace>=<count> ...
AUTOMATION_RESULT: failed <one-line reason>
```

Use `failed` if the script exited non-zero.
