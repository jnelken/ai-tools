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
      reap idle, clean advance-roadmap workspaces only after Superset has recorded the
      PR merge and GitHub confirms its exact HEAD was merged. Uses self-destruct's
      shared teardown so the worktree, branch and finished tabs are all removed.

Environment: ADVANCE_ROADMAP_SUPERSET_BIN (CLI path), ADVANCE_ROADMAP_SUPERSET_HOST_DB
(host.db path; derived from the logged-in org otherwise).
"""
import argparse
from contextlib import closing
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SUPERSET = os.environ.get(
    "ADVANCE_ROADMAP_SUPERSET_BIN",
    "/Applications/Superset.app/Contents/Resources/resources/bin/superset",
)
TAG = "advance-roadmap"
# Agent hook events that mean the agent is still working (Superset's BUSY_EVENT_TYPES).
BUSY_EVENTS = ("Start", "PermissionRequest")
REAP = os.path.expanduser("~/.claude/skills/self-destruct/scripts/reap-workspace.sh")


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
    data = cli("workspaces", "list", "--local", "--project", pid)
    if not isinstance(data, list) and not (isinstance(data, dict) and any(
            isinstance(data.get(key), list) for key in ('workspaces', 'items'))):
        raise RuntimeError('cannot verify existing workspaces')
    for w in as_list(data):
        if w.get("branch") == branch and not w.get("archivedAt"):
            return w
    return None


def remote_default(repo):
    """Use a committed remote base, never local primary-checkout commits."""
    try:
        symbolic = subprocess.run(['git', '-C', repo, 'symbolic-ref', '--quiet', '--short',
                                   'refs/remotes/origin/HEAD'], capture_output=True, text=True, timeout=5)
        candidates = [symbolic.stdout.strip()] if symbolic.returncode == 0 else []
        candidates += ['origin/main', 'origin/master']
        for ref in candidates:
            if ref and subprocess.run(['git', '-C', repo, 'rev-parse', '--verify', '--quiet',
                                       ref + '^{commit}'], capture_output=True, timeout=5).returncode == 0:
                return ref
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def cmd_launch(a):
    pid = ensure_project(a.repo)
    if not pid:
        print(f"no Superset project for {a.repo}", file=sys.stderr)
        return 3
    try:
        existing = find_workspace(pid, a.branch)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    if existing:
        # Do not adopt another user's workspace just because its branch matches.
        tags = existing.get('tags') or []
        tags = [t.strip() for t in tags.split(',')] if isinstance(tags, str) else tags
        if TAG not in tags:
            print(f"existing workspace for {a.branch} is not tagged {TAG}; refusing to adopt it", file=sys.stderr)
            return 1
        # Resume the automation-owned workspace, including its unfinished work.
        ws_id = existing.get("id")
        term = cli("terminals", "create", "--local", "--workspace", ws_id, "--command", a.command)
        if not term:
            return 1
        terminal_id = term.get("terminalId") or term.get("id") if isinstance(term, dict) else None
        out = {"workspace_id": ws_id, "terminal_id": terminal_id, "reused": True}
    else:
        base = a.base or remote_default(a.repo)
        if not base:
            print(f"no committed origin default branch for {a.repo}; refusing an unsafe base", file=sys.stderr)
            return 1
        args = ["workspaces", "create", "--local", "--project", pid, "--name", a.name,
                "--branch", a.branch, "--skip-branch-prefix", "--command", a.command,
                "--base-branch", base]
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
    """advance-roadmap workspace ids with a PR Superset has seen merged and none still open
    (a fix-forward PR from the same workspace may be in flight after the first merges)."""
    rows = db_query(
        """select w.id, max(p.merged_at), max(coalesce(b.last_event_at, 0)),
                  sum(case when b.last_event_type in (?, ?) and b.ended_at is null then 1 else 0 end),
                  sum(case when p.merged_at is null and p.state = 'open' then 1 else 0 end)
             from workspaces w
             join workspace_pull_requests wp on wp.workspace_id = w.id
             join pull_requests p on p.id = wp.pull_request_id
             left join terminal_agent_bindings b on b.workspace_id = w.id
            where w.archived_at is null
            group by w.id
           having max(p.merged_at) is not null""",
        BUSY_EVENTS,
    )
    return {r[0]: {"merged_at": r[1], "last_event_at": r[2], "busy": r[3], "open_prs": r[4]}
            for r in rows or []}


def terminal_sessions(workspace):
    data = cli("terminals", "list", "--local", "--workspace", workspace)
    if isinstance(data, dict) and isinstance(data.get("sessions"), list):
        return data["sessions"]
    return None


def process_exists(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (ValueError, OSError):
        return True  # an invalid or inaccessible process is not proof of completion


def cleanup_terminals(root, dry_run):
    """Close only detached terminals from recorded workers that completed settlement."""
    try:
        records = {}
        with (root / "runs.jsonl").open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    records[row["stamp"]] = row
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return
    for run in (root / "runs").glob("*/worker.complete"):
        directory = run.parent
        record = records.get(directory.name, {})
        if record.get("worker_exit") != 0 or (directory / "terminal.cleaned").exists():
            continue
        try:
            worker = json.loads((directory / "workspace.json").read_text())
            workspace, terminal = worker["workspace_id"], worker["terminal_id"]
            pid = (directory / "worker.pid").read_text().strip()
            if not workspace or not terminal or process_exists(pid):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        sessions = terminal_sessions(workspace)
        if sessions is None:
            continue
        matches = [s for s in sessions if s.get("terminalId") == terminal]
        if not matches:
            if not dry_run:
                (directory / "terminal.cleaned").touch()
            continue
        if len(matches) != 1 or matches[0].get("attached") is not False:
            continue
        print(f"cleanup: {'would close' if dry_run else 'closing'} finished terminal {terminal} ({directory.name})")
        if not dry_run and cli("terminals", "close", "--local", "--workspace", workspace,
                               "--terminal", terminal) is not None:
            (directory / "terminal.cleaned").touch()


def unfinished_branches(root):
    try:
        with closing(sqlite3.connect(f"file:{root / 'workqueue.sqlite3'}?mode=ro", uri=True)) as con:
            rows = con.execute("select plan from work where state != 'done'").fetchall()
        branches = set()
        for (plan,) in rows:
            item = json.loads(plan)
            if not isinstance(item, dict):
                return None
            if not isinstance(item.get("branch"), str) or not item["branch"]:
                return None  # cannot rule out an assignment without its branch identity
            branches.add(item["branch"])
        return branches
    except (sqlite3.Error, ValueError, OSError):
        return None


def cmd_cleanup(a):
    root = Path(a.root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "workspace-cleanup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cleanup_terminals(root, a.dry_run)
        return cleanup_worktrees(a, root)


def merged_pr_carries_head(path, branch, head):
    """Fresh PR evidence must cover this exact branch tip, including squash merges."""
    try:
        result = subprocess.run(['gh', 'pr', 'list', '--head', branch, '--state', 'all',
                                 '--json', 'state,headRefName,headRefOid,mergedAt', '--limit', '100'],
                                cwd=path, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return False
        prs = json.loads(result.stdout)
        if not isinstance(prs, list) or any(p.get('state') == 'OPEN' for p in prs):
            return False
        return any(p.get('state') == 'MERGED' and p.get('mergedAt') and
                   p.get('headRefName') == branch and p.get('headRefOid') == head for p in prs)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def held_worktree(path):
    """Fail closed unless lsof can rule out a process working in this tree."""
    try:
        result = subprocess.run(['lsof', '-n', '-a', '-d', 'cwd', '-Fn'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return True
        target = os.path.realpath(path)
        return any(os.path.realpath(line[1:]) == target or
                   os.path.realpath(line[1:]).startswith(target + os.sep)
                   for line in result.stdout.splitlines() if line.startswith('n/'))
    except (OSError, subprocess.TimeoutExpired):
        return True


def cleanup_worktrees(a, root):
    tagged = as_list(cli("workspaces", "list", "--local", "--tag", TAG))
    if not tagged:
        print("cleanup: no advance-roadmap workspaces")
        return 0
    merged = merged_workspaces()
    unfinished = unfinished_branches(root)
    if unfinished is None:
        print("cleanup: work queue unavailable; keeping workspaces")
        return 0
    now_ms = time.time() * 1000
    doomed = []
    for w in tagged:
        info = merged.get(w.get("id"))
        if not info:
            continue  # unmerged: in flight, or a branch left for Jake — keep it
        if info["busy"] or info["open_prs"]:
            print(f"cleanup: keeping {w.get('branch')} — agent active or PR open")
            continue  # an agent is still working in it, or a fix-forward PR is still open
        if not w.get("branch") or w["branch"] in unfinished:
            continue
        sessions = terminal_sessions(w["id"])
        if sessions is None or any(s.get("exited") is not True for s in sessions):
            print(f"cleanup: keeping {w.get('branch')} — terminal still open or unavailable")
            continue
        path = w.get("worktreePath") or w.get("worktree_path")
        if not path or not os.path.isdir(path):
            continue
        if path and os.path.isdir(path):
            st = subprocess.run(["git", "-C", path, "status", "--porcelain"], capture_output=True, text=True)
            if st.returncode != 0 or st.stdout.strip():
                print(f"cleanup: keeping {w.get('name') or w['id']} — uncommitted work in {path}")
                continue
            if any((Path(path) / f).exists() for f in ('.claude/IN_PROGRESS.md', 'IN_PROGRESS.md')):
                continue  # A handoff needs human reconciliation before teardown.
            head = subprocess.run(['git', '-C', path, 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
            branch = subprocess.run(['git', '-C', path, 'branch', '--show-current'], capture_output=True, text=True)
            common = subprocess.run(['git', '-C', path, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                    capture_output=True, text=True)
            if head.returncode or branch.returncode or common.returncode or branch.stdout.strip() != w['branch']:
                continue
            gitdir = Path(common.stdout.strip())
            project_root = gitdir.parent if gitdir.name == '.git' else None
            if not project_root or same_path(project_root, path) or not (project_root / '.git').is_dir():
                continue
            if not merged_pr_carries_head(path, w['branch'], head.stdout.strip()):
                print(f"cleanup: keeping {w['branch']} — current HEAD not verified in a merged PR")
                continue
            if held_worktree(path):
                print(f"cleanup: keeping {w['branch']} — a process holds the worktree")
                continue
        idle_min = (now_ms - max(info["last_event_at"] or 0, info["merged_at"] or 0)) / 60000
        if idle_min < a.min_idle_minutes:
            continue
        doomed.append((w["id"], str(project_root), path, w['branch'], head.stdout.strip()))
        print(f"cleanup: {w.get('name') or w['id']} ({w.get('branch')}) merged, idle {idle_min:.0f}m")
    if not doomed:
        print(f"cleanup: {len(tagged)} advance-roadmap workspace(s), none merged and idle")
        return 0
    if a.dry_run:
        print(f"cleanup: dry run — would delete {len(doomed)}")
        return 0
    for ws, project_root, path, branch, head in doomed:
        # Recheck mutable evidence immediately before the shared destructive step.
        current = merged_workspaces().get(ws)
        pending = unfinished_branches(root)
        sessions = terminal_sessions(ws)
        status = subprocess.run(['git', '-C', path, 'status', '--porcelain'], capture_output=True, text=True)
        tip = subprocess.run(['git', '-C', path, 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
        if (not current or current['busy'] or current['open_prs'] or pending is None or branch in pending or
                sessions is None or any(s.get('exited') is not True for s in sessions) or
                status.returncode or status.stdout.strip() or tip.returncode or tip.stdout.strip() != head or
                any((Path(path) / f).exists() for f in ('.claude/IN_PROGRESS.md', 'IN_PROGRESS.md')) or
                not merged_pr_carries_head(path, branch, head) or held_worktree(path)):
            print(f'cleanup: keeping {branch} — safety evidence changed')
            continue
        env = dict(os.environ, PATH=os.path.dirname(SUPERSET) + os.pathsep + os.environ.get('PATH', ''))
        result = subprocess.run(['sh', REAP, ws, project_root, path, branch, '', head], timeout=180, env=env)
        if result.returncode:
            print(f'cleanup: self-destruct failed for {branch}; stopping', file=sys.stderr)
            return 1
        refs = subprocess.run(['git', '-C', project_root, 'show-ref', '--verify', '--quiet',
                               f'refs/heads/{branch}'], capture_output=True)
        remaining = cli('workspaces', 'list', '--local', '--tag', TAG)
        valid_listing = isinstance(remaining, list) or (isinstance(remaining, dict) and
            any(isinstance(remaining.get(key), list) for key in ('workspaces', 'items')))
        if (os.path.exists(path) or refs.returncode != 1 or not valid_listing or
                any(item.get('id') == ws for item in as_list(remaining))):
            print(f'cleanup: could not verify complete removal of {branch}; stopping', file=sys.stderr)
            return 1
    return 0


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
    cp.add_argument("--root", default=os.environ.get("ADVANCE_ROADMAP_ROOT", os.path.expanduser("~/.claude/automations/advance-roadmap")))
    a = p.parse_args(argv)
    return {"available": cmd_available, "project-id": cmd_project_id,
            "launch": cmd_launch, "cleanup": cmd_cleanup}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
