#!/usr/bin/env python3
"""The tailnet's front page: every page this Mac serves over Tailscale Serve, on one index.

    serve.py [--port 8420]

GET /   lists each Tailscale Serve handler (read live from `tailscale serve status --json`, so a
        newly mounted page shows up on its own), with the page's own <title> and whether it
        answers right now. Mounted at / on the tailnet:
          tailscale serve --bg --set-path / http://127.0.0.1:8420
Read-only and bound to 127.0.0.1; Tailscale Serve only accepts tailnet peers.
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAILSCALE = os.environ.get("TAILSCALE_BIN", "/usr/local/bin/tailscale")
CACHE_S = 20
_cache = {"at": 0.0, "page": None}
_lock = threading.Lock()


def serve_status():
    """The parsed `tailscale serve status --json` (raises on failure)."""
    p = subprocess.run([TAILSCALE, "serve", "status", "--json"], capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:300] or f"exit {p.returncode}")
    return json.loads(p.stdout or "{}")


def handlers(status):
    """[{host, path, target, kind}] for every web handler except the index itself."""
    out = []
    for hostport, web in (status.get("Web") or {}).items():
        host = hostport.rsplit(":", 1)[0] if hostport.endswith(":443") else hostport
        for path, h in (web.get("Handlers") or {}).items():
            if path == "/":
                continue  # that's this page
            kind, target = ("proxy", h["Proxy"]) if h.get("Proxy") else \
                           ("files", h["Path"]) if h.get("Path") else ("text", None)
            out.append({"host": host, "path": path, "target": target, "kind": kind})
    return sorted(out, key=lambda e: (e["host"], e["path"]))


def probe(target, timeout=3):
    """(up, title) for a proxied local URL."""
    try:
        req = urllib.request.Request(target, headers={"User-Agent": "tailnet-index"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            return True, html.unescape(m.group(1).strip()) if m else None
    except urllib.error.HTTPError as e:
        return e.code < 500, None
    except (OSError, ValueError):
        return False, None


def build(entries, error=None, now=None):
    now = now or datetime.now()
    cards = []
    for e in entries:
        up, title = (probe(e["target"]) if e["kind"] == "proxy" else (True, None))
        href = e["path"].rstrip("/") + "/"
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
    return PAGE.format(cards=body, host=host, when=now.strftime("%H:%M:%S"), n=len(entries))


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
</style>
</head>
<body>
<div class=wrap>
  <h1>Tailnet pages</h1>
  <div class=sub>{n} page(s) served from this Mac over Tailscale Serve{host} · checked {when}</div>
  <div class=grid>{cards}</div>
</div>
</body>
</html>"""


def render():
    with _lock:
        if _cache["page"] and time.time() - _cache["at"] < CACHE_S:
            return _cache["page"]
        try:
            page = build(handlers(serve_status()))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            page = build([], error=str(e))
        _cache.update(at=time.time(), page=page)
        return page


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            data = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        else:
            data = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("TAILNET_INDEX_PORT", "8420")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"tailnet index on http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
