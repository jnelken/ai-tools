#!/usr/bin/env python3
"""Generate a self-contained dashboard.html for the wrapup-repos automation.

Scans run logs, (auto) commits across ~/Dropbox/code, and NEXT-STEPS.md files,
and writes a single static HTML file (data baked in — safe to open via file://).
Run manually or let run.sh call it after each run.
"""
import fnmatch
import glob
import html
import os
import re
import subprocess
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, ".claude/automations/worktree-wrapup")
LOGDIR = os.path.join(ROOT, "logs")
CODE_DIR = os.path.join(HOME, "Dropbox/code")
OUT = os.path.join(ROOT, "dashboard.html")

HEADER_RE = re.compile(r"run (\d{8}-\d{6}) \((.*?)\)")
MODEL_RE = re.compile(r"model=(\S+)")
EXIT_RE = re.compile(r"claude exit=(\d+)\s+finished (.*?) ===")
REPO_RE = re.compile(r"\*\*(.+?)\*\*|`([^`]+?)`|^Repo:\s*`?([^`\n—-]+)")


def esc(s):
    return html.escape(s or "")


def repo_names():
    """Actual repo dir names under CODE_DIR, longest first (so multi-word names win)."""
    names = [os.path.basename(d.rstrip("/")) for d in glob.glob(os.path.join(CODE_DIR, "*/"))]
    return sorted(set(names), key=len, reverse=True)


def disabled_repos():
    """Repos currently opted out — via .wrapup-ignore patterns or a .nowrapup marker.

    Returns a sorted list of (repo_name, reason) mirroring the skill's exclusion logic.
    """
    patterns = []
    ig = os.path.join(CODE_DIR, ".wrapup-ignore")
    if os.path.exists(ig):
        for line in open(ig, encoding="utf-8", errors="replace"):
            s = line.strip()
            if s and not s.startswith("#"):
                patterns.append(s)
    out = {}
    for d in sorted(glob.glob(os.path.join(CODE_DIR, "*/"))):
        name = os.path.basename(d.rstrip("/"))
        if os.path.exists(os.path.join(d, ".nowrapup")):
            out[name] = ".nowrapup marker"
        else:
            for p in patterns:
                if fnmatch.fnmatch(name, p):
                    out[name] = f".wrapup-ignore ({p})" if p != name else ".wrapup-ignore"
                    break
    return sorted(out.items())


def find_repo(summary, names):
    """The repo name mentioned earliest in the summary (word-boundary match)."""
    best, best_pos = "", len(summary) + 1
    for n in names:
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", summary)
        if m and m.start() < best_pos:
            best, best_pos = n, m.start()
    return best


def fmt_when(rid, fallback):
    try:
        return datetime.strptime(rid, "%Y%m%d-%H%M%S").strftime("%a %b %d %H:%M")
    except ValueError:
        return fallback


def parse_runs(names):
    runs = []
    for path in sorted(glob.glob(os.path.join(LOGDIR, "run-*.log")), reverse=True):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rid = os.path.basename(path)[4:-4]
        hm = HEADER_RE.search(text)
        mm = MODEL_RE.search(text)
        em = EXIT_RE.search(text)
        # summary = everything between the model line and the exit footer
        body = text
        if mm:
            body = text[text.index("\n", mm.end()) + 1:]
        if em:
            body = body[: body.index("=== claude exit=")]
        summary = body.strip()
        runs.append({
            "id": rid,
            "when": fmt_when(rid, hm.group(2) if hm else rid),
            "model": mm.group(1) if mm else "?",
            "exit": int(em.group(1)) if em else None,
            "finished": em.group(2) if em else None,
            "repo": find_repo(summary, names),
            "summary": summary,
        })
    return runs


def parse_idle():
    p = os.path.join(CODE_DIR, ".wrapup-idle.log")
    if os.path.exists(p):
        return [l for l in open(p, encoding="utf-8", errors="replace").read().splitlines() if l.strip()]
    return []


