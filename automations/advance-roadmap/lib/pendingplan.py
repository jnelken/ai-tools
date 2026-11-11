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
import time
import sys
from datetime import datetime, timedelta

import workqueue

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
# These outcomes require continuation, including clean durable milestones.
RETRY_OUTCOMES = {"failed", "limit_hit", "yielded"}


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
    with workqueue.Store() as store:
        store.register(result, a.stamp, a.file)
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


def merged_assignment(repo_dir, branch, planned_at):
    """Recognize an already-merged exact branch revision before a recovery worker starts."""
    if not branch:
        return None
    try:
        planned = datetime.fromisoformat(planned_at).astimezone().timestamp()
        head = subprocess.run(['git', '-C', repo_dir, 'rev-parse', '--verify', f'refs/heads/{branch}'],
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        result = subprocess.run(['gh', 'pr', 'list', '--head', branch, '--state', 'merged', '--limit', '30',
                                 '--json', 'headRefName,headRefOid,mergedAt,mergeCommit,url'],
                                cwd=repo_dir, capture_output=True, text=True, timeout=20, check=True)
        candidates = []
        for pr in json.loads(result.stdout):
            merged_at = datetime.fromisoformat(pr.get('mergedAt', '').replace('Z', '+00:00')).timestamp()
            if (pr.get('headRefName') == branch and pr.get('headRefOid') == head
                    and merged_at >= planned and (pr.get('mergeCommit') or {}).get('oid') and pr.get('url')):
                candidates.append({'url': pr['url'], 'merge_commit': pr['mergeCommit']['oid'], 'head': head})
        return candidates[0] if len(candidates) == 1 else None
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return None


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
    if not r.get("repo") or not os.path.isdir(repo_dir):
        why = "repo missing"
    elif not branch_exists(repo_dir, r.get("branch")) and subprocess.run(
        [PREFLIGHT, repo_dir], capture_output=True
    ).returncode != 0:
        why = "repo no longer passes git-safe-to-autocommit"
    if why:
        print(f"pending plan: held ({why}); saved assignment retained")
        return 2
    merged = merged_assignment(repo_dir, r.get('branch'), plan.get('planned_at')) if branch_exists(repo_dir, r.get('branch')) else None
    with workqueue.Store() as store:
        wid = store.register(r, plan.get('last_stamp') or plan.get('planned_stamp') or '00000000-000000', a.file)
        hold = store.hold(r['repo'])
        if hold:
            print(f"pending plan: held ({hold['reason']}); saved assignment retained")
            return 2
        current = next((w for w in store.list(r['repo']) if w['id'] == wid), None)
        if merged and current and current['state'] != 'done':
            store.checkpoint(wid, current['latest_stamp'], 'verifying',
                {'merged_pr': merged, 'next_action': 'Verify the existing merge and deployment; never merge again'})
            current = next((w for w in store.list(r['repo']) if w['id'] == wid), None)
        if current and (current['state'] == 'human' or current['not_before'] > time.time()):
            print('pending plan: this assignment is waiting; retained for its wake condition')
            return 2
        if current and current['state'] == 'done':
            clear(a.file)
            print('pending plan: already completed; no duplicate worker')
            return 2
        if current and current['checkpoint']:
            r = dict(r)
            r['worker_brief'] = (r.get('worker_brief') or '') + (
                "\n\nContinuation checkpoint (verify current branch/HEAD before trusting evidence):\n"
                + json.dumps({'phase': current['phase'], **current['checkpoint']}))
    if age > timedelta(hours=a.max_age_hours) or plan.get('attempts', 0) >= a.max_attempts:
        r = dict(r)
        r['worker_brief'] = (r.get('worker_brief') or '') + (
            "\nSaved assignment needs revalidation because of age/retries. Check the ticket, branch, "
            "existing PR/merge and current scope first. Resume unfinished work; do not restart completed phases. "
            "If a human decision is required, report needs_input=true with a specific input_question.")
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
    result = load(a.worker_result) or {}
    if a.worker_rc != 0:
        # A result written before a forced stop is not proof that shipping
        # finished; reconcile the branch and PR on the next attempt.
        result = {}
    outcome = result.get("outcome")
    stamp = ((load(a.request_file) or {}).get('stamp') if a.request_file else None) or plan.get('last_stamp') or plan.get('planned_stamp') or '00000000-000000'
    with workqueue.Store() as store:
        assigned = plan.get('orchestrator') or {}
        store.attempt(stamp, assigned.get('repo') or 'unknown', assigned)
        store.finish({'stamp': stamp, 'repo': assigned.get('repo'), 'outcome': outcome,
                      'finished_at': datetime.now().isoformat(timespec='seconds'),
                      'worker_exit': a.worker_rc, 'worker': result.get('provider'),
                      'detail': result.get('input_question') or result.get('summary')}, result)
    if a.worker_rc != 0 or outcome is None or outcome in RETRY_OUTCOMES or outcome in {"blocked-branch-left", "shipped-deploy-failed"}:
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
