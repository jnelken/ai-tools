#!/usr/bin/env python3
"""Offline stand-ins for followup.py's report + ticket surface (test_followup.py).

  followup_stubs.py usage ...     lib/usage.py: gates/gate/quota-record from $ROOT/stub-usage.json
  followup_stubs.py linear ...    linear: paginated issues (and failing commands) from $ROOT/stub-linear.json
  followup_stubs.py jlin ...      jlin.py new: files DEV-900

Every call is appended to $ROOT/stub-calls.jsonl. Slice 3b will add worker/review/gh and analyst stubs.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ["ADVANCE_ROADMAP_ROOT"])
ROLES = {"worker": ("cursor", "codex", "claude"), "reviewer": ("codex", "cursor", "claude")}


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


if __name__ == "__main__":
    {"usage": usage, "linear": linear, "jlin": jlin}[sys.argv[1]](sys.argv[2:])
