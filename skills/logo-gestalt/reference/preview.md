# preview — raster pipeline and review board

Convert SVG candidates to **pixels** for critique. Prose about "how it might look" is not a preview.

## Outputs

| Artifact | Path |
|----------|------|
| PNGs per mark × size | `.logo-gestalt/previews/` |
| Review board | `.logo-gestalt/board.html` |
| Size stress ladder | `.logo-gestalt/size-ladder.html` |
| Preview manifest | `.logo-gestalt/PREVIEW.md` (optional, from template) |

## Preferred: skill script

From project root:

```bash
python3 <skill-dir>/scripts/build_previews.py \
  --marks-dir .logo-gestalt/marks \
  --out-dir .logo-gestalt/previews \
  --board .logo-gestalt/board.html \
  --ladder .logo-gestalt/size-ladder.html
```

`<skill-dir>` is the directory containing this skill's `SKILL.md` (e.g. `~/.claude/skills/logo-gestalt` or repo `skills/logo-gestalt`).

### Sizes (fixed)

| Key | Px | Use |
|-----|-----|-----|
| hero | 1024 | Detail, hinge annotation |
| icon | 512 | App icon source |
| favicon | 64 | Dock / tab |
| favicon-sm | 32 | Kill test |

### Raster backends (auto-detected)

Script tries in order: `rsvg-convert`, `inkscape`, `magick`/`convert`. If none installed, HTML still generates; PNG cells show placeholder text.

## board.html

Dark gallery template: `<skill-dir>/templates/board.html`. Script injects candidate rows with hero + icon + favicon columns.

Manual fallback: copy template, replace `{{MARK_ROWS}}` with img tags pointing at previews.

## Size stress

`size-ladder.html` shows **one row per candidate** at 512 → 256 → 128 → 64 → 32. Purpose: catch strokes, holes, and hinge noise that die small.

## Hinge annotations

For gestalt candidates, export an extra **hero crop** or overlay:

- Use `docs/sample-marks/hinge-diagram.svg` as annotation style reference (dashed hinge line).
- Save optional `previews/candidate-N-hinge.png` (1024 with overlay) for vision critique.

## PREVIEW.md manifest

List files, sizes, generation timestamp, converter used. Helps `critique` attach the right images.

## Vision prep

Before `critique`, load [vision.md](vision.md):

- Attach **multiple images** (board screenshots or PNG paths).
- Label: `Image 1: hero`, `Image 2: 32px favicon-sm`, etc.
- Images before text in the message when possible.

## Handoff

→ `critique` with board + ladder + hinge crops.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Empty PNGs | Install `librsvg` (`rsvg-convert`) or Inkscape |
| Fuzzy 32px | Reconstruct with fewer points; no hairlines |
| Wrong colors | Script forces mono; check SVG fill |
| Missing mark | Ensure `.svg` in `--marks-dir` |
