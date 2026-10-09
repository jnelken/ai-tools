#!/usr/bin/env python3
"""Generate a self-contained dashboard.html for the advance-roadmap automation.

Sibling of wrapup-repos/gen-dashboard.py — same look, different data model.

Where the data comes from, and why it's split:
  * RUNS.JSONL is authoritative. run.sh appends one record per run (lib/runrecord.py)
    from exit codes and agent-written result files. When a run has a record,
    nothing below is consulted for its outcome — the rest is the legacy path for
    runs that predate records (2026-09-24), and the log is kept only for display.
  * LOGS are the legacy spine. Every run leaves one, and the lines run.sh itself emits
    (quota skip, lock skip, the exit footer) are deterministic. The model's prose
    summary is NOT — only ~9 of 29 logs carry the documented `Repo:` block — so
    nothing here depends on parsing it.
  * The LEDGER (run memory's `## Run ledger`) carries authoritative outcome
    tokens, but the skill trims it to ~10 rows, so it alone can't be a history.
    We merge each sighting into ledger-cache.json so outcomes survive the trim.
  * Outcomes we couldn't source from the ledger are marked "inferred" in the UI.
    Never present a guess as authoritative.

Writes a single static HTML file, data baked in — safe to open via file://.
Run manually, or let run.sh call it after every run (including skipped ones).
"""
import glob
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

def _personal_code_dir_module():
    """ai-tools' lib/personal_code_dir.py is the one place that decides where the
    personal repos live; find it from this file's real (symlink-resolved) location."""
    d = os.path.dirname(os.path.realpath(__file__))
    while d != os.path.dirname(d) and not os.path.isfile(os.path.join(d, "lib", "personal_code_dir.py")):
        d = os.path.dirname(d)
    if d == os.path.dirname(d):  # a copy outside the repo (e.g. a test root): use the deploy clone
        d = os.environ.get("AI_TOOLS_HOME", os.path.expanduser("~/.ai-tools"))
    sys.path.insert(0, os.path.join(d, "lib"))
    import personal_code_dir
    return personal_code_dir


HOME = os.path.expanduser("~")
ROOT = os.environ.get("ADVANCE_ROADMAP_ROOT", os.path.join(HOME, ".claude/automations/advance-roadmap"))
LOGDIR = os.path.join(ROOT, "logs")
CACHE = os.path.join(ROOT, "ledger-cache.json")
OUT = os.path.join(ROOT, "dashboard.html")
_pcd = _personal_code_dir_module()
CODE_DIR = _pcd.code_dir()
if not CODE_DIR:
    print("gen-dashboard: no personal code dir on this machine (set PERSONAL_CODE_DIR) — nothing to show.")
    sys.exit(0)
# Headless runs use CODE_DIR as cwd, so this is that project's memory dir.
MEMORY = os.path.join(_pcd.memory_dir(CODE_DIR), "project_advance-roadmap-runs.md")
RUNS_DIR = os.path.join(_pcd.memory_dir(CODE_DIR), "advance-roadmap-runs")
USAGE = os.path.join(HOME, ".claude/state/claude-usage.json")
PROVIDERS_USAGE = os.path.join(ROOT, "providers-usage.json")
RUNS_JSONL = os.path.join(ROOT, "runs.jsonl")
LOG_TAIL_CHARS = 4000

# Thresholds mirror lib/usage.py. Kept in sync by hand; shown so the page can say
# whether the next run would be gated.
MAX_SEVEN_DAY_PCT = 80
MAX_FIVE_HOUR_PCT = 70
MAX_CURSOR_PCT = 95
# worker.sh draws Cursor usage from the Auto pool unless a named model is pinned;
# only that pool is gated (lib/usage.py cursor_quota_ok).
CURSOR_WORKER_MODEL = os.environ.get("ADVANCE_ROADMAP_CURSOR_WORKER_MODEL", "auto")
# Before GRID_SINCE launchd fired at fixed 6h slots; from it, hourly at :45 with run.sh keeping
# the base-cadence grid ((hour - 3) % base == 0); from SPRINT_SINCE, hourly ticks inside two
# daily sprint windows (keep SPRINT_TICKS in step with com.jake.advance-roadmap.plist). Old
# history is judged by the slots that applied then.
LEGACY_SLOTS = [(4, 45), (10, 45), (16, 45), (22, 45)]
GRID_SINCE = datetime(2026, 9, 27, 22, 0)
GRID_BASE = 2  # the :45 grid's base cadence, for judging that era's history
SPRINT_SINCE = datetime(2026, 10, 7, 0, 0)
SPRINT_TICKS = [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (16, 0), (17, 0), (18, 0), (19, 0), (20, 0)]
BATCH_SINCE = datetime(2026, 10, 10, 0, 0)
BATCH_DESCRIPTION = "10:30 daily; one item per repo"


def slots_on(day):
    """Scheduled slot datetimes on this calendar day, under whichever schedule applied then."""
    base = base_cadence()
    grid = [(h, 45) for h in range(24) if (h - 3) % GRID_BASE == 0]
    out = []
    for h, m in LEGACY_SLOTS:
        if day.replace(hour=h, minute=m) < GRID_SINCE:
            out.append(day.replace(hour=h, minute=m))
    for h, m in grid:
        if GRID_SINCE <= day.replace(hour=h, minute=m) < SPRINT_SINCE:
            out.append(day.replace(hour=h, minute=m))
    for h, m in SPRINT_TICKS:
        slot = day.replace(hour=h, minute=m)
        if SPRINT_SINCE <= slot < BATCH_SINCE and (h - 3) % base == 0:
            out.append(day.replace(hour=h, minute=m))
    if day.replace(hour=10, minute=30) >= BATCH_SINCE:
        out.append(day.replace(hour=10, minute=30))
    return sorted(out)

QUOTA_RE = re.compile(r"skipping — (\S+) usage (\d+)% >= (\d+)%")
NO_ORCH_RE = re.compile(r"skipping — no orchestrator available")
BACKOFF_RE = re.compile(r"skipping — backed off to every (\d+)h after (\S+) blocked runs")
LOCK_RE = re.compile(r"(another run holds|could not take lock)")
UNCHANGED_RE = re.compile(r"skipping — verdict: nothing changed since (\S+)")
EXIT_RE = re.compile(
    r"=== (?:claude exit=(\d+)\s+finished (.*?)|(?:advance-roadmap exit=(\d+)\s+finished (.*?))) ==="
)
# Legacy single-model line, or new orchestrator=… workers=… line.
MODEL_RE = re.compile(r"(?:model|orchestrator)=(\S+)")
LEDGER_ROW_RE = re.compile(r"^\|\s*`(\d{8}-\d{6})`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")
FIELD_RE = re.compile(r"^(Repo|Item|Test/build|Test|Merge|For you):\s*(.*)$", re.M)
PUSH_RE = re.compile(r"(PUSH_PROBE:.*|[Pp]ush (?:nudge |notification )?(?:did not|not) (?:reach|sent).*)")


def esc(s):
    return html.escape(str(s) if s is not None else "")


def git(args, cwd=None, timeout=15):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


# ── ledger ────────────────────────────────────────────────────────────────────

def read_ledger():
    """Rows currently in every run-memory ledger, keyed by stamp. Ledgers are per repo
    (advance-roadmap-runs/*.md, written by lib/runmemory.py); the legacy single file is read too, for
    rows from before the split."""
    out = {}
    paths = sorted(glob.glob(os.path.join(RUNS_DIR, "*.md"))) + [MEMORY]
    for path in paths:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "## Run ledger" not in text:
            continue
        section = text.split("## Run ledger", 1)[1]
        section = section.split("\n## ", 1)[0]
        for line in section.splitlines():
            m = LEDGER_ROW_RE.match(line.strip())
            if m and m.group(1):
                out[m.group(1)] = {"date": m.group(2), "repo": m.group(3), "outcome": m.group(4)}
    return out


def merge_cache(current):
    """Ledger rows are trimmed to ~10; remember every row we've ever seen."""
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    cache.update(current)
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        atomic_write(CACHE, json.dumps(cache, indent=1, sort_keys=True))
    except OSError:
        pass
    return cache


