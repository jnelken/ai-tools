#!/usr/bin/env python3
"""This Mac's health on one read-only page: daemons, Tailscale, Superset, host, AI providers.

    serve.py [--port 8423]

GET /  runs every check (each with its own timeout, in parallel), caches the page 30 s, and
       renders a card per check with an overall ok / warning / problem state. Mounted at
       /status on the tailnet:
         tailscale serve --bg --set-path /status http://127.0.0.1:8423
Read-only: it only runs `status`-style commands and reads files. Bound to 127.0.0.1.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
DOTFILES_BIN = os.environ.get("MACHINE_STATUS_DOTFILES_BIN", os.path.join(HOME, "dotfiles", "bin"))
TAILSCALE = os.environ.get("TAILSCALE_BIN", "/usr/local/bin/tailscale")
SUPERSET = os.environ.get("SUPERSET_BIN", "/Applications/Superset.app/Contents/Resources/resources/bin/superset")
USAGE = os.environ.get("MACHINE_STATUS_USAGE",
                       os.path.join(HOME, ".claude/automations/advance-roadmap/providers-usage.json"))
USAGE_PY = os.environ.get("MACHINE_STATUS_USAGE_PY",
                          os.path.join(HOME, ".claude/automations/advance-roadmap/lib/usage.py"))
CACHE_S = 30
TIMEOUT_S = 8
_cache = {"at": 0.0, "page": None}
_lock = threading.Lock()
RANK = {"ok": 0, "absent": 0, "warn": 1, "problem": 2}


def run(argv, timeout=TIMEOUT_S):
    """(exit code, output). Exit None means it couldn't run: missing binary or timed out."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return None, "not installed"
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s"
    except OSError as e:
        return None, str(e)


def card(title, state, lines=(), raw=None):
    return {"title": title, "state": state, "lines": list(lines), "raw": raw}


# ── daemons (dotfiles/bin) ────────────────────────────────────────────────────

def parse_rc(out):
    running = re.search(r"launchd:\s*loaded \(running", out) is not None
    tmux = re.search(r"tmux:\s*session '[^']+' running", out) is not None
    # The latest connection message decides, so an old "Connected" can't mask a later drop.
    states = re.findall(r"\b(Connected|Reconnecting|Disconnected|Connecting|Connection lost)\b", out)
    connected = bool(states) and states[-1] == "Connected"
    if re.search(r"launchd:\s*not loaded", out):
        # Not loaded with no plist is "not installed"; with a plist, it's installed but down.
        if re.search(r"plist:\s*\(none\)", out):
            return "absent", "not installed"
        return "problem", "installed but not loaded"
    if running and tmux and connected:
        return "ok", "running · tmux session up · connected"
    missing = [n for n, ok in (("launchd job running", running), ("tmux session", tmux), ("connected", connected)) if not ok]
    return "problem", "missing: " + ", ".join(missing)


def parse_superset_host(out):
    if re.search(r"launchd:\s*not loaded", out):
        return "absent", "not installed (the Superset app runs the host service itself)"
    if re.search(r"launchd:\s*loaded \(running", out):
        return "ok", "running"
    return "problem", "loaded but not running"


def global_sweep_count(report_text):
    """Sessions the report's global sweep found dead outside Superset tabs."""
    i = report_text.find("== Global sweep")
    if i < 0:
        return 0
    section = report_text[i:report_text.find("== Summary", i) if "== Summary" in report_text[i:] else None]
    return len(re.findall(r"^\s+session [0-9a-f-]{8,}", section, re.M))


def parse_crash_check(out):
    if re.search(r"launchd:\s*not loaded", out):
        if re.search(r"plist:\s*\(none\)", out):
            return "absent", "not installed"
        return "problem", "installed but not loaded"
    m = re.search(r"RESUME candidates found:\s*(\d+)", out)
    if not m:
        return "warn", "no report yet this boot"
    # The summary counts Superset tabs only; the same report's global sweep covers the rest.
    outside = 0
    latest = re.search(r"latest:\s*(\S+\.log)", out)
    if latest:
        try:
            with open(latest.group(1), encoding="utf-8", errors="replace") as f:
                outside = global_sweep_count(f.read())
        except OSError:
            pass
    tabs = int(m.group(1))
    if tabs == 0 and outside == 0:
        return "ok", "no dead sessions after the last boot"
    parts = ([f"{tabs} in Superset tabs"] if tabs else []) + ([f"{outside} outside Superset"] if outside else [])
    return "warn", "dead sessions to look at: " + ", ".join(parts) + " (see the latest report)"


DAEMONS = (("claude-rc-daemon", "Claude Remote Control", parse_rc),
           ("superset-host-daemon", "Superset host daemon", parse_superset_host),
           ("crash-session-check", "Crash session check", parse_crash_check))


def check_daemon(cmd, title, parse):
    rc, out = run([os.path.join(DOTFILES_BIN, cmd), "status"])
    if rc is None:
        return card(title, "absent" if out == "not installed" else "problem", [("status", out)])
    state, summary = parse(out)
    return card(title, state, [("status", summary)], raw=out.strip()[-4000:])


