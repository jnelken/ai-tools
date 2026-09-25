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

HOME = os.path.expanduser("~")
ROOT = os.environ.get("ADVANCE_ROADMAP_ROOT", os.path.join(HOME, ".claude/automations/advance-roadmap"))
LOGDIR = os.path.join(ROOT, "logs")
CACHE = os.path.join(ROOT, "ledger-cache.json")
OUT = os.path.join(ROOT, "dashboard.html")
CODE_DIR = os.path.join(HOME, "Dropbox/code")
MEMORY = os.path.join(HOME, ".claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md")
USAGE = os.path.join(HOME, ".claude/state/claude-usage.json")
PROVIDERS_USAGE = os.path.join(ROOT, "providers-usage.json")
RUNS_JSONL = os.path.join(ROOT, "runs.jsonl")

# Thresholds mirror lib/usage.py. Kept in sync by hand; shown so the page can say
# whether the next run would be gated.
MAX_SEVEN_DAY_PCT = 80
MAX_FIVE_HOUR_PCT = 70
MAX_CURSOR_PCT = 95
# worker.sh draws Cursor usage from the Auto pool unless a named model is pinned;
# only that pool is gated (lib/usage.py cursor_quota_ok).
CURSOR_WORKER_MODEL = os.environ.get("ADVANCE_ROADMAP_CURSOR_WORKER_MODEL", "auto")
SLOTS = [(4, 45), (10, 45), (16, 45), (22, 45)]

QUOTA_RE = re.compile(r"skipping — (\S+) usage (\d+)% >= (\d+)%")
NO_ORCH_RE = re.compile(r"skipping — no orchestrator available")
BACKOFF_RE = re.compile(r"skipping — backed off to every (\d+)h after (\S+) blocked runs")
LOCK_RE = re.compile(r"(another run holds|could not take lock)")
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
    """Rows currently in run memory's ## Run ledger, keyed by stamp."""
    out = {}
    try:
        text = open(MEMORY, encoding="utf-8", errors="replace").read()
    except OSError:
        return out
    if "## Run ledger" not in text:
        return out
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
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1, sort_keys=True)
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
            fields = {k: v for k, v in (
                ("Repo", rec.get("repo")), ("Item", rec.get("item")),
                ("Merge", rec.get("merge_commit")), ("Worker", rec.get("worker")),
                ("Summary", rec.get("summary")), ("Detail", detail)) if v}
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
            "push": pm.group(0).strip() if pm else "",
            "duration": duration,
            "body": body,
        })
    return runs