# ── runs ──────────────────────────────────────────────────────────────────────

# A merge line naming a sha range is the strongest "something landed" signal, but
# Step 2b's `.gitignore` prerequisite also produces one — and that run shipped
# nothing. Exclude bookkeeping merges before trusting it.
MERGE_RE = re.compile(r"^Merged?:\s.*?[0-9a-f]{7,}\.\.[0-9a-f]{7,}.*$", re.M)
BOOKKEEPING = ("gitignore", "chore:", "not a feature", "prerequisite", "bookkeeping")
NO_SHIP = ("none shipped", "no repo qualified", "nothing shipped", "nothing was shipped")


def infer(body):
    """Outcome for runs the ledger no longer covers. Order matters."""
    low = body.lower()
    m = MERGE_RE.search(body)
    if m and not any(b in m.group(0).lower() for b in BOOKKEEPING):
        return "shipped"
    if any(p in low for p in NO_SHIP) or "blocked-no-item" in low:
        return "blocked-no-item"
    return "completed"


def classify(text, exit_code, has_header, fresh=False):
    """First match wins. Only deterministic signals decide; prose never does."""
    qm = QUOTA_RE.search(text)
    if qm:
        return "skipped-quota", f"{qm.group(1)} usage {qm.group(2)}% ≥ {qm.group(3)}%"
    if NO_ORCH_RE.search(text):
        return "skipped-quota", "no orchestrator available (codex+claude hot)"
    bm = BACKOFF_RE.search(text)
    if bm:
        return "skipped-backoff", f"cadence {bm.group(1)}h — {bm.group(2)} blocked runs in a row"
    if LOCK_RE.search(text):
        return "skipped-lock", "another run held the lock"
    um = UNCHANGED_RE.search(text)
    if um:
        return "skipped-unchanged", f"nothing changed since {um.group(1)}"
    if exit_code is None:
        if fresh:
            return "running", "in flight — no exit line yet"
        return ("incomplete", "no exit line — killed mid-run") if has_header else ("incomplete", "no exit line")
    if exit_code != 0:
        return "error", f"exit {exit_code}"
    return None, ""


