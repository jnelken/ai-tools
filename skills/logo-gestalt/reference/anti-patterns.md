# anti-patterns — what to reject

Load before drawing or matching. Use as kill criteria in `critique`.

## Collage traps

| Anti-pattern | Why it fails | Fix |
|--------------|--------------|-----|
| Icon A on top of icon B | Two silhouettes; no hinge | `match` for shared edge |
| Semi-transparent overlap | Reads as cheap composite | Boolean union/subtract |
| Mini-icon in corner | Secondary is sticker, not discovery | Void-based secondary |
| Drop shadow separation | Fake depth between glued parts | Single-plane gestalt |

## Cliché inventory

Reject unless brief explicitly subverts:

- Lightbulb, brain, rocket, puzzle piece (generic "AI/startups")
- Globe with orbit swoosh
- Chat bubble with three dots
- Infinite loop / möbius "innovation"
- Letter folded into fake 3D without brand reason

## Construction sins

- Hairline strokes as structure (die at 32px)
- Gradient required to see shape
- More than 2–3 colors in mark phase
- Text converted to paths as the whole logo (wordmark is separate track)
- Excessive anchor points / Figma export noise

## Gestalt fakes

- **Thematic match only** — "both mean speed" with no shared geometry
- **Forced secondary** — only visible when told the pun
- **Symmetric mirror gimmick** — reads as Rorschach, not brand
- **Negative space that needs caption**

## Process anti-patterns

- Skipping `preview` before `critique`
- Critiquing SVG code instead of pixels
- One candidate only (no breadth)
- Ignoring `BRAND.md` non-negotiables for taste
- Endless tweak without `distill` decision

## Originality

- Too close to famous marks in same category (legal + boring)
- "Startup gradient circle" default

When in doubt: squint at 32px mono. If it whispers "template," kill it.

## Positive counterexamples

See [gestalt.md](gestalt.md) and [examples/cursor-case.md](../examples/cursor-case.md) for hinge-done-right calibration.
