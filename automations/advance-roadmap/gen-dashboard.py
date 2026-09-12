#!/usr/bin/env python3
"""Generate a self-contained dashboard.html for the advance-roadmap automation.

Sibling of wrapup-repos/gen-dashboard.py — same look, different data model.

Where the data comes from, and why it's split:
  * LOGS are the spine. Every run leaves one, and the lines run.sh itself emits
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
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
ROOT = os.environ.get("ADVANCE_ROADMAP_ROOT", os.path.join(HOME, ".claude/automations/advance-roadmap"))
LOGDIR = os.path.join(ROOT, "logs")
CACHE = os.path.join(ROOT, "ledger-cache.json")
OUT = os.path.join(ROOT, "dashboard.html")
CODE_DIR = os.path.join(HOME, "Dropbox/code")
MEMORY = os.path.join(HOME, ".claude/projects/-Users-jake-Dropbox-code/memory/project_advance-roadmap-runs.md")
USAGE = os.path.join(HOME, ".claude/state/claude-usage.json")

# Thresholds mirror run.sh. Kept in sync by hand; shown so the page can say
# whether the next run would be gated.
MAX_SEVEN_DAY_PCT = 80
MAX_FIVE_HOUR_PCT = 70
SLOTS = [(4, 45), (10, 45), (16, 45), (22, 45)]

QUOTA_RE = re.compile(r"skipping — (\S+) usage (\d+)% >= (\d+)%")
LOCK_RE = re.compile(r"(another run holds|could not take lock)")
EXIT_RE = re.compile(r"=== claude exit=(\d+)\s+finished (.*?) ===")
MODEL_RE = re.compile(r"model=(\S+)")
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


def classify(text, exit_code, has_header):
    """First match wins. Only deterministic signals decide; prose never does."""
    qm = QUOTA_RE.search(text)
    if qm:
        return "skipped-quota", f"{qm.group(1)} usage {qm.group(2)}% ≥ {qm.group(3)}%"
    if LOCK_RE.search(text):
        return "skipped-lock", "another run held the lock"
    if exit_code is None:
        return ("incomplete", "no exit line — killed mid-run") if has_header else ("incomplete", "no exit line")
    if exit_code != 0:
        return "error", f"exit {exit_code}"
    return None, ""


def parse_runs(ledger):
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

        em = EXIT_RE.search(text)
        mm = MODEL_RE.search(text)
        exit_code = int(em.group(1)) if em else None
        has_header = "advance-roadmap run" in text

        # Body = everything the model printed, minus run.sh's own framing.
        body = text
        if mm:
            nl = text.find("\n", mm.end())
            if nl != -1:
                body = text[nl + 1:]
        if em:
            body = body[: body.index("=== claude exit=")]
        body = body.strip()

        outcome, detail = classify(text, exit_code, has_header)
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

        runs.append({
            "stamp": stamp,
            "when": started.strftime("%a %b %d  %H:%M") if started else stamp,
            "started": started,
            "model": (mm.group(1) if mm else "—").replace("claude-", ""),
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
    try:
        d = json.load(open(USAGE, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    now = datetime.now().timestamp()
    def win(k, cap):
        w = d.get(k) or {}
        pct = w.get("used_percentage", 0)
        if w.get("reset_epoch", 0) and w["reset_epoch"] < now:
            pct = 0                      # window rolled over; cached value is stale
        return {"pct": pct, "cap": cap, "reset": w.get("reset_at", "?"), "gated": pct >= cap}
    return {"five": win("five_hour", MAX_FIVE_HOUR_PCT), "seven": win("seven_day", MAX_SEVEN_DAY_PCT),
            "age": int((now - d.get("timestamp", now)) / 60)}


# ── render ────────────────────────────────────────────────────────────────────

BADGE = {
    "shipped": ("ok", "✓ shipped"), "completed": ("ok", "✓ completed"),
    "blocked-no-item": ("unk", "◦ blocked"), "blocked": ("unk", "◦ blocked"),
    "skipped-quota": ("off", "⏸ quota skip"), "skipped-lock": ("off", "⏸ lock skip"),
    "error": ("fail", "✗ error"), "incomplete": ("fail", "⚠ incomplete"),
}


def badge(outcome):
    cls, label = BADGE.get(outcome, ("unk", outcome or "?"))
    return f'<span class="badge {cls}">{esc(label)}</span>'


def build():
    ledger = merge_cache(read_ledger())
    runs = parse_runs(ledger)
    repos = survey_repos()
    missed = missed_slots(runs)
    q = quota()

    n = len(runs)
    shipped = sum(1 for r in runs if r["outcome"] == "shipped")
    blocked = sum(1 for r in runs if r["outcome"].startswith("blocked"))
    skipped = sum(1 for r in runs if r["outcome"].startswith("skipped"))
    errored = sum(1 for r in runs if r["outcome"] in ("error", "incomplete"))

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
        prov = ('<span class="prov" title="outcome taken from the run ledger">ledger</span>'
                if r["provenance"] == "ledger" else
                '<span class="prov inf" title="ledger row trimmed or absent — outcome inferred from the log">inferred</span>'
                if r["provenance"] == "inferred" else "")
        body = (f'<details><summary>log</summary><pre>{esc(r["body"])}</pre></details>'
                if r["body"] else '<span class=dim>—</span>')
        rows.append(f"""<tr class="{'r-fail' if r['outcome'] in ('error','incomplete') else ''}">
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
        def bar(w, label):
            colour = "var(--fail)" if w["gated"] else "var(--ok)"
            return (f'<div class=qrow><span class=k>{label}</span>'
                    f'<span class=qbar><i style="width:{min(w["pct"],100)}%;background:{colour}"></i></span>'
                    f'<span class=mono>{w["pct"]}% / {w["cap"]}%</span> '
                    f'<span class=dim>resets {esc(w["reset"])}</span></div>')
        gated = q["five"]["gated"] or q["seven"]["gated"]
        verdict = ('<span class="badge fail">next run would be SKIPPED</span>' if gated
                   else '<span class="badge ok">next run would proceed</span>')
        quota_html = (f'<div class=card>{bar(q["five"], "5-hour")}{bar(q["seven"], "7-day")}'
                      f'<div style="margin-top:10px">{verdict} <span class=dim>· reading is '
                      f'{q["age"]}m old; only interactive sessions refresh it</span></div></div>')
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
  .qrow {{ display:flex; align-items:center; gap:10px; font-size:13px; padding:4px 0; flex-wrap:wrap; }}
  .qbar {{ flex:1; min-width:120px; height:8px; background:var(--line); border-radius:6px; overflow:hidden; }}
  .qbar i {{ display:block; height:100%; }}
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
</body>
</html>"""


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(build())
    print(f"wrote {OUT}")