def missed_slots(runs):
    """Scheduled fires with no log at all — the machine was asleep.

    A catch-up fire lands late (e.g. 12:59 for the 10:45 slot), so each run is
    assigned to the nearest PRECEDING slot within 4 hours.
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
        for h, m in SLOTS:
            s = day.replace(hour=h, minute=m)
            if first - timedelta(minutes=30) <= s <= now - timedelta(minutes=20):
                expected.append(s)
        day += timedelta(days=1)
    for st in stamps:
        best = None
        for s in expected:
            if s <= st + timedelta(minutes=5) and (st - s) <= timedelta(hours=4):
                if best is None or s > best:
                    best = s
        if best:
            covered.add(best)
    return [s for s in expected if s not in covered]


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
BACKOFF_STEPS = ((8, 24), (4, 12))


def write_state(runs):
    """Consecutive-blocked count → cadence, plus a one-line summary for Slack."""
    streak = 0
    for r in runs:                      # newest first
        if r["outcome"] in ("skipped-quota", "skipped-lock", "skipped-backoff", "running"):
            continue                    # a skipped tick is not evidence either way
        if r["outcome"] in BLOCKED_OUTCOMES:
            streak += 1
            continue
        break                           # shipped / completed / error ends the streak

    hours = 6
    for need, h in BACKOFF_STEPS:
        if streak >= need:
            hours = h
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
        json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1)
    except OSError:
        pass
    return state


# ── render ────────────────────────────────────────────────────────────────────

# A pool with no reading must not render as a measured 0% — "unknown" and
# "measured zero" mean very different things when deciding whether to run.
# (provider key, pool key, label, gate cap or None when the pool isn't gated)
POOLS = {
    "claude": (("five_hour", "5-hour", MAX_FIVE_HOUR_PCT), ("seven_day", "7-day", MAX_SEVEN_DAY_PCT)),
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


BADGE = {
    "shipped": ("ok", "✓ shipped"), "completed": ("ok", "✓ completed"),
    "blocked-no-item": ("unk", "◦ blocked"), "blocked": ("unk", "◦ blocked"),
    "skipped-quota": ("off", "⏸ quota skip"), "skipped-lock": ("off", "⏸ lock skip"),
    "skipped-backoff": ("off", "⏸ backoff skip"),
    "error": ("fail", "✗ error"), "failed": ("fail", "✗ failed"), "incomplete": ("fail", "⚠ incomplete"),
    "archive-only": ("ok", "✓ archive-only"),
    "running": ("unk", "● running"),
}


def badge(outcome):
    cls, label = BADGE.get(outcome, ("unk", outcome or "?"))
    return f'<span class="badge {cls}">{esc(label)}</span>'


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
    errored = sum(1 for r in runs if r["outcome"] in ("error", "failed", "incomplete"))

    rows = []
    for r in runs:
        f = r["fields"]
        detail = ""
        if f:
            detail = "".join(
                f'<div class=kv><span class=k>{esc(k)}</span> {esc(v)}</div>'
                for k, v in f.items() if v)
        elif r["detail"]:
            detail = f'<span class=dim>{esc(r["detail"])}</span>'
        push = f'<div class="pushnote">{esc(r["push"])}</div>' if r["push"] else ""
        prov = ('<span class="prov" title="outcome from runs.jsonl, written by run.sh">record</span>'
                if r["provenance"] == "record" else
                '<span class="prov" title="outcome taken from the run ledger">ledger</span>'
                if r["provenance"] == "ledger" else
                '<span class="prov inf" title="ledger row trimmed or absent — outcome inferred from the log">inferred</span>'
                if r["provenance"] == "inferred" else "")
        body = (f'<details><summary>log</summary><pre>{esc(r["body"])}</pre></details>'
                if r["body"] else '<span class=dim>—</span>')
        rows.append(f"""<tr class="{'r-fail' if r['outcome'] in ('error','failed','incomplete') else ''}">
          <td class="mono nowrap">{esc(r['when'])}</td>
          <td class=nowrap>{badge(r['outcome'])} {prov}</td>
          <td>{esc(r['repo']) or '<span class=dim>—</span>'}</td>
          <td class="mono dim nowrap">{esc(r['duration'])}</td>
          <td class="mono dim nowrap">{esc(r['model'])}</td>
          <td>{detail}{push}{body}</td>
        </tr>""")
    run_rows = "\n".join(rows) or '<tr><td colspan=6 class=dim>No runs logged yet.</td></tr>'

    repo_cards = ""
    for r in repos:
        src = (f'<code>{esc(r["roadmap"])}</code>' if r["roadmap"]
               else f'{r["plans"]} plan doc(s) <span class=dim>docs/plans/</span>' if r["plans"]
               else '<span class=dim>none</span>')
        if r["eligible"]:
            state = '<span class="badge ok">eligible</span>'
        else:
            state = "".join(f'<span class="badge unk">{esc(b)}</span> ' for b in r["blocks"])
        bl = (f'<div class=kv><span class=k>blockers</span> {r["blockers"]} awaiting <code>/pick-up</code> '
              f'<span class=dim>({esc(r["blockfile"])})</span></div>') if r["blockers"] else ""
        repo_cards += (f'<div class=card><h3>{esc(r["name"])}</h3>'
                       f'<div class=kv><span class=k>roadmap</span> {src}</div>{bl}'
                       f'<div style="margin-top:8px">{state}</div></div>')
    repo_cards = repo_cards or '<p class=dim>No jnelken-owned repos found.</p>'

    block_cards = "".join(
        f'<div class=card><h3>{esc(r["name"])} <span class=dim>({r["blockers"]} open)</span></h3>'
        f'<details><summary>{esc(r["blockfile"])}</summary><pre>{esc(r["blocktext"])}</pre></details></div>'
        for r in repos if r["blockers"]) or \
        '<p class=dim>Nothing waiting on you — no unchecked items in any <code>.claude/IN_PROGRESS.md</code>.</p>'

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
        cad = run_state["cadence_hours"]
        cad_html = (f'<div class=checkline style="border-bottom:0;padding-bottom:0;margin-bottom:0">'
                    f'<strong>Cadence:</strong> every {cad}h'
                    + (f' <span class=dim>— backed off from 6h after {run_state["blocked_streak"]} '
                       f'consecutive blocked runs; resets on the next ship</span>' if cad > 6
                       else ' <span class=dim>— normal</span>') + '</div>')
        quota_html = (f'<div class=card><div class=checkline>{verdict} &nbsp;{route}</div>'
                      f'<div class=provs>{provider_rows(q["providers"])}</div>'
                      f'<div class="checkline dim" style="font-size:12.5px">Last usage check {checked_html} · '
                      f'bar = used, tick = gate threshold · refreshed by run.sh each run</div>'
                      f'{cad_html}</div>')
    else:
        quota_html = '<p class=dim>No usage reading available — the gate would let a run proceed.</p>'

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
        gen=esc(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        total=n, shipped=shipped, blocked=blocked, skipped=skipped, errored=errored,
        last_line=last_line, run_rows=run_rows, repo_cards=repo_cards,
        block_cards=block_cards, quota_html=quota_html, missed_html=missed_html,
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
  .toggle {{ float:right; cursor:pointer; background:var(--card); border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:6px 12px; font-size:13px; }}
</style>
</head>
<body>
<div class=wrap>
  <button class=toggle onclick="var r=document.documentElement;r.dataset.theme=(r.dataset.theme==='dark'?'light':'dark')">◐ theme</button>
  <h1>Roadmap automation</h1>
  <div class=sub>Generated {gen} · runs every 6h at 4:45 / 10:45am, 4:45 / 10:45pm · reload after a run to refresh</div>

  <div class=stats>
    <div class=stat><div class=n>{total}</div><div class=l>runs logged</div></div>
    <div class=stat><div class=n style="color:var(--ok)">{shipped}</div><div class=l>shipped</div></div>
    <div class=stat><div class=n style="color:var(--unk)">{blocked}</div><div class=l>blocked</div></div>
    <div class=stat><div class=n class=dim>{skipped}</div><div class=l>quota / lock skips</div></div>
    <div class=stat><div class=n style="color:var(--fail)">{errored}</div><div class=l>errors</div></div>
  </div>
  <div class=last><strong>Latest:</strong> {last_line}</div>

  <h2>Quota gate</h2>
  {quota_html}

  <h2>Waiting on you <span class="dim" style="text-transform:none;letter-spacing:0">(run <code>/pick-up</code> in the repo)</span></h2>
  <div class=cards>{block_cards}</div>

  <h2>Runs</h2>
  <div class=tablewrap>
    <table>
      <thead><tr><th>When</th><th>Outcome</th><th>Repo</th><th>Took</th><th>Model</th><th>Detail</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>

  <h2>Candidate repos</h2>
  <div class=cards>{repo_cards}</div>

  <h2>Slots that never fired</h2>
  {missed_html}
</div>
<script>
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


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(build())
    print(f"wrote {OUT}")
