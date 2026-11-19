#!/usr/bin/env python3
"""Resume up to two roadmap assignments as quota permits, without a model-based scheduler.

The launch agent checks once per minute. The persisted wake time prevents quota
probes before a known reset; every due attempt rechecks fresh shared capacity.
"""
import argparse
import fcntl
import json
import os
import re
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


def set_schedule(store, tick_started=None, **values):
    now = time.time()
    record = {'scheduled_at': now, **values}
    # Enqueue can race an active controller. Preserve one immediate recheck for
    # decisions arriving during this tick without bypassing capacity gates.
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        requests = store.get('decision_requests', [])
        if tick_started is not None and any(r.get('status') == 'queued' and r.get('created', 0) >= tick_started
                                            for r in requests):
            record['wake_at'] = min(record.get('wake_at', now + 2), now + 2)
            record['provisional'] = False
        store.db.execute("INSERT OR REPLACE INTO settings VALUES ('quota_schedule', ?)", (json.dumps(record),))
    return record


def queue_decision(root, repo, ticket, source_stamp, decision_id, *, wake=True):
    """Queue only an answer already verified and durably recorded by Slackagent."""
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', repo or '')
            or not re.fullmatch(r'DEV-\d+', ticket or '')
            or not re.fullmatch(r'\d{8}-\d{6}', source_stamp or '')
            or not re.fullmatch(r'[a-f0-9]{64}', decision_id or '')):
        raise ValueError('invalid decision identity')
    record = None
    for path in (root / 'slack-decisions').glob('*.json'):
        candidate = workqueue.read(path, {}) or {}
        decision = candidate.get('decision') or {}
        if (candidate.get('dispatchKey') == decision_id and candidate.get('status') in ('recorded', 'finished')
                and decision.get('repo') == repo and decision.get('linear_id') == ticket
                and decision.get('stamp') == source_stamp and decision.get('source') in ('worker', 'planner')
                and str(candidate.get('answer') or '').strip()
                and re.fullmatch(r'<!-- slackagent:unblock:[a-f0-9-]{36} -->', candidate.get('linearMarker') or '')):
            record = candidate
            break
    if record is None:
        raise ValueError('matching verified decision record not found')
    root.mkdir(parents=True, exist_ok=True)
    with workqueue.Store(root) as store:
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            row = store.db.execute("SELECT value FROM settings WHERE key='decision_requests'").fetchone()
            requests = json.loads(row[0]) if row else []
            existing = next((r for r in requests if r.get('key') == decision_id), None)
            if existing:
                disposition = ('already-started' if existing.get('status') == 'started' else
                               'resolved-no-work' if existing.get('status') == 'no-work' else 'already-queued')
            else:
                requests.append({'key': decision_id, 'repo': repo, 'ticket': ticket,
                                 'source_stamp': source_stamp, 'source': record['decision']['source'],
                                 'linear_marker': record['linearMarker'],
                                 'status': 'queued', 'created': time.time()})
                store.db.execute("INSERT OR REPLACE INTO settings VALUES ('decision_requests', ?)",
                                 (json.dumps(requests),))
                # Recheck now, but normal scheduler locks, hold and budget gates still apply.
                store.db.execute("INSERT OR REPLACE INTO settings VALUES ('quota_schedule', ?)",
                                 (json.dumps({'wake_at': time.time(), 'reason': 'answered decision'}),))
                disposition = 'queued'
        enabled = bool(store.get('quota_scheduler_enabled', False))
    if wake and enabled and disposition not in ('already-started', 'resolved-no-work'):
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--root', str(root)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    if not enabled and disposition not in ('already-started', 'resolved-no-work'):
        disposition = 'scheduler-disabled'
    return {'accepted': enabled or disposition in ('already-started', 'resolved-no-work'), 'disposition': disposition, 'scheduler_enabled': enabled,
            'repo': repo, 'ticket': ticket, 'decision_id': decision_id}


def target_decision(store, code, snapshot, repos, live, now):
    """Select the answered ticket only; never replace a different saved assignment."""
    requests = [r for r in store.get('decision_requests', []) if r.get('status') == 'queued']
    for request in requests:
        target = decision_candidate(store, code, snapshot, repos, live, now, request)
        if target:
            return request, target
    return (requests[0], None) if requests else (None, None)