# ── tailscale / superset ─────────────────────────────────────────────────────

def check_tailscale():
    rc, out = run([TAILSCALE, "status", "--self", "--json"])
    if rc is None or rc != 0:
        return card("Tailscale", "problem", [("status", out.strip()[:200] or f"exit {rc}")])
    try:
        me = json.loads(out).get("Self") or {}
    except ValueError:
        return card("Tailscale", "problem", [("status", "unreadable status")])
    lines = [("name", (me.get("DNSName") or "?").rstrip(".")),
             ("addresses", ", ".join(me.get("TailscaleIPs") or []) or "?")]
    rc2, out2 = run([TAILSCALE, "serve", "status", "--json"])
    try:
        serve = (json.loads(out2) or {}) if rc2 == 0 else {}
        n = sum(len(w.get("Handlers") or {}) for w in (serve.get("Web") or {}).values())
        lines.append(("served pages", str(n)))
    except ValueError:
        pass
    return card("Tailscale", "ok" if me.get("Online") else "problem",
                [("state", "online" if me.get("Online") else "offline")] + lines)


def check_superset():
    """Signed in (a cloud call) and the local host service answering (a --local listing, which
    the CLI can only serve through it). `superset status` describes a standalone `superset
    start` host, which this Mac doesn't use — the app runs the host itself — so it isn't asked."""
    rc, out = run([SUPERSET, "auth", "whoami", "--json"])
    try:
        who = json.loads(out) if rc == 0 else None
    except ValueError:
        who = None
    rc2, out2 = run([SUPERSET, "projects", "list", "--local", "--json"])
    host_ok = rc2 == 0
    lines = [("signed in", (who or {}).get("email") or f"no ({out.strip()[:120] or f'exit {rc}'})"),
             ("local host", "answering" if host_ok else f"not answering ({out2.strip()[:120] or f'exit {rc2}'})")]
    return card("Superset", "ok" if who and host_ok else "problem", lines)


# ── host ─────────────────────────────────────────────────────────────────────

def fmt_span(seconds):
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    return (f"{d}d " if d else "") + f"{h}h {rem // 60}m"


def parse_boottime(out):
    m = re.search(r"sec\s*=\s*(\d+)", out)
    return int(m.group(1)) if m else None


def parse_batt(out):
    source = re.search(r"drawing from '([^']+)'", out)
    batt = re.search(r"(\d+)%;\s*([^;]+);", out)
    return (source.group(1) if source else "?"), (f"{batt.group(1)}% ({batt.group(2).strip()})" if batt else None)


def parse_sleep_log(out):
    """The last Sleep and Wake lines from `pmset -g log`, as (when, what)."""
    last = {}
    for line in out.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4} (Sleep|Wake)\s+\t?(.*)$", line)
        if m:
            last[m.group(2)] = (m.group(1), m.group(3).strip()[:80])
    return last


def parse_assertions(out):
    """Processes holding a sleep-preventing assertion, as 'Name (process)'."""
    held = []
    for line in out.splitlines():
        m = re.search(r"pid \d+\(([^)]+)\):.*?(PreventUserIdleSystemSleep|PreventSystemSleep|NoIdleSleepAssertion)"
                      r'.*?named: "([^"]*)"', line)
        if m:
            held.append(f'{m.group(3)} ({m.group(1)})')
    return held


def check_host():
    lines, state = [], "ok"
    du = shutil.disk_usage("/")
    free_pct = 100 * du.free / du.total
    lines.append(("disk free", f"{du.free / 1e9:.0f} GB of {du.total / 1e9:.0f} GB ({free_pct:.0f}%)"))
    if free_pct < 10:
        state = "warn"
    rc, out = run(["sysctl", "-n", "kern.boottime"])
    boot = parse_boottime(out) if rc == 0 else None
    if boot:
        lines.append(("up", f"{fmt_span(time.time() - boot)} (since {datetime.fromtimestamp(boot):%b %-d %H:%M})"))
    load = os.getloadavg()
    lines.append(("load", " / ".join(f"{x:.2f}" for x in load)))
    rc, out = run(["pmset", "-g", "batt"])
    if rc == 0:
        source, batt = parse_batt(out)
        lines.append(("power", source + (f" · battery {batt}" if batt else "")))
    rc, out = run(["pmset", "-g", "log"], timeout=15)
    if rc == 0:
        sl = parse_sleep_log(out)
        for kind in ("Sleep", "Wake"):
            if kind in sl:
                lines.append((f"last {kind.lower()}", f"{sl[kind][0]} · {sl[kind][1]}"))
    rc, out = run(["pmset", "-g", "assertions"])
    if rc == 0:
        held = parse_assertions(out)
        lines.append(("kept awake by", ", ".join(held) if held else "nothing"))
    return card("This Mac", state, lines)


# ── AI providers ─────────────────────────────────────────────────────────────

