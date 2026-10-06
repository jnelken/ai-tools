#!/usr/bin/env python3
"""Superset workspaces for advance-roadmap workers.

The Production Run leaderboard credits a merged PR only when it came from a Superset
workspace with an agent bound to it. So each dispatched item gets its own workspace
(a git worktree under ~/.superset/worktrees, outside Dropbox), and worker.sh runs in
that workspace's terminal — the agent hooks inherit SUPERSET_TERMINAL_ID and bind.

  workspaces.py available
      exit 0 when the CLI answers and this machine's host service is up.
  workspaces.py project-id --repo PATH
      print the Superset project whose checkout is PATH (exit 1 when none).
  workspaces.py launch --repo PATH --branch B --name N --command CMD [--base REF] [--tag T] --out FILE
      create (or reuse) the workspace for branch B and run CMD in a new terminal there. A repo
      with no Superset project yet is imported as one first.
      Writes {"workspace_id", "terminal_id", "reused"} to FILE.
  workspaces.py cleanup [--dry-run] [--min-idle-minutes 30]
      delete advance-roadmap workspaces whose PR Superset has recorded as merged. Never
      earlier: the PR sync skips archived workspaces, so deleting before merged_at is set
      leaves the PR open in Superset's DB forever and it is never counted.

Environment: ADVANCE_ROADMAP_SUPERSET_BIN (CLI path), ADVANCE_ROADMAP_SUPERSET_HOST_DB
(host.db path; derived from the logged-in org otherwise).
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time

SUPERSET = os.environ.get(
    "ADVANCE_ROADMAP_SUPERSET_BIN",
    "/Applications/Superset.app/Contents/Resources/resources/bin/superset",
)
TAG = "advance-roadmap"
# Agent hook events that mean the agent is still working (Superset's BUSY_EVENT_TYPES).
BUSY_EVENTS = ("Start", "PermissionRequest")


def cli(*args, timeout=60):
    """Run the superset CLI with --json and return the parsed payload (None on failure)."""
    try:
        p = subprocess.run([SUPERSET, *args, "--json"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"superset {' '.join(args)}: {e}", file=sys.stderr)
        return None
    if p.returncode != 0:
        print(f"superset {' '.join(args)}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[:400]}",
              file=sys.stderr)
        return None
    try:
        data = json.loads(p.stdout)
    except ValueError:
        print(f"superset {' '.join(args)}: unparseable output", file=sys.stderr)
        return None
    # Some commands wrap the payload in {"data": …}.
    if isinstance(data, dict) and "data" in data and len(data) <= 2:
        return data["data"]
    return data


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("projects", "workspaces", "items", "terminals"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def same_path(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def project_id(repo):
    for p in as_list(cli("projects", "list", "--local")):
        if p.get("path") and same_path(p["path"], repo):
            return p.get("id")
    return None


def host_db():
    override = os.environ.get("ADVANCE_ROADMAP_SUPERSET_HOST_DB")
    if override:
        return override
    org = os.environ.get("SUPERSET_ORGANIZATION_ID")
    if not org:
        who = cli("auth", "whoami") or {}
        org = who.get("organizationId")
    if not org:
        return None
    path = os.path.expanduser(f"~/.superset/host/{org}/host.db")
    return path if os.path.exists(path) else None


def db_query(sql, params=()):
    path = host_db()
    if not path:
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        print(f"host.db: {e}", file=sys.stderr)
        return None


def cmd_available(a):
    who = cli("auth", "whoami", timeout=20)
    if not who:
        return 1
    # A listing proves this machine's host service is answering, not just the cloud API.
    if cli("projects", "list", "--local", timeout=20) is None:
        return 1
    return 0


def ensure_project(repo):
    """The repo's project id, importing the checkout as a project the first time."""
    pid = project_id(repo)
    if pid:
        return pid
    made = cli("projects", "create", "--local", "--import", repo,
               "--name", os.path.basename(os.path.normpath(repo)), timeout=120)
    if made:
        print(f"imported {repo} as a Superset project", file=sys.stderr)
    return project_id(repo)


def cmd_project_id(a):
    pid = project_id(a.repo)
    if not pid:
        print(f"no Superset project for {a.repo}", file=sys.stderr)
        return 1
    print(pid)
    return 0


