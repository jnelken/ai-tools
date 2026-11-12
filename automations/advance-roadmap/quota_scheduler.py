#!/usr/bin/env python3
"""Resume up to two roadmap assignments as quota permits, without a model-based scheduler.

The launch agent checks once per minute. The persisted wake time prevents quota
probes before a known reset; every due attempt rechecks fresh shared capacity.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import daily_batch as batch
import scheduler
import usage
import workqueue


def next_reset(state, now):
    """Earliest known relevant reset; an early wake just rechecks capacity."""
    resets = []
    for provider in ('codex', 'claude'):
        record = state.get('providers', {}).get(provider, {})
        gate = usage.provider_gate(state, provider, 'worker', now)
        for candidate in (gate.get('reset_epoch'),
                          *((record.get(name) or {}).get('reset_epoch')
                            for name in ('five_hour', 'weekly', 'seven_day'))):
            value = usage._epoch(candidate)
            if value is not None and value > now:
                resets.append(value)
    return min(resets) if resets else None


def wake_after_no_capacity(state, budget, now):
    snapshot = budget.snapshot([])
    active = any(any(value.get('reserved_pct', 0) > 0 for value in provider['windows'].values())
                 for provider in snapshot['providers'].values())
    reset = next_reset(state, now)
    if active:
        return {'wake_at': now + 60, 'reason': 'other active reservations may finish', 'reset_at': reset}
    readings = getattr(budget, 'readings', {})
    if readings and any(readings.get(provider, {}).get('error')
                        or not isinstance(readings.get(provider, {}).get('observed_at'), (int, float))
                        or not 0 <= now - readings[provider]['observed_at'] <= 300
                        or not readings[provider].get('windows')
                        or any(not isinstance(window.get('live'), (int, float))
                               for window in readings[provider]['windows'].values())
                        for provider in ('codex', 'claude')):
        return {'wake_at': now + 300, 'reason': 'fresh provider usage unavailable', 'reset_at': reset}
    if reset:
        return {'wake_at': reset + 5, 'reason': 'next reported provider reset', 'reset_at': reset}
    return {'wake_at': now + 900, 'reason': 'no reliable reset; recheck in 15 minutes', 'reset_at': None}


def set_schedule(store, **values):
    record = {'scheduled_at': time.time(), **values}
    store.set('quota_schedule', record)
    return record


def choose_repos(store, code, snapshot, repos, live, now):
    unfinished = [w for w in store.list() if w['repo'] in repos and w['repo'] not in live
                  and w['state'] not in ('done', 'human') and w['not_before'] <= now]
    pinned = store.get('quota_work_ids', [])
    unfinished.sort(key=lambda w: (w['id'] not in pinned, w['created']))
    chosen, seen = [], set()
    for work in unfinished:
        if work['repo'] not in seen:
            chosen.append((work['repo'], work))
            seen.add(work['repo'])
    decisions = scheduler.policy(store, [repo for repo in repos if repo not in live], snapshot,
                                 now=now, daily_limit=False)
    for decision in decisions:
        if decision['decision'] != 'wait' and decision['repo'] not in seen:
            chosen.append((decision['repo'], None))
            seen.add(decision['repo'])
    return chosen


def forecast_candidates(budget, candidates, slots):
    chosen = []
    for repo, work in candidates:
        if len(chosen) >= slots:
            break
        candidate = {'repo': repo, 'stamp': 'quota-forecast-' + repo,
                     'work_phase': (work or {}).get('phase', 'implementing')}
        pending = bool(work or batch.pending_action(repo))
        planners = [None] if pending else budget.planners
        allocation = next((allocation for planner in planners
                           if (allocation := budget.choose(item=candidate, planner=planner,
                                                           commit=False)) is not None), None)
        if allocation is not None:
            budget.forecast.extend(allocation['leases'])
            chosen.append((repo, work))
    if not chosen and os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') != '1':
        prepared = None
        for repo, work in candidates:
            if not (work or batch.pending_action(repo)):
                continue
            if prepared is None:
                prepared = budget.prepare_override()
            if not prepared:
                break
            candidate = {'repo': repo, 'stamp': 'quota-forecast-' + repo,
                         'work_phase': (work or {}).get('phase', 'implementing'), 'job_override': True}
            if budget.choose(item=candidate, planner=None, commit=False) is not None:
                return [(repo, work)]  # the exception is one bounded continuation
    return chosen


def tick(root):
    previous = os.environ.get('ADVANCE_ROADMAP_CONTINUOUS')
    os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = '1'
    try:
        return _tick(root)
    finally:
        if previous is None:
            os.environ.pop('ADVANCE_ROADMAP_CONTINUOUS', None)
        else:
            os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = previous


def _tick(root):
    if (root / 'session-hold.json').exists():
        return 'held'
    code = batch.shared.code_dir()
    if not code:
        raise RuntimeError('personal code directory unavailable')
    root.mkdir(parents=True, exist_ok=True)
    with open(root / 'run.lock.f', 'a') as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 'controller-active'
        with workqueue.Store(root) as store:
            if not store.get('quota_scheduler_enabled', False):
                return 'disabled'
            now = time.time()
            schedule = store.get('quota_schedule', {})
            if not schedule.get('provisional') and schedule.get('wake_at', 0) > now:
                return 'waiting'
            live = workqueue.sync(store, code) | batch.shared.inflight_repos(root)
            scheduler.observe_completed(store, root)
            active = workqueue.active_count(store)
            slots = max(0, 2 - active)
            if not slots:
                set_schedule(store, wake_at=now + 60, reason='existing worker active')
                return 'worker-active'
            snapshot = scheduler.refresh_snapshot(root)
            workqueue.wake_humans(store, workqueue.read(snapshot, {}))
            repos = batch.candidates(Path(code))
            candidates = choose_repos(store, code, snapshot, repos, live, now)
            if not candidates:
                set_schedule(store, wake_at=now + 900, reason='no eligible work')
                return 'no-eligible-work'
            budget = batch.RoadmapBudget()
            chosen = forecast_candidates(budget, candidates, slots)
            store.set('quota_work_ids', [work['id'] for _, work in chosen if work])
            state = usage.load(root)
            if not chosen:
                set_schedule(store, **wake_after_no_capacity(state, budget, now))
                return 'waiting-for-quota'
            if batch.shared.py('workspaces.py', 'available', timeout=60).returncode:
                set_schedule(store, wake_at=now + 300, reason='workspace service unavailable')
                return 'workspace-unavailable'
            reset = next_reset(state, now)
            set_schedule(store, wake_at=(reset + 5 if reset else now + 900),
                         reason='provisional reset while unit runs', reset_at=reset,
                         provisional=True, repos=[repo for repo, _ in chosen],
                         work_ids=[work['id'] for _, work in chosen if work])
            directory = root / 'batches' / ('quota-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
            directory.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(snapshot, directory / 'linear-snapshot.json')
            job = batch.Batch(directory, [repo for repo, _ in chosen], lanes=2, threads=1)
            marker = root / 'run.lock'
            marker.mkdir(exist_ok=True)
            (marker / 'pid').write_text(str(os.getpid()))
            caffeinate = None
            previous_continuous = os.environ.get('ADVANCE_ROADMAP_CONTINUOUS')
            os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = '1'
            try:
                if shutil.which('caffeinate'):
                    caffeinate = subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())])
                job.run(streaming=True, summary=False)
            finally:
                if previous_continuous is None:
                    os.environ.pop('ADVANCE_ROADMAP_CONTINUOUS', None)
                else:
                    os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = previous_continuous
                if caffeinate:
                    caffeinate.terminate()
                (marker / 'pid').unlink(missing_ok=True)
                try:
                    marker.rmdir()
                except OSError:
                    pass
                if job.budget:
                    for item in job.state['queue']:
                        if item['status'] not in {'planning', 'running', 'canceling'}:
                            job.budget.release(item)
            outcomes = [item.get('outcome') for item in job.state['queue']]
            admitted = any(item.get('released_at') for item in job.state['queue'])
            if ((admitted and any(outcome in {'yielded', 'shipped', 'shipped-deploy-failed', 'blocked'}
                                  for outcome in outcomes))
                    or any(outcome in {'blocked-no-item', 'nothing-qualified', 'skipped-unchanged'}
                           for outcome in outcomes)):
                set_schedule(store, wake_at=time.time() + 2, reason='unit completed; recheck headroom',
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            elif any(outcome in {'error', 'failed', 'incomplete', 'unrecorded'} for outcome in outcomes):
                set_schedule(store, wake_at=time.time() + 300, reason='recoverable failure cooldown',
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            else:
                latest = usage.load(root)
                set_schedule(store, **wake_after_no_capacity(latest, job.budget, time.time()),
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            return 'unit-finished'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=batch.ROOT)
    control = parser.add_mutually_exclusive_group()
    control.add_argument('--enable', action='store_true')
    control.add_argument('--disable', action='store_true')
    control.add_argument('--status', action='store_true')
    args = parser.parse_args()
    batch.ROOT = batch.shared.ROOT = args.root
    if args.enable or args.disable or args.status:
        with workqueue.Store(args.root) as store:
            if args.enable:
                previously_enabled = store.get('quota_scheduler_enabled', False)
                store.set('quota_scheduler_enabled', True)
                if not previously_enabled:
                    store.set('quota_scheduler_started', time.time())
                set_schedule(store, wake_at=time.time(), reason='enabled')
            elif args.disable:
                store.set('quota_scheduler_enabled', False)
            print(json.dumps({'enabled': store.get('quota_scheduler_enabled', False),
                              'concurrency': 2, 'work_ids': store.get('quota_work_ids', []),
                              'schedule': store.get('quota_schedule')}))
        return 0
    try:
        print(tick(args.root))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        with workqueue.Store(args.root) as store:
            set_schedule(store, wake_at=time.time() + 300, reason='scheduler error', error=str(error))
        print('quota scheduler error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
