#!/usr/bin/env python3
"""Carry a no-work verdict forward so an unchanged world isn't re-triaged.

A blocked-no-item run costs ~6 min of Codex planning plus ~9 min of a bookkeeping
worker, and the next run six hours later usually reaches the identical verdict.
This fingerprints everything that verdict was derived from — each repo's HEAD,
branches, working tree and session files; the Linear snapshot; the allowlist and
the skill docs — and stores it with the verdict in the provider-neutral
``~/Dropbox/code/.advance-roadmap/last-verdict.json``.

    verdict.py fingerprint --snapshot S --out F
    verdict.py check  --file V --fingerprint F --changes OUT [--max-age-hours N]
    verdict.py save   --file V --fingerprint F --orch-result R --worker-result W --stamp S
    verdict.py clear  --file V

``check`` exits 0 only when the last verdict was no-work, is younger than the cap,
and nothing in the fingerprint moved; otherwise it exits 1 and writes OUT — which
repos and issues changed — for the planner to narrow its re-triage.
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

CODE_DIR = "/Users/jake/Dropbox/code"
SKILL_DIR = os.path.expanduser("~/.claude/skills/advance-roadmap")
ALLOWLIST = os.path.expanduser("~/.claude/automations/advance-roadmap/allowlist.txt")
NO_WORK_ACTIONS = {"blocked_no_item", "nothing_qualified"}
# Worker outcomes that mean the bookkeeping actually landed (comments, ledger).
SETTLED_OUTCOMES = {"bookkeeping", "archive-only"}


def sha(data):
    return hashlib.sha1(data if isinstance(data, bytes) else data.encode()).hexdigest()[:16]


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=60)
    return p.stdout if p.returncode == 0 else b"<err>"


def file_sha(path):
    try:
        with open(path, "rb") as f:
            return sha(f.read())
    except OSError:
        return None


def repo_print(repo):
    # Repos this skill can never touch count only by their eligibility, so churn in them
    # (ai-tools itself, work clones) doesn't force a replan.
    origin = git(repo, "config", "--get", "remote.origin.url").decode(errors="replace")
    if "jnelken/" not in origin and "jnelken:" not in origin:
        return "not-personal"
    if os.path.exists(os.path.join(repo, ".noroadmap")):
        return "noroadmap"
    sessions = sorted(
        (os.path.basename(p), int(os.path.getmtime(p)))
        for p in glob.glob(os.path.join(repo, ".claude-sessions", "*.md"))
    )
    return sha(json.dumps([
        git(repo, "rev-parse", "HEAD").decode(errors="replace"),
        git(repo, "symbolic-ref", "-q", "HEAD").decode(errors="replace"),
        sha(git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")),
        sha(git(repo, "status", "--porcelain")),
        sessions,
    ]))


def cmd_fingerprint(a):
    repos = {
        os.path.basename(d): repo_print(d)
        for d in sorted(glob.glob(os.path.join(CODE_DIR, "*")))
        if os.path.isdir(os.path.join(d, ".git"))
    }
    snap = {}
    try:
        with open(a.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        pass
    issues = {
        # Content, not updated_at: this skill's own blocker comments bump updated_at on every run.
        i["id"]: sha(json.dumps([i["title"], i["description"], i["state"], i["priority"], i["labels"],
                                 i["blocked_by"], i.get("human_comments_sha")]))
        for i in snap.get("issues", [])
    }
    fp = {
        "repos": repos,
        # A failed fetch is its own state: the verdict it produced may not survive Linear's return.
        "linear_ok": bool(snap.get("ok")),
        "issues": issues,
        "allowlist": file_sha(ALLOWLIST),
        "skill": sha("".join(file_sha(p) or "-" for p in sorted(glob.glob(os.path.join(SKILL_DIR, "*.md"))))),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(fp, f, indent=2)
    return 0


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def diff(old, new):
    changes = []
    for key in ("linear_ok", "allowlist", "skill"):
        if old.get(key) != new.get(key):
            changes.append(key)
    for kind in ("repos", "issues"):
        o, n = old.get(kind, {}), new.get(kind, {})
        changes += [f"{kind[:-1]}:{k}" for k in sorted(set(o) | set(n)) if o.get(k) != n.get(k)]
    return changes


def cmd_check(a):
    last, fp = load(a.file), load(a.fingerprint)
    if not last or not fp:
        print("verdict: no carried verdict — full triage")
        return 1
    try:
        age = datetime.now() - datetime.fromisoformat(last["decided_at"])
    except (KeyError, ValueError):
        age = timedelta.max
    changes = diff(last.get("fingerprint", {}), fp)
    with open(a.changes, "w", encoding="utf-8") as f:
        json.dump({"previous": last, "changed": changes}, f, indent=2)
    if age > timedelta(hours=a.max_age_hours):
        print(f"verdict: carried verdict from {last.get('stamp')} is older than {a.max_age_hours}h — full triage")
        return 1
    if changes:
        shown = ", ".join(changes[:12]) + (f" (+{len(changes) - 12} more)" if len(changes) > 12 else "")
        print(f"verdict: changed since {last.get('stamp')}: {shown}")
        return 1
    print(f"verdict: nothing changed since {last.get('stamp')} ({last.get('outcome_token')}) — skipping")
    return 0


def cmd_save(a):
    orch, worker = load(a.orch_result) or {}, load(a.worker_result) or {}
    if orch.get("action") not in NO_WORK_ACTIONS or worker.get("outcome") not in SETTLED_OUTCOMES:
        # Work was dispatched, or bookkeeping didn't land: the next run must look again.
        if os.path.exists(a.file):
            os.remove(a.file)
        print("verdict: not carried forward")
        return 0
    os.makedirs(os.path.dirname(a.file), exist_ok=True)
    tmp = f"{a.file}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "stamp": a.stamp,
            "decided_at": datetime.now().isoformat(timespec="seconds"),
            "outcome_token": orch.get("outcome_token"),
            "blockers": orch.get("blockers", []),
            "considered": orch.get("considered", []),
            "summary": orch.get("summary"),
            "fingerprint": load(a.fingerprint) or {},
        }, f, indent=2)
        f.write("\n")
    os.replace(tmp, a.file)
    print(f"verdict: carried forward from {a.stamp}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("fingerprint"); s.add_argument("--snapshot", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("check"); s.add_argument("--file", required=True); s.add_argument("--fingerprint", required=True)
    s.add_argument("--changes", required=True); s.add_argument("--max-age-hours", type=int, default=24)
    s = sub.add_parser("save"); s.add_argument("--file", required=True); s.add_argument("--fingerprint", required=True)
    s.add_argument("--orch-result", required=True); s.add_argument("--worker-result", required=True); s.add_argument("--stamp", required=True)
    s = sub.add_parser("clear"); s.add_argument("--file", required=True)
    a = ap.parse_args()
    if a.cmd == "clear":
        if os.path.exists(a.file):
            os.remove(a.file)
        return 0
    return {"fingerprint": cmd_fingerprint, "check": cmd_check, "save": cmd_save}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
