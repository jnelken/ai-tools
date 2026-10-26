#!/usr/bin/env python3
"""Carry a no-work verdict forward so an unchanged world isn't re-triaged.

A blocked-no-item run costs ~6 min of Codex planning plus ~9 min of a bookkeeping
worker, and the next run a couple of hours later usually reaches the identical verdict.
This fingerprints everything that verdict was derived from — each repo's HEAD,
branches, working tree and session files; the Linear snapshot; each repo's production
deploy status; the allowlist and the skill docs — and stores it with the verdict in the provider-neutral
``<personal code dir>/.advance-roadmap/last-verdict.json``.

    verdict.py fingerprint --snapshot S --out F
    verdict.py check  --file V --fingerprint F --changes OUT [--max-age-hours N] [--root R]
    verdict.py save   --file V --fingerprint F --orch-result R --worker-result W --stamp S
    verdict.py clear  --file V
    verdict.py ticket-changes --file V --fingerprint F

``check`` exits 0 only when the last verdict was no-work, is younger than the cap,
and nothing in the fingerprint moved; otherwise it exits 1 and writes OUT — which
repos and issues changed — for the planner to narrow its re-triage.

Only a real planner's answer is a verdict. ``save`` refuses a planner failure (a limit
banner, a synthesized fallback, an error outcome), and ``check`` deletes a carried verdict
that is one — judged from the verdict itself and from its originating run under the
advance-roadmap root (runs/<stamp>/orchestrator-result.json, its runs.jsonl row) — so the
next run re-plans the repo instead of skipping it.
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from usage import limit_banner  # noqa: E402

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
    sys.exit("verdict: no personal code dir on this machine (set PERSONAL_CODE_DIR)")
SKILL_DIR = os.path.expanduser("~/.claude/skills/advance-roadmap")
CONDUCTOR_DIR = os.path.expanduser("~/.claude/skills/conductor")  # holds ORCHESTRATOR.md
ALLOWLIST = os.path.expanduser("~/.claude/automations/advance-roadmap/allowlist.txt")
NO_WORK_ACTIONS = {"blocked_no_item", "nothing_qualified"}
# Worker outcomes that mean the bookkeeping actually landed (comments, ledger).
SETTLED_OUTCOMES = {"bookkeeping", "archive-only"}
DEFAULT_ROOT = os.environ.get("ADVANCE_ROADMAP_ROOT",
                              os.path.expanduser("~/.claude/automations/advance-roadmap"))
# Run outcomes (runs.jsonl) under which no verdict can have been a real planner's.
FAILED_RUN_OUTCOMES = {"error", "incomplete", "quota-paused"}


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
        for p in glob.glob(os.path.join(repo, ".claude", ".sessions", "*.md"))
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
    deploys = {}
    if a.deploys:
        try:
            with open(a.deploys, encoding="utf-8") as f:
                # Status only: a red deploy recovering is what has to reopen triage.
                deploys = {k: v.get("status") for k, v in json.load(f).get("repos", {}).items()}
        except (OSError, ValueError):
            pass
    fp = {
        "repos": repos,
        "deploys": deploys,
        # A failed fetch is its own state: the verdict it produced may not survive Linear's return.
        "linear_ok": bool(snap.get("ok")),
        "issues": issues,
        "allowlist": file_sha(ALLOWLIST),
        "skill": sha("".join(file_sha(p) or "-" for p in sorted(glob.glob(os.path.join(SKILL_DIR, "*.md")) + glob.glob(os.path.join(CONDUCTOR_DIR, "*.md"))))),
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
    for kind in ("repos", "issues", "deploys"):
        o, n = old.get(kind, {}), new.get(kind, {})
        changes += [f"{kind[:-1]}:{k}" for k in sorted(set(o) | set(n)) if o.get(k) != n.get(k)]
    return changes


def planner_failure(orch):
    """Why an orchestrator result (or a verdict saved from one) is not a planner's verdict, or None."""
    if orch.get("planner_error"):
        return f"planner failed ({orch['planner_error'].get('kind') or 'error'})"
    if orch.get("parse_error"):
        return f"planner output unusable ({orch['parse_error']})"
    if limit_banner(orch.get("summary") or ""):
        return "its summary is a CLI limit banner"
    return None