def live_gate():
    """advance-roadmap's own gate function (a pure function of the usage state), so this page
    shows what routing would decide right now; None if it can't be loaded."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("advance_roadmap_usage", USAGE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.provider_gate
    except Exception:
        return None

def check_providers():
    try:
        data = json.load(open(USAGE))
    except (OSError, ValueError):
        return card("AI providers", "absent", [("status", "no usage file (advance-roadmap not run here)")])
    lines, worst = [], "ok"
    gate_fn = live_gate()
    for name, p in sorted((data.get("providers") or {}).items()):
        g = p.get("gate") or {}
        if gate_fn:
            try:
                g = gate_fn(data, name, "worker", time.time())
            except Exception:
                pass  # keep the stored gate
        gate = g.get("state") or "?"
        parts = [f"gate {gate}"]
        # Each provider reports its own windows: Claude five_hour/seven_day, Codex five_hour/weekly,
        # Cursor an `included` monthly pool.
        for win, label, key in (("five_hour", "5 h", "used_pct"), ("seven_day", "7 d", "used_pct"),
                                ("weekly", "week", "used_pct"), ("included", "month", "total_pct")):
            w = p.get(win) or {}
            if isinstance(w, dict) and w.get(key) is not None:
                parts.append(f"{label} {w[key]:.0f}%")
        lines.append((name, " · ".join(parts)))
        if gate not in ("ok", "?"):
            worst = "warn"
    updated = data.get("updated_at")
    if updated:
        lines.append(("checked", str(updated)[:16].replace("T", " ")))
    return card("AI providers", worst, lines)


# ── page ─────────────────────────────────────────────────────────────────────

def collect():
    jobs = [lambda c=c, t=t, p=p: check_daemon(c, t, p) for c, t, p in DAEMONS]
    jobs += [check_tailscale, check_superset, check_host, check_providers]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(j) for j in jobs]
        out = []
        for f, j in zip(futures, jobs):
            try:
                out.append(f.result(timeout=30))
            except Exception as e:  # one broken check must not take the page down
                out.append(card(getattr(j, "__name__", "check"), "problem", [("error", str(e)[:200])]))
    return out


def build(cards, now=None):
    now = now or datetime.now()
    worst = max((c["state"] for c in cards), key=lambda s: RANK.get(s, 2), default="ok")
    summary = {"ok": "All good", "absent": "All good", "warn": "Needs a look", "problem": "Something's wrong"}[worst]
    blocks = []
    for c in cards:
        rows = "".join(f"<div class=kv><span class=k>{html.escape(k)}</span><span>{html.escape(v)}</span></div>"
                       for k, v in c["lines"])
        raw = (f"<details><summary>raw output</summary><pre>{html.escape(c['raw'])}</pre></details>"
               if c.get("raw") else "")
        blocks.append(f'<div class="card {c["state"]}"><div class=top><span class="dot {c["state"]}"></span>'
                      f'<span class=name>{html.escape(c["title"])}</span></div>{rows}{raw}</div>')
    return PAGE.format(summary=summary, worst=worst, cards="".join(blocks), when=now.strftime("%H:%M:%S"))


PAGE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Machine status</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8;
           --ok:#0a7d34; --warn:#8a6d00; --problem:#b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0;
           --dim:#9a9aa6; --line:#2c2c33; --ok:#4ade80; --warn:#e3c04f; --problem:#f87171; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:24px 16px; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  .sub {{ color:var(--dim); font-size:13px; margin-bottom:18px; }}
  .banner {{ font-weight:650; font-size:17px; margin:0 0 16px; }}
  .banner.ok, .banner.absent {{ color:var(--ok); }} .banner.warn {{ color:var(--warn); }} .banner.problem {{ color:var(--problem); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .top {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
  .name {{ font-weight:650; }}
  .dot {{ width:9px; height:9px; border-radius:50%; flex:none; background:var(--dim); }}
  .dot.ok {{ background:var(--ok); }} .dot.warn {{ background:var(--warn); }} .dot.problem {{ background:var(--problem); }}
  .kv {{ display:flex; gap:10px; font-size:13px; padding:2px 0; }}
  .k {{ color:var(--dim); min-width:96px; flex:none; }}
  details {{ margin-top:8px; font-size:12px; }} summary {{ color:var(--dim); cursor:pointer; }}
  pre {{ white-space:pre-wrap; overflow-wrap:anywhere; font-size:11.5px; max-height:260px; overflow:auto; }}
</style>
</head>
<body>
<div class=wrap>
  <h1>Machine status</h1>
  <div class=sub>This Mac, checked {when} · <a href="">reload</a></div>
  <div class="banner {worst}">{summary}</div>
  <div class=grid>{cards}</div>
</div>
</body>
</html>"""


def render():
    with _lock:
        if _cache["page"] and time.time() - _cache["at"] < CACHE_S:
            return _cache["page"]
        page = build(collect())
        _cache.update(at=time.time(), page=page)
        return page


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/status", "/status/", "/index.html"):
            code, body, ctype = 404, b"not found", "text/plain"
        else:
            code, body, ctype = 200, render().encode(), "text/html; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("MACHINE_STATUS_PORT", "8423")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"machine status on http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
