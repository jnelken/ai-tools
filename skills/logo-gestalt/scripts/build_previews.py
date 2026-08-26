#!/usr/bin/env python3
"""
build_previews.py — Raster preview pipeline for logo-gestalt skill.

Scans a marks directory for SVG files, renders PNGs at standard sizes,
and writes board.html + size-ladder.html review pages.

Sizes: hero 1024, icon 512, favicon 64, favicon-sm 32

Raster backends (first available): rsvg-convert, inkscape, magick/convert
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

SIZES = {
    "hero": 1024,
    "icon": 512,
    "favicon": 64,
    "favicon-sm": 32,
}

LADDER_SIZES = [512, 256, 128, 64, 32]


def find_converter() -> Optional[tuple[str, Callable[[Path, Path, int], bool]]]:
    """Return (name, render_fn) for first available raster backend."""

    if shutil.which("rsvg-convert"):
        def rsvg_render(svg: Path, png: Path, size: int) -> bool:
            try:
                subprocess.run(
                    [
                        "rsvg-convert",
                        "-w", str(size),
                        "-h", str(size),
                        "-o", str(png),
                        str(svg),
                    ],
                    check=True,
                    capture_output=True,
                )
                return png.exists()
            except (subprocess.CalledProcessError, OSError):
                return False

        return ("rsvg-convert", rsvg_render)

    if shutil.which("inkscape"):
        def inkscape_render(svg: Path, png: Path, size: int) -> bool:
            try:
                subprocess.run(
                    [
                        "inkscape",
                        str(svg),
                        f"--export-filename={png}",
                        f"--export-width={size}",
                        f"--export-height={size}",
                    ],
                    check=True,
                    capture_output=True,
                )
                return png.exists()
            except (subprocess.CalledProcessError, OSError):
                return False

        return ("inkscape", inkscape_render)

    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        def magick_render(svg: Path, png: Path, size: int) -> bool:
            try:
                subprocess.run(
                    [magick, str(svg), "-resize", f"{size}x{size}", str(png)],
                    check=True,
                    capture_output=True,
                )
                return png.exists()
            except (subprocess.CalledProcessError, OSError):
                return False

        return ("magick", magick_render)

    return None


def discover_svgs(marks_dir: Path) -> list[Path]:
    return sorted(marks_dir.glob("*.svg"))


def mark_slug(svg: Path) -> str:
    return svg.stem


def render_mark(
    svg: Path,
    out_dir: Path,
    render_fn: Callable[[Path, Path, int], bool],
) -> dict[str, Optional[Path]]:
    """Render all sizes for one mark. Returns size_key -> png path or None."""
    slug = mark_slug(svg)
    results: dict[str, Optional[Path]] = {}
    for key, px in SIZES.items():
        png = out_dir / f"{slug}-{key}-{px}.png"
        ok = render_fn(svg, png, px)
        results[key] = png if ok else None
    return results


def rel_href(from_path: Path, target: Path) -> str:
    try:
        return str(target.relative_to(from_path.parent))
    except ValueError:
        return str(target)


def img_cell(from_html: Path, png: Optional[Path], css_class: str) -> str:
    if png and png.exists():
        src = html.escape(rel_href(from_html, png))
        return f'<div class="cell {css_class} checker"><img src="{src}" alt=""></div>'
    return f'<div class="cell {css_class}"><span class="missing">no PNG</span></div>'


def build_mark_row(from_html: Path, slug: str, previews: dict[str, Optional[Path]]) -> str:
    cells = [
        f'<td class="mark-name">{html.escape(slug)}</td>',
        f"<td>{img_cell(from_html, previews.get('hero'), 'hero')}</td>",
        f"<td>{img_cell(from_html, previews.get('icon'), 'icon')}</td>",
        f"<td>{img_cell(from_html, previews.get('favicon'), 'favicon')}</td>",
        f"<td>{img_cell(from_html, previews.get('favicon-sm'), 'favicon-sm')}</td>",
    ]
    return f"<tr>{''.join(cells)}</tr>"


def build_ladder_row(from_html: Path, slug: str, out_dir: Path, ladder_sizes: list[int]) -> str:
    cells = [f'<td class="mark-name">{html.escape(slug)}</td>']
    for px in ladder_sizes:
        png = out_dir / f"{slug}-ladder-{px}.png"
        cells.append(f"<td>{img_cell(from_html, png if png.exists() else None, f'ladder-{px}')}</td>")
    return f"<tr>{''.join(cells)}</tr>"


def load_template(skill_dir: Path) -> str:
    template = skill_dir / "templates" / "board.html"
    if template.exists():
        return template.read_text(encoding="utf-8")
    return """<!DOCTYPE html><html><body><table><tbody>{{MARK_ROWS}}</tbody></table></body></html>"""


LADDER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Logo Gestalt — Size Ladder</title>
  <style>
    :root {{
      --bg: #0d0d0f;
      --surface: #16161a;
      --border: #2a2a30;
      --text: #e8e8ed;
      --muted: #8b8b96;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 2rem;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.25rem; }}
    .meta {{ color: var(--muted); font-size: 0.875rem; margin-bottom: 2rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    th, td {{ padding: 1rem; border-bottom: 1px solid var(--border); text-align: center; }}
    th {{ font-size: 0.75rem; text-transform: uppercase; color: var(--muted); }}
    .checker {{
      background-image:
        linear-gradient(45deg, #222 25%, transparent 25%),
        linear-gradient(-45deg, #222 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #222 75%),
        linear-gradient(-45deg, transparent 75%, #222 75%);
      background-size: 12px 12px;
      background-color: #1a1a1e;
      border-radius: 4px;
      padding: 0.5rem;
      display: inline-block;
    }}
    .cell img {{ display: block; image-rendering: crisp-edges; }}
    .missing {{ color: var(--muted); font-size: 0.75rem; font-style: italic; }}
    .mark-name {{ text-align: left; font-weight: 500; }}
  </style>
</head>
<body>
  <h1>Logo Gestalt — Size Ladder</h1>
  <p class="meta">Stress test: 512 → 256 → 128 → 64 → 32 · Generated {timestamp}</p>
  <table>
    <thead>
      <tr>
        <th>Mark</th>
        {size_headers}
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""


def write_board(
    board_path: Path,
    rows_html: str,
    skill_dir: Path,
) -> None:
    template = load_template(skill_dir)
    content = template.replace("{{MARK_ROWS}}", rows_html)
    board_path.parent.mkdir(parents=True, exist_ok=True)
    board_path.write_text(content, encoding="utf-8")


def write_ladder(ladder_path: Path, rows_html: str, sizes: list[int]) -> None:
    headers = "".join(f"<th>{px}px</th>" for px in sizes)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = LADDER_HTML.format(
        timestamp=ts,
        size_headers=headers,
        rows=rows_html,
    )
    ladder_path.parent.mkdir(parents=True, exist_ok=True)
    ladder_path.write_text(content, encoding="utf-8")


def skill_dir_from_script() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build raster previews and HTML review boards for logo-gestalt marks.",
    )
    parser.add_argument(
        "--marks-dir",
        type=Path,
        default=Path(".logo-gestalt/marks"),
        help="Directory containing .svg mark files",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(".logo-gestalt/previews"),
        help="Output directory for PNG previews",
    )
    parser.add_argument(
        "--board",
        type=Path,
        default=Path(".logo-gestalt/board.html"),
        help="Output path for review board HTML",
    )
    parser.add_argument(
        "--ladder",
        type=Path,
        default=Path(".logo-gestalt/size-ladder.html"),
        help="Output path for size ladder HTML",
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=None,
        help="Skill root (defaults to parent of scripts/)",
    )
    args = parser.parse_args()

    skill_dir = args.skill_dir or skill_dir_from_script()
    marks_dir = args.marks_dir.resolve()
    out_dir = args.out_dir.resolve()
    board_path = args.board.resolve()
    ladder_path = args.ladder.resolve()

    if not marks_dir.is_dir():
        print(f"error: marks directory not found: {marks_dir}", file=sys.stderr)
        return 1

    svgs = discover_svgs(marks_dir)
    if not svgs:
        print(f"warning: no .svg files in {marks_dir}", file=sys.stderr)

    out_dir.mkdir(parents=True, exist_ok=True)

    converter = find_converter()
    converter_name = converter[0] if converter else "none"
    render_fn = converter[1] if converter else None

    if converter:
        print(f"using raster backend: {converter_name}")
    else:
        print("warning: no rsvg-convert, inkscape, or magick — HTML only, PNGs skipped", file=sys.stderr)

    board_rows: list[str] = []
    ladder_rows: list[str] = []
    png_count = 0

    for svg in svgs:
        slug = mark_slug(svg)
        print(f"processing: {svg.name}")

        previews: dict[str, Optional[Path]] = {k: None for k in SIZES}
        if render_fn:
            previews = render_mark(svg, out_dir, render_fn)
            png_count += sum(1 for p in previews.values() if p)

            for px in LADDER_SIZES:
                ladder_png = out_dir / f"{slug}-ladder-{px}.png"
                if render_fn(svg, ladder_png, px):
                    png_count += 1

        board_rows.append(build_mark_row(board_path, slug, previews))
        ladder_rows.append(build_ladder_row(board_path, slug, out_dir, LADDER_SIZES))

    write_board(board_path, "\n      ".join(board_rows) if board_rows else "<tr><td colspan='5'>No marks</td></tr>", skill_dir)
    write_ladder(
        ladder_path,
        "\n      ".join(ladder_rows) if ladder_rows else "<tr><td colspan='6'>No marks</td></tr>",
        LADDER_SIZES,
    )

    print(f"wrote board: {board_path}")
    print(f"wrote ladder: {ladder_path}")
    print(f"marks: {len(svgs)} · PNGs: {png_count} · converter: {converter_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
