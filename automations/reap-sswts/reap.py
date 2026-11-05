#!/usr/bin/env python3
"""Reap Superset sessions whose last word was ⛏️ (reapable).

Launched every 10 minutes by launchd (com.jake.reap-sswts). For every Claude tab
on this machine whose transcript ends on a ⛏️ sign-off that is at least
--grace-min old (default 60), and that is idle, with nothing typed since:

- sswt (a worktree workspace): tear down the whole workspace with
  self-destruct's teardown.sh, but only if every other tab in it is also a
  ⛏️ Claude tab or an idle shell prompt, the tree is clean, the branch's PR is
  MERGED (none OPEN), HEAD is the commit that PR carried, and no gitignored
  IN_PROGRESS.md would be lost. The ⛏️ is a model's claim; these checks are
  what actually gate the delete.
- root checkout (type != worktree, the project's own path, or main/master):
  close the tab's terminal only, like /done. The workspace is never touched.

Anything that fails a check is logged and left alone. Local host only.

    reap.py [--dry-run] [--grace-min N]

Env (tests): REAP_SSWTS_NOW (epoch seconds), REAP_SSWTS_TEARDOWN (script path).
"""

import argparse
import datetime as dt
import fcntl
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "state")
TEARDOWN = os.environ.get(
    "REAP_SSWTS_TEARDOWN",
    os.path.join(HOME, ".claude", "skills", "self-destruct", "scripts", "teardown.sh"),
)
PR_LEDGER = os.path.join(STATE, "pr-review-posted.jsonl")
TAIL_BYTES = 2 * 1024 * 1024
REAPABLE = "⛏"
SHELL_PROMPT = re.compile(r"^\S+@\S+:")  # zsh's idle-prompt tab title: user@host:path
ROOT_BRANCHES = {"main", "master"}


def log(msg):
    print(f"{dt.datetime.now():%F %T} {msg}", flush=True)


def run(*cmd, cwd=None, check=False):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}…: {(res.stderr or res.stdout).strip()[:300]}")
    return res


def sjson(*args):
    return json.loads(run("superset", *args, "--json", check=True).stdout)


# --- Transcripts -------------------------------------------------------------

