#!/usr/bin/env python3
"""The tailnet's front page: every page this Mac serves over Tailscale Serve, on one index.

    serve.py [--port 8420]

GET /            lists each Tailscale Serve handler (read live from `tailscale serve status
                 --json`, so a newly mounted page shows up on its own), with the page's own <title>
                 and whether it answers right now; then the library from library.json: reports,
                 claude.ai artifacts and, near the bottom, the sketchpad (see library.py).
GET /lib/<id>/   a library file (markdown rendered), and /lib/<id>/<file> a file beside it.
Mounted at / on the tailnet:
    tailscale serve --bg --set-path / http://127.0.0.1:8420
Read-only and bound to 127.0.0.1; Tailscale Serve only accepts tailnet peers.
"""
import argparse
import html
import json
import mimetypes
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # library.py, beside the real file
import library  # noqa: E402

TAILSCALE = os.environ.get("TAILSCALE_BIN", "/usr/local/bin/tailscale")
PORT = int(os.environ.get("TAILNET_INDEX_PORT", "8420"))
CACHE_S = 20
_cache = {"at": 0.0, "page": None}
_lock = threading.Lock()


def serve_status():
    """The parsed `tailscale serve status --json` (raises on failure)."""
    p = subprocess.run([TAILSCALE, "serve", "status", "--json"], capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:300] or f"exit {p.returncode}")
    return json.loads(p.stdout or "{}") or {}  # no config at all prints `null`


def is_self(target, port=None):
    """Whether a proxy target is this index server (it shouldn't list itself)."""
    port = port or PORT
    t = (target or "").rstrip("/")
    return t in (f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}")


def handlers(status, port=None):
    """[{host, path, target, kind, href}] for every web handler except the index itself. href
    keeps the handler's own origin when it's served on a port other than 443."""
    out = []
    # Mounts made with --bg live at the top level; ones still attached to a foreground
    # `tailscale serve` session live under Foreground, one config per session.
    configs = [status] + [c for c in (status.get("Foreground") or {}).values() if isinstance(c, dict)]
    for cfg in configs:
        out += _handlers(cfg, port)
    return sorted(out, key=lambda e: (e["host"], e["path"]))


def _handlers(status, port):
    out = []
    tcp = status.get("TCP") or {}
    for hostport, web in (status.get("Web") or {}).items():
        name, _, lport = hostport.rpartition(":")
        https = bool((tcp.get(lport) or {}).get("HTTPS", lport == "443"))  # an --http listener isn't TLS
        scheme = "https" if https else "http"
        default_port = (https and lport == "443") or (not https and lport == "80")
        origin = f"{scheme}://{name if default_port else hostport}"
        host = name if default_port else hostport
        for path, h in (web.get("Handlers") or {}).items():
            if h.get("Proxy") and is_self(h["Proxy"], port):
                continue  # that's this page
            kind, target = ("proxy", h["Proxy"]) if h.get("Proxy") else \
                           ("files", h["Path"]) if h.get("Path") else ("text", None)
            # A file mount matches its path exactly, so no trailing slash there.
            link = path if kind == "files" and os.path.isfile(target) else path.rstrip("/") + "/"
            # Absolute, so the cards work from http://127.0.0.1:8420/ as well as the tailnet.
            out.append({"host": host, "path": path, "target": target, "kind": kind,
                        "href": f"{origin}{link}"})
    return out


