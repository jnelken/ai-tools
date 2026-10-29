#!/usr/bin/env python3
"""One daily batch: plan the personal repos, approve the plans, then run them in parallel.

Each repo starts with one thread. Once a repo's first thread has planned a feature, a second
planner for the same repo may be started (--threads, at most three per repo) and told what the
earlier threads will touch. Before any worker starts, a plan approver (lib/planapproval.py) rules
on every extra thread: run in parallel, run only after an earlier thread has merged, or wait for
tomorrow. The first thread of a repo is never deferred or serialized by that ruling; it waits
at the barrier only for the slowest approval (rulings for different repos run side by side).

--lanes bounds both planning and implementation to between one and five threads at once. Ready
planners wait without using a model. No repo refills, quota retries, or second batch on the same
calendar day. One Slack summary is posted when the batch ends; individual runs post nothing.
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
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import supervisor as shared

sys.path.insert(0, str(shared.HERE / "lib"))
import usage  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_budget  # noqa: E402

ROOT = shared.ROOT
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_BATCH_POLL_S", "1"))
PLAN_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_PLAN_TIMEOUT_S", "1800"))
MAX_CONCURRENT_AGENTS = 5
MAX_THREADS_PER_REPO = 3
DEFAULT_THREADS_PER_REPO = 2
# Two workers of one repo creating Superset workspaces in the same second race the project import.
RELEASE_GAP_S = float(os.environ.get("ADVANCE_ROADMAP_THREAD_RELEASE_GAP_S", "20"))
APPROVAL_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_APPROVAL_TIMEOUT_S", "1800"))
# Outcomes whose row carries a `quota` field: who ran out and when it resets.
QUOTA_OUTCOMES = {"quota-paused", "skipped-quota"}
OUT_STATES = ("over", "limited")
# What a worker's bookkeeping or implementation can be asked to do; anything else a planner returns is
# a no-work result, whose verdict is worth keeping even when no worker can run today.
WORK_ACTIONS = {"dispatch_worker", "resume_worker"}


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


def implementation_headroom(budget=None):
    """Preserve A4's provider/reviewer gates, then require one complete job's budget.

    A broken quota gate is unknown, as in run.sh; shared budget admission still
    bounds unknown readings to one job per provider.
    """
    if budget is not None:
        workers, by_provider = budget.workers, budget.gates
        decision = budget.review_strategy
    else:
        try:
            inputs = shared.py("usage.py", "--root", str(ROOT), "budget-input", timeout=120)
            values = json.loads(inputs.stdout) if inputs.returncode == 0 else {}
            workers = values.get("workers", [])
            by_provider = {g.get("provider"): g for g in values.get("reviewer_gates", [])
                           if isinstance(g, dict)}
            decision = values.get("review_strategy") or usage.effective_review_strategy(
                os.environ.get("ADVANCE_ROADMAP_REVIEW_STRATEGY", "cursor-then-codex"),
                list(by_provider.values()))
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            return True, "", None
    if not workers:
        return False, "no worker provider is under its quota gate", "worker"
    if decision.get("strategy") == "cross-provider":
        reviewers = [name for name, gate in by_provider.items() if gate.get("state") not in OUT_STATES]
        if by_provider and not any(r != w for w in workers for r in reviewers):
            reason = "every reviewer is gated" if not reviewers else "no cross-provider reviewer is under its quota gate"
            return False, reason, "reviewer"
    else:
        for name in ("cursor", "codex"):
            g = by_provider.get(name) or {}
            if g.get("state") in OUT_STATES:
                return False, f'the required {name} reviewer is gated ({g.get("reason") or g["state"]})', "reviewer"
    if budget is not None and budget.choose(scale=1, commit=False) is None:
        return False, "no worker plus required review fits the shared agent budget", "worker"
    return True, "", None


def repo_priority(repo, snapshot=None, dry_run=False):
    """Inspect carried state without changing it; run.sh owns the actual skip/reuse.

    Check a temporary copy because verdict.check may discard a contaminated
    verdict. A dry run uses saved snapshots only and never calls Linear/GitHub.
    """
    code = Path(shared.code_dir())
    pending = code / ".advance-roadmap"
    for path in sorted(pending.glob(f"pending-plan-{repo}*.json")):
        if path.name != f"pending-plan-{repo}.json" and not path.name.startswith(f"pending-plan-{repo}.t"):
            continue
        saved = shared.read_json(path) or {}
        action = (saved.get("orchestrator") or saved).get("action")
        if action in WORK_ACTIONS:
            return {"priority": 0, "result": "pending", "action": action}
    verdict = ROOT / "batch-verdicts" / f"{repo}.json"
    if not verdict.exists():
        return {"priority": 2, "result": "no-verdict"}
    if snapshot is None:
        snapshots = sorted((ROOT / "batches").glob("*/linear-snapshot.json"))
        snapshot = snapshots[-1] if snapshots else None
    if snapshot is None or not snapshot.exists():
        return {"priority": 2, "result": "unknown", "reason": "no saved Linear snapshot"}
    with tempfile.TemporaryDirectory(prefix="roadmap-verdict-") as directory:
        temp = Path(directory)
        carried, fingerprint, changes, deploys = [temp / name for name in ("verdict.json", "fp.json", "changes.json", "deploys.json")]
        shutil.copyfile(verdict, carried)
        if dry_run:
            old = shared.read_json(verdict) or {}
            # Preserve saved deploy values; report that deployment changes have not
            # been refreshed, rather than making a network call from a dry run.
            save(deploys, {"repos": {k: {"status": v} for k, v in
                                    old.get("fingerprint", {}).get("deploys", {}).items()}})
        else:
            shared.py("deploysnap.py", "--code-dir", str(code), "--repo", repo, "--out", str(deploys))
        fp = shared.py("verdict.py", "fingerprint", "--snapshot", str(snapshot), "--deploys", str(deploys),
                       "--out", str(fingerprint), "--repo", repo)
        if fp.returncode or not fingerprint.exists():
            return {"priority": 2, "result": "unknown", "reason": "fingerprint unavailable"}
        check = shared.py("verdict.py", "check", "--file", str(carried), "--fingerprint", str(fingerprint),
                          "--changes", str(changes), "--max-age-hours", "72", "--root", str(ROOT))
        changed = (shared.read_json(changes) or {}).get("changed") or []
        return {"priority": 1 if changed else 3 if check.returncode == 0 else 2,
                "result": "changed" if changed else "unchanged" if check.returncode == 0 else "retriage",
                "changed": changed, "detail": check.stdout.strip(),
                "snapshot": "saved" if dry_run else "current"}


def dispatch_rates():
    counts = {}
    for row in agent_budget.rows(ROOT / "runs.jsonl"):
        repo = row.get("repo")
        if not repo:
            continue
        action = row.get("action")
        if action is None:
            action = (shared.read_json(ROOT / "runs" / str(row.get("stamp", "")) / "orchestrator-result.json") or {}).get("action")
        if action not in WORK_ACTIONS | {"blocked_no_item", "nothing_qualified"}:
            continue  # skips/failures are not planning observations
        dispatch, total = counts.get(repo, (0, 0))
        counts[repo] = (dispatch + (action == "dispatch_worker"), total + 1)
    return {repo: n / total for repo, (n, total) in counts.items()}


class RoadmapBudget:
    """Batch jobs transact on exactly the same leases as interactive fan-out."""
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.models = agent_budget.cached_estimates(read_only=dry_run)
        self.rates = dispatch_rates()
        self.forecast = []
        self.refresh()

    def refresh(self):
        options = None
        if not self.dry_run:
            result = shared.py("usage.py", "--root", str(ROOT), "budget-input", timeout=120)
            try:
                options = json.loads(result.stdout) if result.returncode == 0 else None
            except ValueError:
                pass
        if not isinstance(options, dict):
            state = usage.load(ROOT)
            usage.recompute_flags(state)
            options = {"workers": usage.pick_worker_chain(state), "planners": usage.pick_orchestrators(state),
                       "reviewer_gates": usage.role_gates(state, "reviewer")}
        self.workers = [p for p in options.get("workers", []) if p in agent_budget.PROVIDERS]
        self.planners = [p for p in options.get("planners", []) if p in ("codex", "claude")]
        self.gates = {g["provider"]: g for g in options.get("reviewer_gates", []) if isinstance(g, dict)}
        self.review_strategy = options.get("review_strategy") or usage.effective_review_strategy(
            os.environ.get("ADVANCE_ROADMAP_REVIEW_STRATEGY", "cursor-then-codex"),
            list(self.gates.values()))
        self.readings = {p: agent_budget.readings(p, root=ROOT, probe=not self.dry_run and p == "claude")
                         for p in agent_budget.PROVIDERS}

    def review_decision(self):
        return getattr(self, "review_strategy", None) or usage.effective_review_strategy(
            os.environ.get("ADVANCE_ROADMAP_REVIEW_STRATEGY", "cursor-then-codex"),
            list(self.gates.values()))

    def routes(self):
        decision = self.review_decision()
        for worker in self.workers:
            if decision.get("strategy") == "cross-provider":
                order = {"cursor": ["codex", "claude"], "codex": ["cursor", "claude"],
                         "claude": ["codex", "cursor"]}[worker]
                reviews = [[p] for p in order if self.gates.get(p, {}).get("state") not in OUT_STATES]
            else:
                reviews = [["cursor", "codex"]] if all(
                    self.gates.get(p, {}).get("state") not in OUT_STATES for p in ("cursor", "codex")) else []
            for reviewers in reviews:
                yield worker, reviewers

    def choose(self, *, item=None, planner=None, scale=1, commit=True):
        for worker, reviewers in self.routes():
            requests = [{"provider": worker, "role": "worker", "scale": scale},
                        *[{"provider": p, "role": "reviewer", "scale": scale} for p in reviewers]]
            if planner:
                requests.append({"provider": planner, "role": "planner"})
            leases = agent_budget.reserve_bundle(
                requests, replace_ids=(item or {}).get("budget_leases", []),
                reading_by_provider=self.readings, models=self.models,
                pid=(item or {}).get("pid"),
                label=f'roadmap:{item["stamp"]}' if item else "roadmap:forecast",
                ttl=PLAN_TIMEOUT_S + APPROVAL_TIMEOUT_S if planner else None,
                commit=commit and not self.dry_run, extra_leases=self.forecast)
            if leases is not None:
                decision = self.review_decision()
                allocation = {"worker": worker, "reviewers": reviewers, "planner": planner,
                              "review_strategy": decision.get("strategy"),
                              "review_fallback_reason": decision.get("reason"),
                              "requests": requests, "leases": leases}
                if item is not None and commit:
                    item.update(budget_leases=[l["id"] for l in leases], allocation=allocation)
                    if self.dry_run:
                        self.forecast.extend(leases)
                return allocation
        return None

    def admit(self, item):
        # A pending dispatch needs its whole implementation. Prefer a planner
        # fallback if it fits, but saved work can run without that extra cost.
        # Later threads inherit ordering metadata but always plan fresh in run.sh.
        pending = (item.get("thread", 1) == 1
                   and item.get("verdict_check", {}).get("result") == "pending")
        for planner in ([*self.planners, None] if pending else self.planners):
            allocation = self.choose(item=item, planner=planner,
                                     scale=1 if pending else self.rates.get(item["repo"], .3))
            if allocation is not None:
                return True
        return False

    def planned(self, item):
        allocation = item.get("allocation") or {}
        requests = [r for r in allocation.get("requests", []) if r["role"] != "planner"]
        leases = agent_budget.reserve_bundle(requests, replace_ids=item.get("budget_leases", []),
            reading_by_provider=self.readings, models=self.models,
            pid=item.get("pid"), label=f'roadmap:{item["stamp"]}', reduce_only=True)
        item["budget_leases"] = [l["id"] for l in leases]
        allocation.update(requests=requests, leases=leases, planner=None)

    def release(self, item):
        ids = item.pop("budget_leases", [])
        if ids and not self.dry_run:
            agent_budget.release_many(ids)

    def snapshot(self, queue):
        with agent_budget.lock() if not self.dry_run else agent_budget.nullcontext():
            leases = agent_budget.reap(persist=not self.dry_run) + self.forecast
        return {"providers": {p: agent_budget.view(p, self.readings[p], self.models[p], leases)
                              for p in agent_budget.PROVIDERS},
                "held": [{"repo": i["repo"], "thread": i.get("thread", 1), "reason": i["budget_hold"]}
                         for i in queue if i.get("budget_hold")]}


def plan_of(result, item):
    """What a later planner and the plan approver need to know about one thread's plan."""
    return {"stamp": item["stamp"], "thread": item["thread"], "repo": item["repo"],
            "action": result.get("action"), "item": result.get("item"),
            "linear_id": result.get("linear_id"), "branch": result.get("branch"),
            "work_kind": result.get("work_kind") or "feature",
            "touchpoints": [t for t in result.get("touchpoints") or [] if isinstance(t, str)],
            "conflict_notes": [n for n in result.get("conflict_notes") or [] if isinstance(n, str)],
            "worker_brief": (result.get("worker_brief") or "")[:2500],
            "summary": (result.get("summary") or "")[:600]}