def find_workspace(pid, branch):
    for w in as_list(cli("workspaces", "list", "--local", "--project", pid)):
        if w.get("branch") == branch and not w.get("archivedAt"):
            return w
    return None


def cmd_launch(a):
    pid = ensure_project(a.repo)
    if not pid:
        print(f"no Superset project for {a.repo}", file=sys.stderr)
        return 3
    existing = find_workspace(pid, a.branch)
    if existing:
        # Resume: the branch already has a workspace (git allows one worktree per branch).
        ws_id = existing.get("id")
        term = cli("terminals", "create", "--local", "--workspace", ws_id, "--command", a.command)
        if not term:
            return 1
        terminal_id = term.get("terminalId") or term.get("id") if isinstance(term, dict) else None
        out = {"workspace_id": ws_id, "terminal_id": terminal_id, "reused": True}
    else:
        args = ["workspaces", "create", "--local", "--project", pid, "--name", a.name,
                "--branch", a.branch, "--skip-branch-prefix", "--command", a.command]
        if a.base:  # default: the project's default branch (main or master)
            args += ["--base-branch", a.base]
        for tag in a.tag or [TAG]:
            args += ["--tag", tag]
        made = cli(*args, timeout=180)
        if not made or not isinstance(made, dict) or not (made.get("workspace") or {}).get("id"):
            return 1
        terms = made.get("terminals") or []
        out = {"workspace_id": made["workspace"]["id"],
               "terminal_id": terms[0].get("terminalId") if terms else None,
               "reused": bool(made.get("alreadyExists"))}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out))
    return 0


def merged_workspaces():
    """advance-roadmap workspace ids whose linked PR Superset has seen merged."""
    rows = db_query(
        """select w.id, max(p.merged_at), max(coalesce(b.last_event_at, 0)),
                  sum(case when b.last_event_type in (?, ?) and b.ended_at is null then 1 else 0 end)
             from workspaces w
             join workspace_pull_requests wp on wp.workspace_id = w.id
             join pull_requests p on p.id = wp.pull_request_id
             left join terminal_agent_bindings b on b.workspace_id = w.id
            where w.archived_at is null and p.merged_at is not null
            group by w.id""",
        BUSY_EVENTS,
    )
    return {r[0]: {"merged_at": r[1], "last_event_at": r[2], "busy": r[3]} for r in rows or []}


def cmd_cleanup(a):
    tagged = as_list(cli("workspaces", "list", "--local", "--tag", TAG))
    if not tagged:
        print("cleanup: no advance-roadmap workspaces")
        return 0
    merged = merged_workspaces()
    now_ms = time.time() * 1000
    doomed = []
    for w in tagged:
        info = merged.get(w.get("id"))
        if not info:
            continue  # unmerged: in flight, or a branch left for Jake — keep it
        if info["busy"]:
            continue  # an agent is still working in it (a fix-forward, say)
        idle_min = (now_ms - max(info["last_event_at"] or 0, info["merged_at"] or 0)) / 60000
        if idle_min < a.min_idle_minutes:
            continue
        doomed.append(w["id"])
        print(f"cleanup: {w.get('name') or w['id']} ({w.get('branch')}) merged, idle {idle_min:.0f}m")
    if not doomed:
        print(f"cleanup: {len(tagged)} advance-roadmap workspace(s), none merged and idle")
        return 0
    if a.dry_run:
        print(f"cleanup: dry run — would delete {len(doomed)}")
        return 0
    return 0 if cli("workspaces", "delete", "--local", *doomed, timeout=180) is not None else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("available")
    pp = sub.add_parser("project-id")
    pp.add_argument("--repo", required=True)
    lp = sub.add_parser("launch")
    lp.add_argument("--repo", required=True)
    lp.add_argument("--branch", required=True)
    lp.add_argument("--base")
    lp.add_argument("--name", required=True)
    lp.add_argument("--command", required=True)
    lp.add_argument("--tag", action="append")
    lp.add_argument("--out", required=True)
    cp = sub.add_parser("cleanup")
    cp.add_argument("--dry-run", action="store_true")
    cp.add_argument("--min-idle-minutes", type=float, default=30)
    a = p.parse_args(argv)
    return {"available": cmd_available, "project-id": cmd_project_id,
            "launch": cmd_launch, "cleanup": cmd_cleanup}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