def bindings():
    """terminal_id -> transcript path, from every org's host.db."""
    out = {}
    for db in glob.glob(os.path.join(HOME, ".superset", "host", "*", "host.db")):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = conn.execute(
                "select terminal_id, transcript_path, agent_session_id from terminal_agent_bindings"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for term, path, session in rows:
            if not path and session:
                hits = glob.glob(os.path.join(HOME, ".claude", "projects", "*", f"{session}.jsonl"))
                path = max(hits, key=os.path.getmtime) if hits else None
            if path:
                out[term] = path
    return out


def tail_entries(path):
    with open(path, "rb") as f:
        size = os.path.getsize(path)
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
        lines = f.read().decode("utf-8", "replace").splitlines()
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def final_signoff(path):
    """(glyph_line, epoch) of the transcript's last turn when it ends on assistant
    text, else (None, None). Any later user entry (a prompt or a tool result)
    means the session moved on."""
    last = None
    for e in tail_entries(path):
        if e.get("type") in ("user", "assistant") and not e.get("isSidechain") and not e.get("isMeta"):
            last = e
    if not last or last.get("type") != "assistant":
        return None, None
    texts = [
        b.get("text", "")
        for b in last.get("message", {}).get("content") or []
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    lines = [ln.strip() for ln in "\n".join(texts).splitlines() if ln.strip()]
    if not lines:
        return None, None
    ts = last.get("timestamp")
    try:
        when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        when = os.path.getmtime(path)
    return lines[-1], when


# --- Git / GitHub checks -----------------------------------------------------

def repo_slug(root):
    url = run("git", "-C", root, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def worktree_blocker(wt, root, branch):
    """None if the worktree is safe to tear down, else (reason, pr_number)."""
    if not os.path.isdir(wt):
        return "worktree folder missing", None
    if run("git", "-C", wt, "status", "--porcelain").stdout.strip():
        return "uncommitted changes", None
    for rel in (".claude/IN_PROGRESS.md", "IN_PROGRESS.md"):
        if os.path.exists(os.path.join(wt, rel)) and run("git", "-C", wt, "check-ignore", "-q", rel).returncode == 0:
            return f"gitignored {rel} would be lost", None
    slug = repo_slug(root)
    if not slug:
        return "origin is not a GitHub repo", None
    res = run("gh", "pr", "list", "--repo", slug, "--head", branch, "--state", "all",
              "--json", "number,state,headRefOid", "--limit", "20")
    if res.returncode != 0:
        return f"gh pr list failed: {res.stderr.strip()[:120]}", None
    prs = json.loads(res.stdout)
    if any(p["state"] == "OPEN" for p in prs):
        return "branch has an OPEN PR", None
    merged = [p for p in prs if p["state"] == "MERGED"]
    if not merged:
        return "no MERGED PR for the branch", None
    head = run("git", "-C", wt, "rev-parse", "HEAD").stdout.strip()
    for p in merged:
        if p["headRefOid"] == head:
            return None, (slug, p["number"])
    for p in merged:
        shas = run("gh", "api", f"repos/{slug}/pulls/{p['number']}/commits", "--paginate", "--jq", ".[].sha").stdout.split()
        if head in shas:
            return None, (slug, p["number"])
    return "HEAD has commits no merged PR carried", None


def drop_ledger_entry(slug, number, dry):
    if not os.path.exists(PR_LEDGER):
        return
    with open(PR_LEDGER, encoding="utf-8") as f:
        lines = f.readlines()
    keep = []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            keep.append(line)
            continue
        if not (e.get("repo") == slug and e.get("pr") == number):
            keep.append(line)
    if len(keep) != len(lines) and not dry:
        with open(PR_LEDGER, "w", encoding="utf-8") as f:
            f.writelines(keep)


# --- Sweep -------------------------------------------------------------------

def classify_terminal(t, bind, now, grace):
    """('reapable', label) | ('shell', None) | ('blocker', reason)."""
    agent = t.get("agent")
    if not agent:
        if SHELL_PROMPT.match(t.get("title") or ""):
            return "shell", None
        return "blocker", f"non-agent tab {t.get('title')!r}"
    if agent.get("id") != "claude":
        return "blocker", f"{agent.get('id')} tab"
    if agent.get("state") != "idle":
        return "blocker", f"claude {agent.get('state')}"
    path = bind.get(t["id"])
    if not path or not os.path.exists(path):
        return "blocker", "no transcript"
    line, when = final_signoff(path)
    if not line or not line.startswith(REAPABLE):
        return "blocker", f"last line {(line or '')[:30]!r}"
    if now - when < grace:
        return "blocker", f"⛏️ only {int((now - when) / 60)}m old"
    return "reapable", line


def sweep(dry, grace_min):
    now = float(os.environ.get("REAP_SSWTS_NOW") or time.time())
    grace = grace_min * 60
    me = os.environ.get("SUPERSET_TERMINAL_ID")
    snap = sjson("snapshot")
    meta = {w["id"]: w for w in sjson("workspaces", "list", "--local")}
    roots = {p["id"]: p.get("path") for p in sjson("projects", "list", "--local")}
    bind = bindings()

    for ws in snap.get("workspaces", []):
        terms = [t for t in ws.get("terminals", []) if t["id"] != me]
        verdicts = {t["id"]: classify_terminal(t, bind, now, grace) for t in terms}
        reapable = [t for t in terms if verdicts[t["id"]][0] == "reapable"]
        if not reapable:
            continue
        w = meta.get(ws["id"], {})
        name = w.get("name") or ws.get("name")
        root = roots.get(w.get("projectId"))
        wt = w.get("worktreePath")
        branch = w.get("branch") or ws.get("branch")
        is_root = (
            w.get("type") != "worktree" or not wt or not root
            or os.path.realpath(wt) == os.path.realpath(root) or branch in ROOT_BRANCHES
        )

        if is_root:
            for t in reapable:
                log(f"{'DRY ' if dry else ''}close root tab: {ws.get('project')}/{name} [{t.get('title')}]")
                if not dry:
                    res = run("superset", "terminals", "close", "--workspace", ws["id"], "--terminal", t["id"], "--local")
                    if res.returncode != 0:
                        log(f"  close failed: {(res.stderr or res.stdout).strip()[:200]}")
            continue

        blockers = [v[1] for v in verdicts.values() if v[0] == "blocker"]
        if blockers:
            log(f"skip {ws.get('project')}/{name}: other tab not reapable ({blockers[0]})")
            continue
        reason, pr = worktree_blocker(wt, root, branch)
        if reason:
            log(f"skip {ws.get('project')}/{name}: {reason}")
            continue
        sha = run("git", "-C", wt, "rev-parse", "HEAD").stdout.strip()
        log(f"{'DRY ' if dry else ''}reap {ws.get('project')}/{name}: {branch} (#{pr[1]} merged) @ {sha[:9]}")
        drop_ledger_entry(pr[0], pr[1], dry)
        env = dict(os.environ, SELF_DESTRUCT_DELAY="0")
        if dry:
            env.update(SELF_DESTRUCT_DRY_RUN="1", SELF_DESTRUCT_LOG="/dev/stdout")
        subprocess.run(["sh", TEARDOWN, ws["id"], reapable[0]["id"], root, wt, branch, sha], env=env)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grace-min", type=int, default=int(os.environ.get("REAP_SSWTS_GRACE_MIN", "60")))
    args = ap.parse_args()
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "reap-sswts.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another sweep is running")
            return 0
        try:
            sweep(args.dry_run, args.grace_min)
        except (RuntimeError, json.JSONDecodeError) as e:
            log(f"sweep aborted: {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
