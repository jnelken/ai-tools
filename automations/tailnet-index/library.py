"""The index's library: reports, sketchpad pages and outside links, from a local config.

The config is `~/.claude/automations/tailnet-index/library.json` (TAILNET_INDEX_LIBRARY), kept
out of the repo because it lists private claude.ai artifact links and personal paths. Shape
(see library.example.json):

    {"reports":   [{"glob": "~/Dropbox/code/slackagent/var/reports/**/report.md", "group": "Finance"}],
     "sketchpad": [{"glob": "~/Dropbox/code/**/.logo-gestalt/board.html"}],
     "links":     [{"title": "Brew Radar", "url": "https://claude.ai/artifact/…", "section": "claude"}]}

Globs are expanded with `**` meaning any depth, pruning node_modules, .git and build output.
Only files a glob found are ever served (by an id derived from their path), plus files beside
them in the same folder — so a board's preview images load, and nothing else on disk does.
"""
import fnmatch
import hashlib
import html
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

CONFIG = Path(os.environ.get("TAILNET_INDEX_LIBRARY",
                             Path.home() / ".claude" / "automations" / "tailnet-index" / "library.json"))
CODE_DIR = os.path.expanduser(os.environ.get("TAILNET_INDEX_CODE_DIR", "~/Dropbox/code"))
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".venv", "venv",
             "__pycache__", ".turbo", ".cache", "site-packages"}
MAX_DEPTH = 8
CACHE_S = 300
_cache = {"at": 0.0, "lib": None}


def load_config():
    try:
        data = json.loads(CONFIG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def expand(pattern):
    """Files matching a glob where `**` spans any number of folders, without descending into
    dependency or build folders (a plain recursive glob would crawl every node_modules)."""
    pattern = os.path.expanduser(pattern)
    if not os.path.isabs(pattern):
        return []  # a relative glob would be walked from wherever the server runs; refuse it
    parts = pattern.split(os.sep)
    i = next((n for n, p in enumerate(parts) if any(c in p for c in "*?[")), len(parts))
    root = os.sep.join(parts[:i]) or os.sep
    rel_pattern = os.sep.join(parts[i:])
    if not rel_pattern:
        return [root] if os.path.isfile(root) else []
    segs = rel_pattern.split(os.sep)
    out = []
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
            dirnames[:] = []
        for f in filenames:
            full = os.path.join(dirpath, f)
            if match_segments(os.path.relpath(full, root).split(os.sep), segs):
                out.append(full)
    return sorted(out)


def match_segments(parts, segs):
    """Glob match by path segment: `**` spans zero or more folders; any other segment (with
    `*`, `?`, `[…]`) matches exactly one name, so `*` never crosses a `/`."""
    if not segs:
        return not parts
    if segs[0] == "**":
        return any(match_segments(parts[k:], segs[1:]) for k in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], segs[0]) and match_segments(parts[1:], segs[1:])


def entry_id(path):
    return hashlib.sha1(os.path.realpath(path).encode()).hexdigest()[:12]


def repo_of(path):
    """The repo a file belongs to, for grouping: the folder under the code dir (a `.worktrees/<name>`
    checkout counts as its own name)."""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(CODE_DIR))
    if rel.startswith(".."):
        return os.path.basename(os.path.dirname(path))
    parts = rel.split(os.sep)
    return parts[1] if parts[0] == ".worktrees" and len(parts) > 1 else parts[0]


def title_of(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except OSError:
        return os.path.basename(path)
    if path.endswith(".md"):
        m = re.search(r"^#\s+(.+?)\s*#*\s*$", head, re.M)
    else:
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    t = html.unescape(m.group(1).strip()) if m else ""
    return t or f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}"


