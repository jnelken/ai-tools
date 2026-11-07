#!/usr/bin/env python3
"""Jake's standing on Paradev's (formerly Superset's) Production Run leaderboard — the data
behind the Paradev page (gen-page.py, served at /paradev on the tailnet).

Reads `leaderboard.me` from Superset's API — the same read-only query the desktop app makes —
with the Superset CLI's own login (~/.superset/config.json). Periods are UTC days.

    leaderboard.py snapshot [--force] [--max-age-min 10]
        fetch all-time, 7d and today; append one line to leaderboard-history.jsonl and write
        leaderboard.json (what gen-page.py renders). Without --force, a snapshot younger
        than --max-age-min is kept as is.
        Also finalizes the last week's ended days that aren't in leaderboard-daily.jsonl yet.
    leaderboard.py backfill (--top N | --all | --days D [D ...])
        record final single-day ranks for past UTC days: the N busiest days on the public
        profile, every active day, or the days given. Days already recorded are skipped.
    leaderboard.py show
        print leaderboard.json.

`leaderboard.me` only ever reports today, and a snapshot catches it mid-day. A finished day's
rank comes from the public standings for that single day (`leaderboard.public.standings` with
from = to = day, no login), stored in leaderboard-daily.jsonl. Machines keep republishing past
days, so a record stays provisional (and is refreshed) until SETTLE_HOURS after the day ends.
The API pages only to offset 1000: a day where Jake isn't in the top 1100 is stored as
`rank: null, below: 1100` — below the reachable range, which is not the same as absent.
The 7-day best and the personal records are built from those final ranks; today's rank is the
latest snapshot. launchd snapshots once a day near the end of the UTC day
(com.jake.paradev-snapshot), and every page regeneration snapshots too.

Never prints or stores the token. Exits 0 even on failure: leaderboard.json then carries
{"ok": false, "error": …} and the dashboard says why.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("PARADEV_ROOT", Path.home() / ".claude" / "automations" / "paradev"))
CONFIG = Path(os.environ.get("SUPERSET_CONFIG", Path.home() / ".superset" / "config.json"))
SUPERSET = os.environ.get("ADVANCE_ROADMAP_SUPERSET_BIN",
                          "/Applications/Superset.app/Contents/Resources/resources/bin/superset")
API = os.environ.get("SUPERSET_API_URL", "https://api.superset.sh")
# Cloudflare in front of the API rejects urllib's default agent (error 1010); look like the app.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Superset/1.30.2 Chrome/138.0.0.0 Electron/37.0.0 Safari/537.36")
WEB = os.environ.get("PARADEV_WEB_URL", "https://paradev.com")
OUT = ROOT / "leaderboard.json"
HISTORY = ROOT / "leaderboard-history.jsonl"
DAILY = ROOT / "leaderboard-daily.jsonl"
DEFAULT_HANDLE = "jnelken"
# Where these files lived before the Paradev page got its own automation; merged in once.
LEGACY_ROOT = Path(os.environ.get("PARADEV_LEGACY_ROOT",
                                  Path.home() / ".claude" / "automations" / "advance-roadmap"))
PAGE_SLEEP_S = float(os.environ.get("LEADERBOARD_PAGE_SLEEP_S", "0.5"))
FINALIZE_DAYS = 7  # how far back a snapshot fills in missing final ranks
# A snapshot runs inside the page generator's 90 s budget: finalizing stops after this long and the
# next snapshot picks up where it left off.
FINALIZE_BUDGET_S = float(os.environ.get("LEADERBOARD_FINALIZE_BUDGET_S", "40"))
SETTLE_HOURS = 48     # other machines republish past days on a 2-hour cadence for a while
MAX_OFFSET = 1000     # the standings API refuses a larger offset


class OutOfTime(Exception):
    """The time budget ran out partway through a day; nothing was stored for it."""


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


def utc_now():
    return datetime.now(timezone.utc)


def utc_today():
    return utc_now().date().isoformat()


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def standings(day, offset, timeout=30):
    """One page (100 rows) of the public ranking for a single UTC day. No login."""
    inp = json.dumps({"0": {"json": {"from": day, "to": day, "limit": 100, "offset": offset}}})
    raw = get(f"{API}/api/trpc/leaderboard.public.standings?batch=1&input={urllib.parse.quote(inp)}",
              timeout=timeout)
    return json.loads(raw)[0]["result"]["data"]["json"]


def day_rank(day, handle, deadline=None):
    """The handle's rank for one ended UTC day. rank None with `below` set when it isn't in the
    top 1100 the API can reach; rank None without it when the whole board was read and it wasn't
    there. Raises OutOfTime when `deadline` (a time.monotonic() value) passes mid-day."""
    offset = 0
    while True:
        timeout = 30
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise OutOfTime(day)
            timeout = max(1, min(30, left))
        page = standings(day, offset, timeout)
        rows = page.get("rows") or []
        for row in rows:
            if row.get("handle") == handle:
                return {"day": day, "rank": row.get("rank"), "total": page.get("total"),
                        "tokens": int(row.get("tokens") or 0), "usd": float(row.get("usd") or 0),
                        "sessions": row.get("sessions")}
        if not page.get("hasMore") or not rows:
            return {"day": day, "rank": None, "total": page.get("total"), "tokens": 0, "usd": 0.0,
                    "sessions": 0}
        offset += len(rows)
        if offset > MAX_OFFSET:
            return {"day": day, "rank": None, "below": offset, "total": page.get("total"),
                    "tokens": None, "usd": None, "sessions": None}
        time.sleep(PAGE_SLEEP_S)


def profile_days(handle):
    """{day: tokens} for every active day, from the public profile page's contributions data."""
    html = get(f"{WEB}/{urllib.parse.quote(handle)}", timeout=60).decode("utf-8", "replace")
    days = {d: int(t) for d, t in re.findall(
        r'\\?"day\\?":\\?"(\d{4}-\d\d-\d\d)\\?",\\?"tokens\\?":\\?"(\d+)\\?"', html)}
    if not days:
        raise RuntimeError(f"no daily contributions found on {WEB}/{handle} — the page layout changed")
    return days


