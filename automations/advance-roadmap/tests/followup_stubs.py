#!/usr/bin/env python3
"""Offline stand-ins for followup.py's report-only surface (test_followup.py).

  followup_stubs.py usage ...     lib/usage.py: gates/gate/quota-record from $ROOT/stub-usage.json
  followup_stubs.py codex ...     codex exec -o FILE: the analyst answer in $ROOT/stub-analyst.txt
  followup_stubs.py claude ...    claude -p --output-format json: $ROOT/stub-claude-analyst.txt

Every call is appended to $ROOT/stub-calls.jsonl. Slice 3 will add worker/review/gh/linear stubs.
"""
import json
import os
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
        out = [gate(p) for p in ROLES[role] if not gate(p)["available"]]
        first = out[0] if out else {}
        print(json.dumps({"role": role, "provider": first.get("provider"), "reset_epoch": first.get("reset_epoch"),
                          "reset_at": first.get("reset_at"), "cause": "over its gate" if out else None,
                          "providers": out}))


def codex(args):
    note("codex", args=args[:2])
    if (ROOT / "stub-codex-hang").exists():
        # Spawn a SIGTERM-ignoring child in this process group so timeout reaping must SIGKILL it.
        import signal
        import subprocess
        orphan = ROOT / "stub-codex-orphan.pid"
        subprocess.Popen([sys.executable, "-c",
                          "import os,signal,time\n"
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          f"open({str(orphan)!r},'w').write(str(os.getpid()))\n"
                          "time.sleep(120)\n"])
        while not orphan.exists():
            time.sleep(0.05)
        time.sleep(120)
    if (ROOT / "stub-codex-crash").exists():
        print("codex crashed", file=sys.stderr)
        sys.exit(2)
    try:
        print((ROOT / "stub-codex-transcript.txt").read_text())
    except OSError:
        pass
    if (ROOT / "stub-codex-limit").exists():
        print("You've hit your usage limit. Upgrade to Pro or try again at 7:00 PM.")
        sys.exit(1)
    print("session id: 11111111-2222-3333-4444-555555555555")
    Path(opt(args, "-o")).write_text((ROOT / "stub-analyst.txt").read_text())


def claude(args):
    note("claude", args=args[:1])
    if (ROOT / "stub-claude-crash").exists():
        print("claude crashed", file=sys.stderr)
        sys.exit(2)
    if (ROOT / "stub-claude-hang").exists():
        time.sleep(120)
    print(json.dumps({"type": "result", "result": (ROOT / "stub-claude-analyst.txt").read_text(),
                      "session_id": "claude-analyst-session"}))


if __name__ == "__main__":
    {"usage": usage, "codex": codex, "claude": claude}[sys.argv[1]](sys.argv[2:])
