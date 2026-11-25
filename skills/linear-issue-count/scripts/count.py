#!/usr/bin/env python3
"""Count unarchived Linear issues per authenticated workspace against the plan cap.

Linear's issue limit counts every non-archived issue (done and canceled ones included until
they auto-archive), which is exactly what the GraphQL `issues` connection returns by default.
Appends one record per workspace to ~/.claude/automations/linear-issue-count/state/history.jsonl.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

CAP = int(os.environ.get("LINEAR_ISSUE_CAP", "250"))
WARN = 0.80
STATE = Path.home() / ".claude/automations/linear-issue-count/state"


def api(workspace, query):
    r = subprocess.run(["linear", "--workspace", workspace, "api", query], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200] or "linear api failed")
    return json.loads(r.stdout)["data"]


def workspaces():
    # Personal workspace only; Concentro is deliberately excluded even if the CLI is logged in to it.
    if os.environ.get("LINEAR_COUNT_WORKSPACES"):
        return os.environ["LINEAR_COUNT_WORKSPACES"].split(",")
    return ["jnelken"]


def _all_authenticated_workspaces():
    out = subprocess.run(["linear", "auth", "list"], capture_output=True, text=True).stdout
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    rows = [l.split() for l in out.splitlines()[1:] if l.strip()]
    return [r[1] if r[0] == "*" else r[0] for r in rows]


def count(workspace):
    total, by_type, cursor = 0, {}, None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        d = api(workspace, '{ issues(first: 250%s) { nodes { state { type } } pageInfo { hasNextPage endCursor } } }' % after)["issues"]
        for n in d["nodes"]:
            t = n["state"]["type"]
            by_type[t] = by_type.get(t, 0) + 1
        total += len(d["nodes"])
        if not d["pageInfo"]["hasNextPage"]:
            return total, by_type
        cursor = d["pageInfo"]["endCursor"]


def last_record(workspace):
    f = STATE / "history.jsonl"
    prev = None
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("workspace") == workspace:
                prev = r
    return prev


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    worst, failed = 0.0, []
    for ws in workspaces():
        try:
            total, by_type = count(ws)
        except Exception as e:  # keep going so one bad workspace doesn't hide the rest
            failed.append(ws)
            print(f"{ws}: ERROR {e}")
            continue
        prev = last_record(ws)
        delta = f" ({total - prev['count']:+d} since {prev['date']})" if prev else " (first record)"
        pct = total / CAP
        worst = max(worst, pct)
        flag = " OVER 80% — archive or close issues soon" if pct >= WARN else ""
        types = ", ".join(f"{k} {v}" for k, v in sorted(by_type.items()))
        print(f"{ws}: {total}/{CAP} ({pct:.0%}){delta}{flag}\n  by state: {types}")
        with (STATE / "history.jsonl").open("a") as f:
            f.write(json.dumps({"date": time.strftime("%F"), "workspace": ws, "count": total, "cap": CAP, "by_type": by_type}) + "\n")
    if worst >= WARN:
        subprocess.run(["osascript", "-e", f'display notification "Linear at {worst:.0%} of {CAP} issues" with title "Linear issue cap"'],
                       capture_output=True)
    sys.exit(1 if failed else 0)


main()
