#!/usr/bin/env python3
"""Serve paradev.html on localhost with a one-click refresh.

    serve.py [--port 8422]

GET  /          the Paradev page (also reached as /paradev through Tailscale Serve)
POST …/refresh  fetch fresh ranks from Paradev (leaderboard.py snapshot --force) and rebuild

Bound to 127.0.0.1. /refresh checks Host and Origin so a random web page can't trigger it (or
DNS-rebind to it); *.ts.net is allowed because Tailscale Serve, which only accepts tailnet
peers, sets that Host itself.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.environ.get("PARADEV_ROOT", os.path.expanduser("~/.claude/automations/paradev"))
PAGE = os.path.join(ROOT, "paradev.html")
REFRESH = threading.Lock()


def refresh():
    t = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(HERE, "gen-page.py"), "--force-snapshot"],
                           capture_output=True, text=True, timeout=150, env={**os.environ, "PARADEV_ROOT": ROOT})
        ok, err = p.returncode == 0, (p.stderr or p.stdout).strip()[-400:]
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, err = False, str(e)
    # The page records a failed Paradev fetch itself; surface it here too.
    try:
        with open(os.path.join(ROOT, "leaderboard.json"), encoding="utf-8") as f:
            lb = json.load(f)
        if lb.get("ok") is False:
            ok, err = False, f"Paradev: {lb.get('error')}"
    except (OSError, ValueError):
        pass
    return [{"step": "paradev", "ok": ok, "secs": round(time.time() - t, 1), **({} if ok else {"error": err})}]


class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

        def ok(hostport):
            return hostport in hosts or hostport.split(":", 1)[0].endswith(".ts.net")

        host, origin = self.headers.get("Host"), self.headers.get("Origin")
        return bool(host) and ok(host) and (origin is None or ok(origin.split("//", 1)[-1]))

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/paradev", "/paradev/", "/paradev.html"):
            return self._send(404, "not found", "text/plain")
        try:
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        except OSError:
            self._send(503, '<meta http-equiv=refresh content=10><p>Building the Paradev page for the first '
                       'time; this reloads in 10 seconds.</p>', "text/html; charset=utf-8")

    def do_POST(self):
        # Tailscale Serve strips its mount prefix; accept any …/refresh (see advance-roadmap/serve.py).
        if not self.path.split("?")[0].endswith("/refresh"):
            return self._send(404, "not found", "text/plain")
        if not self._allowed():
            return self._send(403, "forbidden", "text/plain")
        if not REFRESH.acquire(blocking=False):
            return self._send(409, json.dumps({"error": "a refresh is already running"}), "application/json")
        try:
            steps = refresh()
        finally:
            REFRESH.release()
        ok = all(s["ok"] for s in steps)
        self._send(200 if ok else 500, json.dumps({"ok": ok, "steps": steps}), "application/json")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def ensure_page():
    """A fresh install has no page yet: build one in the background instead of serving 503
    until the 18:50 snapshot."""
    if not os.path.exists(PAGE):
        threading.Thread(target=lambda: REFRESH.acquire() and (refresh(), REFRESH.release()),
                         daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PARADEV_PORT", "8422")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    ensure_page()
    print(f"paradev page on http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