def probe(target, timeout=3):
    """(up, title) for a proxied local URL."""
    ctx = None
    if target.startswith("https+insecure://"):  # Serve's "HTTPS, don't verify the cert" target
        target = "https://" + target[len("https+insecure://"):]
        ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(target, headers={"User-Agent": "tailnet-index"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            return True, html.unescape(m.group(1).strip()) if m else None
    except urllib.error.HTTPError as e:
        return e.code < 500, None
    except (OSError, ValueError):
        return False, None


def lib_card(e):
    return (f'<a class=card href="/lib/{e["id"]}/"><div class=top><span class=name>{html.escape(e["title"])}</span></div>'
            f'<div class=target>{html.escape(e["group"])} · {library.when(e["mtime"])}</div></a>')


def link_card(l):
    meta = " · ".join(x for x in (l.get("note"), l.get("updated")) if x)
    return (f'<a class="card ext" href="{html.escape(l["url"])}" target=_blank rel=noopener>'
            f'<div class=top><span class=name>{html.escape(l["title"])}</span><span class=arrow>↗</span></div>'
            + (f'<div class=target>{html.escape(meta)}</div>' if meta else "") + '</a>')


def library_sections(lib):
    if not lib:
        return ""
    out = []
    if lib["reports"]:
        out.append(f'<h2>Reports</h2><div class=grid>{"".join(lib_card(e) for e in lib["reports"])}</div>')
    if lib["claude"]:
        out.append('<h2>On claude.ai</h2><p class=sub>Published artifacts; they open on claude.ai, signed in.</p>'
                   f'<div class=grid>{"".join(link_card(l) for l in lib["claude"])}</div>')
    sketch = "".join(lib_card(e) for e in lib["sketchpad"]) + "".join(link_card(l) for l in lib["sketch_links"])
    if sketch:
        out.append('<h2>Sketchpad</h2><p class=sub>Boards, mocks, prototypes and handoffs: work in progress.</p>'
                   f'<div class="grid small">{sketch}</div>')
    return "".join(out)


def build(entries, error=None, now=None, lib=None):
    now = now or datetime.now()
    cards = []
    for e in entries:
        up, title = (probe(e["target"]) if e["kind"] == "proxy" else (True, None))
        href = e.get("href") or e["path"].rstrip("/") + "/"
        name = title or e["path"].strip("/") or e["path"]
        cards.append(
            f'<a class=card href="{html.escape(href)}"><div class=top><span class="dot {"up" if up else "down"}"'
            f' title="{"answering" if up else "not answering"}"></span><span class=name>{html.escape(name)}</span></div>'
            f'<div class=path>{html.escape(e["path"])}</div>'
            f'<div class=target>{html.escape(e["kind"])} → {html.escape(e["target"] or "")}'
            f'{"" if up else " · down"}</div></a>')
    body = "".join(cards) or '<p class=dim>Nothing else is mounted on Tailscale Serve yet.</p>'
    if error:
        body = f'<p class=err>Couldn\'t read Tailscale Serve: {html.escape(error)}</p>'
    host = f' · {html.escape(entries[0]["host"])}' if entries else ""
    return PAGE.format(cards=body, host=host, when=now.strftime("%H:%M:%S"), n=len(entries),
                       library=library_sections(lib))


PAGE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Tailnet pages</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8; --up:#0a7d34; --down:#b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{ --bg:#141417; --card:#1d1d21;
           --fg:#ececf0; --dim:#9a9aa6; --line:#2c2c33; --up:#4ade80; --down:#f87171; }} }}
  :root[data-theme=dark] {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0; --dim:#9a9aa6; --line:#2c2c33;
           --up:#4ade80; --down:#f87171; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:24px 16px; }}
  .wrap {{ max-width:880px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  .dim, .sub {{ color:var(--dim); }}
  .sub {{ font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:12px; }}
  .card {{ display:block; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;
           color:inherit; text-decoration:none; }}
  .card:hover {{ border-color:var(--dim); }}
  .top {{ display:flex; align-items:center; gap:8px; }}
  .name {{ font-weight:650; font-size:16px; }}
  .dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
  .dot.up {{ background:var(--up); }} .dot.down {{ background:var(--down); }}
  .path {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; margin-top:4px; }}
  .target {{ color:var(--dim); font-size:12px; margin-top:2px; overflow-wrap:anywhere; }}
  .err {{ color:var(--down); }}
  h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); margin:32px 0 6px; }}
  h2 + .grid {{ margin-top:12px; }}
  .arrow {{ margin-left:auto; color:var(--dim); }}
  .grid.small {{ grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); }}
  .grid.small .name {{ font-size:14px; font-weight:600; }}
</style>
</head>
<body>
<div class=wrap>
  <h1>Tailnet pages</h1>
  <div class=sub>{n} page(s) served from this Mac over Tailscale Serve{host} · checked {when}</div>
  <div class=grid>{cards}</div>
  {library}
</div>
</body>
</html>"""


def render():
    with _lock:
        if _cache["page"] and time.time() - _cache["at"] < CACHE_S:
            return _cache["page"]
        try:
            lib = library.library()
        except OSError:
            lib = None
        try:
            page = build(handlers(serve_status()), lib=lib)
        except (OSError, ValueError, RuntimeError, AttributeError, subprocess.SubprocessError) as e:
            page = build([], error=str(e), lib=lib)
        _cache.update(at=time.time(), page=page)
        return page


DOC = """<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0; --dim:#9a9aa6; --line:#2c2c33; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:24px 16px; }}
  .wrap {{ max-width:780px; margin:0 auto; }}
  a {{ color:inherit; }}
  .back {{ font-size:13px; color:var(--dim); }}
  h1 {{ font-size:26px; line-height:1.25; }} h2 {{ font-size:19px; margin-top:28px; }} h3 {{ font-size:16px; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.88em; background:var(--card);
          border:1px solid var(--line); border-radius:4px; padding:0 4px; }}
  pre {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; overflow-x:auto; }}
  pre code {{ border:0; padding:0; background:none; }}
  blockquote {{ margin:0; padding-left:14px; border-left:3px solid var(--line); color:var(--dim); }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--card); margin:12px 0; }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); }}
  tr:last-child td {{ border-bottom:0; }}
  hr {{ border:0; border-top:1px solid var(--line); margin:24px 0; }}
</style></head>
<body><div class=wrap><a class=back href="/">← Tailnet pages</a>
{body}
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype):
        data = data if isinstance(data, bytes) else data.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _lib(self, path):
        parts = path[len("/lib/"):].split("/", 1)
        eid, rel = parts[0], (parts[1] if len(parts) > 1 else None)
        if rel is None:  # /lib/<id> → /lib/<id>/, so the page's relative links resolve in its folder
            self.send_response(301)
            self.send_header("Location", f"/lib/{eid}/")
            self.end_headers()
            return
        target = library.resolve(eid, urllib.parse.unquote(rel))
        if not target:
            return self._send(404, "not found", "text/plain")
        try:
            raw = open(target, "rb").read()
        except OSError:
            return self._send(404, "not found", "text/plain")
        if target.endswith(".md"):
            text = raw.decode("utf-8", "replace")
            return self._send(200, DOC.format(title=html.escape(library.title_of(target)),
                                              body=library.md_to_html(text)), "text/html; charset=utf-8")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, raw, ctype)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, render(), "text/html; charset=utf-8")
        if path.startswith("/lib/"):
            return self._lib(path)
        self._send(404, "not found", "text/plain")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("TAILNET_INDEX_PORT", "8420")))
    a = ap.parse_args()
    global PORT
    PORT = a.port  # so the index recognises its own handler whatever port it's on
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    threading.Thread(target=library.library, daemon=True).start()  # warm the ~2 s file scan
    print(f"tailnet index on http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
