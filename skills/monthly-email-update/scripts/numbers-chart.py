#!/usr/bin/env python3
"""Draw the Numbers-section trend chart for the monthly email update.

Counts commits (no merges, origin/main) and merged PRs per month across the woodrow and api
checkouts for the 12 months ending at --month, then writes a small two-panel SVG (and a PNG next
to it when macOS Quick Look is available). Standard library only.

  numbers-chart.py --month 2026-10 --out-dir /Users/jake/code/folio-platform/docs/monthly-email-updates

Prints the chart path and the monthly series as JSON so the draft's Numbers can be cross-checked.
PRs are counted with `gh` (not `(#NNN)` suffixes), matching the skill's Numbers rules.
"""
import argparse
import calendar
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPOS = {"woodrow": "/Users/jake/code/woodrow", "api": "/Users/jake/code/api"}
GH_REPO = "Concentro-Inc/{}"
MONTHS = 12


def month_range(end: str, n: int):
    y, m = map(int, end.split("-"))
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def commits(path: str, y: int, m: int) -> int:
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    out = subprocess.run(
        ["git", "-C", path, "log", "origin/main", "--no-merges", "--format=%h",
         f"--since={y}-{m:02d}-01 00:00", f"--until={ny}-{nm:02d}-01 00:00"],
        capture_output=True, text=True, check=True).stdout
    return len(out.split())


def prs(repo: str, y: int, m: int) -> int:
    last = calendar.monthrange(y, m)[1]
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", GH_REPO.format(repo), "--state", "merged", "--limit", "1000",
         "--search", f"merged:{y}-{m:02d}-01..{y}-{m:02d}-{last}", "--json", "number"],
        capture_output=True, text=True, check=True).stdout
    return len(json.loads(out))


def crop_png_top(path: Path, height: int) -> None:
    """Keep the top `height` rows of a non-interlaced PNG (rows only depend on the row above)."""
    data = path.read_bytes()
    pos, chunks, idat = 8, [], b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IDAT":
            idat += body
        elif kind != b"IEND":
            chunks.append((kind, body))
    raw = zlib.decompress(idat)
    w, h = struct.unpack(">II", chunks[0][1][:8])
    stride = len(raw) // h
    raw = raw[: stride * height]
    chunks[0] = (b"IHDR", struct.pack(">II", w, height) + chunks[0][1][8:])

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    out = data[:8] + b"".join(chunk(k, b) for k, b in chunks)
    out += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.write_bytes(out)


def panel(x0, y0, w, h, title, labels, values, color):
    top = max(values) * 1.15 or 1
    px = lambda i: x0 + i * w / (len(values) - 1)
    py = lambda v: y0 + h - v / top * h
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(values))
    g = [f'<text x="{x0}" y="{y0 - 22}" font-size="13" font-weight="600" fill="#111827">{title}</text>',
         f'<line x1="{x0}" y1="{y0 + h}" x2="{x0 + w}" y2="{y0 + h}" stroke="#d1d5db"/>',
         f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>']
    for i, v in enumerate(values):
        r = 4.5 if i == len(values) - 1 else 2.5
        g.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="{r}" fill="{color}"/>')
    g.append(f'<text x="{px(len(values) - 1):.1f}" y="{py(values[-1]) - 10:.1f}" font-size="12" '
             f'font-weight="600" text-anchor="end" fill="{color}">{values[-1]:,}</text>')
    for i in (0, len(values) // 2, len(values) - 1):
        anchor = "start" if i == 0 else "end" if i == len(values) - 1 else "middle"
        g.append(f'<text x="{px(i):.1f}" y="{y0 + h + 16}" font-size="11" text-anchor="{anchor}" '
                 f'fill="#6b7280">{labels[i]}</text>')
    return "\n".join(g)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM, last month on the chart")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    months = month_range(a.month, MONTHS)
    labels = [f"{calendar.month_abbr[m]} {str(y)[2:]}" for y, m in months]
    c = [sum(commits(p, y, m) for p in REPOS.values()) for y, m in months]
    p = [sum(prs(r, y, m) for r in REPOS) for y, m in months]
    w, h = 300, 110
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="200" viewBox="0 0 720 200" '
           f'font-family="-apple-system,Helvetica,Arial,sans-serif">'
           f'<rect width="720" height="200" fill="#ffffff"/>'
           + panel(30, 50, w, h, "Commits landed per month", labels, c, "#2563eb")
           + panel(390, 50, w, h, "PRs merged per month", labels, p, "#059669")
           + "</svg>")
    out = Path(a.out_dir) / f"{a.month}-numbers.svg"
    out.write_text(svg)
    final = out
    if sys.platform == "darwin":
        try:  # Quick Look renders SVG to PNG, which survives email clients; SVG often does not.
            subprocess.run(["qlmanage", "-t", "-s", "1440", "-o", str(out.parent), str(out)],
                           capture_output=True, check=True)
            png = out.parent / (out.name + ".png")
            target = out.with_suffix(".png")
            if png.exists():
                png.replace(target)
                crop_png_top(target, 400)  # Quick Look pads the thumbnail to a square.
                final = target
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    print(json.dumps({"chart": str(final), "months": labels, "commits": c, "prs": p}))


if __name__ == "__main__":
    main()