class Batch:
    def __init__(self, directory, repos, lanes, threads=1):
        if not 1 <= lanes <= MAX_CONCURRENT_AGENTS:
            raise ValueError("batch concurrency must be between 1 and 5 agents")
        if not 1 <= threads <= MAX_THREADS_PER_REPO:
            raise ValueError(f"threads per repo must be between 1 and {MAX_THREADS_PER_REPO}")
        self.directory = directory
        self.limit = lanes
        self.threads = threads
        self.procs = {}
        self.budget = None
        self.state = {"date": directory.name, "phase": "preparing", "lanes_max": self.limit,
                      "threads_max": self.threads,
                      "started_at": datetime.now().isoformat(timespec="seconds"), "queue": []}
        # Keep the legacy timestamp format: runmemory and dashboard parse it. Reserve all
        # stamps before launch, rather than sleeping a second between parallel starts.
        self.stamp_time = int(time.time())
        for repo in repos:
            self.reserve(repo, 1)

    def reserve(self, repo, thread, parent=None):
        """Add a queued entry for one thread of a repo, with a stamp nobody else has used."""
        self.stamp_time = max(self.stamp_time, int(time.time())) if thread > 1 else self.stamp_time
        while True:
            stamp = datetime.fromtimestamp(self.stamp_time).strftime("%Y%m%d-%H%M%S")
            self.stamp_time += 1
            try:
                (ROOT / "runs" / stamp).mkdir(parents=True)
                break
            except FileExistsError:
                continue
        item = {"repo": repo, "lane": len(self.state["queue"]) + 1, "stamp": stamp,
                "thread": thread, "status": "queued"}
        if parent:
            item["parent"] = parent
            prior = next(i for i in self.state["queue"] if i["stamp"] == parent)
            item["verdict_check"] = prior.get("verdict_check", {"priority": 2})
        self.state["queue"].append(item)
        return item

    def siblings(self, item):
        return [i for i in self.state["queue"] if i["repo"] == item["repo"] and i is not item]

    def write(self):
        if self.budget:
            self.state["budget"] = self.budget.snapshot(self.state["queue"])
        self.state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save(self.directory / "batch.json", self.state)

    def verdict_path(self, item):
        suffix = "" if item["thread"] == 1 else f'.t{item["thread"]}'
        return ROOT / "batch-verdicts" / f'{item["repo"]}{suffix}.json'

    def launch(self, item):
        run = ROOT / "runs" / item["stamp"]
        peers = [i["plan"] for i in self.siblings(item) if i.get("plan") and i["thread"] < item["thread"]]
        save(run / "peer-plans.json", peers)
        env = {**os.environ, "ADVANCE_ROADMAP_LANE": str(item["lane"]),
               "ADVANCE_ROADMAP_STAMP": item["stamp"], "ADVANCE_ROADMAP_TARGET_REPO": item["repo"],
               "ADVANCE_ROADMAP_THREAD": str(item["thread"]),
               "ADVANCE_ROADMAP_THREADS_PER_REPO": str(self.threads),
               "ADVANCE_ROADMAP_PEER_PLANS": str(run / "peer-plans.json"),
               "ADVANCE_ROADMAP_BATCH_PID": str(os.getpid()),
               "ADVANCE_ROADMAP_BATCH_SNAPSHOT": str(self.directory / "linear-snapshot.json"),
               "ADVANCE_ROADMAP_INFLIGHT_STAMPS": " ".join(i["stamp"] for i in self.state["queue"]),
               "ADVANCE_ROADMAP_LAST_VERDICT": str(self.verdict_path(item))}
        allocation = item.get("allocation") or {}
        env["ADVANCE_ROADMAP_BUDGET_PLANNER"] = allocation.get("planner") or "none"
        if item["thread"] == 1 and item.get("verdict_check", {}).get("result") == "pending":
            env["ADVANCE_ROADMAP_BUDGET_PENDING"] = "1"
        # Other repos are never this planner's fallback, even before they have a worker. Threads of
        # its own repo are not busy: they are the siblings it is planning around.
        env["ADVANCE_ROADMAP_BUSY_REPOS"] = " ".join(
            sorted({i["repo"] for i in self.state["queue"] if i["repo"] != item["repo"]}))
        with open(ROOT / "logs" / f'batch-lane-{item["stamp"]}.err', "w") as err:
            proc = subprocess.Popen(["/bin/zsh", str(shared.RUN_SH)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=err, start_new_session=True)
        self.procs[item["stamp"]] = proc
        if self.budget:
            agent_budget.transfer(item.get("budget_leases", []), proc.pid)
        item.update(status="planning", pid=proc.pid, started=time.time())
        shared.log(f'planning {item["repo"]} thread {item["thread"]} [{item["stamp"]}]')

    def poll(self):
        for item in self.state["queue"]:
            proc = self.procs.get(item["stamp"])
            if not proc or item["status"] in {"finished", "deferred"}:
                continue
            if proc.poll() is not None:
                row = shared.run_records({item["stamp"]}).get(item["stamp"], {})
                item.update(status="finished", outcome=row.get("outcome", "unrecorded"),
                            pr_url=row.get("pr_url"), exit_code=proc.returncode, detail=row.get("detail"))
                if self.budget:
                    self.budget.release(item)
                if row.get("quota"):
                    item["quota"] = row["quota"]  # who ran out, when it resets, the CLI's words
                continue
            run = ROOT / "runs" / item["stamp"]
            if item["status"] == "planning" and (run / "batch-ready").exists():
                result = shared.read_json(run / "orchestrator-result.json") or {}
                item.update(status="ready", action=result.get("action"),
                            linear_id=result.get("linear_id"), item=result.get("item"),
                            plan=plan_of(result, item))
                if self.budget:
                    self.budget.planned(item)
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
                if self.budget:
                    self.budget.release(item)

    def spawn_threads(self):
        """Once a thread has planned a feature, queue the repo's next thread (up to --threads).

        Code-health passes and resumes never grow a second thread: the first means the repo has no
        feature work left, the second is finishing a parked branch nobody should plan around.
        """
        for item in list(self.state["queue"]):
            plan = item.get("plan")
            if (item["status"] != "ready" or item.get("spawned") or not plan
                    or plan["action"] != "dispatch_worker" or plan["work_kind"] != "feature"):
                continue
            item["spawned"] = True
            count = len(self.siblings(item)) + 1
            if count < self.threads:
                self.reserve(item["repo"], count + 1, parent=item["stamp"])

    def plan_ruling(self, items):
        """The approver's result for one repo's ready plans; a failure defers every extra thread."""
        repo = items[0]["repo"]
        request = {"repo": repo, "plans": [i["plan"] for i in items]}
        req = self.directory / f"approval-{repo}-request.json"
        out = self.directory / f"approval-{repo}.json"
        save(req, request)
        reason = "the plan approver did not run"
        approval_leases = []
        try:
            doc = Path(os.environ.get("ADVANCE_ROADMAP_SKILL_DIR",
                                      str(Path.home() / ".claude/skills/advance-roadmap"))) / "PLAN_APPROVAL.md"
            provider = None
            for candidate in self.budget.planners:
                approval_leases = agent_budget.reserve_bundle(
                    [{"provider": candidate, "role": "planner"}],
                    reading_by_provider=self.budget.readings, models=self.budget.models,
                    label=f"roadmap:approve:{repo}", ttl=APPROVAL_TIMEOUT_S)
                if approval_leases is not None:
                    provider = candidate
                    break
            if provider is None:
                raise RuntimeError("no shared headroom for the plan approver")
            proc = subprocess.run([sys.executable, str(shared.LIB / "planapproval.py"), "--root", str(ROOT),
                                  "approve", "--request", str(req), "--out", str(out), "--doc", str(doc),
                                  "--code-dir", str(shared.code_dir())], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=APPROVAL_TIMEOUT_S,
                                  env={**os.environ, "ADVANCE_ROADMAP_BUDGET_PLANNER": provider})
            result = shared.read_json(out) if proc.returncode == 0 else None
            if result and result.get("ok"):
                if result.get("error"):
                    shared.log(f"{repo}: approver unavailable ({result['error']}); extra threads wait")
                return result
            reason = f"the plan approver failed (exit {proc.returncode}): {(proc.stderr or '').strip()[-300:]}"
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            reason = f"the plan approver could not run: {error}"
        finally:
            if approval_leases:
                agent_budget.release_many([l["id"] for l in approval_leases])
        shared.log(f"{repo}: {reason}")
        return {"ok": True, "repo": repo, "standing_conflicts": [], "error": reason,
                "decisions": [{"stamp": i["stamp"], "thread": i["thread"],
                               "verdict": "approve" if n == 0 else "defer",
                               "reason": "first thread always proceeds" if n == 0 else reason,
                               "after": None} for n, i in enumerate(items)]}

    def approve_threads(self):
        """Rule on every repo that planned more than one thread, before any worker starts."""
        groups = {}
        for item in self.state["queue"]:
            if item["status"] == "ready" and item.get("action") == "dispatch_worker" and item.get("plan"):
                groups.setdefault(item["repo"], []).append(item)
        pending = {repo: sorted(items, key=lambda i: i["thread"])
                   for repo, items in sorted(groups.items()) if len(items) > 1}
        if not pending:
            return
        # One model call per repo, side by side: thread 1 of every repo waits for the slowest
        # ruling rather than the sum of them.
        with ThreadPoolExecutor(max_workers=max(1, min(self.limit, len(pending)))) as pool:
            rulings = dict(zip(pending, pool.map(self.plan_ruling, pending.values())))
        for repo, items in pending.items():
            result = rulings[repo]
            decisions = {d.get("stamp"): d for d in result.get("decisions", [])}
            for item in items:
                d = decisions.get(item["stamp"]) or {"verdict": "defer", "reason": "no ruling"}
                verdict = d.get("verdict") if item is not items[0] else "approve"
                item.update(ruling=verdict, ruling_reason=d.get("reason"))
                note = {"repo": repo, "thread": item["thread"], "verdict": verdict,
                        "reason": d.get("reason"), "after": d.get("after"),
                        "acknowledged_conflicts": d.get("acknowledged_conflicts") or [],
                        "standing_conflicts": result.get("standing_conflicts") or [],
                        "siblings": [{"stamp": o["stamp"], "thread": o["thread"], "item": o.get("item"),
                                      "branch": o["plan"].get("branch"),
                                      "touchpoints": o["plan"].get("touchpoints"),
                                      "verdict": (decisions.get(o["stamp"]) or {}).get("verdict")}
                                     for o in items if o is not item]}
                save(ROOT / "runs" / item["stamp"] / "batch-approval.json", note)
                if verdict == "defer" and (result.get("quota") or {}).get("provider"):
                    # The approver ran out of quota: this thread's skipped-quota row says who and until when.
                    save(ROOT / "runs" / item["stamp"] / "quota-gates.json", result["quota"])
                if verdict == "defer":
                    self.cancel(item, f'plan approval: {d.get("reason") or "deferred"}')
                elif verdict == "serialize":
                    item["after"] = d.get("after")
                shared.log(f'{repo} thread {item["thread"]}: {verdict}'
                           + (f' after {item["after"]}' if verdict == "serialize" else "")
                           + (f' ({d.get("reason")})' if d.get("reason") else ""))

    def cancel(self, item, reason):
        if getattr(self, "budget", None):
            self.budget.release(item)
        (ROOT / "runs" / item["stamp"] / "batch-cancel").write_text(reason + "\n")
        item.update(status="canceling", reason=reason)

    def releasable(self, item, chosen):
        """A serialized thread waits for the thread it follows; two threads of a repo start apart."""
        if item.get("after"):
            prior = next((i for i in self.state["queue"] if i["stamp"] == item["after"]), None)
            if prior and prior["status"] != "finished":
                return False
        for other in self.siblings(item):
            if other in chosen:
                return False
            if other.get("released_at") and time.time() - other["released_at"] < RELEASE_GAP_S \
                    and other["status"] in {"running", "canceling"}:
                return False
        return True

    def defer_ready(self, reason, quota=None):
        """Cancel every saved dispatch and resume plan; `quota` (usage.py quota-record output) goes
        beside each one so its skipped-quota record says which provider ran out and when it resets.
        A no-work result is a verdict, not a plan to implement: it is never canceled here
        (defer_bookkeeping decides what becomes of it)."""
        for item in self.state["queue"]:
            if item["status"] == "ready" and item.get("action") in WORK_ACTIONS:
                if quota:
                    (ROOT / "runs" / item["stamp"] / "quota-gates.json").write_text(quota)
                self.cancel(item, reason)

    def defer_bookkeeping(self, reason, quota=None):
        """No worker can post a no-work result's bookkeeping now. Cancel it so its lane ends, but
        run.sh keeps the verdict (marked: its blockers were never posted) rather than losing the triage."""
        for item in self.state["queue"]:
            if item["status"] == "ready" and item.get("action") not in WORK_ACTIONS:
                if quota:
                    (ROOT / "runs" / item["stamp"] / "quota-gates.json").write_text(quota)
                self.cancel(item, reason)

    def skip_planning(self, reason, role="worker"):
        """Plan nothing. Every repo gets a skipped-quota row saying why; carried verdicts and
        pending plans stay as they are, so the next batch that has headroom starts from them."""
        quota = self.quota_record(role)
        for item in self.state["queue"]:
            if item["status"] != "queued":
                continue
            item["budget_hold"] = reason
            if self.budget:
                self.budget.release(item)
            gates = ROOT / "runs" / item["stamp"] / "quota-gates.json"
            if quota:
                gates.write_text(quota)
            shared.py("runrecord.py", "--root", str(ROOT), "append", "--stamp", item["stamp"],
                      "--status", "skipped-quota", "--repo", item["repo"], "--detail", f"planning skipped: {reason}",
                      *(["--quota-gates", str(gates)] if quota else []), "--lane", str(item["lane"]))
            row = shared.run_records({item["stamp"]}).get(item["stamp"], {})
            item.update(status="finished", outcome="skipped-quota", detail=row.get("detail") or reason)
            if row.get("quota"):
                item["quota"] = row["quota"]
        shared.log(f"planning skipped: {reason}")

    def quota_record(self, role="worker"):
        try:
            out = shared.py("usage.py", "--root", str(ROOT), "quota-record", "--role", role, timeout=120)
            if out.returncode == 0 and isinstance(json.loads(out.stdout), dict):
                return out.stdout
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        # quota-record refreshes first; the gates the wave just read are still saved.
        try:
            return json.dumps(usage.saved_quota_record(ROOT, role))
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def run(self):
        self.budget = RoadmapBudget()
        decision = self.budget.review_strategy
        if decision.get("reason"):
            self.state["review_fallback"] = f'review fallback: cross-provider ({decision["reason"]})'
        for item in self.state["queue"]:
            item["verdict_check"] = repo_priority(item["repo"], self.directory / "linear-snapshot.json")
        self.state["queue"].sort(key=lambda i: i["verdict_check"]["priority"])
        self.write()
        ok, reason, blocking_role = implementation_headroom(self.budget)
        if not ok:
            self.state["phase"] = "skipped"
            self.state["skipped_reason"] = reason
            self.skip_planning(reason, blocking_role)
            self.state["quota_paused"] = quota_paused(self.state)
            self.write()
            self.post_summary()
            return 0
        self.state["phase"] = "planning"
        # A ready lane is only a shell waiting on disk: free its planning slot so the
        # whole finite queue can reach the barrier even when repos outnumber the cap.
        while any(i["status"] in {"queued", "planning"} for i in self.state["queue"]):
            self.poll()
            self.spawn_threads()
            self.budget.refresh()
            slots = self.limit - sum(i["status"] == "planning" for i in self.state["queue"])
            launched = 0
            for item in sorted((i for i in self.state["queue"] if i["status"] == "queued"),
                               key=lambda i: i.get("verdict_check", {}).get("priority", 2)):
                if launched >= slots:
                    break
                if self.budget.admit(item):
                    item.pop("budget_hold", None)
                    self.launch(item)
                    launched += 1
                else:
                    item["budget_hold"] = "planner plus expected implementation does not fit shared headroom"
            self.state["lanes"] = sum(i["status"] == "planning" for i in self.state["queue"])
            if not launched and not any(i["status"] == "planning" for i in self.state["queue"]):
                self.skip_planning("planner plus expected implementation does not fit shared headroom", "orchestrator")
            self.write()
            time.sleep(POLL_S)
        self.state["phase"] = "approving"
        self.write()
        self.budget.refresh()
        self.approve_threads()
        self.state["phase"] = "implementing"
        self.write()
        while any(i["status"] in {"ready", "running", "canceling"} for i in self.state["queue"]):
            self.poll()
            slots = self.limit - sum(i["status"] in {"running", "canceling"}
                                    for i in self.state["queue"])
            ready, chosen = sorted(
                (i for i in self.state["queue"] if i["status"] == "ready"),
                key=lambda i: (i.get("action") not in WORK_ACTIONS,
                               i.get("verdict_check", {}).get("priority", 2), i["thread"])), []
            for item in ready:
                if len(chosen) < slots and self.releasable(item, chosen):
                    chosen.append(item)
            if chosen:
                # Convert expected costs to full costs atomically against a fresh
                # reading. Other jobs' and interactive reservations remain charged.
                self.budget.refresh()
                held = []
                for item in chosen:
                    allocation = self.budget.choose(item=item)
                    if allocation is None:
                        reason = "worker plus required review no longer fits shared headroom; saved plan waits for the next daily batch"
                        item["budget_hold"] = reason
                        held.append(item)
                        continue
                    run = ROOT / "runs" / item["stamp"]
                    save(run / "batch-budget.json", {"worker": allocation["worker"],
                                                    "reviewers": allocation["reviewers"],
                                                    "review_strategy": allocation.get("review_strategy"),
                                                    "review_fallback_reason": allocation.get("review_fallback_reason")})
                    (run / "batch-release").touch()
                    item.pop("budget_hold", None)
                    item.update(status="running", released_at=time.time())
                    shared.log(f'released {item["repo"]} thread {item["thread"]} {item.get("linear_id") or ""}')
                if held and not any(i["status"] in {"running", "canceling"} for i in self.state["queue"]):
                    # Running jobs can free headroom: wait for their completion.
                    # Otherwise free the lowest priority reservation across the
                    # whole barrier, including jobs outside this wave's lane cap.
                    # A one-lane candidate slice contains the highest priority
                    # job, whose expectation must not be sacrificed first.
                    reserved = [i for i in ready if i.get("budget_leases")]
                    item = reserved[-1] if reserved else held[-1]
                    item.setdefault("budget_hold", "shared headroom is needed for higher priority plans; "
                                    "saved plan waits for the next daily batch")
                    quota = self.quota_record()
                    if quota:
                        (ROOT / "runs" / item["stamp"] / "quota-gates.json").write_text(quota)
                    self.cancel(item, item["budget_hold"])
            self.write()
            time.sleep(POLL_S)
        self.state["phase"] = "done"
        self.state["quota_paused"] = quota_paused(self.state)
        self.write()
        self.post_summary()
        return 0

    def post_summary(self):
        """The batch's only Slack post, sent once every thread has stopped (or the batch failed)."""
        if os.environ.get("ADVANCE_ROADMAP_BATCH_SUMMARY", "1") != "1":
            return False
        posted = post_slack_summary(self.directory)
        self.state["summary_posted"] = posted
        self.write()
        return posted


def quota_paused(state):
    """One entry per provider whose usage ran out during the batch: the repos it paused and the
    latest reset seen. A pause is not a failure; this is what the summary says instead. A lane
    skipped because an earlier lane's limit already gated its provider counts here too, and so
    does every provider a row lists as out, not only the one it names first. A row lists every
    provider in its role; one that was still under its gate is not out."""
    out = {}
    for item in state.get("queue", []):
        q = item.get("quota")
        if item.get("outcome") not in QUOTA_OUTCOMES or not q:
            continue
        listed = [p for p in q.get("providers") or []
                  if isinstance(p, dict) and p.get("state", "limited") in ("over", "limited")]
        for p in listed or [q]:
            cause = (p.get("cause") or p.get("reason")) if listed else q.get("cause")
            entry = out.setdefault(p.get("provider") or "?", {
                "provider": p.get("provider"), "role": q.get("role"), "repos": [],
                "reset_epoch": None, "reset_at": None, "cause": cause})
            if item["repo"] not in entry["repos"]:
                entry["repos"].append(item["repo"])
            if p.get("reset_epoch") and (entry["reset_epoch"] or 0) < p["reset_epoch"]:
                entry.update(reset_epoch=p["reset_epoch"], reset_at=p.get("reset_at"))
    return list(out.values())


def quota_line(state):
    parts = [f'{q["provider"] or "?"} out of quota for {len(q["repos"])} repo(s)'
             + (f', resets {q["reset_at"]}' if q.get("reset_at") else "")
             for q in state.get("quota_paused") or []]
    text = ("; ".join(parts) + ".") if parts else ""
    return "\n".join(filter(None, (state.get("review_fallback"), text, budget_hold_line(state))))


def budget_hold_line(state):
    held = (state.get("budget") or {}).get("held") or []
    return ("Budget hold: " + "; ".join(f'{i["repo"]}: {i["reason"]}' for i in held) + ".") if held else ""


def summary_note_line(state):
    return "\n".join(filter(None, (state.get("review_fallback"), budget_hold_line(state))))


def slackagent_script():
    code = shared.code_dir() or ""
    base = Path(os.environ.get("ADVANCE_ROADMAP_SLACKAGENT_DIR", str(Path(code) / "slackagent")))
    return base / "scripts" / "post-roadmap-summary.mjs"


def webhook_url():
    url = os.environ.get("ADVANCE_ROADMAP_SLACK_WEBHOOK") or os.environ.get("SLACK_CCUSAGE_WEBHOOK_URL")
    if url:
        return url
    secrets = Path(os.environ.get("ADVANCE_ROADMAP_SECRETS", str(Path.home() / ".claude/automations/secrets.env")))
    try:
        for line in secrets.read_text().splitlines():
            match = re.match(r"\s*(?:export\s+)?(ADVANCE_ROADMAP_SLACK_WEBHOOK|SLACK_CCUSAGE_WEBHOOK_URL)=(.*)", line)
            if match:
                return match.group(2).strip().strip("'\"")
    except OSError:
        pass
    return ""


def post_slack_summary(directory):
    """Post one summary for the whole batch through slackagent; webhook text if that fails."""
    script = slackagent_script()
    node = shutil.which("node")
    args = ["--batch", str(directory / "batch.json"), "--runs", str(ROOT / "runs.jsonl")]
    text = ""
    state = shared.read_json(directory / "batch.json") or {}
    hold_line = summary_note_line(state)
    command = [node, str(script)]
    if hold_line:
        command = [node, str(Path(__file__).resolve().parent / "lib/post_batch_summary.mjs"),
                   "--script", str(script), "--hold-line", hold_line]
    if node and script.exists():
        try:
            done = subprocess.run([*command, *args], capture_output=True, text=True,
                                  timeout=120, stdin=subprocess.DEVNULL)
            if done.returncode == 0:
                shared.log("posted the batch summary to Slack")
                return True
            shared.log(f"slackagent batch post failed: {(done.stderr or done.stdout).strip()[-300:]}")
            dry = subprocess.run([*command, *args, "--dry-run"], capture_output=True, text=True,
                                 timeout=60, stdin=subprocess.DEVNULL)
            text = dry.stdout.strip() if dry.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired) as error:
            shared.log(f"slackagent batch post could not run: {error}")
    else:
        shared.log("no slackagent post script; batch summary not posted through slackagent")
    url = webhook_url()
    if not url:
        return False
    text = text or " ".join(filter(None, [f'advance-roadmap batch {state.get("date")}: {state.get("phase")}.',
                                          quota_line(state)]))
    try:
        import urllib.request
        request = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15):
            shared.log("posted the batch summary through the webhook")
            return True
    except Exception as error:  # a Slack outage must never fail the batch
        shared.log(f"webhook batch post failed: {error}")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes", type=int, default=int(os.environ.get("ADVANCE_ROADMAP_LANES", "5")))
    parser.add_argument("--threads", type=int, default=int(os.environ.get(
        "ADVANCE_ROADMAP_THREADS_PER_REPO", str(DEFAULT_THREADS_PER_REPO))),
        help=f"most parallel threads of work in one repo (1-{MAX_THREADS_PER_REPO}); more than one "
             "needs the plan approver")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.lanes <= MAX_CONCURRENT_AGENTS:
        parser.error("--lanes must be between 1 and 5 agents")
    if not 1 <= args.threads <= MAX_THREADS_PER_REPO:
        parser.error(f"--threads must be between 1 and {MAX_THREADS_PER_REPO}")
    marker = Path.home() / "dotfiles/bin/is-personal-machine"
    if marker.exists() and subprocess.run([str(marker)]).returncode == 1:
        return 0
    code = shared.code_dir()
    if not code:
        return 0
    repos = candidates(Path(code))
    if args.dry_run:
        budget = RoadmapBudget(dry_run=True)
        queue = [{"repo": repo, "stamp": f"dry-{n}", "verdict_check": repo_priority(repo, dry_run=True)}
                 for n, repo in enumerate(repos)]
        queue.sort(key=lambda i: i["verdict_check"]["priority"])
        lanes = 0
        implementation_fits = budget.choose(commit=False) is not None
        for item in queue:
            if implementation_fits and lanes < args.lanes and budget.admit(item):
                lanes += 1
            else:
                item["budget_hold"] = ("lane cap" if lanes >= args.lanes else
                                       "shared headroom" if implementation_fits else
                                       "no worker plus required review fits shared headroom")
        print(json.dumps({"repos": repos, "concurrency": args.lanes, "lanes": lanes,
                          "verdict_checks": [{"repo": i["repo"], **i["verdict_check"]} for i in queue],
                          "budget": budget.snapshot(queue),
                          "schedule": "daily at 10:30 local time", "threads_per_repo": args.threads}, indent=2))
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "run.lock.f", "a") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            shared.log("another roadmap scheduler is running; no second batch")
            return 0
        directory = ROOT / "batches" / datetime.now().date().isoformat()
        if directory.exists():
            shared.log("today's batch already started; no automatic repeat")
            return 0
        (ROOT / "logs").mkdir(exist_ok=True)
        shared.LOG = ROOT / "logs" / f"batch-{directory.name}.log"
        # Preflight first, and the date directory only after it passes: that directory is what says
        # "today's batch already started", so an outage (Superset down, Linear unreachable) used to
        # use up the day. Nothing but the snapshot's temp file is written until here.
        snapshot = ROOT / f"linear-snapshot.preflight.{os.getpid()}.json"
        try:
            preflight(snapshot)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            shared.log(f"preflight failed, no batch started today: {error}")
            snapshot.unlink(missing_ok=True)
            notify_preflight_failure(directory.name, str(error))
            return 1
        directory.parent.mkdir(exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            snapshot.unlink(missing_ok=True)
            shared.log("today's batch already started; no automatic repeat")
            return 0
        os.replace(snapshot, directory / "linear-snapshot.json")
        (ROOT / "batch-verdicts").mkdir(exist_ok=True)
        batch = Batch(directory, repos, args.lanes, args.threads)
        batch.write()
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
            batch.post_summary()
            return 1
        finally:
            if batch.budget:
                for item in batch.state["queue"]:
                    # Model calls in detached lane processes can outlive this
                    # controller. Their PID-bound leases must follow those calls
                    # until process death or TTL, including unfinished planners.
                    if item["status"] not in {"planning", "running", "canceling"}:
                        batch.budget.release(item)
            shutil.rmtree(marker_dir, ignore_errors=True)


def preflight(snapshot):
    """Everything the batch needs from the outside world, checked before it claims the day.

    Raises RuntimeError (or a subprocess error) when Superset is down, the Linear snapshot cannot be
    taken or the usage readings cannot be refreshed. The snapshot lands at `snapshot`; the caller
    moves it into the batch directory, where it stays immutable for the day's planners."""
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
    result = shared.py("linearsnap.py", "--out", str(snapshot), timeout=180)
    if result.returncode or not snapshot.exists():
        raise RuntimeError("could not create the batch's Linear snapshot")
    try:
        snap = json.loads(snapshot.read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError("could not read the batch's Linear snapshot") from error
    if not isinstance(snap, dict) or snap.get("ok") is not True:
        raise RuntimeError("the batch's Linear snapshot is unavailable")
    if shared.py("usage.py", "--root", str(ROOT), "refresh", timeout=120).returncode:
        raise RuntimeError("could not read provider usage")


def notify_preflight_failure(date, error):
    """A preflight failure starts no batch, so there is no batch directory to summarize from: write
    a failed batch.json beside the logs and post the same summary a failed batch would."""
    if os.environ.get("ADVANCE_ROADMAP_BATCH_SUMMARY", "1") != "1":
        return False
    failed = ROOT / "preflight-failures" / f'{date}-{datetime.now():%H%M%S}'
    failed.mkdir(parents=True, exist_ok=True)
    save(failed / "batch.json", {"date": date, "phase": "failed", "error": error, "queue": [],
                                 "started_at": datetime.now().isoformat(timespec="seconds")})
    return post_slack_summary(failed)


if __name__ == "__main__":
    sys.exit(main())
