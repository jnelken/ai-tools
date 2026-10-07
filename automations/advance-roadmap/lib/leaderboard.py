#!/usr/bin/env python3
"""Jake's standing on Superset's Production Run leaderboard, for the dashboard header.

Reads `leaderboard.me` from Superset's API — the same read-only query the desktop app makes —
with the Superset CLI's own login (~/.superset/config.json). Periods are UTC days.

    leaderboard.py snapshot [--force] [--max-age-min 10]
        fetch all-time, 7d and today; append one line to leaderboard-history.jsonl and write
        leaderboard.json (what gen-dashboard.py renders). Without --force, a snapshot younger
        than --max-age-min is kept as is.
    leaderboard.py show
        print leaderboard.json.

The API has no per-day history (`day` is always today), so the 7-day best is built here:
each UTC day's latest recorded rank, best of the last 7 days. It covers only days a snapshot
was taken — whenever the dashboard regenerates, so on every run and every page refresh.

Never prints or stores the token. Exits 0 even on failure: leaderboard.json then carries
{"ok": false, "error": …} and the dashboard says why.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ADVANCE_ROADMAP_ROOT", "/Users/jake/.claude/automations/advance-roadmap"))
CONFIG = Path(os.environ.get("SUPERSET_CONFIG", Path.home() / ".superset" / "config.json"))
SUPERSET = os.environ.get("ADVANCE_ROADMAP_SUPERSET_BIN",
                          "/Applications/Superset.app/Contents/Resources/resources/bin/superset")
API = os.environ.get("SUPERSET_API_URL", "https://api.superset.sh")
# Cloudflare in front of the API rejects urllib's default agent (error 1010); look like the app.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Superset/1.30.2 Chrome/138.0.0.0 Electron/37.0.0 Safari/537.36")
OUT = ROOT / "leaderboard.json"
HISTORY = ROOT / "leaderboard-history.jsonl"


def token(refresh=False):
    """The CLI's access token; asks the CLI to refresh it when expired (or when told to)."""
    def read():
        try:
            return json.loads(CONFIG.read_text())["auth"]
        except (OSError, ValueError, KeyError):
            return {}
    auth = read()
    if refresh or (auth.get("expiresAt") or 0) / 1000 < time.time() + 60:
        subprocess.run([SUPERSET, "auth", "whoami", "--json"], capture_output=True, timeout=60)
        auth = read()
    return auth.get("accessToken")


def me(period, tok):
    inp = json.dumps({"0": {"json": {"period": period}}})
    req = urllib.request.Request(
        f"{API}/api/trpc/leaderboard.me?batch=1&input={urllib.parse.quote(inp)}",
        headers={"Authorization": f"Bearer {tok}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())[0]["result"]["data"]["json"]


def fetch():
    tok = token()
    if not tok:
        raise RuntimeError("no Superset login (run `superset auth login`)")
    out = {}
    for key, period in (("all_time", "all"), ("rolling_7d", "7d"), ("today", "day")):
        try:
            r = me(period, tok)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise RuntimeError(f"leaderboard.me {period}: HTTP {e.code}") from None
            tok = token(refresh=True)
            r = me(period, tok)
        out[key] = {"rank": r.get("rank"), "total": r.get("total"), "tokens": r.get("tokens"),
                    "range": r.get("range"), "approximate": r.get("approximate")}
        out["handle"] = r.get("handle")
        out["last_published_at"] = r.get("lastPublishedAt")
    return out


def history():
    rows = []
    try:
        for line in HISTORY.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def best_7d(rows, today):
    """Best (lowest) of each UTC day's latest recorded rank over the last 7 days, today included."""
    since = (datetime.fromisoformat(today) - timedelta(days=6)).date().isoformat()
    latest = {}
    for r in rows:
        day = r.get("day")
        if day and since <= day <= today and (r.get("today") or {}).get("rank"):
            latest[day] = r["today"]  # rows are in time order, so the last one per day wins
    if not latest:
        return None
    day, best = min(latest.items(), key=lambda kv: kv[1]["rank"])
    return {"rank": best["rank"], "total": best.get("total"), "day": day, "days_recorded": len(latest)}


def write(data):
    tmp = OUT.with_name(f"{OUT.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(tmp, OUT)


def cmd_snapshot(a):
    try:
        prior = json.loads(OUT.read_text())
    except (OSError, ValueError):
        prior = {}
    if not a.force and prior.get("ok"):
        try:
            age = time.time() - datetime.fromisoformat(prior["fetched_at"]).timestamp()
            if age < a.max_age_min * 60:
                return 0
        except (KeyError, ValueError):
            pass
    now = datetime.now(timezone.utc)
    try:
        got = fetch()
    except Exception as e:  # network, auth, API shape — the dashboard shows the reason
        write({**{k: v for k, v in prior.items() if k != "error"}, "ok": False,
               "error": str(e)[:200], "failed_at": now.isoformat(timespec="seconds")})
        print(f"leaderboard: {e}", file=sys.stderr)
        return 0
    day = ((got["today"].get("range") or {}).get("from")) or now.date().isoformat()
    row = {"fetched_at": now.isoformat(timespec="seconds"), "day": day, **got}
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "a") as f:
        f.write(json.dumps(row) + "\n")
    write({"ok": True, **row, "best_7d": best_7d(history(), day)})
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--force", action="store_true")
    s.add_argument("--max-age-min", type=float, default=10)
    sub.add_parser("show")
    a = p.parse_args(argv)
    if a.cmd == "show":
        print(OUT.read_text() if OUT.exists() else "{}")
        return 0
    return cmd_snapshot(a)


if __name__ == "__main__":
    sys.exit(main())