def build_library(cfg=None):
    cfg = load_config() if cfg is None else cfg
    files = {}
    sections = {"reports": [], "sketchpad": []}
    for section in sections:
        for src in cfg.get(section) or []:
            if not isinstance(src, dict) or not src.get("glob"):
                continue
            for path in expand(src["glob"]):
                try:
                    eid = entry_id(path)
                    if eid in files:
                        continue
                    e = {"id": eid, "section": section, "path": path, "title": title_of(path),
                         "group": src.get("group") or repo_of(path),
                         "kind": "md" if path.endswith(".md") else "html",
                         "mtime": os.path.getmtime(path)}
                except OSError:
                    continue  # vanished mid-scan, or a dangling symlink: skip just this file
                files[eid] = e
                sections[section].append(e)
        sections[section].sort(key=lambda e: -e["mtime"])
    links = [l for l in (cfg.get("links") or []) if isinstance(l, dict) and l.get("url") and l.get("title")]
    return {"files": files, "reports": sections["reports"], "sketchpad": sections["sketchpad"],
            "claude": [l for l in links if l.get("section", "claude") == "claude"],
            "sketch_links": [l for l in links if l.get("section") == "sketchpad"]}


def library():
    if _cache["lib"] is None or time.time() - _cache["at"] > CACHE_S:
        _cache.update(at=time.time(), lib=build_library())
    return _cache["lib"]


def resolve(eid, rel=""):
    """The file to serve for /lib/<eid>/<rel>: the entry itself, or a file in its folder."""
    e = library()["files"].get(eid)
    if not e:
        return None
    if not rel:
        return e["path"]
    base = os.path.realpath(os.path.dirname(e["path"]))
    target = os.path.realpath(os.path.join(base, rel))
    if not target.startswith(base + os.sep) or not os.path.isfile(target):
        return None
    return target


def when(ts):
    return datetime.fromtimestamp(ts).strftime("%b %-d, %Y")


# ── markdown ────────────────────────────────────────────────────────────────

def _inline(s):
    # Code spans and links are cut out first and put back last, so emphasis rules never touch
    # a URL (`exports/_draft_/x.md`) or code.
    s = s.replace("\x00", "\ufffd")  # NUL marks held fragments below; never take one from the source
    held = []

    def hold(fragment):
        held.append(fragment)
        return f"\x00{len(held) - 1}\x00"

    s = re.sub(r"`([^`]+)`", lambda m: hold(f"<code>{html.escape(m.group(1), quote=False)}</code>"), s)

    def link(m):
        # Browsers drop control characters and spaces inside a URL, so check the scheme of what
        # they'd actually see, and allow only web, mail and relative links.
        href = re.sub(r"[\x00-\x20\x7f]", "", html.unescape(m.group(2)))
        scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", href)
        text = html.escape(m.group(1), quote=False)
        if scheme and scheme.group(1).lower() not in ("http", "https", "mailto"):
            return hold(text)
        return hold(f'<a href="{html.escape(href)}">{text}</a>')
    # The destination may hold one level of balanced parentheses: [doc](https://x/a_(b)).
    s = re.sub(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)", link, s)
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s)
    for _ in range(len(held) + 1):  # held fragments can hold others (a link label with code in it)
        if "\x00" not in s:
            break
        s = re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)
    return s


def md_to_html(text):
    """A small, dependency-free markdown renderer: headings, paragraphs, lists, tables, quotes,
    fenced code, rules and inline code/bold/italic/links. Raw HTML in the source is escaped."""
    lines = text.splitlines()
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(l.strip() for l in para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            flush()

            def cells(l):
                return [c.strip() for c in l.strip().strip("|").split("|")]
            head = "".join(f"<th>{_inline(c)}</th>" for c in cells(line))
            j, rows = i + 2, []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(lines[j])) + "</tr>")
                j += 1
            out.append(f'<div class=tablewrap><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
            i = j
            continue
        if line.startswith(">"):
            flush()
            j, quote = i, []
            while j < len(lines) and lines[j].startswith(">"):
                quote.append(lines[j][1:].lstrip())
                j += 1
            out.append("<blockquote>" + md_to_html("\n".join(quote)) + "</blockquote>")
            i = j
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            flush()
            ordered = m.group(2)[0].isdigit()
            items, j = [], i
            while j < len(lines):
                mm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[j])
                if mm:
                    items.append(mm.group(3))
                elif lines[j].startswith(("  ", "\t")) and lines[j].strip() and items:
                    items[-1] += " " + lines[j].strip()  # a wrapped or nested continuation
                else:
                    break
                j += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            i = j
            continue
        if not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return "\n".join(out)
