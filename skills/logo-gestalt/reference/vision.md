# vision — multimodal critique protocol

Adapted from Anthropic multimodal cookbooks for logo gestalt review. Use for `preview`, `critique`, and hinge debugging.

## Sources (read for depth)

- [Best practices for using vision with Claude](https://platform.claude.com/cookbook/multimodal-best-practices-for-vision) — role prompting, visual prompting, few-shot, multi-image
- [Vision overview](https://platform.claude.com/docs/en/build-with-claude/vision) — image placement, multi-image labeling
- [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) — few-shot structure, zoom/crop
- [Multimodal crop tool recipe](https://platform.claude.com/cookbook/multimodal-crop-tool) — magnify small regions (hinges at 32px)
- [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — canonical few-shot examples over rule laundry lists

## Role + scan

Open every vision critique with role and task:

```
You are a senior identity designer specializing in monogram marks and gestalt figure-ground.
Scan each image for silhouette clarity at the smallest size first, then evaluate hinge geometry at hero size.
Do not praise; list failures before strengths.
```

## Multi-image messages

- Put **images before** the instruction text when possible.
- Label each: `Image 1 — candidate A, 32px favicon-sm`, `Image 2 — candidate A, 1024 hero`, …
- Compare candidates **in one turn** so relative ranking is grounded.

## Visual prompting

Draw attention inside the image plane:

- Reference **regions**: "the vertical stroke at center x", "top-left facet"
- For hinge work, attach `hinge-diagram.svg` style reference or orange dashed overlay exports
- Ask: "Is this edge shared or two overlapping strokes?" with arrow/crop on hinge

## Few-shot calibration

Include 1–2 canonical examples in `CRITIQUE.md` or the vision message:

| Example type | Teaches |
|--------------|---------|
| Strong gestalt (Cursor-class) | Shared spine, single silhouette |
| Weak collage | Two icons, overlapping transparency |
| Pass/fail 32px pair | Same mark at 512 vs 32 |

Keep examples **diverse** but **short** — 3–5 lines of expected output format each.

## Describe-then-judge

Two-step chain reduces logo hallucination:

1. **Describe**: "List only the shapes you see in black silhouette. Do not name brand or intended metaphor."
2. **Judge**: Compare description to `CONSTRUCTION.md` intended reads. Flag extra shapes or missing hinge.

## Zoom / crop

When detail is small (favicon, hinge):

- Crop hero to hinge bounding box (≈128–256px square) before attach
- Or use a crop tool / second-pass zoom on the hinge region
- Never critique 32px hinge detail from prose alone

## Optional sub-agents

For large batches (5+ candidates × 4 sizes):

| Sub-task | Input | Output |
|----------|-------|--------|
| Scale pass | All 32px PNGs | pass/fail table |
| Hinge pass | Hero + hinge crop for gestalt only | shared-edge yes/no |
| Originality pass | Hero + competitor refs from brief | collision notes |

Merge sub-results into one `CRITIQUE.md` — user sees single verdict table.

## Output format (enforce)

```markdown
### [candidate-id]
- 32px: PASS | FAIL — [reason]
- Primary read: [what viewer sees]
- Secondary read: [discovered | forced | none]
- Hinge: [shared edge | collage | n/a]
- Verdict: KEEP | REMIX | REVISE | KILL
```

## Anti-patterns in vision workflow

- Critiquing from SVG path data without raster render
- Single hero image only (misses scale failure)
- Asking "is this good?" without criteria
- Letting model **name** the intended symbol before describe step (biases gestalt judgment)

## Handoff

Results feed [critique.md](critique.md) → `distill` / `remix` / `construct`.
