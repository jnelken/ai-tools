#!/usr/bin/env python3
"""Serve dashboard.html on localhost with a one-click refresh.

    serve.py [--port 8421]

GET  /          the dashboard
POST /refresh   re-read provider usage, take a fresh Linear snapshot, regenerate the page

A static file:// page can't run anything, so this is the only way the dashboard's
refresh button can do real work. Bound to 127.0.0.1, and /refresh checks Host and
Origin so a random web page can't trigger it (or DNS-rebind to it).
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
ROOT = os.environ.get("ADVANCE_ROADMAP_ROOT", os.path.expanduser("~/.claude/automations/advance-roadmap"))
DASHBOARD = os.path.join(ROOT, "dashboard.html")
LIVE_SNAPSHOT = os.path.join(ROOT, "live-snapshot.json")
RUN_LOCK = os.path.join(ROOT, "run.lock")
REFRESH = threading.Lock()


def step(name, argv, timeout):
    t = time.time()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        ok, err = p.returncode == 0, (p.stderr or p.stdout).strip()[-400:]
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, err = False, str(e)
    return {"step": name, "ok": ok, "secs": round(time.time() - t, 1), **({} if ok else {"error": err})}


def refresh():
    py = sys.executable
    steps = []
    # A run in flight refreshes usage and Linear itself; racing its writes buys nothing.
    if os.path.exists(RUN_LOCK):
        steps.append({"step": "usage + linear", "ok": True, "secs": 0, "skipped": "a run is in progress"})
    else:
        steps.append(step("usage", [py, os.path.join(HERE, "lib/usage.py"), "--root", ROOT, "refresh"], 120))
        steps.append(step("linear", [py, os.path.join(HERE, "lib/linearsnap.py"), "--out", LIVE_SNAPSHOT], 120))
    steps.append(step("dashboard", [py, os.path.join(HERE, "gen-dashboard.py")], 120))
    return steps


class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin.split("//", 1)[-1] in hosts)

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/dashboard.html"):
            return self._send(404, "not found", "text/plain")
        try:
            with open(DASHBOARD, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        except OSError:
            self._send(503, "dashboard.html not generated yet — POST /refresh", "text/plain")

    def do_POST(self):
        if self.path != "/refresh":
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("ADVANCE_ROADMAP_DASHBOARD_PORT", "8421")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"dashboard on http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
