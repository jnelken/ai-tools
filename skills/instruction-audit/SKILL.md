---
name: instruction-audit
description: >-
  Audit Jake's agent instruction overhead: the global CLAUDE.md, resolved profile fragments per repo,
  the skill listing, memory indexes, and a real end-to-end token probe that also captures claude.ai
  connectors, plugins and hooks. Ranks duplicate, stale, situational and bloated instructions by
  tokens saved and diffs against last month. Report-only; never edits instructions. Use for "audit my
  instructions", "what is eating my context", "instruction overhead", "token overhead check", or the
  monthly scheduled run (automation `instruction-audit`).
allowed-tools: Read, ToolSearch, Bash(python3 ~/.claude/skills/instruction-audit/scripts/audit.py *), Bash(python3 ~/.ai-tools/bin/agent-profile resolve *), Bash(grep *), Bash(ls *), Bash(wc *), mcp__claude_ai_Slack__slack_send_message
---

# Instruction audit

Report-only. Your only writer is `scripts/audit.py`, which writes snapshots and reports under
`~/.claude/automations/instruction-audit/`; the rest of your Bash is read-only searching (`grep`, `ls`,
`wc`, the profile resolver), because this build has no Grep or Glob tool. Never edit instruction files,
skills or settings: findings are recommendations for Jake. The only outward action is the unattended Slack
post in step 4.

`$ARGUMENTS` picks the mode:
- `unattended` (the scheduled run): always do steps 1–3, then end with the `AUTOMATION_RESULT` line.
- empty (on demand): if `~/.claude/automations/instruction-audit/reports/<this month>.md` exists, show its
  Findings and Trend and offer a refresh instead of re-running; otherwise do steps 1–3.
- `refresh`: do steps 1–3 even if this month's report exists.

## 1. Measure

```sh
python3 ~/.claude/skills/instruction-audit/scripts/audit.py measure
```

Run it with the Bash `timeout` set to 600000: it runs two real `claude -p` probes and takes a few
minutes. It prints the measurements, snapshot and previous-snapshot paths. Its model, targets and probe
directories can be overridden with `INSTRUCTION_AUDIT_*` environment variables (see the script header);
leave them alone unless Jake asks.

## 2. Analyze

Read the measurements file, the snapshot and the previous snapshot (if any). Follow
[references/analysis.md](references/analysis.md) for what counts as a win, how to verify each claim, and the
exact output format. Verify every "stale" or "duplicate" claim with Read or `grep` before writing it.

## 3. Save

Pipe the finished analysis (the markdown the rubric specifies, starting at `## Findings`) into `save`:

```sh
python3 ~/.claude/skills/instruction-audit/scripts/audit.py save <<'EOF'
<analysis markdown>
EOF
```

It prints the report path. On demand, tell Jake the top findings and the path and stop here (no Slack).

## 4. Post to Slack (unattended only)

When `$ARGUMENTS` is `unattended` and `save` succeeded, post one message with
`mcp__claude_ai_Slack__slack_send_message` (load it with ToolSearch first if it is not loaded).

- **Channel:** `#sandbox-jake`, id `C0BAK0N631R`. This is the test channel until Jake approves the format;
  then change this line to the real channel and id.
- **Format**, all numbers from the snapshot and report, nothing recomputed:

  ```
  **Instruction audit · <YYYY-MM>** (<baseline | Δ vs last month: <neutral-probe delta> tokens>)
  A fresh session loads **<neutral probe input tokens>** input tokens; instruction files explain ~**<file layers total>** of that.
  **<n> wins**, top three:
  1. <win, est. tokens saved>
  2. ...
  3. ...
  Full report: `~/.claude/automations/instruction-audit/reports/<YYYY-MM>.md`
  ```

- A Slack failure does not fail the audit: still print the `ok` line below and say in one sentence that the
  post failed.

## Unattended contract

When run by `skill-automation`, the last output line before any sign-off must be exactly one of:

```
AUTOMATION_RESULT: ok reports/<YYYY-MM>.md
AUTOMATION_RESULT: failed <one-line reason>
```

Use `failed` when `measure` or `save` errored or the analysis could not be written. The runner only marks
the month done on `ok`, so a failed run is retried on the next scheduled day.
