#!/usr/bin/env python3
"""One daily batch: plan one item per personal repo, then run the plans in parallel.

The queue is fixed before planning. --lanes bounds both planning and implementation
to between one and five repos at once. Ready planners wait without using a model.
No repo refills, quota retries, or second batch on the same calendar day.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import supervisor as shared

ROOT = shared.ROOT
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_BATCH_POLL_S", "1"))
PLAN_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_PLAN_TIMEOUT_S", "1800"))
MAX_CONCURRENT_REPOS = 5


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(tmp, path)


def candidates(code):
    """Coarse eligibility only; the existing planner/worker still apply all safety gates."""
    repos = []
    for repo in sorted(code.iterdir()):
        if (repo.is_symlink() or not (repo / ".git").exists()
                or (repo / ".noroadmap").exists()
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo.name)):
            continue
        origin = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                                capture_output=True, text=True, timeout=10)
        if origin.returncode == 0 and re.fullmatch(
                r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
                r"jnelken/[^/\s]+(?:\.git)?", origin.stdout.strip()):
            repos.append(repo.name)
    return repos


class Batch:
    def __init__(self, directory, repos, lanes):
        if not 1 <= lanes <= MAX_CONCURRENT_REPOS:
            raise ValueError("batch concurrency must be between 1 and 5 repos")
        self.directory = directory
        self.limit = lanes
        self.procs = {}
        self.state = {"date": directory.name, "phase": "preparing", "lanes_max": self.limit,
                      "started_at": datetime.now().isoformat(timespec="seconds"), "queue": []}
        # Keep the legacy timestamp format: runmemory and dashboard parse it. Reserve all
        # stamps before launch, rather than sleeping a second between parallel starts.
        stamp_time = int(time.time())
        for n, repo in enumerate(repos, 1):
            while True:
                stamp = datetime.fromtimestamp(stamp_time).strftime("%Y%m%d-%H%M%S")
                stamp_time += 1
                try:
                    (ROOT / "runs" / stamp).mkdir(parents=True)
                    break
                except FileExistsError:
                    continue
            self.state["queue"].append({"repo": repo, "lane": n, "stamp": stamp,
                                        "status": "queued"})

    def write(self):
        self.state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save(self.directory / "batch.json", self.state)

    def launch(self, item):
        env = {**os.environ, "ADVANCE_ROADMAP_LANE": str(item["lane"]),
               "ADVANCE_ROADMAP_STAMP": item["stamp"], "ADVANCE_ROADMAP_TARGET_REPO": item["repo"],
               "ADVANCE_ROADMAP_BATCH_PID": str(os.getpid()),
               "ADVANCE_ROADMAP_BATCH_SNAPSHOT": str(self.directory / "linear-snapshot.json"),
               "ADVANCE_ROADMAP_INFLIGHT_STAMPS": " ".join(i["stamp"] for i in self.state["queue"]),
               "ADVANCE_ROADMAP_LAST_VERDICT": str(ROOT / "batch-verdicts" / f'{item["repo"]}.json')}
        # Other batch repos are never this planner's fallback, even before they have a worker.
        env["ADVANCE_ROADMAP_BUSY_REPOS"] = " ".join(
            i["repo"] for i in self.state["queue"] if i is not item)
        with open(ROOT / "logs" / f'batch-lane-{item["stamp"]}.err', "w") as err:
            proc = subprocess.Popen(["/bin/zsh", str(shared.RUN_SH)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=err, start_new_session=True)
        self.procs[item["stamp"]] = proc
        item.update(status="planning", pid=proc.pid, started=time.time())
        shared.log(f'planning {item["repo"]} [{item["stamp"]}]')

    def poll(self):
        for item in self.state["queue"]:
            proc = self.procs.get(item["stamp"])
            if not proc or item["status"] in {"finished", "deferred"}:
                continue
            if proc.poll() is not None:
                row = shared.run_records({item["stamp"]}).get(item["stamp"], {})
                item.update(status="finished", outcome=row.get("outcome", "unrecorded"),
                            pr_url=row.get("pr_url"), exit_code=proc.returncode)
                continue
            run = ROOT / "runs" / item["stamp"]
            if item["status"] == "planning" and (run / "batch-ready").exists():
                result = shared.read_json(run / "orchestrator-result.json") or {}
                item.update(status="ready", action=result.get("action"),
                            linear_id=result.get("linear_id"), item=result.get("item"))
            elif item["status"] == "planning" and time.time() - item["started"] > PLAN_TIMEOUT_S:
                # Only planning gets this deadline. Already dispatched workers retain run.sh's
                # existing timeout/recovery behavior and are never killed by this controller.
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                item.update(status="finished", outcome="planning-timeout")

    def defer_ready(self, reason):
        for item in self.state["queue"]:
            if item["status"] == "ready":
                (ROOT / "runs" / item["stamp"] / "batch-cancel").write_text(reason + "\n")
                item.update(status="canceling", reason=reason)

    def run(self):
        self.write()
        self.state["phase"] = "planning"
        # A ready lane is only a shell waiting on disk: free its planning slot so the
        # whole finite queue can reach the barrier even when repos outnumber the cap.
        while any(i["status"] in {"queued", "planning"} for i in self.state["queue"]):
            self.poll()
            slots = self.limit - sum(i["status"] == "planning" for i in self.state["queue"])
            for item in [i for i in self.state["queue"] if i["status"] == "queued"][:slots]:
                self.launch(item)
            self.write()
            time.sleep(POLL_S)
        self.state["phase"] = "implementing"
        self.write()
        while any(i["status"] in {"ready", "running", "canceling"} for i in self.state["queue"]):
            self.poll()
            slots = self.limit - sum(i["status"] in {"running", "canceling"}
                                    for i in self.state["queue"])
            ready = sorted((i for i in self.state["queue"] if i["status"] == "ready"),
                           key=lambda i: i.get("action") not in {"dispatch_worker", "resume_worker"})[:slots]
            if ready:
                # Recheck before each wave; exhaustion defers the remaining saved plans to
                # tomorrow. It never turns the daily job into an all-day retry loop.
                shared.py("usage.py", "--root", str(ROOT), "refresh", timeout=120)
                providers = shared.py("usage.py", "--root", str(ROOT), "pick-worker-chain")
                if providers.returncode or providers.stdout.strip() in {"", "none"}:
                    self.defer_ready("no worker quota available; saved plans wait for the next daily batch")
                else:
                    for item in ready:
                        (ROOT / "runs" / item["stamp"] / "batch-release").touch()
                        item["status"] = "running"
                        shared.log(f'released {item["repo"]} {item.get("linear_id") or ""}')
            self.write()
            time.sleep(POLL_S)
        self.state["phase"] = "done"
        self.write()
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes", type=int, default=int(os.environ.get("ADVANCE_ROADMAP_LANES", "5")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.lanes <= MAX_CONCURRENT_REPOS:
        parser.error("--lanes must be between 1 and 5 repos")
    marker = Path.home() / "dotfiles/bin/is-personal-machine"
    if marker.exists() and subprocess.run([str(marker)]).returncode == 1:
        return 0
    code = shared.code_dir()
    if not code:
        return 0
    repos = candidates(Path(code))
    if args.dry_run:
        print(json.dumps({"repos": repos, "concurrency": args.lanes,
                          "schedule": "daily at 10:30 local time", "items_per_repo": 1}, indent=2))
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "run.lock.f", "a") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            shared.log("another roadmap scheduler is running; no second batch")
            return 0
        directory = ROOT / "batches" / datetime.now().date().isoformat()
        directory.parent.mkdir(exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            shared.log("today's batch already started; no automatic repeat")
            return 0
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "batch-verdicts").mkdir(exist_ok=True)
        batch = Batch(directory, repos, args.lanes)
        batch.write()
        shared.LOG = ROOT / "logs" / f"batch-{directory.name}.log"
        marker_dir = ROOT / "run.lock"
        shutil.rmtree(marker_dir, ignore_errors=True)  # stale marker; we hold the kernel lock
        marker_dir.mkdir()
        (marker_dir / "pid").write_text(str(os.getpid()) + "\n")
        # Legacy timed-out workers can outlive their scheduler. Omit their repos today.
        busy = shared.Supervisor(argparse.Namespace(lanes=args.lanes)).busy_repos()
        for item in batch.state["queue"]:
            if item["repo"] in busy:
                item.update(status="deferred", reason="previous worker is still running")
        try:
            if os.environ.get("ADVANCE_ROADMAP_SUPERSET", "1") == "1":
                if shared.py("workspaces.py", "available", timeout=60).returncode:
                    raise RuntimeError("Superset is unavailable")
                shared.py("workspaces.py", "cleanup", timeout=600)
            # Preserve label inference, once per batch rather than once per concurrent lane.
            skill = Path(os.environ.get("ADVANCE_ROADMAP_SKILL_DIR",
                                        str(Path.home() / ".claude/skills/advance-roadmap")))
            infer = skill.parent / "jnelken-linear/jlin.py"
            if infer.exists():
                subprocess.run([sys.executable, str(infer), "infer", "--apply"],
                               stdout=subprocess.DEVNULL, timeout=180, check=False)
            # The snapshot is immutable for this batch; each planner gets the same queue.
            snapshot = directory / "linear-snapshot.json"
            result = shared.py("linearsnap.py", "--out", str(snapshot), timeout=180)
            if result.returncode or not snapshot.exists():
                raise RuntimeError("could not create the batch's Linear snapshot")
            shared.py("usage.py", "--root", str(ROOT), "refresh", timeout=120)
            caffeinate = None
            if shutil.which("caffeinate"):
                caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            try:
                return batch.run()
            finally:
                if caffeinate:
                    caffeinate.terminate()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            batch.state.update(phase="failed", error=str(error))
            batch.write()
            shared.log(str(error))
            return 1
        finally:
            shutil.rmtree(marker_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