def pending_decision_directive(issue, repo, marker):
    """A historical marker is not authority once its directive is consumed."""
    if not issue or issue.get('state_type') not in ('backlog', 'unstarted', 'started'):
        return False
    if str(issue.get('state', '')).lower() in ('needs input', 'paused'):
        return False
    labels = {str(label).lower() for label in issue.get('labels') or []}
    if labels & {'human-only', 'human only'} or f'repo/{repo}'.lower() not in labels:
        return False
    if not isinstance(marker, str) or not marker:
        return False
    directive = re.search(
        r'<!--\s*advance-roadmap:directive:' + re.escape(repo) + r'\s*-->\s*\n\s*##\s+Directive[ \t]*(?:\r?\n|$)(.*?)(?=^##\s|\Z)',
        str(issue.get('description') or ''), re.MULTILINE | re.DOTALL)
    return bool(directive and marker in directive.group(1))


def decision_candidate(store, code, snapshot, repos, live, now, request):
    if request['repo'] not in repos or request['repo'] in live:
        return None
    repo, ticket = request['repo'], request['ticket']
    snap = workqueue.read(snapshot, {}) or {}
    if snap.get('ok') is not True or snap.get('truncated'):
        return None
    issue = next((i for i in snap.get('issues', []) if i.get('id') == ticket), None)
    marker = request.get('linear_marker')
    if not pending_decision_directive(issue, repo, marker):
        return None
    pending_dir = Path(code) / '.advance-roadmap'
    matching_plans = []
    conflicting_plan = False
    for path in pending_dir.glob('pending-plan-' + repo + '*.json'):
        if path.name != f'pending-plan-{repo}.json' and not re.fullmatch(
                re.escape(f'pending-plan-{repo}') + r'\.t\d+\.json', path.name):
            continue
        saved = workqueue.read(path, {}) or {}
        plan = saved.get('orchestrator') or saved
        if plan.get('action') in batch.WORK_ACTIONS:
            if plan.get('linear_id') == ticket:
                matching_plans.append((path, plan))
            conflicting_plan |= plan.get('linear_id') != ticket
    matches = [w for w in store.list(repo) if (w.get('plan') or {}).get('linear_id') == ticket
               and w['state'] not in ('done', 'human') and w['not_before'] <= now]
    work = matches[0] if len(matches) == 1 else None
    if len(matches) > 1:
        return None
    if work:
        exact = [path for path, plan in matching_plans
                 if workqueue.identity(plan) == work['id']
                 and workqueue.requirements_fingerprint(plan) == workqueue.requirements_fingerprint(work['plan'])]
        if len(exact) != 1:
            return None
        if request.get('source') == 'worker':
            attempt = store.db.execute('SELECT work_id FROM attempts WHERE stamp=?',
                                       (request['source_stamp'],)).fetchone()
            if not attempt or attempt['work_id'] != work['id']:
                return None
        work = {**work, 'decision_plan_path': str(exact[0]),
                'decision_requirements': workqueue.requirements_fingerprint(work['plan'])}
    elif request.get('source') == 'worker':
        return None
    else:
        # A repo-wide pending plan could belong to another ticket. Do not run it.
        if conflicting_plan or matching_plans or batch.pending_action(repo):
            return None
        if ticket not in tickets_by_repo(workqueue.read(snapshot, {}) or {}).get(repo, []):
            return None
        decisions = scheduler.policy(store, [repo], snapshot, now=now, daily_limit=False)
        if not decisions or decisions[0]['decision'] != 'plan':
            return None
    return repo, work