def read_records():
    """runs.jsonl keyed by stamp; the last line for a stamp wins."""
    out = {}
    try:
        with open(RUNS_JSONL, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("stamp"):
                    out[r["stamp"]] = r
    except OSError:
        pass
    return out


def fmt_duration(secs):
    if secs is None or not 0 <= secs < 86400:
        return ""
    return f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"


def parse_runs(ledger, records=None):
    records = records or {}
    runs = []
    for path in sorted(glob.glob(os.path.join(LOGDIR, "run-*.log")), reverse=True):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        stamp = os.path.basename(path)[4:-4]
        try:
            started = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        except ValueError:
            started = None

        # Last match, not first: the orchestrator often tails an earlier run's log
        # while checking for a resume, which quotes that run's exit line verbatim.
        em = next(reversed(list(EXIT_RE.finditer(text))), None)
        mm = MODEL_RE.search(text)
        if em and em.group(1) is not None:
            exit_code = int(em.group(1))
        elif em and em.group(3) is not None:
            exit_code = int(em.group(3))
        else:
            exit_code = None
        has_header = "advance-roadmap run" in text

        # Body = everything the model printed, minus run.sh's own framing.
        start, end = 0, len(text)
        if mm:
            nl = text.find("\n", mm.end())
            if nl != -1:
                start = nl + 1
        if em and em.start() >= start:
            end = em.start()
        body = text[start:end].strip()

        fresh = (datetime.now().timestamp() - mtime) < 90 * 60
        outcome, detail = classify(text, exit_code, has_header, fresh)
        provenance = "run.sh"
        row = ledger.get(stamp)
        repo = ""
        if outcome is None:
            if row:
                outcome, repo, provenance = (row["outcome"] or "completed"), row["repo"], "ledger"
                detail = ""
            else:
                provenance = "inferred"
                outcome, detail = infer(body), ""

        fields = {k.replace("Test/build", "Test"): v.strip() for k, v in FIELD_RE.findall(body)}
        if not repo:
            repo = fields.get("Repo", "").split("—")[0].strip()[:60]
        pm = PUSH_RE.search(body)

        duration = ""
        if started and exit_code is not None:
            secs = int(mtime - started.timestamp())
            if 0 <= secs < 86400:
                duration = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"

        rec = records.get(stamp)
        if rec:
            outcome, provenance = rec.get("outcome") or "error", "record"
            detail = rec.get("detail") or ""
            exit_code = rec.get("exit")
            duration = fmt_duration(rec.get("duration_s")) or duration
            repo = rec.get("repo") or ""
            review = ""
            if rec.get("reviewer") or rec.get("review_rounds"):
                review = " · ".join(str(x) for x in (
                    rec.get("reviewer"), rec.get("review_verdict"),
                    f'{rec.get("review_rounds")} round(s)' if rec.get("review_rounds") else None) if x)
            fields = {k: v for k, v in (
                ("Repo", rec.get("repo")), ("Item", rec.get("item")),
                ("Branch", rec.get("branch")), ("Merge", rec.get("merge_commit")),
                ("Route", " → ".join(x for x in (rec.get("orchestrator"), rec.get("worker")) if x)),
                ("Review", review), ("Summary", rec.get("summary")), ("Detail", detail)) if v}
            mm_model = rec.get("orchestrator")
        else:
            mm_model = mm.group(1) if mm else None

        runs.append({
            "stamp": stamp,
            "when": started.strftime("%a %b %d  %H:%M") if started else stamp,
            "started": started,
            "model": (mm_model or "—").replace("claude-", ""),
            "exit": exit_code,
            "outcome": outcome,
            "detail": detail,
            "provenance": provenance,
            "repo": repo,
            "fields": fields,
            "linear_id": (rec or {}).get("linear_id") or "",
            "tokens": (rec or {}).get("tokens"),
            "push": pm.group(0).strip() if pm else "",
            "duration": duration,
            "log_tail": body[-LOG_TAIL_CHARS:],
            "log_chars": len(body),
            "log_path": ("~/" + str(Path(path).relative_to(HOME))
                         if Path(path).is_relative_to(HOME) else path),
        })
    return runs


def missed_slots(runs):
    """Scheduled fires with no log at all — the machine was asleep.

    A catch-up fire lands late (e.g. 12:59 for the 10:45 slot), so each run is
    assigned to the nearest PRECEDING slot within 4 hours (or one cadence, if shorter).
    """
    if not runs:
        return []
    stamps = sorted(r["started"] for r in runs if r["started"])
    if not stamps:
        return []
    first, now = stamps[0], datetime.now()
    covered, expected = set(), []
    day = first.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= now:
        for s in slots_on(day):
            if first - timedelta(minutes=30) <= s <= now - timedelta(minutes=20):
                expected.append(s)
        day += timedelta(days=1)
    for st in stamps:
        best = None
        for s in expected:
            window = timedelta(hours=4 if s < GRID_SINCE else min(4, GRID_BASE if s < SPRINT_SINCE else base_cadence()))
            if s <= st + timedelta(minutes=5) and (st - s) <= window:
                if best is None or s > best:
                    best = s
        if best:
            covered.add(best)
    return [s for s in expected if s not in covered]


# ── leaderboard ───────────────────────────────────────────────────────────────

def leaderboard_html():
    """Jake's Production Run ranks, refreshed by lib/leaderboard.py when older than 10 minutes."""
    script = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib", "leaderboard.py")
    if os.path.exists(script):
        try:
            subprocess.run([sys.executable, script, "snapshot"], capture_output=True, timeout=90,
                           env={**os.environ, "ADVANCE_ROADMAP_ROOT": ROOT})
        except subprocess.SubprocessError:
            pass
    try:
        lb = json.load(open(os.path.join(ROOT, "leaderboard.json")))
    except (OSError, ValueError):
        return ""

    def tile(label, r, note=""):
        if not r or not r.get("rank"):
            return (f'<div class=stat><div class="n dim">—</div><div class=l>{esc(label)}</div></div>')
        total = f'{r["total"]:,}' if isinstance(r.get("total"), int) else "?"
        return (f'<div class=stat><div class=n>#{r["rank"]:,}<span class=dim style="font-size:14px;font-weight:500">'
                f' / {total}</span></div><div class=l>{esc(label)}</div>'
                + (f'<div class=dim style="font-size:12px">{esc(note)}</div>' if note else "") + '</div>')

    best = lb.get("best_7d") or {}
    best_note = ""
    if best.get("day"):
        best_note = f'{best["day"]} · {best.get("days_recorded", 0)} of 7 days recorded'
    roll = lb.get("rolling_7d") or {}
    tiles = (tile("all-time rank", lb.get("all_time"))
             + tile("7-day best (daily)", best, best_note)
             + tile("today (UTC day)", lb.get("today"))
             + tile("rolling 7 days", roll))
    when = lb.get("fetched_at") or ""
    stale = "" if lb.get("ok") else f' · <span style="color:var(--fail)">last fetch failed: {esc(lb.get("error") or "?")}</span>'
    return (f'<div class=stats>{tiles}</div>'
            f'<div class=sub style="margin:4px 0 18px">Superset Production Run · @{esc(lb.get("handle") or "?")}'
            f' · fetched {esc(when[:16].replace("T", " "))} UTC{stale}</div>')


# ── repos ─────────────────────────────────────────────────────────────────────

def find_roadmap(repo):
    skip = {"node_modules", ".git", "dist", "build", ".next", "vendor"}
    for base, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.lower() == "roadmap.md":
                return os.path.relpath(os.path.join(base, f), repo)
        if base.count(os.sep) - repo.count(os.sep) > 3:
            dirs[:] = []
    return ""


def blockers(repo):
    """Unchecked items in the handoff file /pick-up reads."""
    for rel in (".claude/IN_PROGRESS.md", "IN_PROGRESS.md"):
        p = os.path.join(repo, rel)
        if os.path.exists(p):
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            return rel, len(re.findall(r"^\s*- \[ \]", text, re.M)), text
    return "", 0, ""


def survey_repos():
    out = []
    for d in sorted(glob.glob(os.path.join(CODE_DIR, "*/"))):
        repo = d.rstrip("/")
        dotgit = os.path.join(repo, ".git")
        if not os.path.isdir(dotgit):          # a .git FILE is a linked worktree
            continue
        origin = git(["remote", "get-url", "origin"], cwd=repo) or ""
        if "jnelken/" not in origin:
            continue
        name = os.path.basename(repo)
        roadmap = find_roadmap(repo)
        plans = len(glob.glob(os.path.join(repo, "docs/plans/*.md")))
        dirty = git(["status", "--porcelain"], cwd=repo)
        has_origin_main = git(["rev-parse", "--verify", "origin/main"], cwd=repo) is not None
        unpushed = ""
        if has_origin_main:
            c = git(["rev-list", "--count", "origin/main..HEAD"], cwd=repo)
            unpushed = c if c and c != "0" else ""
        rel, n_block, text = blockers(repo)

        blocks = []
        if os.path.exists(os.path.join(repo, ".noroadmap")):
            blocks.append(".noroadmap opt-out")
        if not roadmap and not plans:
            blocks.append("no roadmap or docs/plans")
        if dirty:
            blocks.append(f"dirty tree ({len(dirty.splitlines())} files)")
        if not has_origin_main:
            blocks.append("no origin/main — push flow has no target")
        if unpushed:
            blocks.append(f"{unpushed} unpushed commit(s) — ff-only merge would refuse")

        out.append({
            "name": name, "roadmap": roadmap, "plans": plans, "blocks": blocks,
            "eligible": not blocks, "blockers": n_block, "blockfile": rel, "blocktext": text,
        })
    out.sort(key=lambda r: (not r["eligible"], r["name"]))
    return out


def quota():
    """Prefer providers-usage.json; fall back to Claude statusline file."""
    now = datetime.now().timestamp()
    try:
        p = json.load(open(PROVIDERS_USAGE, encoding="utf-8"))
        providers = p.get("providers") or {}
        try:
            # Re-derive availability now, with the gate's own code, so the
            # badges and verdict agree with the bars (a window that reset since
            # the reading must not still show "hot").
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
            import usage
            state = usage.load(Path(ROOT))
            usage.recompute_flags(state)
            providers = state["providers"]
            orch = usage.pick_orchestrator(state)
            workers = usage.pick_worker_chain(state)
        except Exception:  # noqa: BLE001 — fall back to the flags as stored
            def ok(name, role):
                return (providers.get(name) or {}).get(f"available_for_{role}", role == "worker")
            orch = next((n for n in ("codex", "claude") if ok(n, "orchestrator")), None)
            workers = [n for n in ("cursor", "codex", "claude") if ok(n, "worker")]

        updated = p.get("updated_at")
        age = 0
        if updated:
            try:
                age = int((now - datetime.fromisoformat(updated).timestamp()) / 60)
            except ValueError:
                age = 0
        return {"age": age, "orch": orch, "workers": workers, "updated": updated,
                "providers": providers}
    except (OSError, ValueError):
        pass
    try:
        d = json.load(open(USAGE, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Reshape the statusline file into a one-provider reading so it renders the same way.
    claude = {k: {"used_pct": (d.get(k) or {}).get("used_percentage"),
                  "reset_at": (d.get(k) or {}).get("reset_at"),
                  "reset_epoch": (d.get(k) or {}).get("reset_epoch"),
                  "source": "statusline"} for k in ("five_hour", "seven_day")}
    ts = d.get("timestamp", now)
    return {"age": int((now - ts) / 60), "orch": "claude", "workers": ["claude"],
            "updated": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
            "providers": {"claude": claude}}



# ── run state (backoff + Slack summary) ───────────────────────────────────────
# This module already classifies every run, so it is the one place that knows
# the outcome history. run.sh reads what we write here rather than re-deriving
# it from prose — two classifiers would drift.
STATE = os.path.join(ROOT, "state.json")
BLOCKED_OUTCOMES = ("blocked-no-item", "blocked", "nothing-qualified")
# Consecutive blocked runs before each step down. At 4 runs/day, 4 is one full
# day of finding nothing, 8 is two.
# run.sh exports the base; the backoff multiplies it: (blocked streak, multiplier).
def base_cadence():
    # Must match the plist's default: serve.py regenerates this without the plist's env, and
    # run.sh obeys the cadence_hours this writes to state.json.
    v = os.environ.get("ADVANCE_ROADMAP_CADENCE_HOURS", "1")
    return int(v) if v.isdigit() and int(v) > 0 else 1


BACKOFF_STEPS = ((8, 4), (4, 2))
MAX_CADENCE = 24


def write_state(runs):
    """Consecutive-blocked count → cadence, plus a one-line summary for Slack."""
    streak = 0
    for r in runs:                      # newest first
        if r["outcome"] in ("skipped-quota", "skipped-lock", "skipped-backoff", "skipped-unchanged", "skipped-superset", "running"):
            continue                    # a skipped tick is not evidence either way
        if r["outcome"] in BLOCKED_OUTCOMES:
            streak += 1
            continue
        break                           # shipped / completed / error ends the streak

    hours = base_cadence()
    for need, mult in BACKOFF_STEPS:
        if streak >= need:
            hours = max(hours, min(hours * mult, MAX_CADENCE))
            break

    last = next((r for r in runs if r["outcome"] != "running"), None)
    summary = ""
    if last:
        bits = [last["outcome"]]
        if last["repo"]:
            bits.append(last["repo"][:80])
        if last["duration"]:
            bits.append(f"took {last['duration']}")
        summary = " · ".join(bits)

    state = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "blocked_streak": streak,
        "cadence_hours": hours,
        "last_stamp": last["stamp"] if last else "",
        "last_outcome": last["outcome"] if last else "",
        "last_repo": last["repo"] if last else "",
        "last_summary": summary,
    }
    try:
        atomic_write(STATE, json.dumps(state, indent=1))
    except OSError:
        pass
    return state


# ── render ────────────────────────────────────────────────────────────────────

# A pool with no reading must not render as a measured 0% — "unknown" and
# "measured zero" mean very different things when deciding whether to run.
# (provider key, pool key, label, gate cap or None when the pool isn't gated)
POOLS = {
    "claude": (("five_hour", "5-hour", None), ("seven_day", "7-day", None)),
    "codex": (("five_hour", "5-hour", MAX_FIVE_HOUR_PCT), ("weekly", "Weekly", MAX_SEVEN_DAY_PCT)),
}
# Within this many points of the cap the bar turns amber.
WARN_MARGIN = 15


def fmt_when(epoch, now):
    """Relative + short absolute, e.g. 'in 4h 50m · Sat 3:02 PM'."""
    delta = int(epoch - now)
    mins = abs(delta) // 60
    d, h, m = mins // 1440, (mins % 1440) // 60, mins % 60
    rel = f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"
    rel = "just now" if mins == 0 else f"in {rel}" if delta >= 0 else f"{rel} ago"
    ab = datetime.fromtimestamp(epoch).strftime("%a %b %-d %-I:%M %p")
    return rel, ab


def reset_html(epoch, text, now):
    if epoch:
        rel, ab = fmt_when(float(epoch), now)
        return f'<span class=dim>resets <time data-epoch="{int(float(epoch))}">{rel}</time> · {ab}</span>'
    if text and text not in ("unknown", "?"):
        return f'<span class=dim>resets {esc(text)}</span>'
    return '<span class=dim>reset time unknown</span>'


def pool_bar(label, pct, cap, reset_epoch, reset_text, source, now):
    """One labelled bar: fill = used %, tick = gate threshold."""
    tip = f"source: {source}"
    if pct is None:
        return (f'<div class=pbar title="{esc(tip)}"><span class=plabel>{esc(label)}</span>'
                f'<span class="qbar none"></span><span class="pval dim">no reading</span>'
                f'<span class=preset>{reset_html(reset_epoch, reset_text, now)}</span></div>')
    pct = float(pct)
    rolled = bool(reset_epoch) and float(reset_epoch) < now
    if rolled:
        pct = 0.0  # same rule as window_exhausted: a passed reset means a fresh window
    if cap is not None and pct >= cap:
        colour = "var(--fail)"
    elif cap is not None and pct >= cap - WARN_MARGIN:
        colour = "var(--unk)"
    else:
        colour = "var(--ok)"
    shown = f"{round(pct)}%" if pct >= 1 or pct == 0 else "&lt;1%"
    tick = f'<i class=cap style="left:{cap}%"></i>' if cap is not None else ""
    capnote = (f'<span class=dim> / {cap}%</span>' if cap is not None
               else '<span class=dim title="worker.sh doesn\'t draw from this pool, so it never gates a run"> · no gate</span>')
    reset = ('<span class=dim>window reset since reading</span>' if rolled
             else reset_html(reset_epoch, reset_text, now))
    return (f'<div class=pbar title="{esc(tip)}"><span class=plabel>{esc(label)}</span>'
            f'<span class=qbar><i class=fill style="width:{min(pct, 100)}%;background:{colour}"></i>{tick}</span>'
            f'<span class="pval mono">{shown}{capnote}</span>'
            f'<span class=preset>{reset}</span></div>')


def limit_hit_html(hit, now):
    """Readable summary of the last recorded limit hit, and whether it still gates."""
    if not isinstance(hit, dict):
        return ""
    try:
        observed = datetime.fromisoformat(hit["observed_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        observed = None
    reset = hit.get("reset_epoch")
    # Mirrors provider_available_from_limit: gates until its reset, or for an
    # hour when no reset was parsed.
    active = (bool(reset) and float(reset) > now) or \
        (not reset and observed is not None and now - observed < 3600)
    when = ""
    if observed is not None:
        rel, ab = fmt_when(observed, now)
        when = f'{ab} <span class=dim>({rel})</span>'
    state = ('<span class="badge fail">gating until reset</span>' if active
             else '<span class=dim>expired — not gating</span>')
    scope = hit.get("scope") or "unknown"
    text = hit.get("exact_cli_text") or ""
    detail = (f'<details><summary>CLI text</summary><pre>{esc(text)}</pre></details>'
              if text else "")
    return (f'<div class="hit{"" if active else " old"}"><span class=plabel>Last limit hit</span> '
            f'{when} · scope {esc(scope)} · {state}{detail}</div>')


def provider_rows(providers):
    """One card per provider: a bar per pool, availability, and any recorded limit hit."""
    now = datetime.now().timestamp()
    rows = []
    for name in ("claude", "codex", "cursor"):
        if name not in providers:
            continue
        pr = providers[name] or {}
        pools = pr.get("pools") or pr
        bars = []
        for key, label, cap in POOLS.get(name, ()):
            b = pools.get(key)
            if not isinstance(b, dict):
                continue
            pct = b.get("used_pct")
            if pct is None:
                pct = b.get("used_percentage")
            bars.append(pool_bar(label, pct, cap, b.get("reset_epoch"), b.get("reset_at"),
                                 b.get("source") or "unknown", now))
        # Cursor's monthly included pool: Auto and named-model (API) usage.
        inc = pr.get("included")
        if isinstance(inc, dict):
            gated = "auto_pct" if CURSOR_WORKER_MODEL == "auto" else "api_pct"
            for label, key in (("Auto (monthly)", "auto_pct"), ("API (monthly)", "api_pct")):
                bars.append(pool_bar(label, inc.get(key), MAX_CURSOR_PCT if key == gated else None,
                                     inc.get("reset_epoch"), inc.get("reset_at"),
                                     inc.get("source") or "unknown", now))
        if not bars:
            bars.append(f'<div class=dim>no pools tracked ({esc(pr.get("source") or "—")})</div>')
        avail = []
        for role, key in (("orchestrator", "available_for_orchestrator"), ("worker", "available_for_worker")):
            v = pr.get(key)
            if v is None:
                continue
            avail.append(f'<span class="badge {"ok" if v else "fail"}">{role}: {"ok" if v else "hot"}</span>')
        rows.append(f'<div class=prow><div class=phead><span class=pname>{esc(name)}</span>'
                    f'<span class=pavail>{" ".join(avail)}</span></div>'
                    f'{"".join(bars)}{limit_hit_html(pr.get("last_limit_hit"), now)}</div>')
    return "".join(rows)


# ── Linear: read the latest snapshot run.sh already took; never call Linear here ──

def latest_snapshot():
    """Newest ok snapshot: any run's (post-worker, pre-run, backoff peek) or serve.py's refresh."""
    paths = glob.glob(os.path.join(ROOT, "runs/*/*snapshot*.json")) + [os.path.join(ROOT, "live-snapshot.json")]
    paths = [x for x in paths if os.path.exists(x)]
    for path in sorted(paths, key=os.path.getmtime, reverse=True):
        try:
            snap = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if snap.get("ok"):
            return snap
    return None


def repo_of(issue):
    return next((l.split("/", 1)[1] for l in issue.get("labels", []) if l.startswith("repo/")), "")


def comment_field(body, name):
    m = re.search(r"\*\*" + name + r":\*\*\s*(.+)", body or "")
    return m.group(1).strip() if m else ""


def ticket_item(i, note, note_cls="dim"):
    repo = repo_of(i)
    return (f'<div class=wi><a class=mono href="{esc(i["url"])}" target=_blank rel=noopener>{esc(i["id"])}</a> '
            f'{esc(i["title"])}{f" <span class=dim>· {esc(repo)}</span>" if repo else ""}'
            f'<div class="wnote {note_cls}">{note}</div></div>')


def waiting_html(snap, repos):
    if snap:
        issues = snap.get("issues", [])
        needs = [i for i in issues if i.get("state") == "Needs Input"]
        paused = [i for i in issues if i.get("state") == "Paused"]
        n_items = []
        for i in needs:
            c = i.get("blocker_comment", "")
            q = comment_field(c, "Details") or comment_field(c, "Needed from you")
            n_items.append(ticket_item(i, esc(q) if q else "parked by you — no question from the bot",
                                       "" if q else "dim"))
        p_items = [ticket_item(i, esc(comment_field(i.get("blocker_comment", ""), "Blocker") or "waiting on a live condition"))
                   for i in paused]
        try:
            ts = datetime.fromisoformat(snap["fetched_at"]).timestamp()
            asof = f'as of <time data-epoch="{int(ts)}">{esc(snap["fetched_at"])}</time>'
        except (KeyError, TypeError, ValueError):
            asof = "as of the last run"
    else:
        n_items, p_items, asof = [], [], "no Linear snapshot yet"
    d_items = [f'<div class=wi><b>{esc(r["name"])}</b> <span class=dim>· {r["blockers"]} open</span>'
               f'<details><summary>{esc(r["blockfile"])}</summary><pre>{esc(r["blocktext"])}</pre></details></div>'
               for r in repos if r["blockers"]]

    def col(icon, title, count_cls, items, foot, empty):
        body = "".join(items) or f'<div class="wi dim">{empty}</div>'
        return (f'<div class=wcol><div class=whead><span>{icon} {title}</span>'
                f'<span class="badge {count_cls if items else "off"}">{len(items)}</span></div>{body}'
                f'<div class=wfoot>{foot}</div></div>')
    return (f'<div class=wgrid>'
            + col("?", "Needs input", "unk", n_items, "Answer with <code>/unblock-roadmap</code>",
                  "Nothing needs your answer.")
            + col("‖", "Paused", "off", p_items, "Re-checked every run — clears itself",
                  "Nothing paused.")
            + col("☐", "Open decisions", "unk", d_items, "From <code>IN_PROGRESS.md</code> · <code>/unblock-roadmap</code> asks these too",
                  "No unchecked items in any <code>IN_PROGRESS.md</code>.")
            + f'</div><div class="dim" style="font-size:12px;margin-top:6px">Linear {asof}</div>')


CATEGORY = {"shipped": "shipped", "completed": "shipped", "archive-only": "shipped",
            "blocked-no-item": "blocked", "blocked": "blocked",
            "error": "error", "failed": "error", "incomplete": "error", "shipped-deploy-failed": "error"}


def category(outcome):
    return CATEGORY.get(outcome) or ("skipped" if (outcome or "").startswith("skipped") else "other")


def run_title(r):
    f = r["fields"]
    if f.get("Item"):
        return f["Item"]
    if r["outcome"].startswith("blocked"):
        return "No item qualified"
    return r["detail"] or BADGE.get(r["outcome"], ("", r["outcome"]))[1].lstrip("✓◦⏸✗⚠● ")


BADGE = {
    "shipped": ("ok", "✓ shipped"), "completed": ("ok", "✓ completed"),
    "blocked-no-item": ("unk", "◦ blocked"), "blocked": ("unk", "◦ blocked"),
    "skipped-quota": ("off", "⏸ quota skip"), "skipped-lock": ("off", "⏸ lock skip"),
    "skipped-backoff": ("off", "⏸ backoff skip"),
    "skipped-superset": ("off", "⏸ Superset down"),
    "skipped-unchanged": ("off", "⏸ unchanged skip"),
    "error": ("fail", "✗ error"), "failed": ("fail", "✗ failed"), "incomplete": ("fail", "⚠ incomplete"),
    "archive-only": ("ok", "✓ archive-only"),
    "shipped-deploy-failed": ("fail", "✗ deploy failed"),
    "running": ("unk", "● running"),
}


def badge(outcome):
    cls, label = BADGE.get(outcome, ("unk", outcome or "?"))
    return f'<span class="badge {cls}">{esc(label)}</span>'


def fmt_tok(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def call_label(c):
    role = c.get("role") or "?"
    return f"review round {c['round']}" if role == "reviewer" and c.get("round") else role


def tokens_table(t):
    """Per-call token breakdown for one run: orchestrator, worker, each review round."""
    order = {"orchestrator": 0, "worker": 1, "reviewer": 2}
    calls = sorted(t.get("calls") or [], key=lambda c: (order.get(c.get("role"), 9), c.get("round") or 0))
    body = "".join(
        f'<tr><td>{esc(call_label(c))}</td><td class=mono>{esc(c.get("provider") or "")}/{esc(c.get("model") or "?")}</td>'
        f'<td class=num>{fmt_tok(c["input"] + c["cache_write"])}</td><td class=num>{fmt_tok(c["cache_read"])}</td>'
        f'<td class=num>{fmt_tok(c["output"])}</td><td class=num><b>{fmt_tok(c["total"])}</b></td></tr>'
        for c in calls)
    cost = f' · ${t["cost_usd"]:.2f} Claude list cost' if t.get("cost_usd") else ""
    return (f'<h4>Tokens</h4><div class=tablewrap><table class=tok><tr><th>Step</th><th>Model</th>'
            f'<th class=num>New in</th><th class=num>Cached in</th><th class=num>Out</th><th class=num>Total</th></tr>'
            f'{body}<tr><td colspan=5><b>Run total</b></td><td class=num><b>{fmt_tok(t["total"])}</b></td></tr>'
            f'</table></div><p class=dim>Total = new input + output. Cached in = cache reads, not counted in the total{esc(cost)}.</p>')


def token_summary(runs):
    """Per-model token totals over 24h / 7d / all tracked runs, from runs.jsonl rows."""
    now = datetime.now()
    windows = (("24h", 86400), ("7d", 7 * 86400), ("all", None))
    models, first, tracked = {}, None, 0
    for r in runs:
        t = r.get("tokens")
        if not t or not r["started"]:
            continue
        tracked += 1
        first = min(first, r["started"]) if first else r["started"]
        age = (now - r["started"]).total_seconds()
        for c in t.get("calls") or []:
            m = models.setdefault((c.get("provider"), c.get("model")),
                                  {k: {"total": 0, "input": 0, "cache": 0, "output": 0, "cost": 0.0} for k, _ in windows})
            for k, span in windows:
                if span is None or age <= span:
                    w = m[k]
                    w["total"] += c["total"]; w["input"] += c["input"] + c["cache_write"]
                    w["cache"] += c["cache_read"]; w["output"] += c["output"]
                    w["cost"] += c.get("cost_usd") or 0
    if not models:
        return ('<p class=dim>No token data yet — runs record tokens per model from the first run after this '
                'was added.</p>')
    def cell(w):
        tip = (f'new in {fmt_tok(w["input"])} · cached in {fmt_tok(w["cache"])} · out {fmt_tok(w["output"])}'
               + (f' · ${w["cost"]:.2f} list' if w["cost"] else ""))
        return f'<td class=num title="{esc(tip)}">{fmt_tok(w["total"]) if w["total"] else "—"}</td>'
    rows = "".join(
        f'<tr><td class=mono>{esc(p or "?")}/{esc(m or "?")}</td>' + "".join(cell(v[k]) for k, _ in windows) + '</tr>'
        for (p, m), v in sorted(models.items(), key=lambda kv: -kv[1]["all"]["total"]))
    grand = {k: sum(v[k]["total"] for v in models.values()) for k, _ in windows}
    rows += ('<tr><td><b>All models</b></td>'
             + "".join(f'<td class=num><b>{fmt_tok(grand[k])}</b></td>' for k, _ in windows) + '</tr>')
    return (f'<div class=tablewrap><table class=tok><tr><th>Model</th><th class=num>Last 24h</th>'
            f'<th class=num>Last 7d</th><th class=num>All tracked</th></tr>{rows}</table></div>'
            f'<p class=dim>Tokens = new input + output (cache reads excluded), across orchestrator, worker and reviewers. '
            f'{tracked} run(s) tracked since {first.strftime("%b %-d")}; earlier runs have no token data. '
            f'Hover a cell for the split. Cursor reports its Auto pool, not the model it routed to.</p>')


def build():
    ledger = merge_cache(read_ledger())
    runs = parse_runs(ledger, read_records())
    repos = survey_repos()
    missed = missed_slots(runs)
    q = quota()
    run_state = write_state(runs)

    n = len(runs)
    shipped = sum(1 for r in runs if r["outcome"] == "shipped")
    blocked = sum(1 for r in runs if r["outcome"].startswith("blocked"))
    skipped = sum(1 for r in runs if r["outcome"].startswith("skipped"))
    errored = sum(1 for r in runs if r["outcome"] in ("error", "failed", "incomplete", "shipped-deploy-failed"))

    snap = latest_snapshot()
    urls = {i["id"]: i["url"] for i in (snap or {}).get("issues", [])}
    cards, panels = [], []
    for idx, r in enumerate(runs):
        f = r["fields"]
        cat = category(r["outcome"])
        prov = ('<span class="prov" title="outcome from runs.jsonl, written by run.sh">record</span>'
                if r["provenance"] == "record" else
                '<span class="prov" title="outcome taken from the run ledger">ledger</span>'
                if r["provenance"] == "ledger" else
                '<span class="prov inf" title="ledger row trimmed or absent — outcome inferred from the log">inferred</span>'
                if r["provenance"] == "inferred" else "")
        started = r["started"]
        day = started.strftime("%a %d") if started else ""
        hm = started.strftime("%H:%M") if started else esc(r["stamp"])
        tk = r.get("tokens")
        meta = " · ".join(x for x in (r["repo"], r["duration"], r["model"] if r["model"] != "—" else "",
                                      f'{fmt_tok(tk["total"])} tok' if tk else "") if x)
        summary = f.get("Summary") or (r["detail"] if f.get("Item") else "")
        rid = f'run-{r["stamp"]}'
        cards.append(
            f'<a class="rc c-{cat}" href="#{rid}" data-cat="{cat}">'
            f'<span class="rwhen mono">{esc(day)}<br>{esc(hm)}</span>'
            f'<span class=rmain><span class=rtop>{badge(r["outcome"])} <span class="mono dim">{esc(meta)}</span></span>'
            f'<span class=rtitle>{esc(run_title(r))}</span>'
            + (f'<span class=rsum>{esc(summary)}</span>' if summary else "")
            + '</span><span class=rchev aria-hidden=true>›</span></a>')
        kv = "".join(f'<dt>{esc(k)}</dt><dd{" class=mono" if k in ("Merge", "Branch") else ""}>{esc(v)}</dd>'
                     for k, v in f.items() if v and k not in ("Summary", "Item", "Repo"))
        lid = r.get("linear_id") or (re.match(r"DEV-\d+", f.get("Item", "")) or [None])[0]
        links = (f'<a class=btn href="{esc(urls[lid])}" target=_blank rel=noopener>{esc(lid)} ↗</a>'
                 if lid and lid in urls else "")
        omitted_chars = r["log_chars"] - len(r["log_tail"])
        log_note = (f'<p class=dim>Log truncated — {omitted_chars:,} chars omitted; showing the last '
                    f'{len(r["log_tail"]):,} of {r["log_chars"]:,} chars.</p>'
                    if omitted_chars else "")
        log_path = f'<p class=dim>Full log on this Mac: <code>{esc(r["log_path"])}</code></p>'
        log = (f'<h4>Log</h4>{log_note}'
               + (f'<pre>{esc(r["log_tail"])}</pre>' if r["log_tail"] else '<p class=dim>No log body.</p>')
               + log_path)
        panels.append(
            f'<template id="{rid}"><div class=ptop>{badge(r["outcome"])} {prov}</div>'
            f'<h3 class=ptitle>{esc(run_title(r))}</h3>'
            f'<div class="mono dim">{esc(" · ".join(x for x in (r["repo"], r["when"], r["duration"], r["model"]) if x and x != "—"))}</div>'
            + (f'<dl class=pkv>{kv}</dl>' if kv else "")
            + (f'<h4>Summary</h4><p class=psum>{esc(summary)}</p>' if summary else "")
            + (f'<div class=pushnote>{esc(r["push"])}</div>' if r["push"] else "")
            + (f'<div class=plinks>{links}</div>' if links else "")
            + (tokens_table(r["tokens"]) if r.get("tokens") else "")
            + log
            + '</template>')
    run_cards = "\n".join(cards) or '<p class=dim style="padding:14px">No runs logged yet.</p>'
    run_panels = "\n".join(panels)
    counts = {c: sum(1 for r in runs if category(r["outcome"]) == c) for c in ("shipped", "blocked", "skipped", "error")}

    repo_cards = ""
    for r in repos:
        src = (f'<code>{esc(r["roadmap"])}</code>' if r["roadmap"]
               else f'{r["plans"]} plan doc(s) <span class=dim>docs/plans/</span>' if r["plans"]
               else '<span class=dim>none</span>')
        if r["eligible"]:
            state = '<span class="badge ok">eligible</span>'
        else:
            state = "".join(f'<span class="badge unk">{esc(b)}</span> ' for b in r["blocks"])
        bl = (f'<div class=kv><span class=k>decisions</span> {r["blockers"]} open '
              f'<span class=dim>({esc(r["blockfile"])})</span></div>') if r["blockers"] else ""
        for st, cls in (("Needs Input", "unk"), ("Paused", "off")):
            n_st = sum(1 for i in (snap or {}).get("issues", []) if i.get("state") == st and repo_of(i) == r["name"])
            if n_st:
                state_note = f'<span class="badge {cls}">{n_st} {st.lower()}</span> '
                bl += f'<div class=kv><span class=k>linear</span> {state_note}</div>'

        repo_cards += (f'<div class=card><h3>{esc(r["name"])}</h3>'
                       f'<div class=kv><span class=k>roadmap</span> {src}</div>{bl}'
                       f'<div style="margin-top:8px">{state}</div></div>')
    repo_cards = repo_cards or '<p class=dim>No jnelken-owned repos found.</p>'

    waiting = waiting_html(snap, repos)

    if q:
        orch, workers = q["orch"], q["workers"]
        verdict = ('<span class="badge fail">next run would be SKIPPED — no orchestrator available</span>'
                   if not orch else '<span class="badge ok">next run would proceed</span>')
        route = (f'<span class=dim>orchestrator</span> <b>{esc(orch or "none")}</b> '
                 f'<span class=dim>· workers</span> <b>{esc(" → ".join(workers) or "none")}</b>')
        checked = q.get("updated")
        try:
            ts = datetime.fromisoformat(checked).timestamp()
            rel, ab = fmt_when(ts, datetime.now().timestamp())
            checked_html = f'{ab} <span class=dim>(<time data-epoch="{int(ts)}">{rel}</time>)</span>'
        except (TypeError, ValueError):
            checked_html = '<span class=dim>unknown</span>'
        cad_html = (f'<div class=checkline style="border-bottom:0;padding-bottom:0;margin-bottom:0">'
                    '<strong>Schedule:</strong> one batch daily at 10:30'
                    ' <span class=dim>— one item per repo; no automatic refill</span></div>')
        quota_html = (f'<div class=card><div class=checkline>{verdict} &nbsp;{route}</div>'
                      f'<div class=provs>{provider_rows(q["providers"])}</div>'
                      f'<div class="checkline dim" style="font-size:12.5px">Last usage check {checked_html} · '
                      f'bar = used, tick = gate threshold · refreshed by run.sh each run and by ↻ refresh</div>'
                      f'{cad_html}</div>')
    else:
        quota_html = '<p class=dim>No usage reading available — the gate would let a run proceed.</p>'

    token_html = token_summary(runs)

    missed_html = ('<p class=dim>None — every scheduled slot since the first run produced a log.</p>'
                   if not missed else
                   '<div class=card><p class=dim style="margin-top:0">Slots with no log at all — the '
                   'machine was asleep. Not failures.</p>' +
                   "".join(f'<span class="badge off">{s.strftime("%a %b %d %H:%M")}</span> ' for s in missed[-40:]) +
                   '</div>')

    last = runs[0] if runs else None
    last_line = (f'{badge(last["outcome"])} &nbsp;<span class=mono>{esc(last["when"])}</span> &nbsp;{esc(last["repo"])}'
                 if last else '<span class=dim>no runs yet</span>')

    return TEMPLATE.format(
        gen=esc(datetime.now().strftime("%Y-%m-%d %H:%M:%S")), rank_html=leaderboard_html(),
        cadence=base_cadence(), windows=esc(BATCH_DESCRIPTION),
        total=n, shipped=shipped, blocked=blocked, skipped=skipped, errored=errored,
        last_line=last_line, run_cards=run_cards, run_panels=run_panels, repo_cards=repo_cards,
        waiting=waiting, quota_html=quota_html, token_html=token_html, missed_html=missed_html, **{f"n_{k}": v for k, v in counts.items()},
    )


TEMPLATE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Roadmap automation</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8;
           --ok:#0a7d34; --okbg:#e4f6ea; --fail:#b3261e; --failbg:#fbe6e5; --unk:#8a6d00; --unkbg:#fbf3d6;
           --accent:#4b56d2; --mono:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0; --dim:#9a9aa6;
           --line:#2c2c33; --ok:#4ade80; --okbg:#0f2a1a; --fail:#f87171; --failbg:#2a1414; --unk:#e3c04f; --unkbg:#2a2410; }} }}
  :root[data-theme=dark] {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0; --dim:#9a9aa6; --line:#2c2c33;
           --ok:#4ade80; --okbg:#0f2a1a; --fail:#f87171; --failbg:#2a1414; --unk:#e3c04f; --unkbg:#2a2410; }}
  :root[data-theme=light] {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8;
           --ok:#0a7d34; --okbg:#e4f6ea; --fail:#b3261e; --failbg:#fbe6e5; --unk:#8a6d00; --unkbg:#fbf3d6; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:24px; }}
  .wrap {{ max-width:1180px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); margin:32px 0 12px; }}
  h3 {{ font-size:14px; margin:0 0 8px; }}
  .sub {{ color:var(--dim); font-size:13px; margin-bottom:20px; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; min-width:96px; }}
  .stat .n {{ font-size:26px; font-weight:650; }}
  .stat .l {{ color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .last {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:12px 0; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--card); }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--dim); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  tr:last-child td {{ border-bottom:0; }}
  th.num, td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  tr.r-fail {{ background:var(--failbg); }}
  .badge {{ display:inline-block; padding:2px 9px; border-radius:20px; font-size:12px; font-weight:600; white-space:nowrap; }}
  .badge.ok {{ background:var(--okbg); color:var(--ok); }}
  .badge.fail {{ background:var(--failbg); color:var(--fail); }}
  .badge.unk {{ background:var(--unkbg); color:var(--unk); }}
  .badge.off {{ background:var(--line); color:var(--fg); margin:0 6px 6px 0; }}
  .prov {{ font-size:10.5px; color:var(--dim); border:1px solid var(--line); border-radius:6px; padding:1px 5px; margin-left:2px; }}
  .prov.inf {{ font-style:italic; opacity:.75; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kv {{ font-size:13px; padding:2px 0; }}
  .kv .k {{ display:inline-block; min-width:72px; color:var(--dim); font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; }}
  .pushnote {{ font-size:12px; color:var(--unk); margin-top:4px; }}
  .checkline {{ font-size:13px; padding-bottom:10px; margin-bottom:10px; border-bottom:1px solid var(--line); }}
  .provs {{ margin-bottom:10px; }}
  .prow {{ padding:10px 0; border-bottom:1px solid var(--line); font-size:13px; }}
  .prow:last-child {{ border-bottom:0; }}
  .phead {{ display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }}
  .pname {{ font-weight:650; font-size:14px; text-transform:capitalize; }}
  .pavail {{ display:flex; gap:4px; flex-wrap:wrap; }}
  .pbar {{ display:grid; grid-template-columns:128px minmax(120px,1fr) 120px minmax(0,230px); align-items:center; gap:12px; padding:3px 0; }}
  .plabel {{ font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); }}
  .pval {{ text-align:right; white-space:nowrap; }}
  .preset {{ font-size:12.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .qbar {{ position:relative; height:10px; background:var(--line); border-radius:6px; }}
  .qbar .fill {{ display:block; height:100%; border-radius:6px; min-width:2px; }}
  .qbar .cap {{ position:absolute; top:-3px; bottom:-3px; width:2px; margin-left:-1px; background:var(--fg); opacity:.55; border-radius:1px; }}
  .qbar.none {{ background:repeating-linear-gradient(45deg,var(--line) 0 6px,transparent 6px 12px); border:1px dashed var(--line); }}
  .hit {{ margin-top:6px; font-size:12.5px; }}
  .hit.old {{ color:var(--dim); }}
  .hit details {{ display:inline-block; margin-left:6px; }}
  .hit pre {{ font-size:12px; }}
  @media (max-width:640px) {{
    .pbar {{ grid-template-columns:1fr auto; row-gap:4px; }}
    .pbar .qbar {{ grid-column:1 / -1; grid-row:2; }}
    .pbar .preset {{ grid-column:1 / -1; white-space:normal; }}
  }}
  code, .mono {{ font-family:var(--mono); font-size:12.5px; }}
  .dim {{ color:var(--dim); }} .nowrap {{ white-space:nowrap; }}
  pre {{ background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:12px; overflow-x:auto; font-size:12.5px; white-space:pre-wrap; word-break:break-word; margin:8px 0 0; }}
  details summary {{ cursor:pointer; }}
  summary {{ color:var(--accent); }}
  .wgrid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }}
  .wcol {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; }}
  .whead {{ display:flex; justify-content:space-between; align-items:center; font-weight:650; font-size:14px; margin-bottom:4px; }}
  .wi {{ font-size:13px; padding:7px 0; border-top:1px solid var(--line); }}
  .wi a {{ color:var(--accent); text-decoration:none; }}
  .wnote {{ font-size:12.5px; margin-top:2px; }}
  .wfoot {{ margin-top:auto; padding-top:8px; font-size:12px; color:var(--dim); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }}
  .chip {{ font:inherit; font-size:13px; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:20px; padding:4px 12px; cursor:pointer; }}
  .chip b {{ font-weight:650; margin-left:4px; }}
  .chip.on {{ border-color:var(--accent); color:var(--accent); }}
  .runlist {{ border:1px solid var(--line); border-radius:10px; background:var(--card); overflow:hidden; }}
  .rc {{ display:grid; grid-template-columns:64px minmax(0,1fr) 16px; gap:14px; align-items:start; padding:11px 14px; border-bottom:1px solid var(--line); color:inherit; text-decoration:none; }}
  .rc:last-child {{ border-bottom:0; }}
  .rc[hidden] {{ display:none; }}
  .rc:hover, .rc.sel {{ background:var(--bg); }}
  .rc:focus-visible {{ outline:2px solid var(--accent); outline-offset:-2px; }}
  .rc.c-error {{ box-shadow:inset 3px 0 0 var(--fail); }}
  .rc.c-shipped {{ box-shadow:inset 3px 0 0 var(--ok); }}
  .rwhen {{ color:var(--dim); line-height:1.35; }}
  .rmain {{ display:flex; flex-direction:column; min-width:0; gap:2px; }}
  .rtop {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  .rtitle {{ font-size:14.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .rsum {{ font-size:13px; color:var(--dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .rchev {{ color:var(--dim); font-size:20px; line-height:1; align-self:center; }}
  .scrim {{ position:fixed; inset:0; background:rgba(0,0,0,.28); opacity:0; pointer-events:none; transition:opacity .18s; }}
  .drawer {{ position:fixed; top:0; right:0; bottom:0; width:min(560px,100vw); background:var(--card); border-left:1px solid var(--line);
            overflow-y:auto; padding:20px 22px 40px; transform:translateX(100%); transition:transform .2s ease; z-index:10; }}
  body.drawer-open .drawer {{ transform:none; }}
  body.drawer-open .scrim {{ opacity:1; pointer-events:auto; }}
  body.drawer-open {{ overflow:hidden; }}
  @media (prefers-reduced-motion: reduce) {{ .drawer, .scrim {{ transition:none; }} }}
  .pclose {{ float:right; color:var(--dim); text-decoration:none; font-size:18px; padding:2px 6px; border-radius:6px; }}
  .pclose:hover {{ background:var(--bg); color:var(--fg); }}
  .ptitle {{ font-size:17px; margin:12px 0 4px; line-height:1.35; }}
  .pkv {{ display:grid; grid-template-columns:80px minmax(0,1fr); gap:5px 12px; margin:16px 0; font-size:13px; }}
  .pkv dt {{ color:var(--dim); font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; padding-top:2px; }}
  .pkv dd {{ margin:0; overflow-wrap:anywhere; }}
  .drawer h4 {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); margin:18px 0 6px; }}
  .psum {{ font-size:14px; margin:0; }}
  .plinks {{ margin-top:14px; }}
  .btn {{ display:inline-block; font-size:13px; border:1px solid var(--line); border-radius:8px; padding:4px 10px; color:var(--accent); text-decoration:none; }}
  @media (max-width:640px) {{ body {{ padding:14px; }} .rc {{ grid-template-columns:52px minmax(0,1fr); }} .rchev {{ display:none; }} }}
  .toggle.refresh {{ margin-right:8px; }}
  .toggle.refresh[disabled] {{ opacity:.6; cursor:progress; }}
  .rstatus {{ float:right; clear:right; font-size:12.5px; color:var(--dim); margin-top:6px; max-width:420px; text-align:right; }}
  .rstatus.err {{ color:var(--fail); }}
  .toggle {{ float:right; cursor:pointer; background:var(--card); border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:6px 12px; font-size:13px; }}
</style>
</head>
<body>
<div class=wrap>
  <button class=toggle onclick="var r=document.documentElement;r.dataset.theme=(r.dataset.theme==='dark'?'light':'dark')">◐ theme</button>
  <button class="toggle refresh" id=refresh title="Re-read provider usage, take a fresh Linear snapshot, rebuild this page">↻ refresh</button>
  <span class=rstatus id=rstatus role=status></span>
  <h1>Roadmap automation</h1>
  <div class=sub>Generated {gen} · daily batch {windows} · ↻ refresh re-reads usage and Linear (via serve.py)</div>
  {rank_html}

  <div class=stats>
    <div class=stat><div class=n>{total}</div><div class=l>runs logged</div></div>
    <div class=stat><div class=n style="color:var(--ok)">{shipped}</div><div class=l>shipped</div></div>
    <div class=stat><div class=n style="color:var(--unk)">{blocked}</div><div class=l>blocked</div></div>
    <div class=stat><div class=n class=dim>{skipped}</div><div class=l>quota / lock / unchanged skips</div></div>
    <div class=stat><div class=n style="color:var(--fail)">{errored}</div><div class=l>errors</div></div>
  </div>
  <div class=last><strong>Latest:</strong> {last_line}</div>

  <h2>Quota gate</h2>
  {quota_html}

  <h2>Token usage</h2>
  {token_html}

  <h2>Waiting on you</h2>
  {waiting}

  <h2 id=runs>Runs</h2>
  <div class=chips role=group aria-label="Filter runs">
    <button class="chip on" data-f=all>All <b>{total}</b></button>
    <button class=chip data-f=shipped>Shipped <b>{n_shipped}</b></button>
    <button class=chip data-f=blocked>Blocked <b>{n_blocked}</b></button>
    <button class=chip data-f=skipped>Skipped <b>{n_skipped}</b></button>
    <button class=chip data-f=error>Errors <b>{n_error}</b></button>
  </div>
  <div class=runlist id=runlist>{run_cards}</div>
  {run_panels}

  <h2>Candidate repos</h2>
  <div class=cards>{repo_cards}</div>

  <h2>Slots that never fired</h2>
  {missed_html}
</div>
<div class=scrim id=scrim></div>
<aside class=drawer id=drawer aria-hidden=true aria-label="Run details">
  <a class=pclose href="#runs" aria-label="Close">✕</a>
  <div id=pbody></div>
</aside>
<script>
(function () {{
  var drawer = document.getElementById("drawer"), scrim = document.getElementById("scrim"),
      pbody = document.getElementById("pbody"), last = null;
  function sync() {{
    var id = location.hash.slice(1), tpl = id.indexOf("run-") === 0 && document.getElementById(id);
    document.querySelectorAll(".rc.sel").forEach(function (c) {{ c.classList.remove("sel"); }});
    if (tpl) {{
      pbody.replaceChildren(tpl.content.cloneNode(true));
      drawer.scrollTop = 0;
      var card = document.querySelector('.rc[href="#' + id + '"]');
      if (card) {{ card.classList.add("sel"); last = card; }}
      document.body.classList.add("drawer-open");
      drawer.setAttribute("aria-hidden", "false");
      drawer.querySelector(".pclose").focus({{preventScroll: true}});
    }} else if (document.body.classList.contains("drawer-open")) {{
      document.body.classList.remove("drawer-open");
      drawer.setAttribute("aria-hidden", "true");
      if (last) last.focus({{preventScroll: true}});
    }}
  }}
  window.addEventListener("hashchange", sync);
  scrim.addEventListener("click", function () {{ location.hash = "runs"; }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape" && document.body.classList.contains("drawer-open")) location.hash = "runs";
  }});
  document.querySelectorAll(".chip").forEach(function (b) {{
    b.addEventListener("click", function () {{
      document.querySelectorAll(".chip").forEach(function (x) {{ x.classList.toggle("on", x === b); }});
      var f = b.dataset.f;
      document.querySelectorAll(".rc").forEach(function (c) {{ c.hidden = f !== "all" && c.dataset.cat !== f; }});
    }});
  }});
  sync();
}})();
(function () {{
  var btn = document.getElementById("refresh"), st = document.getElementById("rstatus");
  var served = (location.protocol === "http:" && ["127.0.0.1", "localhost"].indexOf(location.hostname) >= 0)
            || location.hostname.endsWith(".ts.net");
  btn.addEventListener("click", function () {{
    if (!served) {{
      st.className = "rstatus err";
      st.innerHTML = 'Refresh needs the server — open <a href="http://127.0.0.1:8421/">127.0.0.1:8421</a> or the tailnet URL';
      return;
    }}
    btn.disabled = true; btn.textContent = "↻ refreshing…";
    st.className = "rstatus"; st.textContent = "Reading usage and Linear — about 10–30s";
    var base = location.pathname;
    if (base.charAt(base.length - 1) === "/") {{ base = base.slice(0, -1); }}
    fetch(base + "/refresh", {{method: "POST"}}).then(function (r) {{
      return r.json().then(function (j) {{ return [r.status, j]; }});
    }}).then(function (res) {{
      var j = res[1];
      if (res[0] === 200) {{ sessionStorage.setItem("ar-refreshed", Date.now()); location.reload(); return; }}
      var bad = (j.steps || []).filter(function (x) {{ return !x.ok; }})
        .map(function (x) {{ return x.step + ": " + (x.error || "failed"); }}).join(" · ");
      st.className = "rstatus err"; st.textContent = j.error || bad || "Refresh failed";
      btn.disabled = false; btn.textContent = "↻ refresh";
      if (j.steps) setTimeout(function () {{ location.reload(); }}, 4000);
    }}).catch(function (e) {{
      st.className = "rstatus err"; st.textContent = "Server unreachable — is serve.py running?";
      btn.disabled = false; btn.textContent = "↻ refresh";
    }});
  }});
  try {{
    var t = +sessionStorage.getItem("ar-refreshed");
    if (t && Date.now() - t < 15000) {{ st.textContent = "Refreshed just now"; sessionStorage.removeItem("ar-refreshed"); }}
  }} catch (e) {{}}
}})();
(function () {{
  var now = Date.now() / 1000;
  document.querySelectorAll("time[data-epoch]").forEach(function (t) {{
    var d = Math.round(+t.dataset.epoch - now), m = Math.floor(Math.abs(d) / 60);
    var dd = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60;
    var r = dd ? dd + "d " + h + "h" : h ? h + "h " + mm + "m" : mm + "m";
    t.textContent = !m ? "just now" : d >= 0 ? "in " + r : r + " ago";
  }});
}})();
</script>
</body>
</html>"""


def atomic_write(path, text):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


if __name__ == "__main__":
    import fcntl
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # Parallel lanes each regenerate this page when they finish; one writer at a time.
    with open(os.path.join(os.path.dirname(OUT), "dashboard.lock"), "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        atomic_write(OUT, build())
    print(f"wrote {OUT}")
