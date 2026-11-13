#!/usr/bin/env python3
"""One scheduled batch: resume carried work first, then plan and run new work.

Each repo starts with one thread. Once a repo's first thread has planned a feature, a second
planner for the same repo may be started (--threads, at most three per repo) and told what the
earlier threads will touch. Before any worker starts, a plan approver (lib/planapproval.py) rules
on every extra thread: run in parallel, run only after an earlier thread has merged, or wait for
the next scheduled batch. The first thread of a repo is never deferred or serialized by that ruling; it waits
at the barrier only for the slowest approval (rulings for different repos run side by side).

--lanes bounds both planning and implementation to between one and five threads at once. Ready
planners wait without using a model. No new-work refills or quota retries within a slot.
The two local-time slots are 10:30 and 15:30; each can start at most once. One Slack summary is posted when the batch ends; individual runs post nothing.
"""
import argparse
import copy
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
from datetime import datetime, timedelta
from pathlib import Path

import supervisor as shared

sys.path.insert(0, str(shared.HERE / "lib"))
import usage  # noqa: E402
import workqueue  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_budget  # noqa: E402

ROOT = shared.ROOT
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_BATCH_POLL_S", "1"))
PLAN_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_PLAN_TIMEOUT_S", "1800"))
MAX_CONCURRENT_AGENTS = 5
MAX_THREADS_PER_REPO = 3
DEFAULT_THREADS_PER_REPO = 1
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
    """Check initial headroom for the configured execution mode.

    Incremental execution checks a bounded implementation unit; legacy mode
    retains the complete-job and reviewer gates. Unknown readings remain
    limited by shared admission.
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
    if budget is not None and getattr(budget, 'incremental', False):
        if not workers:
            return False, 'no worker provider is under its quota gate', 'worker'
        if budget.choose(commit=False) is None:
            return False, 'no bounded implementation unit fits shared headroom', 'worker'
        return True, '', None
    if not workers:
        return False, "no worker provider is under its quota gate", "worker"
    if decision.get("strategy") == "cross-provider":
        reviewers = [name for name, gate in by_provider.items() if name in usage.ROUTED_PROVIDERS['reviewer']
                     and gate.get("state") not in OUT_STATES]
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


def scheduled_slot(now):
    """The most recent scheduled local wall-clock slot, including wake-up catch-up."""
    morning = now.replace(hour=10, minute=30, second=0, microsecond=0)
    afternoon = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= afternoon:
        return afternoon
    if now >= morning:
        return morning
    return afternoon - timedelta(days=1)


def pending_action(repo):
    pending = Path(shared.code_dir()) / ".advance-roadmap"
    for path in sorted(pending.glob(f"pending-plan-{repo}*.json")):
        if path.name != f"pending-plan-{repo}.json" and not path.name.startswith(f"pending-plan-{repo}.t"):
            continue
        saved = shared.read_json(path) or {}
        action = (saved.get("orchestrator") or saved).get("action")
        if action in WORK_ACTIONS:
            return action
    return None


def repo_priority(repo, snapshot=None, dry_run=False):
    """Inspect carried state without changing it; run.sh owns the actual skip/reuse.

    Check a temporary copy because verdict.check may discard a contaminated
    verdict. A dry run uses saved snapshots only and never calls Linear/GitHub.
    """
    code = Path(shared.code_dir())
    action = pending_action(repo)
    if action:
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


def worker_configuration(provider):
    prefix = provider.upper() + '_WORKER_'
    try:
        defaults = (shared.HERE / 'lib/models.sh').read_text()
    except OSError:
        defaults = ''
    values = {}
    for field in ('MODEL', 'EFFORT'):
        match = re.search(r'^' + prefix + field + r'_DEFAULT=([^\s#]+)', defaults, re.M)
        values[field.lower()] = os.environ.get('ADVANCE_ROADMAP_' + prefix + field) or (
            match.group(1) if match else None)
    return values


class RoadmapBudget:
    """Batch jobs transact on exactly the same leases as interactive fan-out."""
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.incremental = os.environ.get('ADVANCE_ROADMAP_INCREMENTAL', '1') == '1'
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
        self.workers = [p for p in options.get("workers", []) if p in usage.ROUTED_PROVIDERS["worker"]]
        self.planners = [p for p in options.get("planners", []) if p in ("codex", "claude")]
        self.gates = {g["provider"]: g for g in options.get("reviewer_gates", []) if isinstance(g, dict)}
        self.review_strategy = options.get("review_strategy") or usage.effective_review_strategy(
            os.environ.get("ADVANCE_ROADMAP_REVIEW_STRATEGY", "cursor-then-codex"),
            list(self.gates.values()))
        self.readings = {p: agent_budget.readings(p, root=ROOT, probe=not self.dry_run and p == "claude")
                         for p in agent_budget.PROVIDERS}
        if os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
            now = time.time()
            def fresh(provider):
                reading = self.readings[provider]
                observed = reading.get('observed_at')
                return (not reading.get('error') and isinstance(observed, (int, float)) and 0 <= now - observed <= 300
                        and bool(reading.get('windows'))
                        and all(isinstance(window.get('live'), (int, float))
                                for window in reading['windows'].values()))
            self.workers = [provider for provider in self.workers if fresh(provider)]
            self.planners = [provider for provider in self.planners if fresh(provider)]

    def review_decision(self):
        decision = getattr(self, "review_strategy", None) or usage.effective_review_strategy(
            os.environ.get("ADVANCE_ROADMAP_REVIEW_STRATEGY", "cursor-then-codex"),
            list(self.gates.values()))
        if decision.get('strategy') == 'cursor-then-codex':
            return usage.effective_review_strategy('cursor-then-codex', list(self.gates.values()))
        return decision

    def routes(self):
        decision = self.review_decision()
        for worker in self.workers:
            if worker not in usage.ROUTED_PROVIDERS["worker"]:
                continue
            if decision.get("strategy") == "cross-provider":
                order = {"codex": ["claude"], "claude": ["codex"]}[worker]
                reviews = [[p] for p in order if self.gates.get(p, {}).get("state") not in OUT_STATES]
            else:
                reviews = [["cursor", "codex"]] if all(
                    self.gates.get(p, {}).get("state") not in OUT_STATES for p in ("cursor", "codex")) else []
            for reviewers in reviews:
                yield worker, reviewers

    def choose(self, *, item=None, planner=None, scale=1, commit=True):
        if os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
            phase = (item or {}).get('work_phase') or 'implementing'
            decision = self.review_decision()
            for worker in self.workers:
                if worker not in ('codex', 'claude'):
                    continue
                reviewers = []
                if phase in ('reviewing', 'shipping'):
                    if decision.get('strategy') == 'cross-provider':
                        reviewers = next(([p] for p in ({'codex': ['claude'],
                            'claude': ['codex']}[worker])
                            if self.gates.get(p, {}).get('state') not in OUT_STATES), None)
                    else:
                        reviewers = (['cursor', 'codex'] if all(self.gates.get(p, {}).get('state')
                                     not in OUT_STATES for p in ('cursor', 'codex')) else None)
                    if reviewers is None:
                        continue
                allocation = {'worker': worker, 'reviewers': reviewers, 'planner': planner,
                              'phase': phase, 'requests': [], 'leases': [],
                              'baseline': self.readings[worker], 'live_threshold': True,
                              'review_strategy': decision.get('strategy'),
                              'review_fallback_reason': decision.get('reason')}
                if item is not None and commit:
                    item.update(budget_leases=[], allocation=allocation)
                return allocation
            return None
        if getattr(self, 'incremental', False):
            return self.choose_incremental(item=item, planner=planner, scale=scale, commit=commit,
                                           override=bool((item or {}).get('job_override')))
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

    def prepare_override(self):
        """Refresh once; prepare a job-scoped ceiling without changing shared gates."""
        try:
            result = shared.py('usage.py', '--root', str(ROOT), 'refresh', timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode:
            return False
        state = usage.load(ROOT)
        gates = {g['provider']: g for g in usage.role_gates(state, 'worker')}
        readings = {p: agent_budget.readings(p, root=ROOT, probe=p == 'claude')
                    for p in ('codex', 'claude')}
        now = time.time()
        self.override_workers = []
        self.override_readings = copy.deepcopy(self.readings)
        for provider, reading in readings.items():
            gate = gates.get(provider, {})
            if gate.get('state') == 'limited' or 'rate limit reached' in (gate.get('reason') or '').lower():
                continue  # an actual provider limit is not an estimate
            if reading.get('error') or not isinstance(reading.get('observed_at'), (int, float)) or not 0 <= now - reading['observed_at'] <= 60:
                continue
            if not reading.get('windows') or any(not isinstance(w.get('live'), (int, float))
                                                 for w in reading['windows'].values()):
                continue
            self.override_workers.append(provider)
            self.override_readings[provider] = copy.deepcopy(reading)
        return bool(self.override_workers)

    def choose_incremental(self, *, item=None, planner=None, scale=1, commit=True, override=False):
        phase = (item or {}).get('work_phase') or 'implementing'
        if phase == 'ready':
            phase = 'implementing'
        decision = self.review_decision()
        workers = getattr(self, 'override_workers', []) if override else self.workers
        base_readings = getattr(self, 'override_readings', {}) if override else self.readings
        tried = False
        for worker in workers:
            # Cursor has no bounded streaming continuation adapter yet.
            if worker not in ('codex', 'claude'):
                continue
            readings = copy.deepcopy(base_readings) if override else base_readings
            if override:
                for window in readings[worker]['windows'].values():
                    window['cap'] = 100
            config = worker_configuration(worker)
            cohorts = [c for c in self.models[worker].get('worker_cohorts', [])
                       if c.get('model') == config['model'] and c.get('effort') == config['effort']
                       and c.get('phase') == phase]
            mature = [c for c in cohorts if c.get('sample_count', 0) >= 5
                      and isinstance(c.get('newest_at'), (int, float))
                      and 0 <= time.time() - c['newest_at'] <= 90 * 86400
                      and isinstance(c.get('tokens_p80'), (int, float))]
            models = self.models
            if {'fresh', 'native', 'fallback'} <= {c['continuation_mode'] for c in mature}:
                # Admission happens before workspace validation; use the most
                # expensive observed mode rather than assuming native resume.
                tokens = max(c['tokens_p80'] for c in mature)
                models = copy.deepcopy(self.models)
                role = models[worker]['roles']['worker']
                role['tokens_p80'], role['source'] = tokens, 'phase-mode-cohorts'
                role['pct'] = {name: max(.1, tokens * factor)
                               for name, factor in models[worker]['factor'].items()}
            worker_cost = models[worker]['roles']['worker']['pct'].get('five_hour', 0)
            phase_cost = worker_cost
            phase_estimate_source = 'worker'
            if phase in ('reviewing', 'shipping'):
                review_proxy = models[worker]['roles']['reviewer']['pct'].get('five_hour', 0)
                if review_proxy > 0:
                    phase_cost = min(worker_cost, review_proxy)
                    phase_estimate_source = 'reviewer-proxy'
            chunk_scale = min(1, 10 / worker_cost, phase_cost / worker_cost) if worker_cost > 0 else 1
            reviews = [[]]
            if phase in ('reviewing', 'shipping'):
                if decision.get('strategy') == 'cross-provider':
                    order = {'codex': ['claude'], 'claude': ['codex']}[worker]
                    reviews = [[p] for p in order if self.gates.get(p, {}).get('state') not in OUT_STATES]
                else:
                    reviews = [['cursor', 'codex']] if all(
                        self.gates.get(p, {}).get('state') not in OUT_STATES for p in ('cursor', 'codex')) else []
            for reviewers in reviews:
                tried = True
                requests = [{'provider': worker, 'role': 'worker', 'scale': chunk_scale * scale},
                            *[{'provider': p, 'role': 'reviewer', 'scale': scale} for p in reviewers]]
                if planner:
                    requests.append({'provider': planner, 'role': 'planner'})
                if override and any(readings[p].get('error')
                                    or not isinstance(readings[p].get('observed_at'), (int, float))
                                    or not 0 <= time.time() - readings[p]['observed_at'] <= 60
                                    or any(not isinstance(w.get('live'), (int, float))
                                           for w in readings[p].get('windows', {}).values())
                                    for p in {r['provider'] for r in requests}):
                    continue
                diagnostics = []
                leases = agent_budget.reserve_bundle(requests,
                    replace_ids=(item or {}).get('budget_leases', []),
                    reading_by_provider=readings, models=models,
                    pid=(item or {}).get('pid'),
                    label=f'roadmap:{item["stamp"]}' if item else 'roadmap:forecast',
                    ttl=PLAN_TIMEOUT_S + APPROVAL_TIMEOUT_S if planner else None,
                    commit=commit and not self.dry_run, extra_leases=self.forecast,
                    diagnostics=diagnostics)
                if item is not None and commit and not self.dry_run:
                    run = ROOT / 'runs' / item['stamp']
                    if run.is_dir():
                        role = models[worker]['roles']['worker']
                        event = {'at': time.time(), 'stamp': item['stamp'], 'repo': item['repo'],
                                 'phase': phase, 'worker': worker, 'reviewers': reviewers,
                                 **config,
                                 'planner': planner, 'chunk_scale': chunk_scale * scale,
                                 'estimate': {'tokens_p80': role.get('tokens_p80'),
                                              'sample_count': role.get('sample_count'),
                                              'source': role.get('source'),
                                              'phase_estimate_source': phase_estimate_source,
                                              'phase_reference_five_hour_pct': phase_cost,
                                              'reviewer_proxy_samples': self.models[worker]['roles']['reviewer'].get('sample_count'),
                                              'factor_source': self.models[worker].get('factor_source'),
                                              'sample_quality': self.models[worker].get('sample_quality'),
                                              'quantile': .8, 'matching_cohorts': cohorts,
                                              'model_match': bool(cohorts),
                                              'historical_sample_age_seconds': None,
                                              'confidence': 'account-level calibration may include other sessions'},
                                 'decision': 'admitted' if leases is not None else 'forecast rejected',
                                 'job_override': override, 'capacity': diagnostics}
                        with (run / 'admission.jsonl').open('a') as log:
                            log.write(json.dumps(event) + '\n')
                if leases is None:
                    continue
                allocation = {'worker': worker, 'reviewers': reviewers, 'planner': planner,
                              'phase': phase, 'chunk_scale': chunk_scale, 'requests': requests,
                              'job_override': override, 'baseline': readings[worker],
                              'worker_estimate': models[worker] if models is not self.models else None,
                              'review_strategy': decision.get('strategy'),
                              'review_fallback_reason': decision.get('reason'), 'leases': leases}
                if item is not None and commit:
                    item.update(budget_leases=[lease['id'] for lease in leases], allocation=allocation)
                    if self.dry_run:
                        self.forecast.extend(leases)
                return allocation
        if not tried and item is not None and commit and not self.dry_run:
            run = ROOT / 'runs' / item['stamp']
            if run.is_dir():
                with (run / 'admission.jsonl').open('a') as log:
                    log.write(json.dumps({'at': time.time(), 'stamp': item['stamp'], 'repo': item['repo'],
                        'phase': phase, 'job_override': override, 'decision': 'no eligible worker/reviewer route',
                        'workers': workers, 'reviewer_gates': self.gates}) + '\n')
        return None

    def admit(self, item):
        # A saved assignment can proceed without a planner reservation. Later
        # threads inherit ordering metadata but plan fresh in run.sh.
        pending = (item.get("thread", 1) == 1
                   and item.get("verdict_check", {}).get("result") == "pending")
        for planner in ([*self.planners, None] if pending else self.planners):
            allocation = self.choose(item=item, planner=planner,
                                     scale=1 if pending or item.get("streaming") else self.rates.get(item["repo"], .3))
            if allocation is not None:
                return True
        return False

    def planned(self, item):
        allocation = item.get("allocation") or {}
        if allocation.get('live_threshold'):
            allocation['planner'] = None
            return
        requests = [r for r in allocation.get("requests", []) if r["role"] != "planner"]
        models = self.models
        if allocation.get('worker_estimate'):
            models = {**self.models, allocation['worker']: allocation['worker_estimate']}
        leases = agent_budget.reserve_bundle(requests, replace_ids=item.get("budget_leases", []),
            reading_by_provider=self.readings, models=models,
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


def budget_record(allocation, budget):
    worker = allocation['worker']
    return {'worker': worker, 'reviewers': allocation['reviewers'],
            'review_strategy': allocation.get('review_strategy'),
            'review_fallback_reason': allocation.get('review_fallback_reason'),
            'phase': allocation.get('phase'), 'chunk_scale': allocation.get('chunk_scale'),
            'baseline': allocation.get('baseline') or budget.readings.get(worker),
            'job_override': allocation.get('job_override', False),
            'live_threshold': allocation.get('live_threshold', False),
            'lease_ids': [lease['id'] for lease in allocation.get('leases', [])],
            'worker_reservation': next((lease.get('pct') for lease in allocation.get('leases', [])
                                        if lease.get('provider') == worker), {})}


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
        if getattr(self.budget, 'incremental', False):
            env['ADVANCE_ROADMAP_INCREMENTAL'] = '1'
        allocation = item.get("allocation") or {}
        env["ADVANCE_ROADMAP_BUDGET_PLANNER"] = allocation.get("planner") or "none"
        if item["thread"] == 1 and item.get("verdict_check", {}).get("result") == "pending":
            env["ADVANCE_ROADMAP_BUDGET_PENDING"] = "1"
        # Other repos are never this planner's fallback, even before they have a worker. Threads of
        # its own repo are not busy: they are the siblings it is planning around.
        env["ADVANCE_ROADMAP_BUSY_REPOS"] = " ".join(
            sorted({i["repo"] for i in self.state["queue"] if i["repo"] != item["repo"]}))
        with workqueue.Store(ROOT) as store:
            snapshot = workqueue.read(self.directory / 'linear-snapshot.json', {})
            store.set('eligibility_at_start:' + item['stamp'], workqueue.eligibility_fingerprint(
                item['repo'], shared.code_dir(), snapshot,
                shared.HERE.parents[1] / 'skills/advance-roadmap'))
            if snapshot.get('ok'):
                store.set('no_item_inputs_at_start:' + item['stamp'],
                          workqueue.no_item_fingerprint(item['repo'], snapshot))
        with open(ROOT / "logs" / f'batch-lane-{item["stamp"]}.err', "w") as err:
            proc = subprocess.Popen(["/bin/zsh", str(shared.RUN_SH)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=err, start_new_session=True)
        self.procs[item["stamp"]] = proc
        if self.budget:
            agent_budget.transfer(item.get("budget_leases", []), proc.pid)
        item.update(status="planning", pid=proc.pid, started=time.time())
        with workqueue.Store(ROOT) as store:
            store.attempt(item['stamp'], item['repo'], pid=proc.pid)
        shared.log(f'planning {item["repo"]} thread {item["thread"]} [{item["stamp"]}]')

    def try_idle_override(self):
        """At most one saved continuation, only after normal admission selects nothing."""
        if os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
            return False
        if (not getattr(self.budget, 'incremental', False) or self.state.get('normal_work_admitted')
                or self.state.get('idle_override_used')):
            return False
        candidates = [item for item in self.state['queue'] if item['status'] == 'queued'
                      and item.get('verdict_check', {}).get('result') == 'pending'
                      and (item.get('verdict_check', {}).get('action') == 'resume_worker'
                           or item.get('work_phase') not in (None, 'ready'))]
        if not candidates or not self.budget.prepare_override():
            return False
        for item in candidates:
            item['job_override'] = True
            if self.budget.choose(item=item, planner=None):
                self.state['idle_override_used'] = {'repo': item['repo'], 'stamp': item['stamp'],
                    'reason': 'no normal admission; one saved bounded continuation', 'ceiling': 100}
                item.pop('budget_hold', None)
                self.launch(item)
                return True
            item.pop('job_override', None)
        return False

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
                with workqueue.Store(ROOT) as store:
                    workqueue.capture(store, item['stamp'], item['repo'], phase=None, code=shared.code_dir())
                    if row:
                        store.finish(row, workqueue.read(ROOT / 'runs' / item['stamp'] / 'worker-result.json', {}))
                continue
            run = ROOT / "runs" / item["stamp"]
            if item["status"] == "planning" and (run / "batch-ready").exists():
                result = shared.read_json(run / "orchestrator-result.json") or {}
                item.update(status="ready", action=result.get("action"),
                            linear_id=result.get("linear_id"), item=result.get("item"),
                            plan=plan_of(result, item))
                if self.budget:
                    self.budget.planned(item)
                with workqueue.Store(ROOT) as store:
                    wid = workqueue.capture(store, item['stamp'], item['repo'], result, 'ready', shared.code_dir())
                    work = next((w for w in store.list(item['repo']) if w['id'] == wid), None)
                    if work:
                        item['work_phase'] = work['phase']
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

        Resumes never grow a second thread: they finish a parked branch nobody should plan around.
        """
        if self.state.get("recovery_first"):
            return
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

    def sync_review_fallback(self):
        """Keep the visible batch note aligned with the latest reservation decision."""
        decision = self.budget.review_strategy
        reason = decision.get("reason")
        if reason:
            self.state["review_fallback"] = f"review fallback: cross-provider ({reason})"
        else:
            self.state.pop("review_fallback", None)
        return decision

    def refresh_budget(self):
        if (os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1'
                and time.monotonic() - getattr(self, '_budget_refreshed_at', 0) < 120):
            return self.sync_review_fallback()
        self.budget.refresh()
        self._budget_refreshed_at = time.monotonic()
        return self.sync_review_fallback()

    def record_starvation(self):
        if not getattr(self.budget, 'incremental', False):
            return
        now = time.time()
        admitted = sum(bool(item.get('released_at')) for item in self.state['queue'])
        with workqueue.Store(ROOT) as store:
            prior = store.get('incremental_starvation', {})
            eligible = [work for work in store.list() if work['state'] not in ('done', 'human')
                        and work['not_before'] <= now]
            count = 0 if admitted else prior.get('consecutive_zero_admission', 0) + 1
            oldest = min((work['created'] for work in eligible), default=None)
            snapshot = self.budget.snapshot(self.state['queue'])
            headroom = {provider: {name: value.get('headroom') for name, value in view['windows'].items()}
                        for provider, view in snapshot['providers'].items()}
            exception = self.state.get('idle_override_used')
            if exception:
                item = next((i for i in self.state['queue'] if i['stamp'] == exception['stamp']), {})
                exception = {**exception, 'outcome': item.get('outcome') or item.get('status')}
            record = {'at': now, 'admitted': admitted, 'consecutive_zero_admission': count,
                      'oldest_eligible_age_seconds': round(now - oldest) if oldest is not None else None,
                      'unused_headroom': headroom, 'job_override': exception}
            store.set('incremental_starvation', record)
            self.state['starvation'] = record

    def run(self, streaming=False, summary=True):
        self.streaming = streaming
        self.summary_enabled = summary
        self.budget = RoadmapBudget()
        self._budget_refreshed_at = time.monotonic()
        self.sync_review_fallback()
        with workqueue.Store(ROOT) as store:
            workqueue.sync(store, shared.code_dir())
            self.limit = max(0, self.limit - workqueue.active_count(store))
            self.state['lanes_available'] = self.limit
            workqueue.wake_humans(store, workqueue.read(self.directory / 'linear-snapshot.json', {}))
            for item in self.state['queue']:
                hold = store.hold(item['repo'])
                eligible = [w for w in store.list(item['repo']) if w['state'] != 'done']
                if len(eligible) == 1:
                    item['work_phase'] = eligible[0]['phase']
                if hold and item['status'] == 'queued':
                    item.update(status='deferred', reason=hold['reason'], waiting_state=hold['state'],
                                not_before=hold['not_before'])
        for item in self.state["queue"]:
            item["verdict_check"] = repo_priority(item["repo"], self.directory / "linear-snapshot.json")
        self.state["queue"].sort(key=lambda i: i["verdict_check"]["priority"])
        if self.limit == 0:
            for item in self.state['queue']:
                if item['status'] == 'queued':
                    item.update(status='deferred', reason='surviving workers occupy the agent cap')
            self.state['phase'] = 'done'
            self.record_starvation()
            self.write()
            self.post_summary()
            return 0
        self.write()
        ok, reason, blocking_role = implementation_headroom(self.budget)
        can_try_override = (getattr(self.budget, 'incremental', False)
                            and any(i['status'] == 'queued' and i.get('verdict_check', {}).get('result') == 'pending'
                                    for i in self.state['queue']))
        if not ok and not can_try_override:
            self.state["phase"] = "skipped"
            self.state["skipped_reason"] = reason
            self.skip_planning(reason, blocking_role)
            self.state["quota_paused"] = quota_paused(self.state)
            self.record_starvation()
            self.write()
            self.post_summary()
            return 0
        carried = [i for i in self.state["queue"]
                   if (i["verdict_check"]["priority"] == 0 and i['status'] == 'queued')
                   or i.get("reason") == "previous worker is still running"]
        fresh = [i for i in self.state["queue"] if i not in carried and i["status"] == "queued"]
        if carried:
            self.state["recovery_first"] = True
            for item in fresh:
                item["status"] = "waiting-for-recovery"
            self.write()
            (self.run_stream if streaming else self.run_work)()
            for item in fresh:
                item["status"] = "queued"
            self.state["recovery_first"] = False
            self.write()
            if fresh:
                (self.run_stream if streaming else self.run_work)()
        else:
            (self.run_stream if streaming else self.run_work)()
        self.state["phase"] = "done"
        self.state["quota_paused"] = quota_paused(self.state)
        self.record_starvation()
        self.write()
        self.post_summary()
        return 0

    def run_stream(self):
        """Stream work under the shared cap, refilling yielded units in continuous mode."""
        if self.threads != 1:
            raise ValueError('streaming requires one thread per repo')
        self.state['phase'] = 'streaming'
        for item in self.state['queue']:
            item['streaming'] = True
        while any(i['status'] in {'queued', 'planning', 'ready', 'running', 'canceling'}
                  for i in self.state['queue']):
            self.poll()
            if os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
                held = (ROOT / 'session-hold.json').exists()
                if held:
                    for item in self.state['queue']:
                        if item['status'] == 'queued':
                            item.update(status='deferred', reason='session hold')
                        elif item['status'] == 'ready':
                            self.cancel(item, 'session hold')
                else:
                    for item in list(self.state['queue']):
                        if (item.get('status') != 'finished' or item.get('outcome') != 'yielded'
                                or item.get('continuation_queued') or not pending_action(item['repo'])):
                            continue
                        item['continuation_queued'] = True
                        with workqueue.Store(ROOT) as store:
                            hold = store.hold(item['repo'])
                        if hold:
                            item['continuation_hold'] = hold['reason']
                            continue
                        new = self.reserve(item['repo'], 1)
                        new['streaming'] = True
                        new['verdict_check'] = {'priority': 0, 'result': 'pending',
                                                'action': pending_action(item['repo'])}
                        with workqueue.Store(ROOT) as store:
                            work = [w for w in store.list(item['repo']) if w['state'] != 'done']
                            if len(work) == 1:
                                new['work_phase'] = work[0]['phase']
            self.refresh_budget()
            active = sum(i['status'] in {'planning', 'running', 'canceling'} for i in self.state['queue'])
            slots = max(0, self.limit - active)
            ready = sorted((i for i in self.state['queue'] if i['status'] == 'ready'),
                           key=lambda i: i.get('verdict_check', {}).get('priority', 2))
            released = 0
            for item in ready[:slots]:
                if item.get('job_override') and not self.budget.prepare_override():
                    item['budget_hold'] = 'job override requires fresh provider usage'
                    continue
                allocation = self.budget.choose(item=item)
                if allocation is None:
                    item['budget_hold'] = 'implementation and review need shared headroom'
                    continue
                run = ROOT / 'runs' / item['stamp']
                save(run / 'batch-budget.json', budget_record(allocation, self.budget))
                (run / 'batch-release').touch()
                item.update(status='running', released_at=time.time())
                item.pop('budget_hold', None)
                released += 1
            slots -= released
            launched = 0
            for item in [i for i in self.state['queue'] if i['status'] == 'queued'][:slots]:
                if self.budget.admit(item):
                    self.launch(item)
                    self.state['normal_work_admitted'] = True
                    launched += 1
                else:
                    item['budget_hold'] = 'complete job does not fit shared headroom'
            if not active and not released and not launched:
                if self.try_idle_override():
                    self.write()
                    continue
                # No model or worker can free capacity: save work and end this tick.
                for item in ready:
                    self.cancel(item, item.get('budget_hold') or 'waiting for shared headroom')
                self.skip_planning('complete job does not fit shared headroom', 'orchestrator')
            self.write()
            time.sleep(POLL_S)

    def run_work(self):
        """Plan, approve and finish the currently queued wave under the existing caps."""
        self.state["phase"] = "planning"
        # A ready lane is only a shell waiting on disk: free its planning slot so the
        # whole finite queue can reach the barrier even when repos outnumber the cap.
        while any(i["status"] in {"queued", "planning"} for i in self.state["queue"]):
            self.poll()
            self.spawn_threads()
            self.refresh_budget()
            slots = self.limit - sum(i["status"] == "planning" for i in self.state["queue"])
            launched = 0
            for item in sorted((i for i in self.state["queue"] if i["status"] == "queued"),
                               key=lambda i: i.get("verdict_check", {}).get("priority", 2)):
                if launched >= slots:
                    break
                if self.budget.admit(item):
                    item.pop("budget_hold", None)
                    self.launch(item)
                    self.state['normal_work_admitted'] = True
                    launched += 1
                else:
                    item["budget_hold"] = "planner plus expected implementation does not fit shared headroom"
            self.state["lanes"] = sum(i["status"] == "planning" for i in self.state["queue"])
            if not launched and not any(i["status"] == "planning" for i in self.state["queue"]):
                if not self.try_idle_override():
                    self.skip_planning("planner plus expected implementation does not fit shared headroom", "orchestrator")
            self.write()
            time.sleep(POLL_S)
        self.state["phase"] = "approving"
        self.write()
        self.refresh_budget()
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
                self.refresh_budget()
                held = []
                for item in chosen:
                    if item.get('job_override') and not self.budget.prepare_override():
                        item['budget_hold'] = 'job override requires fresh provider usage'
                        held.append(item)
                        continue
                    allocation = self.budget.choose(item=item)
                    if allocation is None:
                        reason = "worker plus required review no longer fits shared headroom; saved plan waits for the next scheduled batch"
                        item["budget_hold"] = reason
                        held.append(item)
                        continue
                    run = ROOT / "runs" / item["stamp"]
                    save(run / "batch-budget.json", budget_record(allocation, self.budget))
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
                                    "saved plan waits for the next scheduled batch")
                    quota = self.quota_record()
                    if quota:
                        (ROOT / "runs" / item["stamp"] / "quota-gates.json").write_text(quota)
                    self.cancel(item, item["budget_hold"])
            self.write()
            time.sleep(POLL_S)

    def post_summary(self):
        """The batch's only Slack post, sent once every thread has stopped (or the batch failed)."""
        if not getattr(self, 'summary_enabled', True) or os.environ.get("ADVANCE_ROADMAP_BATCH_SUMMARY", "1") != "1":
            return False
        # Dynamic attempts are folded into the existing scheduled post, never one post per tick.
        summarized = {}
        with workqueue.Store(ROOT) as store:
            seen = store.get('summary_seen', {})
            current = {i['stamp'] for i in self.state.get('queue', [])}
            for path in sorted((ROOT / 'batches').glob('dynamic-*/batch.json')):
                for item in workqueue.read(path, {}).get('queue', []):
                    signature = [item.get('status'), item.get('outcome')]
                    if item['stamp'] not in current and seen.get(item['stamp']) != signature:
                        self.state['queue'].append(item)
                        summarized[item['stamp']] = signature
            if summarized:
                self.state['quota_paused'] = quota_paused(self.state)
                self.write()
        posted = post_slack_summary(self.directory)
        if posted and summarized:
            with workqueue.Store(ROOT) as store:
                seen = store.get('summary_seen', {})
                seen.update(summarized)
                store.set('summary_seen', dict(sorted(seen.items())[-5000:]))
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
    command = [node, str(script)]
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
    state = shared.read_json(directory / 'batch.json') or {}
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
    if (ROOT / 'workqueue.sqlite3').exists():
        with workqueue.Store(ROOT) as store:
            if store.get('mode', 'shadow') in {'trial', 'dynamic'}:
                pilot = set(store.get('pilot', []))
                repos = [r for r in repos if r not in pilot]
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
                          "schedule": "10:30 and 15:30 local time; carried work first", "threads_per_repo": args.threads}, indent=2))
        return 0
    if (ROOT / 'session-hold.json').exists():
        shared.log('roadmap work is paused for the active implementation session; slot remains unclaimed')
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "run.lock.f", "a") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            shared.log("another roadmap scheduler is running; no second batch")
            return 0
        slot = scheduled_slot(datetime.now())
        directory = ROOT / "batches" / slot.strftime("%Y-%m-%d-%H%M")
        legacy_morning = ROOT / "batches" / slot.date().isoformat()
        if directory.exists() or slot.hour == 10 and legacy_morning.exists():
            shared.log("this scheduled batch already started; no automatic repeat")
            return 0
        (ROOT / "logs").mkdir(exist_ok=True)
        shared.LOG = ROOT / "logs" / f"batch-{directory.name}.log"
        # Claim a slot only after preflight succeeds. An outage must not consume its slot.
        snapshot = ROOT / f"linear-snapshot.preflight.{os.getpid()}.json"
        try:
            preflight(snapshot)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            shared.log(f"preflight failed, no batch started in this slot: {error}")
            snapshot.unlink(missing_ok=True)
            notify_preflight_failure(directory.name, str(error))
            return 1
        directory.parent.mkdir(exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            snapshot.unlink(missing_ok=True)
            shared.log("this scheduled batch already started; no automatic repeat")
            return 0
        os.replace(snapshot, directory / "linear-snapshot.json")
        (ROOT / "batch-verdicts").mkdir(exist_ok=True)
        batch = Batch(directory, repos, args.lanes, args.threads)
        batch.write()
        marker_dir = ROOT / "run.lock"
        shutil.rmtree(marker_dir, ignore_errors=True)  # stale marker; we hold the kernel lock
        marker_dir.mkdir()
        (marker_dir / "pid").write_text(str(os.getpid()) + "\n")
        # Detached workers can outlive their scheduler; hold new work behind them too.
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
    """Everything the batch needs from the outside world, checked before it claims the slot.

    Raises RuntimeError (or a subprocess error) when Superset is down, the Linear snapshot cannot be
    taken or the usage readings cannot be refreshed. The snapshot lands at `snapshot`; the caller
    moves it into the batch directory, where it stays immutable for the slot's planners."""
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