def reconcile_decision_requests(store):
    """Recognize a launched batch even if its controller was interrupted."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        requests = store.get('decision_requests', [])
        changed = False
        for request in requests:
            if request.get('status') != 'queued' or not request.get('batch'):
                continue
            state = workqueue.read(Path(request['batch']) / 'batch.json', {}) or {}
            for item in state.get('queue', []):
                run = store.root / 'runs' / str(item.get('stamp') or '')
                assignment = (workqueue.read(run / 'worker-request.json', {}) or {}).get('orchestrator') or {}
                launched = ((run / 'worker.pid').is_file() or (run / 'worker-status.json').is_file())
                if (item.get('repo') == request['repo'] and item.get('decision_target') == request['key']
                        and item.get('released_at') and item.get('linear_id') == request['ticket']
                        and assignment.get('repo') == request['repo']
                        and assignment.get('linear_id') == request['ticket'] and launched):
                    request['status'] = 'started'
                    request['started_at'] = item['released_at']
                    request['result_stamp'] = item['stamp']
                    request['outcome'] = 'started'
                    request['summary'] = 'The assigned worker started.'
                    changed = True
                    break
                if (item.get('repo') == request['repo'] and item.get('decision_target') == request['key']
                        and item.get('status') == 'finished' and item.get('outcome') in ('blocked-no-item', 'nothing-qualified')
                        and scheduler.verified_no_work(store.root, item)):
                    request['status'] = 'no-work'
                    request['result_stamp'] = item['stamp']
                    request['outcome'] = item['outcome']
                    plan = workqueue.read(run / 'orchestrator-result.json', {}) or {}
                    request['summary'] = plan.get('summary') or 'No actionable work was found after this decision.'
                    changed = True
                    break
        if changed:
            store.db.execute("INSERT OR REPLACE INTO settings VALUES ('decision_requests', ?)", (json.dumps(requests),))


def set_decision_batch(store, key, directory):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        requests = store.get('decision_requests', [])
        for request in requests:
            if request.get('key') == key and request.get('status') == 'queued':
                request['batch'] = str(directory)
                break
        store.db.execute("INSERT OR REPLACE INTO settings VALUES ('decision_requests', ?)", (json.dumps(requests),))


def eligible_tickets(snapshot):
    """Rank actionable Linear candidates before mapping them to local repositories."""
    if not snapshot.get('ok') or snapshot.get('truncated'):
        return []
    issues = [issue for issue in snapshot.get('issues', []) if isinstance(issue, dict) and issue.get('id')]
    by_id = {issue['id']: issue for issue in issues}
    candidates = []
    for issue in issues:
        if issue.get('state_type') not in ('backlog', 'unstarted', 'started') or str(issue.get('state', '')).lower() in ('needs input', 'paused'):
            continue
        labels = issue.get('labels') or []
        normalized_labels = {str(label).lower() for label in labels}
        if normalized_labels & {'human-only', 'human only'}:
            continue
        # Match the planner's feature-only policy before picking a repository.
        if 'code-health' in normalized_labels and 'do-next' not in normalized_labels:
            continue
        if any(blocker not in by_id or by_id[blocker].get('state_type') not in ('completed', 'canceled')
               for blocker in issue.get('blocked_by') or []):
            continue
        repo_labels = [label[5:] for label in labels if isinstance(label, str) and label.startswith('repo/')]
        if len(repo_labels) != 1:
            continue
        priority = issue.get('priority')
        rank = priority if isinstance(priority, int) and 1 <= priority <= 4 else 5
        candidates.append((rank, issue['id'], repo_labels[0]))
    return sorted(candidates)


def tickets_by_repo(snapshot):
    result = {}
    for _, ticket, repo in eligible_tickets(snapshot):
        result.setdefault(repo, []).append(ticket)
    return result


def choose_repos(store, code, snapshot, repos, live, now, *, allow_new=True):
    unfinished = [w for w in store.list() if w['repo'] in repos and w['repo'] not in live
                  and w['state'] not in ('done', 'human') and w['not_before'] <= now]
    pinned = store.get('quota_work_ids', [])
    unfinished.sort(key=lambda w: (w['id'] not in pinned, w['created']))
    chosen, seen = [], set()
    for work in unfinished:
        if work['repo'] not in seen:
            chosen.append((work['repo'], work))
            seen.add(work['repo'])
    # Saved plans are also resumable work, even when their ticket has since closed.
    pending_repos = [repo for repo in repos if repo not in live and repo not in seen
                     and batch.pending_action(repo)]
    for decision in scheduler.policy(store, pending_repos, snapshot, now=now, daily_limit=False):
        if decision['decision'] == 'resume':
            chosen.append((decision['repo'], None))
            seen.add(decision['repo'])
    # The snapshot is fetched before this point. Only map eligible tickets to a
    # local repository after ranking them; missing or incomplete snapshots fail closed.
    ticket_repos = tickets_by_repo(workqueue.read(snapshot, {}) or {}) if allow_new else {}
    available = [repo for repo in ticket_repos if repo in repos and repo not in live and repo not in seen]
    decisions = {d['repo']: d for d in scheduler.policy(store, available, snapshot,
                                                        now=now, daily_limit=False)}
    for repo in available:
        if decisions.get(repo, {}).get('decision') != 'wait' and repo in decisions:
            chosen.append((repo, None))
            seen.add(repo)
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
    tick_started = time.time()
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
            reconcile_decision_requests(store)
            now = time.time()
            schedule = store.get('quota_schedule', {})
            if not schedule.get('provisional') and schedule.get('wake_at', 0) > now:
                return 'waiting'
            live = workqueue.sync(store, code) | batch.shared.inflight_repos(root)
            scheduler.observe_completed(store, root)
            active = workqueue.active_count(store)
            slots = max(0, 2 - active)
            if not slots:
                set_schedule(store, tick_started=tick_started, wake_at=now + 60, reason='existing worker active')
                return 'worker-active'
            try:
                snapshot = scheduler.refresh_snapshot(root, force=True)
                fresh_snapshot = True
            except RuntimeError:
                # Never select new work from stale data, but a saved assignment can
                # continue even while Linear is unavailable.
                snapshot = root / 'continuation-only-snapshot.json'
                snapshot.write_text(json.dumps({'ok': False, 'issues': []}))
                fresh_snapshot = False
            if fresh_snapshot:
                workqueue.wake_humans(store, workqueue.read(snapshot, {}))
            repos = batch.candidates(Path(code))
            request, target = (target_decision(store, code, snapshot, repos, live, now)
                               if fresh_snapshot else (None, None))
            decision_repos = {r['repo'] for r in store.get('decision_requests', []) if r.get('status') == 'queued'}
            ordinary_repos = [repo for repo in repos if repo not in decision_repos]
            candidates = ([target] if target else []) + choose_repos(
                store, code, snapshot, ordinary_repos, live, now, allow_new=fresh_snapshot)
            if not candidates:
                set_schedule(store, tick_started=tick_started, wake_at=now + (60 if request else (900 if fresh_snapshot else 300)),
                             reason=('answered decision waiting for eligible work' if request else
                                     ('no eligible work' if fresh_snapshot else 'fresh Linear snapshot unavailable')))
                return 'no-eligible-work' if fresh_snapshot else 'snapshot-unavailable'
            budget = batch.RoadmapBudget()
            chosen = forecast_candidates(budget, candidates, slots)
            store.set('quota_work_ids', [work['id'] for _, work in chosen if work])
            state = usage.load(root)
            if not chosen:
                set_schedule(store, tick_started=tick_started, **wake_after_no_capacity(state, budget, now))
                return 'waiting-for-quota'
            if batch.shared.py('workspaces.py', 'available', timeout=60).returncode:
                set_schedule(store, tick_started=tick_started, wake_at=now + 300, reason='workspace service unavailable')
                return 'workspace-unavailable'
            reset = next_reset(state, now)
            set_schedule(store, tick_started=tick_started, wake_at=(reset + 5 if reset else now + 900),
                         reason='provisional reset while unit runs', reset_at=reset,
                         provisional=True, repos=[repo for repo, _ in chosen],
                         work_ids=[work['id'] for _, work in chosen if work])
            directory = root / 'batches' / ('quota-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
            directory.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(snapshot, directory / 'linear-snapshot.json')
            job = batch.Batch(directory, [repo for repo, _ in chosen], lanes=2, threads=1)
            selected_tickets = tickets_by_repo(workqueue.read(snapshot, {}) or {})
            for item in job.state['queue']:
                if request and target and item['repo'] == request['repo']:
                    item['selected_tickets'] = [request['ticket']]
                    item['decision_target'] = request['key']
                    item['decision_resume_only'] = request['source'] == 'worker'
                    if target[1]:
                        item['decision_plan_path'] = target[1]['decision_plan_path']
                        item['decision_requirements'] = target[1]['decision_requirements']
                    store.set('targeted_at_start:' + item['stamp'], True)
                    continue
                if (next((work for repo, work in chosen if repo == item['repo']), None) is None
                        and not batch.pending_action(item['repo'])):
                    item['selected_tickets'] = selected_tickets.get(item['repo'], [])
            if request and target and any(repo == request['repo'] for repo, _ in chosen):
                set_decision_batch(store, request['key'], directory)
            marker = root / 'run.lock'
            marker.mkdir(exist_ok=True)
            (marker / 'pid').write_text(str(os.getpid()))
            caffeinate = None
            previous_continuous = os.environ.get('ADVANCE_ROADMAP_CONTINUOUS')
            previous_continuation_only = os.environ.get('ADVANCE_ROADMAP_CONTINUATION_ONLY')
            os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = '1'
            if not fresh_snapshot:
                os.environ['ADVANCE_ROADMAP_CONTINUATION_ONLY'] = '1'
            try:
                if shutil.which('caffeinate'):
                    caffeinate = subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())])
                job.run(streaming=True, summary=False)
            finally:
                if previous_continuous is None:
                    os.environ.pop('ADVANCE_ROADMAP_CONTINUOUS', None)
                else:
                    os.environ['ADVANCE_ROADMAP_CONTINUOUS'] = previous_continuous
                if previous_continuation_only is None:
                    os.environ.pop('ADVANCE_ROADMAP_CONTINUATION_ONLY', None)
                else:
                    os.environ['ADVANCE_ROADMAP_CONTINUATION_ONLY'] = previous_continuation_only
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
                reconcile_decision_requests(store)
            outcomes = [item.get('outcome') for item in job.state['queue']]
            admitted = any(item.get('released_at') for item in job.state['queue'])
            if outcomes and all(outcome == 'skipped-unchanged' for outcome in outcomes):
                pending_decision = any(r.get('status') == 'queued' for r in store.get('decision_requests', []))
                set_schedule(store, tick_started=tick_started, wake_at=time.time() + (2 if pending_decision else 900),
                             reason='answered decision pending' if pending_decision else 'unchanged verdict cooldown',
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            elif ((admitted and any(outcome in {'yielded', 'shipped', 'shipped-deploy-failed', 'blocked'}
                                  for outcome in outcomes))
                    or any(outcome in {'blocked-no-item', 'nothing-qualified', 'skipped-unchanged'}
                           for outcome in outcomes)):
                set_schedule(store, tick_started=tick_started, wake_at=time.time() + 2, reason='unit completed; recheck headroom',
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            elif any(outcome in {'error', 'failed', 'incomplete', 'unrecorded'} for outcome in outcomes):
                set_schedule(store, tick_started=tick_started, wake_at=time.time() + 300, reason='recoverable failure cooldown',
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            else:
                latest = usage.load(root)
                set_schedule(store, tick_started=tick_started, **wake_after_no_capacity(latest, job.budget, time.time()),
                             repos=[repo for repo, _ in chosen], outcomes=outcomes)
            return 'unit-finished'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=batch.ROOT)
    control = parser.add_mutually_exclusive_group()
    control.add_argument('--enable', action='store_true')
    control.add_argument('--disable', action='store_true')
    control.add_argument('--status', action='store_true')
    control.add_argument('--decision-ready', action='store_true')
    parser.add_argument('--repo')
    parser.add_argument('--ticket')
    parser.add_argument('--source-stamp')
    parser.add_argument('--decision-id')
    args = parser.parse_args()
    batch.ROOT = batch.shared.ROOT = args.root
    if args.decision_ready:
        try:
            result = queue_decision(args.root, args.repo, args.ticket, args.source_stamp, args.decision_id)
        except (ValueError, OSError, TypeError) as error:
            print(json.dumps({'accepted': False, 'disposition': 'invalid', 'error': str(error)}))
            return 1
        print(json.dumps(result))
        return 0 if result['accepted'] else 2
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
    tick_started = time.time()
    try:
        print(tick(args.root))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        with workqueue.Store(args.root) as store:
            set_schedule(store, tick_started=tick_started, wake_at=time.time() + 300, reason='scheduler error', error=str(error))
        print('quota scheduler error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
