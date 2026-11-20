---
name: instruction-audit
description: >-
  Audit Jake's agent instruction overhead: the global CLAUDE.md, resolved profile fragments per repo,
  the skill listing, memory indexes, and a real end-to-end token probe that also captures claude.ai
  connectors, plugins and hooks. Ranks duplicate, stale, situational and bloated instructions by
  tokens saved and diffs against last month. Report-only; never edits instructions. Use for "audit my
  instructions", "what is eating my context", "instruction overhead", "token overhead check", or the
  monthly scheduled run (automation `instruction-audit`).
allowed-tools: Read, Grep, Glob, Bash(python3 ~/.claude/skills/instruction-audit/scripts/audit.py *)
---

# Instruction audit

Report-only. The only command you run is `scripts/audit.py`; it writes snapshots and reports under
`~/.claude/automations/instruction-audit/` and nothing else. Never edit instruction files, skills or
settings: findings are recommendations for Jake.

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
exact output format. Verify every "stale" or "duplicate" claim with Read, Grep or Glob before writing it.

## 3. Save

Pipe the finished analysis (the markdown the rubric specifies, starting at `## Findings`) into `save`:

```sh
python3 ~/.claude/skills/instruction-audit/scripts/audit.py save <<'EOF'
<analysis markdown>
EOF
```

It prints the report path. Then tell Jake the top findings and the path (on demand), or print only the
final line (unattended).

## Unattended contract

When run by `skill-automation`, the last output line before any sign-off must be exactly one of:

```
AUTOMATION_RESULT: ok reports/<YYYY-MM>.md
AUTOMATION_RESULT: failed <one-line reason>
```

Use `failed` when `measure` or `save` errored or the analysis could not be written. The runner only marks
the month done on `ok`, so a failed run is retried on the next scheduled day.