def auto_commits():
    out = []
    for d in sorted(glob.glob(os.path.join(CODE_DIR, "*/"))):
        if not os.path.exists(os.path.join(d, ".git")):
            continue
        try:
            r = subprocess.run(
                ["git", "-C", d, "log", "--grep=(auto)", "--pretty=format:%h\x1f%cI\x1f%s", "-n", "25"],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        rows = []
        for line in r.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                rows.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
        if rows:
            out.append({"repo": os.path.basename(d.rstrip("/")), "commits": rows})
    return out


def next_steps():
    out = []
    for p in glob.glob(os.path.join(CODE_DIR, "*/NEXT-STEPS.md")):
        try:
            content = open(p, encoding="utf-8", errors="replace").read()
            mtime = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            continue
        out.append({"repo": os.path.basename(os.path.dirname(p)), "mtime": mtime, "content": content})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def badge(exit_code):
    if exit_code == 0:
        return '<span class="badge ok">✓ ok</span>'
    if exit_code is None:
        return '<span class="badge unk">? running/incomplete</span>'
    return f'<span class="badge fail">✗ exit {exit_code}</span>'


def build():
    runs = parse_runs(repo_names())
    idle = parse_idle()
    commits = auto_commits()
    steps = next_steps()

    total = len(runs)
    ok = sum(1 for r in runs if r["exit"] == 0)
    failed = sum(1 for r in runs if r["exit"] not in (0, None))
    last = runs[0] if runs else None
    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    run_rows = "\n".join(
        f"""<tr class="{'r-fail' if r['exit'] not in (0, None) else ''}">
          <td class="mono nowrap">{esc(r['when'])}</td>
          <td>{esc(r['repo']) or '<span class=dim>—</span>'}</td>
          <td class="mono dim">{esc(r['model'])}</td>
          <td>{badge(r['exit'])}</td>
          <td><details><summary>{esc(r['summary'][:120])}{'…' if len(r['summary'])>120 else ''}</summary><pre>{esc(r['summary'])}</pre></details></td>
        </tr>""" for r in runs) or '<tr><td colspan=5 class=dim>No runs logged yet.</td></tr>'

    commit_html = ""
    for c in commits:
        rows = "\n".join(
            f'<li><code>{esc(x["hash"])}</code> <span class=dim>{esc(x["date"][:16].replace("T"," "))}</span> {esc(x["subject"])}</li>'
            for x in c["commits"])
        commit_html += f'<div class=card><h3>{esc(c["repo"])} <span class=dim>({len(c["commits"])})</span></h3><ul class=commits>{rows}</ul></div>'
    commit_html = commit_html or '<p class=dim>No <code>(auto)</code> commits found yet.</p>'

    steps_html = ""
    for s in steps:
        steps_html += f'<div class=card><h3>{esc(s["repo"])} <span class="dim mono">{esc(s["mtime"])}</span></h3><details><summary>Show NEXT-STEPS.md</summary><pre>{esc(s["content"])}</pre></details></div>'
    steps_html = steps_html or '<p class=dim>No NEXT-STEPS.md files found yet.</p>'

    idle_html = ("<pre>" + esc("\n".join(idle)) + "</pre>") if idle else '<p class=dim>None — every scheduled run has found work.</p>'

    disabled = disabled_repos()
    if disabled:
        disabled_html = "".join(
            f'<span class="badge off">{esc(name)} <span class=dim>· {esc(reason)}</span></span>'
            for name, reason in disabled)
    else:
        disabled_html = '<p class=dim>None — all repos eligible. Edit <code>~/Dropbox/code/.wrapup-ignore</code> to disable some.</p>'

    last_line = (f'{badge(last["exit"])} &nbsp;<span class=mono>{esc(last["when"])}</span> &nbsp;'
                 f'{esc(last["repo"])}' if last else '<span class=dim>no runs yet</span>')

    return TEMPLATE.format(
        gen=esc(gen), total=total, ok=ok, failed=failed, last_line=last_line,
        run_rows=run_rows, commit_html=commit_html, steps_html=steps_html, idle_html=idle_html,
        disabled_html=disabled_html,
    )


TEMPLATE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Wrap-up automation</title>
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
  .wrap {{ max-width:1100px; margin:0 auto; }}
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
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  ul.commits {{ margin:0; padding-left:0; list-style:none; font-size:13px; }}
  ul.commits li {{ padding:3px 0; border-bottom:1px solid var(--line); }}
  ul.commits li:last-child {{ border-bottom:0; }}
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
  <h1>Repo wrap-up automation</h1>
  <div class=sub>Generated {gen} · runs at 2:45 / 7:45am, 12:45 / 5:45pm · reload this page after a run to refresh</div>

  <div class=stats>
    <div class=stat><div class=n>{total}</div><div class=l>runs logged</div></div>
    <div class=stat><div class=n style="color:var(--ok)">{ok}</div><div class=l>succeeded</div></div>
    <div class=stat><div class=n style="color:var(--fail)">{failed}</div><div class=l>failed</div></div>
  </div>
  <div class=last><strong>Latest:</strong> {last_line}</div>

  <h2>Runs</h2>
  <div class=tablewrap>
    <table>
      <thead><tr><th>When</th><th>Repo</th><th>Model</th><th>Status</th><th>Summary</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>

  <h2>NEXT-STEPS decision lists</h2>
  <div class=cards>{steps_html}</div>

  <h2>Automated commits</h2>
  <div class=cards>{commit_html}</div>

  <h2>Disabled repos <span class="dim" style="text-transform:none;letter-spacing:0">(toggle in ~/Dropbox/code/.wrapup-ignore)</span></h2>
  {disabled_html}

  <h2>Idle runs (nothing recent to do)</h2>
  {idle_html}
</div>
</body>
</html>"""


if __name__ == "__main__":
    open(OUT, "w", encoding="utf-8").write(build())
    print(f"wrote {OUT}")
