#!/usr/bin/env python3
"""Offline stand-ins for followup.py's report + ticket + repair + analyst surface (test_followup.py).

  followup_stubs.py usage ...     lib/usage.py: gates/gate/quota-record from $ROOT/stub-usage.json
  followup_stubs.py worker ...    worker.sh: builds on the follow-up branch per $ROOT/stub-worker.json
  followup_stubs.py review ...    review.sh: verdicts per round from $ROOT/stub-review.json
  followup_stubs.py gh ...        gh: PR checks / merge / merge commit / deploy status from $ROOT/stub-gh.json
  followup_stubs.py linear ...    linear: paginated issues (and failing commands) from $ROOT/stub-linear.json
  followup_stubs.py jlin ...      jlin.py new: files DEV-900
  followup_stubs.py workspaces .. lib/workspaces.py launch: a worktree, the command run detached
  followup_stubs.py codex ...     codex exec -o FILE: analyst answer in $ROOT/stub-analyst.txt
  followup_stubs.py claude ...    claude -p --output-format json: $ROOT/stub-claude-analyst.txt

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
        print(json.dumps({"role": role, "gates": [gate(p) for p in ROLES.get(role, ())]}))
    elif cmd == "gate":
        g = gate(opt(args, "--provider"))
        print(json.dumps(g))
        sys.exit(0 if g["available"] else 3)
    elif cmd == "parse-limit":
        print(json.dumps({"provider": opt(args, "--provider"), "exact_cli_text": opt(args, "--text"),
                          "scope": opt(args, "--scope"), "reset_epoch": 1760396400, "reset_at": "2025-10-13T19:00:00"}))
    elif cmd == "record-limit":
        note("usage-record-limit", provider=opt(args, "--provider"), scope=opt(args, "--scope"))
        if u.get("record_limit_fails"):
            sys.exit(1)
    elif cmd == "quota-record":
        if u.get("quota_record_fails"):
            sys.exit(1)
        out = [gate(p) for p in ROLES.get(role, ()) if not gate(p)["available"]]
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
    elif w.get("mode") == "missing":   # worker.sh with no CLI installed: exit 3, nothing gated
        status = {"used": None, "exit": 3, "limited": [], "missing": ["cursor", "codex", "claude"], "quota": None}
        rc = 3
    else:
        wt = os.environ["SUPERSET_WORKSPACE_PATH"]
        if not Path(wt).resolve().is_relative_to(ROOT.parent.resolve()):
            sys.exit(f"stub worker: refusing to commit outside the test's temp dir: {wt}")
        n = len(list(Path(wt).glob("fix-*.txt")))
        Path(wt, f"fix-{n}.txt").write_text(orch["worker_brief"][:200])
        for rel, body in (w.get("extra_files") or {}).items():
            path = Path(wt) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", f"follow-up fix {n}")
        git(wt, "push", "-q", "-u", "origin", orch["branch"])
        (ROOT / "stub-pr-head.txt").write_text(git(wt, "rev-parse", "HEAD"))
        used = (opt(args, "--providers") or "cursor").split()[0]
        status = {"used": used, "session_id": f"{used}-sess-{n}"}
        repo = orch.get("repo") or "demo"
        result = {"outcome": w.get("outcome", "blocked-branch-left"),
                  "branch": w.get("branch", orch["branch"]),
                  "pr_url": f"https://github.com/jnelken/{repo}/pull/7", "summary": "fixed",
                  "validation": {"status": w.get("validation", "passed"), "commands": ["make test → ok"]}}
        rc = int(w.get("exit", 0))
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
    note("review", round=rnd, repo=opt(args, "--repo"), worker=opt(args, "--worker"),
         exclude=opt(args, "--exclude-reviewers"), strategy=opt(args, "--strategy"))
    if r.get("after_usage") is not None:
        (ROOT / "stub-usage.json").write_text(json.dumps(r["after_usage"]))
    if r.get("unavailable"):
        if r.get("limited"):   # review.sh's own log line for a round every reviewer was limited in
            with open(opt(args, "--log"), "a") as f:
                f.write(json.dumps({"round": rnd, "reviewer": None, "verdict": "unavailable",
                                    "limited": r["limited"]}) + "\n")
        print("no reviewer available")
        sys.exit(3)
    verdicts = r["verdicts"]
    verdict = verdicts[min(rnd, len(verdicts)) - 1]
    head = git(opt(args, "--repo"), "rev-parse", "--short", "HEAD")
    with open(opt(args, "--log"), "a") as f:
        f.write(json.dumps({"round": rnd, "reviewer": "codex", "verdict": verdict, "head": head,
                            "session_id": f"codex-sess-{rnd}"}) + "\n")
    print(f"VERDICT: {verdict}\n- finding {rnd}" if verdict != "clean" else "VERDICT: clean")
    if r.get("exit"):
        sys.exit(int(r["exit"]))


def pr_head(g):
    if g.get("head"):
        return g["head"]
    try:
        return (ROOT / "stub-pr-head.txt").read_text().strip()
    except OSError:
        return ""


def calls(tool):
    try:
        rows = [json.loads(l) for l in (ROOT / "stub-calls.jsonl").read_text().splitlines()]
    except OSError:
        return []
    return [r for r in rows if r["tool"] == tool]


def gh(args):
    g = cfg("stub-gh.json", {"checks": "pass", "deploy": "success"})
    note("gh", args=args)
    if args[:2] == ["pr", "checks"]:
        if g["checks"] == "none":
            print("no checks reported on the 'x' branch")
            sys.exit(1)
        if g["checks"] == "unreadable":
            # Mimic followrepair.gh OSError/timeout → returncode 99.
            print("timed out", file=sys.stderr)
            sys.exit(99)
        if g["checks"] == "pass":
            sys.exit(0)
        print("Some Check\tfail\t0\thttps://example.com", file=sys.stderr)
        sys.exit(1)
    if args[:2] == ["pr", "merge"]:
        if g.get("merge_fails"):
            print("already merged", file=sys.stderr)
            sys.exit(1)
        want = opt(args, "--match-head-commit")
        if want and want != pr_head(g):
            print("GraphQL: Head branch was modified. Review and try the merge again.", file=sys.stderr)
            sys.exit(1)
        return
    if args[:2] == ["pr", "view"]:
        joined = " ".join(args)
        if "state" in joined and "headRefOid" in joined:
            print(json.dumps({"state": g.get("pr_state", "OPEN"),
                              "headRefOid": g.get("merged_head") or pr_head(g)}))
            return
        if "state" in args:
            print(g.get("pr_state", "OPEN"))
            return
        json_fields = " ".join(a for a in args if a.startswith("--json") or "," in a or a.endswith("Name"))
        if "headRefName" in json_fields or "baseRefName" in json_fields:
            head = g.get("head_ref")
            if not head:
                for c in reversed(calls("worker")):
                    if c.get("branch"):
                        head = c["branch"]
                        break
            print(json.dumps({"headRefName": head or "roadmap/followup-unknown",
                              "baseRefName": g.get("base_ref", "main")}))
            return
        if g.get("head_unreadable") and any("headRefOid" in a for a in args):
            print("timed out", file=sys.stderr)
            sys.exit(99)
        # "merge_commit": "head" stands the PR head in for the merge commit (a real, fetchable sha).
        merge = pr_head(g) if g.get("merge_commit") == "head" else "f" * 40
        print(pr_head(g) if "headRefOid" in args else merge)
        return
    deploy = g["deploy"]
    if isinstance(deploy, list):   # one reading per status call, the last repeating
        n = sum(1 for c in calls("gh") if c["args"][:1] == ["api"] and c["args"][1].endswith("/status"))
        deploy = deploy[min(n, len(deploy)) - 1]
    if args[0] == "api" and deploy == "error":
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        sys.exit(1)
    if args[0] == "api" and args[1].endswith("/status"):
        if "statuses" in g:
            print(json.dumps({"statuses": g["statuses"]}))
            return
        if g.get("check_runs") is not None:
            print(json.dumps({"statuses": []}))
            return
        state = {"success": "success", "failure": "failure"}.get(deploy)
        print(json.dumps({"statuses": [{"context": "Vercel", "state": state}] if state else []}))
        return
    if args[0] == "api":
        if g.get("check_runs") is not None:
            print(json.dumps({"check_runs": g["check_runs"]}))
            return
        print(json.dumps({"check_runs": []}))


def workspaces(args):
    """lib/workspaces.py launch: a worktree for the branch, the command started detached in it."""
    repo, branch, cmd = opt(args, "--repo"), opt(args, "--branch"), opt(args, "--command")
    note("workspaces", branch=branch)
    wt = Path(os.environ["ADVANCE_ROADMAP_FOLLOWUP_WORKTREES"]) / ("ws-" + branch.replace("/", "-"))
    if not wt.exists():
        has = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                             capture_output=True).returncode == 0
        add = [str(wt), branch] if has else ["-b", branch, str(wt), "origin/main"]
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", *add], check=True, capture_output=True)
    subprocess.Popen(["/bin/zsh", "-c", cmd], env={**os.environ, "SUPERSET_WORKSPACE_PATH": str(wt)},
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    Path(opt(args, "--out")).write_text(json.dumps({"path": str(wt)}))


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
    body_file = opt(args, "--body-file") or opt(args, "--description-file")
    body = Path(body_file).read_text() if body_file else None
    failed = any(" ".join(args).startswith(prefix) for prefix in lin.get("fail") or [])
    note("linear", cmd=" ".join(args[:3]), issue=args[3] if len(args) > 3 else None, body=body, args=args,
         failed=failed)
    if failed:   # "fail": ["issue update DEV-501"] → that command exits 1, like a transient API error
        print("Error: request failed (503)", file=sys.stderr)
        sys.exit(1)


def jlin(args):
    desc = Path(opt(args, "--description-file")).read_text()
    note("jlin", repo=opt(args, "--repo"), title=opt(args, "--title"), state=opt(args, "--state"), description=desc)
    print("Created DEV-900: https://linear.app/jnelken/issue/DEV-900")


def codex(args):
    # Record full argv so tests can assert analyst isolation flags (--disable shell_tool, …).
    note("codex", args=list(args), cwd=os.getcwd())
    try:
        print((ROOT / "stub-codex-transcript.txt").read_text())
    except OSError:
        pass
    if (ROOT / "stub-codex-limit").exists():
        print("You've hit your usage limit. Upgrade to Pro or try again at 7:00 PM.")
        sys.exit(1)
    out = opt(args, "-o")
    if out:
        Path(out).write_text((ROOT / "stub-analyst.txt").read_text())
    print("session id: 11111111-2222-3333-4444-555555555555")
    if (ROOT / "stub-codex-fail").exists():
        print("codex crashed", file=sys.stderr)
        sys.exit(1)


def claude(args):
    note("claude", args=args[:1])
    if (ROOT / "stub-claude-prefix-garbage").exists():
        print("{not-json")
    result = (ROOT / "stub-claude-analyst.txt").read_text()
    payload = {"type": "result", "result": result, "session_id": "claude-analyst-session"}
    if (ROOT / "stub-claude-is-error").exists():
        payload["is_error"] = True
    print(json.dumps(payload))


if __name__ == "__main__":
    {"usage": usage, "worker": worker, "review": review, "gh": gh, "linear": linear, "jlin": jlin,
     "codex": codex, "claude": claude, "workspaces": workspaces}[sys.argv[1]](sys.argv[2:])