def daily():
    """Final per-day records, {day: record}."""
    out = {}
    try:
        for line in DAILY.read_text().splitlines():
            try:
                r = json.loads(line)
                out[r["day"]] = r
            except (ValueError, KeyError):
                pass
    except OSError:
        pass
    return out


def settled(rec):
    """Fetched at least SETTLE_HOURS after its day ended, so late publishes are in."""
    try:
        day_end = datetime.fromisoformat(rec["day"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
        return datetime.fromisoformat(rec["fetched_at"]) >= day_end + timedelta(hours=SETTLE_HOURS)
    except (KeyError, TypeError, ValueError):
        return False


def store_day(rec):
    """Merge one day into leaderboard-daily.jsonl. Snapshots and backfills can run at once, so
    the store is re-read under a lock and rewritten atomically."""
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(DAILY.with_name(DAILY.name + ".lock"), "w") as lk:
        fcntl.lockf(lk, fcntl.LOCK_EX)
        store = daily()
        store[rec["day"]] = rec
        tmp = DAILY.with_name(f"{DAILY.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(json.dumps(store[d]) + "\n" for d in sorted(store)))
        os.replace(tmp, DAILY)


def record_days(days, handle, log=print, budget_s=None):
    """Fetch and store ranks for ended days that are missing or still provisional: missing days
    first, then provisional refreshes, each oldest first (the oldest leaves the retry window
    soonest), so a budget-limited run always makes progress and never starves a missing day.
    With budget_s, give up once that much time has passed (a day is stored only when complete).
    Returns how many days it wrote."""
    store, today, wrote = daily(), utc_today(), 0
    deadline = time.monotonic() + budget_s if budget_s is not None else None
    for day in sorted(set(days), key=lambda d: (d in store, d)):
        if day >= today or (day in store and settled(store[day])):
            continue  # not over yet, or already final
        try:
            got = day_rank(day, handle, deadline)
        except OutOfTime:
            break
        rec = {**got, "source": "standings", "fetched_at": utc_now().isoformat(timespec="seconds")}
        rec["final"] = settled(rec)
        store_day(rec)  # after every day, so a timeout keeps what's done
        wrote += 1
        log(f"{day}: " + (f"#{rec['rank']} / {rec['total']}" if rec["rank"]
                          else f"below #{rec['below']} / {rec['total']}" if rec.get("below")
                          else f"not ranked ({rec['total']} ranked)")
            + ("" if rec["final"] else " (provisional)"))
    return wrote


def records(store):
    """Personal records from final daily ranks: best rank, busiest day, top 10 days by tokens."""
    active = [r for r in store.values() if r.get("rank")]
    if not active:
        return None

    def view(r):
        total = r.get("total") or 0
        return {"day": r["day"], "rank": r["rank"], "total": total, "tokens": r.get("tokens", 0),
                "usd": r.get("usd", 0), "sessions": r.get("sessions"),
                "top_pct": round(100 * r["rank"] / total, 1) if total else None,
                "final": bool(r.get("final"))}  # provisional until SETTLE_HOURS after the day
    best = min(active, key=lambda r: (r["rank"], -(r.get("total") or 0)))
    busiest = max(active, key=lambda r: r.get("tokens", 0))
    top = sorted(active, key=lambda r: -r.get("tokens", 0))[:10]
    return {"best_rank": view(best), "busiest": view(busiest), "top_days": [view(r) for r in top],
            "days_recorded": len(store)}


def handle_of(prior):
    return (prior or {}).get("handle") or DEFAULT_HANDLE


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


def best_7d(rows, today, final=None):
    """Best (lowest) daily rank over the last 7 days, today included: a day's final rank when
    recorded, else its latest snapshot (today's is always a snapshot)."""
    since = (datetime.fromisoformat(today) - timedelta(days=6)).date().isoformat()
    latest = {}
    for r in rows:
        day = r.get("day")
        if day and since <= day <= today and (r.get("today") or {}).get("rank"):
            latest[day] = r["today"]  # rows are in time order, so the last one per day wins
    for day, r in (final or {}).items():
        if since <= day < today:
            if r.get("rank"):
                latest[day] = {"rank": r["rank"], "total": r.get("total")}
            else:
                latest.pop(day, None)  # not on the board that day
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
    # Today's ranks are saved before anything slower runs, so a timeout can't lose them.
    final = daily()
    write({"ok": True, **row, "best_7d": best_7d(history(), day, final), "records": records(final)})
    # Then fill in the last week's days that are missing or still provisional.
    finalize_error = None
    recent = [(datetime.fromisoformat(day) - timedelta(days=n)).date().isoformat()
              for n in range(1, FINALIZE_DAYS + 1)]
    try:
        record_days(recent, handle_of(got), log=lambda m: None, budget_s=FINALIZE_BUDGET_S)
    except Exception as e:  # the ranks for today still stand; the next snapshot retries
        finalize_error = f"finalizing past days: {e}"[:200]
    final = daily()
    write({"ok": True, **row, "best_7d": best_7d(history(), day, final), "records": records(final),
           **({"finalize_error": finalize_error} if finalize_error else {})})
    return 0


def cmd_backfill(a):
    try:
        prior = json.loads(OUT.read_text())
    except (OSError, ValueError):
        prior = {}
    handle = handle_of(prior)
    if a.days:
        days = a.days
    else:
        today = utc_today()  # only finished days have a final rank
        busiest = sorted(((d, t) for d, t in profile_days(handle).items() if d < today), key=lambda kv: -kv[1])
        days = [d for d, _ in (busiest if a.all else busiest[:a.top])]
    wrote = record_days(days, handle)
    final = daily()
    try:  # re-read: a snapshot may have written newer ranks while this was fetching
        current = json.loads(OUT.read_text())
    except (OSError, ValueError):
        current = {}
    if current:
        today = current.get("day") or utc_today()
        write({**current, "best_7d": best_7d(history(), today, final), "records": records(final)})
    print(f"backfill: {wrote} day(s) recorded, {len(final)} in {DAILY.name}")
    return 0


def migrate_legacy():
    """Merge leaderboard files left in the advance-roadmap folder into ROOT, once: history lines
    go first (they're older), daily records fill days ROOT lacks, leaderboard.json is copied
    only when ROOT has none. Each migrated file is renamed *.migrated so this runs once."""
    if not LEGACY_ROOT.is_dir() or LEGACY_ROOT.resolve() == ROOT.resolve():
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / ".migrate.lock", "w") as lk:  # two first runs at once mustn't both migrate
        fcntl.lockf(lk, fcntl.LOCK_EX)
        _migrate_locked()


def _migrate_locked():
    old_hist = LEGACY_ROOT / HISTORY.name
    if old_hist.exists():
        new_lines = HISTORY.read_text().splitlines() if HISTORY.exists() else []
        old_lines = [l for l in old_hist.read_text().splitlines() if l not in set(new_lines)]
        tmp = HISTORY.with_name(f"{HISTORY.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(l + "\n" for l in old_lines + new_lines))
        os.replace(tmp, HISTORY)
        old_hist.rename(old_hist.with_name(old_hist.name + ".migrated"))
    old_daily = LEGACY_ROOT / DAILY.name
    if old_daily.exists():
        have = daily()
        for line in old_daily.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("day") and rec["day"] not in have:
                store_day(rec)
        old_daily.rename(old_daily.with_name(old_daily.name + ".migrated"))
    old_out = LEGACY_ROOT / OUT.name
    if old_out.exists():
        if not OUT.exists():
            os.replace(old_out, OUT)
        else:
            old_out.rename(old_out.with_name(old_out.name + ".migrated"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--force", action="store_true")
    s.add_argument("--max-age-min", type=float, default=10)
    b = sub.add_parser("backfill")
    which = b.add_mutually_exclusive_group(required=True)
    which.add_argument("--top", type=int, help="the N busiest days on the public profile")
    which.add_argument("--all", action="store_true", help="every active day on the public profile")
    which.add_argument("--days", nargs="+", help="these UTC days (YYYY-MM-DD)")
    sub.add_parser("show")
    a = p.parse_args(argv)
    migrate_legacy()
    if a.cmd == "show":
        print(OUT.read_text() if OUT.exists() else "{}")
        return 0
    if a.cmd == "backfill":
        return cmd_backfill(a)
    return cmd_snapshot(a)


if __name__ == "__main__":
    sys.exit(main())
