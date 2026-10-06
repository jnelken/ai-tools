#!/usr/bin/env python3
"""The planning pass's dispatch decision, kept until a worker settles it.

A Codex/Claude planning pass costs a full high-effort turn. When the worker it
dispatches then dies (limit, crash, launchd kill), the next run used to re-plan
from scratch. This file keeps the plan so the next run can hand it straight to a
worker instead.

It lives in the code parent (``<personal code dir>/.advance-roadmap/``), not under
``~/.claude``: the plan belongs to the repos, whichever provider orchestrates
them. That parent is not a git checkout, so the file never dirties a repo.

    pendingplan.py --file F save   --result R --stamp S
    pendingplan.py --file F reuse  --out R [--max-age-hours N] [--max-attempts N]
    pendingplan.py --file F settle --worker-result W --worker-rc N [--request-file Q]
    pendingplan.py --file F refund   (a reused plan whose worker never launched: give the attempt back)
    pendingplan.py --file F clear
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

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


CODE_DIR = _personal_code_dir_module().code_dir()
if not CODE_DIR:
    sys.exit("pendingplan: no personal code dir on this machine (set PERSONAL_CODE_DIR)")
PREFLIGHT = os.path.expanduser("~/dotfiles/bin/git-safe-to-autocommit")
DISPATCH_ACTIONS = {"dispatch_worker", "resume_worker"}
# A worker that reports one of these made no durable progress; keep the plan.
RETRY_OUTCOMES = {"failed", "limit_hit"}


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def clear(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def cmd_save(a):
    result = load(a.result)
    if not result or result.get("action") not in DISPATCH_ACTIONS:
        clear(a.file)  # a fresh non-dispatch decision supersedes any old plan
        print("pending plan: none (no dispatch)")
        return 0
    prior = load(a.file) or {}
    same = (prior.get("orchestrator") or {}).get("item") == result.get("item") and (
        prior.get("orchestrator") or {}
    ).get("repo") == result.get("repo")
    write(a.file, {
        "planned_stamp": prior.get("planned_stamp") if same else a.stamp,
        "planned_at": prior.get("planned_at") if same else datetime.now().isoformat(timespec="seconds"),
        "attempts": prior.get("attempts", 0) if same else 0,
        "last_stamp": a.stamp,
        "orchestrator": result,
    })
    print(f"pending plan: saved {result.get('repo')} / {result.get('linear_id') or result.get('item')}")
    return 0


def branch_exists(repo_dir, branch):
    if not branch:
        return False
    return subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    ).returncode == 0


def cmd_reuse(a):
    plan = load(a.file)
    if not plan:
        return 1
    r = plan.get("orchestrator") or {}
    why = None
    try:
        age = datetime.now() - datetime.fromisoformat(plan["planned_at"])
    except (KeyError, ValueError):
        age = timedelta.max
    repo_dir = os.path.join(CODE_DIR, r.get("repo") or "")
    if age > timedelta(hours=a.max_age_hours):
        why = f"older than {a.max_age_hours}h"
    elif plan.get("attempts", 0) >= a.max_attempts:
        why = f"already reused {plan.get('attempts')} time(s)"
    elif not r.get("repo") or not os.path.isdir(repo_dir):
        why = "repo missing"
    elif not branch_exists(repo_dir, r.get("branch")) and subprocess.run(
        [PREFLIGHT, repo_dir], capture_output=True
    ).returncode != 0:
        # No branch = the worker never started; the repo must still be safe to build in.
        why = "repo no longer passes git-safe-to-autocommit"
    if why:
        print(f"pending plan: not reusing ({why}) — orchestrator will re-plan")
        return 1
    if branch_exists(repo_dir, r.get("branch")):
        r = {**r, "action": "resume_worker"}
    r["summary"] = (r.get("summary") or "") + (
        f"\n(Reused pending plan from run {plan.get('planned_stamp')}; "
        f"attempt {plan.get('attempts', 0) + 1} — planning pass skipped.)"
    )
    plan["attempts"] = plan.get("attempts", 0) + 1
    write(a.file, plan)
    write(a.out, r)
    print(f"pending plan: reusing {r.get('repo')} / {r.get('linear_id') or r.get('item')} as {r['action']}")
    return 0


def cmd_settle(a):
    plan = load(a.file)
    if not plan:
        return 0
    if a.request_file:
        # A worker finishing after run.sh gave up on it settles its own plan — and only that one.
        want = (load(a.request_file) or {}).get("orchestrator") or {}
        have = plan.get("orchestrator") or {}
        if (want.get("repo"), want.get("item")) != (have.get("repo"), have.get("item")):
            print("pending plan: belongs to another item — left alone")
            return 0
    outcome = (load(a.worker_result) or {}).get("outcome")
    if outcome is None or outcome in RETRY_OUTCOMES:
        print(f"pending plan: kept for next run (worker outcome={outcome or 'none'}, rc={a.worker_rc})")
    else:
        clear(a.file)
        print(f"pending plan: cleared (worker outcome={outcome})")
    return 0


def cmd_refund(a):
    plan = load(a.file)
    if not plan or plan.get("attempts", 0) <= 0:
        return 0
    plan["attempts"] -= 1
    write(a.file, plan)
    print(f"pending plan: attempt refunded (no worker launched) — {plan['attempts']} used")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save"); s.add_argument("--result", required=True); s.add_argument("--stamp", required=True)
    s = sub.add_parser("reuse"); s.add_argument("--out", required=True)
    s.add_argument("--max-age-hours", type=int, default=36); s.add_argument("--max-attempts", type=int, default=2)
    s = sub.add_parser("settle"); s.add_argument("--worker-result", required=True); s.add_argument("--worker-rc", type=int, default=0)
    s.add_argument("--request-file", help="settle only if the plan is still this request's item")
    sub.add_parser("clear")
    sub.add_parser("refund")
    a = p.parse_args()
    if a.cmd == "clear":
        clear(a.file)
        return 0
    return {"save": cmd_save, "reuse": cmd_reuse, "settle": cmd_settle, "refund": cmd_refund}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
