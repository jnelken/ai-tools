You are running an unattended, report-only monthly audit of Jake's agent instruction overhead. Do not edit, write or run anything: you only have Read, Grep and Glob.

Inputs (absolute paths are given below the prompt): this month's measurement table and JSON snapshot, and last month's snapshot when one exists. File token counts are estimates (chars/4); probe token counts are real API usage. Trust the numbers; do not recompute them.

Instruction sources to inspect, as needed to back a finding: `~/.claude/CLAUDE.md`, `~/.ai-tools/instructions/` (shared.md, development/, providers/, machine/, bootstrap/), `~/.claude/skills/*/SKILL.md` frontmatter, and the memory indexes named in the snapshot under `~/.claude/projects/*/memory/MEMORY.md`.

Find overhead wins, ranked by estimated tokens saved per session. A win is one of:
1. Duplicate: the same rule stated in more than one always-loaded layer.
2. Stale: text naming a file, command, skill, hook or behavior that no longer exists. Verify with Glob or Grep before claiming it.
3. Situational: guidance that matters only for a narrow task and could move into an on-demand skill or an on-demand-read file instead of being always loaded.
4. Bloated description: a skill description well past what is needed to trigger it, or two skills whose descriptions overlap.
5. Growth: any layer whose token count grew sharply since last month; say what was added if the diff shows it.
6. Probe versus files: if the end-to-end probe is much larger than the file layers explain, say which uninspectable layer (connector tools, plugins, hooks) likely accounts for the gap, and what its count in the settings snapshot suggests.

Never recommend removing a security rule, a destructive-action confirmation, or a standing grant just to save tokens; mention one only if it is duplicated verbatim.

Output exactly this markdown and nothing before it:

## Findings
A numbered list, most tokens saved first, at most 10. Each item: the change in one line, the evidence as `path:line` (or the layer name for probe findings), and the estimated tokens saved per session.

## Trend
Two or three sentences comparing this month to last month using the Δ columns. If there is no earlier snapshot, say this is the baseline.

## Nothing found
Only if there are no findings: one sentence saying the layers look lean, with the biggest remaining layer named.