def run_row(root, stamp):
    try:
        with open(os.path.join(root, "runs.jsonl"), encoding="utf-8") as f:
            for line in f:
                if f'"{stamp}"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("stamp") == stamp:
                    return row
    except OSError:
        pass
    return None


def contamination(last, root):
    """Why a carried verdict must not be trusted, from it and from its originating run; or None."""
    why = planner_failure(last)
    if why or not last.get("stamp"):
        return why
    orch = load(os.path.join(root, "runs", last["stamp"], "orchestrator-result.json"))
    if isinstance(orch, dict) and (why := planner_failure(orch)):
        return f"run {last['stamp']}: {why}"
    row = run_row(root, last["stamp"])
    if row and row.get("outcome") in FAILED_RUN_OUTCOMES:
        return f"run {last['stamp']} ended {row['outcome']}" + (f" ({row['detail']})" if row.get("detail") else "")
    return None


def cmd_check(a):
    last, fp = load(a.file), load(a.fingerprint)
    if not isinstance(last, dict) or not last or not fp:
        print("verdict: no carried verdict — full triage")
        return 1
    why = contamination(last, a.root)
    if why:
        # Self-healing: the next check finds no verdict at all, and the planner never sees it.
        if os.path.exists(a.file):
            os.remove(a.file)
        print(f"verdict: discarded the carried verdict from {last.get('stamp')} — {why} — full triage")
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


def cmd_ticket_changes(a):
    """Exit 0 iff a Linear issue moved since the carried verdict — lets a ticket edit
    bypass cadence backoff. Repo churn doesn't count, and no verdict means no bypass."""
    last, fp = load(a.file), load(a.fingerprint)
    if not last or not fp:
        return 1
    issues = [c for c in diff(last.get("fingerprint", {}), fp) if c.startswith("issue:")]
    if not issues:
        return 1
    print(", ".join(issues[:12]) + (f" (+{len(issues) - 12} more)" if len(issues) > 12 else ""))
    return 0


def cmd_save(a):
    orch, worker = load(a.orch_result) or {}, load(a.worker_result) or {}
    why = planner_failure(orch) or (worker.get("limit_text") and "the worker reported a limit")
    if why or orch.get("action") not in NO_WORK_ACTIONS or worker.get("outcome") not in SETTLED_OUTCOMES:
        # Work was dispatched, bookkeeping didn't land, or the planner never really planned:
        # the next run must look again.
        if os.path.exists(a.file):
            os.remove(a.file)
        print("verdict: not carried forward" + (f" — {why}" if why else ""))
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
    s.add_argument("--deploys", default="")
    s = sub.add_parser("check"); s.add_argument("--file", required=True); s.add_argument("--fingerprint", required=True)
    s.add_argument("--changes", required=True); s.add_argument("--max-age-hours", type=int, default=24)
    s.add_argument("--root", default=DEFAULT_ROOT, help="advance-roadmap root holding runs/ and runs.jsonl")
    s = sub.add_parser("save"); s.add_argument("--file", required=True); s.add_argument("--fingerprint", required=True)
    s.add_argument("--orch-result", required=True); s.add_argument("--worker-result", required=True); s.add_argument("--stamp", required=True)
    s = sub.add_parser("clear"); s.add_argument("--file", required=True)
    s = sub.add_parser("ticket-changes"); s.add_argument("--file", required=True); s.add_argument("--fingerprint", required=True)
    a = ap.parse_args()
    if a.cmd == "clear":
        if os.path.exists(a.file):
            os.remove(a.file)
        return 0
    return {"fingerprint": cmd_fingerprint, "check": cmd_check, "save": cmd_save,
            "ticket-changes": cmd_ticket_changes}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
