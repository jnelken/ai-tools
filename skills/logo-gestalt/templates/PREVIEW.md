# Preview manifest

> Raster outputs for vision critique. Path: `.logo-gestalt/PREVIEW.md`

## Generation

- **Date:** 
- **Script:** `python3 <skill-dir>/scripts/build_previews.py`
- **Converter:** rsvg-convert | inkscape | magick | none (HTML only)
- **Marks dir:** `.logo-gestalt/marks/`
- **Out dir:** `.logo-gestalt/previews/`

## Sizes

| Key | Px | Purpose |
|-----|-----|---------|
| hero | 1024 | Detail, hinge annotation |
| icon | 512 | App icon source |
| favicon | 64 | Tab / dock |
| favicon-sm | 32 | Kill test |

## Files

| Mark | hero | icon | favicon | favicon-sm | hinge crop |
|------|------|------|---------|------------|------------|
| candidate-1 | | | | | |
| candidate-2 | | | | | |
| candidate-3 | | | | | |

## HTML boards

- **Review board:** `.logo-gestalt/board.html`
- **Size ladder:** `.logo-gestalt/size-ladder.html`

## Vision attach list

<!-- Copy into critique message -->

1. Image 1 — [mark] favicon-sm 32px
2. Image 2 — [mark] hero 1024px
3. Image 3 — size ladder row
4. Image 4 — hinge crop (gestalt only)

## Next

→ `critique` with images attached
