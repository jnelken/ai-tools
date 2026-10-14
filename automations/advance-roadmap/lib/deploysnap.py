#!/usr/bin/env python3
"""Snapshot each personal repo's production deploy state for the read-only planning pass.

The planner runs in a sandbox that can't reach the GitHub API, so on its own it can only
assume a repo left with a red deploy is still red — forever. run.sh takes this snapshot
*before* launching it, the same way linearsnap.py hands over the Linear queue.

A repo's production is judged from GitHub's deployments API (Vercel and Netlify both
publish there), comparing each `Production*` environment's newest deployment with the
default branch's head commit:

    green    every production environment has deployed head successfully
    pending  head is building, or was pushed too recently to have started
    stale    head was pushed a while ago and production never deployed it
    failed   production's deployment of head failed
    none     the repo has no production deployments at all
    unknown  the GitHub calls failed — the planner falls back to its ledger

Always writes OUT and exits 0.

    deploysnap.py --code-dir DIR --out FILE [--repo NAME]
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# How long after a push production may still not have started a deploy before it's stale.
START_GRACE = timedelta(minutes=20)
# A production environment whose newest deploy trails the repo's newest by more than this was
# abandoned (a renamed or retired Vercel project) and no longer says anything about production.
ABANDONED_AFTER = timedelta(days=1)
RANK = {"failed": 4, "stale": 3, "pending": 2, "green": 1}
FAILED_STATES = {"failure", "error"}
PENDING_STATES = {"pending", "queued", "in_progress"}


def gh(path):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:300] or f"gh exited {p.returncode}")
    return json.loads(p.stdout)


def github_slug(repo_dir):
    p = subprocess.run(["git", "-C", repo_dir, "config", "--get", "remote.origin.url"],
                       capture_output=True, text=True, timeout=30)
    m = re.search(r"github\.com[:/](jnelken/[^/\s]+?)(?:\.git)?/?$", p.stdout.strip())
    return m.group(1) if m else None


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live_environments(production):
    if not production:
        return []
    newest = max(parse_time(d["created_at"]) for d in production)
    return [d for d in production if newest - parse_time(d["created_at"]) <= ABANDONED_AFTER]


def classify(head_sha, head_time, production, now):
    """production: [{env, sha, state, created_at}] — the newest deployment per Production* environment."""
    live = live_environments(production)
    if not live:
        return "none"
    worst = "green"
    for d in live:
        if d["sha"] == head_sha:
            s = "failed" if d["state"] in FAILED_STATES else "pending" if d["state"] in PENDING_STATES \
                else "green" if d["state"] == "success" else "pending"
        else:
            s = "pending" if now - head_time < START_GRACE else "stale"
        if RANK[s] > RANK[worst]:
            worst = s
    return worst


def snapshot_repo(slug, now):
    head = gh(f"repos/{slug}/commits/HEAD")
    head_sha, head_time = head["sha"], parse_time(head["commit"]["committer"]["date"])
    newest = {}
    for d in gh(f"repos/{slug}/deployments?per_page=50"):
        env = d.get("environment") or ""
        if env.lower().startswith("production") and env not in newest:
            newest[env] = d  # the API lists newest first
    production = []
    for env, d in sorted(newest.items()):
        statuses = gh(f"repos/{slug}/deployments/{d['id']}/statuses?per_page=1")
        production.append({
            "env": env,
            "sha": d["sha"],
            "state": statuses[0]["state"] if statuses else "pending",
            "url": (statuses[0].get("environment_url") or statuses[0].get("target_url")) if statuses else None,
            "created_at": d["created_at"],
        })
    live = {d["env"] for d in live_environments(production)}
    for d in production:
        d["abandoned"] = d["env"] not in live
    return {
        "status": classify(head_sha, head_time, production, now),
        "head": head_sha,
        "head_at": head["commit"]["committer"]["date"],
        "production": production,
    }


def one(repo_dir, now):
    name = os.path.basename(repo_dir)
    slug = github_slug(repo_dir)
    if not slug:
        return name, None
    try:
        return name, {"slug": slug, **snapshot_repo(slug, now)}
    except Exception as e:  # noqa: BLE001 — one repo's failure never sinks the snapshot
        return name, {"slug": slug, "status": "unknown", "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default="")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    dirs = [d for d in sorted(glob.glob(os.path.join(a.code_dir, "*")))
            if os.path.isdir(os.path.join(d, ".git")) and (not a.repo or os.path.basename(d) == a.repo)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        repos = {n: r for n, r in pool.map(lambda d: one(d, now), dirs) if r is not None}
    snap = {"fetched_at": now.astimezone().isoformat(timespec="seconds"), "source": "gh-deployments",
            "repos": repos}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
        f.write("\n")
    counts = {}
    for r in repos.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    red = sorted(n for n, r in repos.items() if r["status"] in ("failed", "stale"))
    print(f"deploys: {', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}"
          + (f" — red: {', '.join(red)}" if red else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
