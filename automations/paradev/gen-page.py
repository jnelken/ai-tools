#!/usr/bin/env python3
"""Build paradev.html: Jake's standing on Paradev's Production Run leaderboard.

    gen-page.py [--force-snapshot]

Takes a leaderboard snapshot first (leaderboard.py; skipped when the last one is under 10
minutes old unless --force-snapshot), then renders leaderboard.json and leaderboard-daily.jsonl.
serve.py serves the page at http://127.0.0.1:8422/ and, through Tailscale Serve, at /paradev.
Every link is relative, because Tailscale Serve strips the /paradev mount prefix.
"""
import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.environ.get("PARADEV_ROOT", os.path.expanduser("~/.claude/automations/paradev"))
OUT = os.path.join(ROOT, "paradev.html")
WEB = os.environ.get("PARADEV_WEB_URL", "https://paradev.com")


def esc(s):
    return html.escape(str(s if s is not None else ""))


def fmt_tokens(n):
    n = int(n or 0)
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n:,}"


def fmt_day(day):
    try:
        return datetime.fromisoformat(day).strftime("%a %b %-d")
    except (TypeError, ValueError):
        return day or "?"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_daily():
    out = []
    try:
        with open(os.path.join(ROOT, "leaderboard-daily.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return sorted(out, key=lambda r: r.get("day", ""), reverse=True)


def stat(big, small, label, note=""):
    return (f'<div class=stat><div class=n>{big}<span class=sm>{esc(small)}</span></div>'
            f'<div class=l>{esc(label)}</div>' + (f'<div class=note>{esc(note)}</div>' if note else "") + '</div>')


def rank_stat(label, r, note=""):
    if not r or not r.get("rank"):
        return stat('<span class=dim>—</span>', "", label, note)
    total = f' / {r["total"]:,}' if isinstance(r.get("total"), int) else ""
    return stat(f'#{r["rank"]:,}', total, label, note)


def status_of(r):
    if r.get("rank"):
        return "final" if r.get("final") else "provisional"
    if r.get("below"):
        return f'below #{r["below"]:,}'
    return "not ranked"


def build(lb, days):
    handle = lb.get("handle") or "jnelken"
    best = lb.get("best_7d") or {}
    ranks = (rank_stat("all-time rank", lb.get("all_time"))
             + rank_stat("7-day best (daily)", best,
                         f'{fmt_day(best["day"])} · {best.get("days_recorded", 0)} of 7 days' if best.get("day") else "")
             + rank_stat("today (UTC day)", lb.get("today"))
             + rank_stat("rolling 7 days", lb.get("rolling_7d")))

    rec = lb.get("records") or {}
    records = ""
    if rec.get("best_rank"):
        b = rec["best_rank"]
        records += stat(f'#{b["rank"]:,}', f' / {b["total"]:,}', "best day rank",
                        f'{fmt_day(b["day"])} · top {b["top_pct"]}% · {fmt_tokens(b["tokens"])} tokens · '
                        f'${b["usd"]:,.0f}' + ("" if b.get("final", True) else " · provisional"))
    if rec.get("busiest"):
        b = rec["busiest"]
        records += stat(fmt_tokens(b["tokens"]), " tokens", "busiest day",
                        f'{fmt_day(b["day"])} · ${b["usd"]:,.0f} · #{b["rank"]:,} / {b["total"]:,}'
                        + ("" if b.get("final", True) else " · provisional"))

    def row(r):
        st = status_of(r)
        ranked = bool(r.get("rank"))
        return (f'<tr><td>{esc(fmt_day(r.get("day")))}<span class=dim> {esc(r.get("day", ""))}</span></td>'
                f'<td class=num>{f"#{r['rank']:,}" if ranked else "—"}</td>'
                f'<td class=num>{f"{r['total']:,}" if r.get("total") else "—"}</td>'
                f'<td class=num>{f"{100 * r['rank'] / r['total']:.1f}%" if ranked and r.get("total") else "—"}</td>'
                f'<td class=num>{fmt_tokens(r["tokens"]) if r.get("tokens") else "—"}</td>'
                f'<td class=num>{f"${r['usd']:,.0f}" if r.get("usd") else "—"}</td>'
                f'<td class=num>{f"{r['sessions']:,}" if r.get("sessions") else "—"}</td>'
                f'<td><span class="badge {"ok" if st == "final" else "unk" if st == "provisional" else "off"}">'
                f'{esc(st)}</span></td></tr>')
    head = ('<thead><tr><th>Day</th><th class=num>Rank</th><th class=num>Ranked</th><th class=num>Top</th>'
            '<th class=num>Tokens</th><th class=num>Cost</th><th class=num>Sessions</th><th>Status</th></tr></thead>')
    top_days = [d for d in days if d.get("rank")]
    top_days = sorted(top_days, key=lambda r: -(r.get("tokens") or 0))[:10]
    top_table = (f'<div class=tablewrap><table>{head}<tbody>{"".join(row(r) for r in top_days)}</tbody></table></div>'
                 if top_days else '<p class=dim>No days recorded yet.</p>')
    history = (f'<div class=tablewrap><table>{head}<tbody>{"".join(row(r) for r in days)}</tbody></table></div>'
               if days else '<p class=dim>No days recorded yet — run <code>leaderboard.py backfill --top 10</code>.</p>')

    when = (lb.get("fetched_at") or "")[:16].replace("T", " ")
    problem = ""
    if lb.get("ok") is False:
        problem = f' · <span class=fail>last fetch failed: {esc(lb.get("error") or "?")}</span>'
    elif lb.get("finalize_error"):
        problem = f' · <span class=fail>{esc(lb["finalize_error"])}</span>'
    return TEMPLATE.format(
        handle=esc(handle), profile=esc(f"{WEB}/{handle}"), when=esc(when or "never"), problem=problem,
        gen=esc(datetime.now().strftime("%Y-%m-%d %H:%M")), ranks=ranks,
        records=f'<div class=stats>{records}</div>' if records else '<p class=dim>No days recorded yet.</p>',
        top_table=top_table, history=history, n_days=len(days))


TEMPLATE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Paradev standing</title>
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --fg:#1a1a1e; --dim:#6b6b76; --line:#e3e3e8;
           --ok:#0a7d34; --okbg:#e4f6ea; --fail:#b3261e; --unk:#8a6d00; --unkbg:#fbf3d6; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0;
           --dim:#9a9aa6; --line:#2c2c33; --ok:#4ade80; --okbg:#0f2a1a; --fail:#f87171; --unk:#e3c04f; --unkbg:#2a2410; }} }}
  :root[data-theme=dark] {{ --bg:#141417; --card:#1d1d21; --fg:#ececf0; --dim:#9a9aa6; --line:#2c2c33;
           --ok:#4ade80; --okbg:#0f2a1a; --fail:#f87171; --unk:#e3c04f; --unkbg:#2a2410; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:24px 16px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  a {{ color:inherit; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); margin:32px 0 12px; }}
  .sub, .dim {{ color:var(--dim); }}
  .sub {{ font-size:13px; margin-bottom:18px; }}
  .fail {{ color:var(--fail); }}
  .stats {{ display:flex; flex-wrap:wrap; gap:12px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; min-width:150px; }}
  .stat .n {{ font-size:26px; font-weight:650; font-variant-numeric:tabular-nums; }}
  .stat .sm {{ font-size:14px; font-weight:500; color:var(--dim); }}
  .stat .l {{ color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .stat .note {{ color:var(--dim); font-size:12px; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--card); }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th,td {{ text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--dim); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  tr:last-child td {{ border-bottom:0; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .badge {{ display:inline-block; padding:1px 8px; border-radius:20px; font-size:12px; font-weight:600; }}
  .badge.ok {{ background:var(--okbg); color:var(--ok); }}
  .badge.unk {{ background:var(--unkbg); color:var(--unk); }}
  .badge.off {{ background:var(--line); }}
  .bar {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; float:right; }}
  button {{ font:inherit; font-size:13px; padding:4px 10px; border-radius:8px; border:1px solid var(--line);
            background:var(--card); color:var(--fg); cursor:pointer; }}
  #rstatus {{ font-size:13px; color:var(--dim); }}
  #rstatus.err {{ color:var(--fail); }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }}
</style>
</head>
<body>
<div class=wrap>
  <div class=bar>
    <span id=rstatus role=status></span>
    <button id=refresh title="Fetch fresh ranks from Paradev and rebuild this page">↻ refresh</button>
    <button onclick="var r=document.documentElement;r.dataset.theme=(r.dataset.theme==='dark'?'light':'dark')">◐ theme</button>
  </div>
  <h1>Paradev standing</h1>
  <div class=sub>@{handle} on the Production Run leaderboard · fetched {when} UTC{problem} ·
    <a href="{profile}">public profile</a></div>

  <div class=stats>{ranks}</div>

  <h2>Personal records</h2>
  {records}

  <h2>Top days</h2>
  {top_table}

  <h2>Every recorded day</h2>
  {history}
  <p class=sub style="margin-top:10px">{n_days} days recorded. A day's rank comes from Paradev's public
    leaderboard for that single UTC day. It stays <em>provisional</em> until 48 h after the day ends, while
    late publishes can still move it. Paradev only pages to about #1,100, so a lower day shows as
    <em>below #1,100</em>. Generated {gen}.</p>
</div>
<script>
(function () {{
  var btn = document.getElementById("refresh"), st = document.getElementById("rstatus");
  btn.addEventListener("click", function () {{
    if (location.protocol === "file:") {{
      st.className = "err"; st.textContent = "Refresh needs the server: open http://127.0.0.1:8422/ or /paradev on the tailnet";
      return;
    }}
    btn.disabled = true; btn.textContent = "↻ refreshing…"; st.className = ""; st.textContent = "Asking Paradev — up to a minute";
    var base = location.pathname.replace(/\\/$/, "");
    fetch(base + "/refresh", {{method: "POST"}}).then(function (r) {{
      return r.json().then(function (j) {{
        if (r.ok && j.ok) {{ location.reload(); return; }}
        st.className = "err"; st.textContent = "Refresh failed: " + ((j.steps || []).filter(function (s) {{ return !s.ok; }})
          .map(function (s) {{ return s.step + ": " + (s.error || "?"); }}).join("; ") || j.error || r.status);
      }});
    }}).catch(function (e) {{ st.className = "err"; st.textContent = "Refresh failed: " + e; }})
      .finally(function () {{ btn.disabled = false; btn.textContent = "↻ refresh"; }});
  }});
}})();
</script>
</body>
</html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force-snapshot", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true", help="render from the files as they are")
    a = ap.parse_args(argv)
    os.makedirs(ROOT, exist_ok=True)
    snap_error = None
    if not a.no_snapshot:
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, "leaderboard.py"), "snapshot",
                                *(["--force"] if a.force_snapshot else [])],
                               capture_output=True, text=True, timeout=90, env={**os.environ, "PARADEV_ROOT": ROOT})
            if p.returncode != 0:
                snap_error = f"snapshot exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}"
        except subprocess.SubprocessError as e:
            snap_error = f"snapshot: {e}"
        # Still render what's on disk, but exit non-zero so ↻ says the ranks weren't refreshed.
    page = build(load_json(os.path.join(ROOT, "leaderboard.json"), {}), load_daily())
    tmp = f"{OUT}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, OUT)
    print(f"wrote {OUT}")
    if snap_error:
        print(snap_error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
