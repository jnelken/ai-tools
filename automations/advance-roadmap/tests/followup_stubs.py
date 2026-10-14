#!/usr/bin/env python3
"""Offline stand-ins for everything followup.py reaches outside itself (test_followup.py).

  followup_stubs.py usage ...     lib/usage.py: gates/gate/quota-record from $ROOT/stub-usage.json
  followup_stubs.py worker ...    worker.sh: builds on the follow-up branch per $ROOT/stub-worker.json
  followup_stubs.py review ...    review.sh: verdicts per round from $ROOT/stub-review.json
  followup_stubs.py gh ...        gh: PR checks / merge / deploy status from $ROOT/stub-gh.json
  followup_stubs.py linear ...    linear: paginated issues from $ROOT/stub-linear.json
  followup_stubs.py jlin ...      jlin.py new: files DEV-900
  followup_stubs.py codex ...     codex exec -o FILE: the analyst answer in $ROOT/stub-analyst.txt

Every call is appended to $ROOT/stub-calls.jsonl. Nothing here reaches GitHub or Linear.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ["ADVANCE_ROADMAP_ROOT"])
ROLES = {"worker": ("cursor", "codex", "claude"), "reviewer": ("codex", "cursor", "claude"),
         "analyst": ("codex", "claude")}


def cfg(name, default=None):
    try:
        return json.loads((ROOT / name).read_text())
    except (OSError, ValueError):
        return default


def note(tool, **kw):
    with open(ROOT / "stub-calls.jsonl", "a") as f:
        f.write(json.dumps({"tool": tool, "at": time.time(), **kw}) + "\n")


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def usage(args):
    u = cfg("stub-usage.json", {})
    args = args[2:] if args[:1] == ["--root"] else args
    cmd, role = args[0], opt(args, "--role")
    note("usage", cmd=cmd, role=role)
    if u.get("fail"):
        sys.exit(1)
    states = u.get(role) or {}

    def gate(p):
        s = states.get(p, "ok")
        out = s in ("over", "limited")
        return {"provider": p, "available": not out, "state": s, "reason": "over its gate" if out else "",
                "reset_epoch": u.get("reset_epoch") if out else None, "reset_at": u.get("reset_at") if out else None}
    if cmd == "gates":
        print(json.dumps({"role": role, "gates": [gate(p) for p in ROLES[role]]}))
    elif cmd == "gate":
        g = gate(opt(args, "--provider"))
        print(json.dumps(g))
        sys.exit(0 if g["available"] else 3)
    elif cmd == "quota-record":
        out = [gate(p) for p in ROLES[role] if not gate(p)["available"]]
        first = out[0] if out else {}
        print(json.dumps({"role": role, "provider": first.get("provider"), "reset_epoch": first.get("reset_epoch"),
                          "reset_at": first.get("reset_at"), "cause": "over its gate" if out else None,
                          "providers": out}))


def git(wt, *args):
    return subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True, check=True).stdout.strip()


def worker(args):
    w = cfg("stub-worker.json", {"mode": "ok"})
    req = json.loads(Path(opt(args, "--request-file")).read_text())
    orch = req["orchestrator"]
    start = time.time()
    time.sleep(w.get("sleep", 0))
    status, result, rc = {}, {}, 0
    if w.get("mode") == "quota":
        status = {"used": None, "limited": [{"provider": "cursor", "state": "limited"}],
                  "quota": {"role": "worker", "provider": "cursor", "reset_epoch": time.time() + 3600,
                            "reset_at": "2026-10-14T12:00:00", "cause": "You've hit your usage limit"}}
        rc = 3
    else:
        wt = os.environ["SUPERSET_WORKSPACE_PATH"]
        n = len(list(Path(wt).glob("fix-*.txt")))
        Path(wt, f"fix-{n}.txt").write_text(orch["worker_brief"][:200])
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", f"follow-up fix {n}")
        git(wt, "push", "-q", "-u", "origin", orch["branch"])
        used = (opt(args, "--providers") or "cursor").split()[0]
        status = {"used": used, "session_id": f"{used}-sess-{n}"}
        result = {"outcome": w.get("outcome", "blocked-branch-left"), "branch": orch["branch"],
                  "pr_url": "https://github.com/jnelken/demo/pull/7", "summary": "fixed",
                  "validation": {"status": w.get("validation", "passed"), "commands": ["make test → ok"]}}
    if w.get("after_usage") is not None:
        (ROOT / "stub-usage.json").write_text(json.dumps(w["after_usage"]))
    Path(opt(args, "--status-file")).write_text(json.dumps(status))
    if result:
        Path(opt(args, "--result-file")).write_text(json.dumps(result))
    note("worker", start=start, end=time.time(), branch=orch["branch"], brief=orch["worker_brief"],
         mode=req["mode"], providers=opt(args, "--providers"), followup_env=os.environ.get("ADVANCE_ROADMAP_FOLLOWUP"))
    sys.exit(rc)


def review(args):
    r = cfg("stub-review.json", {"verdicts": ["clean"]})
    rnd = int(opt(args, "--round", "1"))
    note("review", round=rnd, repo=opt(args, "--repo"), worker=opt(args, "--worker"))
    if r.get("after_usage") is not None:
        (ROOT / "stub-usage.json").write_text(json.dumps(r["after_usage"]))
    if r.get("unavailable"):
        print("no reviewer available")
        sys.exit(3)
    verdicts = r["verdicts"]
    verdict = verdicts[min(rnd, len(verdicts)) - 1]
    with open(opt(args, "--log"), "a") as f:
        f.write(json.dumps({"round": rnd, "reviewer": "codex", "verdict": verdict, "head": "abc",
                            "session_id": f"codex-sess-{rnd}"}) + "\n")
    print(f"VERDICT: {verdict}\n- finding {rnd}" if verdict != "clean" else "VERDICT: clean")


def gh(args):
    g = cfg("stub-gh.json", {"checks": "pass", "deploy": "success"})
    note("gh", args=args)
    if args[:2] == ["pr", "checks"]:
        if g["checks"] == "none":
            print("no checks reported on the 'x' branch")
            sys.exit(1)
        sys.exit(0 if g["checks"] == "pass" else 1)
    if args[:2] == ["pr", "merge"]:
        return
    if args[:2] == ["pr", "view"]:
        print("f" * 40)
        return
    if args[0] == "api" and args[1].endswith("/status"):
        state = {"success": "success", "failure": "failure"}.get(g["deploy"])
        print(json.dumps({"statuses": [{"context": "Vercel", "state": state}] if state else []}))
        return
    if args[0] == "api":
        print(json.dumps({"check_runs": []}))


def linear(args):
    lin = cfg("stub-linear.json", {"pages": [[]]})
    args = args[2:] if args[:1] == ["--workspace"] else args
    if args[0] == "api":
        after = json.loads(opt(args, "--variables-json")).get("after")
        i = int(after or 0)
        pages = lin["pages"]
        note("linear", cmd="api", page=i)
        print(json.dumps({"data": {"issues": {"nodes": pages[i], "pageInfo": {
            "hasNextPage": i + 1 < len(pages), "endCursor": str(i + 1)}}}}))
        return
    body = Path(opt(args, "--body-file")).read_text() if "--body-file" in args else None
    note("linear", cmd=" ".join(args[:3]), issue=args[3] if len(args) > 3 else None, body=body, args=args)


def jlin(args):
    desc = Path(opt(args, "--description-file")).read_text()
    note("jlin", repo=opt(args, "--repo"), title=opt(args, "--title"), state=opt(args, "--state"), description=desc)
    print("Created DEV-900: https://linear.app/jnelken/issue/DEV-900")


def codex(args):
    note("codex", args=args[:2])
    out = opt(args, "-o")
    Path(out).write_text((ROOT / "stub-analyst.txt").read_text())


if __name__ == "__main__":
    {"usage": usage, "worker": worker, "review": review, "gh": gh, "linear": linear, "jlin": jlin,
     "codex": codex}[sys.argv[1]](sys.argv[2:])
