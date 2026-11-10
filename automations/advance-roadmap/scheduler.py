#!/usr/bin/env python3
"""Cheap queue eligibility and seven-day shadow evaluation; shares the batch's lock/budgets."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import daily_batch as batch
from workqueue import Store, read, rows, sync, human_changed, eligibility_fingerprint

DAY = 86400
SHADOW_DAYS = 7
TRIAL_DAYS = 7
# Existing schedule can admit two new items per repo each day. More ticks must not raise that cap.
MAX_NEW_PER_REPO_DAY = 2


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def policy(store, repos, snapshot, now=None):
    """No model calls, budget probes or external mutations. Every skip has a wake condition."""
    now = time.time() if now is None else now
    code = Path(batch.shared.code_dir())
    snapshot_data = read(snapshot, {}) or {}
    issues = snapshot_data.get('issues', [])
    decisions = []
    for repo in repos:
        cached = store.get('eligibility:' + repo, {})
        fp = eligibility_fingerprint(repo, code, snapshot_data,
                                     batch.shared.HERE.parents[1] / 'skills/advance-roadmap')
        hold = store.hold(repo, now)
        active = [w for w in store.list(repo) if w['state'] != 'done']
        changed_human = any(w['state'] == 'human' and human_changed(store, w, snapshot_data) for w in active)
        if hold and not changed_human:
            decision, reason = 'wait', hold['reason'] or hold['state']
        elif active or batch.pending_action(repo):
            decision, reason = 'resume', 'unfinished assignment is eligible; reuse checkpoint before planning'
        elif cached.get('fingerprint') == fp and now - cached.get('checked', 0) < 72 * 3600:
            decision, reason = 'wait', 'inputs unchanged; wake on relevant change or 72-hour safety audit'
        else:
            decision, reason = 'plan', 'relevant inputs changed or safety audit is due'
        decisions.append({'repo': repo, 'decision': decision, 'reason': reason, 'fingerprint': fp})
    return decisions


def record_shadow(store, decisions, now=None):
    now = time.time() if now is None else now
    tick = datetime.fromtimestamp(now).strftime('%Y%m%d-%H%M')
    with store.db:
        for d in decisions:
            store.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?)',
                             (tick, d['repo'], d['decision'], d['reason'], d['fingerprint'], now))
    if store.get('shadow_started') is None:
        store.set('shadow_started', now)
    store.set('last_tick', {'at': now, 'decisions': decisions})


def observe_completed(store, root):
    """Only an actual completed planner decision can establish a cached no-work verdict."""
    assignments = {r['stamp']: r['assigned_repo'] for r in store.db.execute('SELECT * FROM attempts')}
    last_decisions = {d['repo']: d for d in store.get('last_tick', {}).get('decisions', [])}
    for row in rows(root / 'runs.jsonl'):
        stamp = row.get('stamp') or ''
        signature = digest(row)
        if store.get('observed:' + stamp) == signature:
            continue
        store.set('observed:' + stamp, signature)
        repo = row.get('assigned_repo') or assignments.get(stamp)
        fingerprint = store.get('eligibility_at_start:' + stamp)
        if not fingerprint:
            continue  # historical or uncorrelated fingerprints cannot establish a new cache
        outcome = row.get('outcome')
        if outcome in {'blocked-no-item', 'nothing-qualified', 'skipped-unchanged'}:
            cached = store.get('eligibility:' + repo, {})
            checked = cached.get('checked', time.time()) if outcome == 'skipped-unchanged' else time.time()
            store.set('eligibility:' + repo, {'fingerprint': fingerprint, 'checked': checked,
                                            'stamp': stamp, 'outcome': outcome})
        decision = last_decisions.get(repo, {})
        if (decision.get('fingerprint') == fingerprint and decision.get('decision') == 'wait'
                and outcome == 'shipped'):
            store.set('shadow_failure:' + stamp, {'at': time.time(), 'repo': repo,
                                                 'reason': 'shadow would have skipped a ship'})


def promotion_readiness(store, now=None):
    now = time.time() if now is None else now
    started = store.get('shadow_started', now)
    window = max(started, now - SHADOW_DAYS * DAY)
    days = store.db.execute("SELECT COUNT(DISTINCT substr(tick,1,8)) FROM decisions WHERE created>=?", (window,)).fetchone()[0]
    failures = [json.loads(r[0]) for r in store.db.execute("SELECT value FROM settings WHERE key LIKE 'shadow_failure:%'")]
    errors = sum(isinstance(f, dict) and f.get('at', 0) >= window for f in failures)
    fresh = now - store.get('last_tick', {}).get('at', 0) <= 1800
    return {'ready': now - started >= SHADOW_DAYS * DAY and days >= SHADOW_DAYS and errors == 0 and fresh,
            'elapsed_days': round((now - started) / DAY, 2), 'observed_dates': days,
            'false_skip_or_errors': errors, 'fresh_observation': fresh}


def trial_evaluation(store, now=None):
    now = time.time() if now is None else now
    start = store.get('trial_started')
    if not start:
        return {'ready': False, 'reason': 'trial has not started'}
    pilot = set(store.get('pilot', []))
    assigned = {r['stamp']: r['assigned_repo'] for r in store.db.execute('SELECT * FROM attempts')}
    metrics = [{'records': 0, 'ships': 0, 'no_work_planning_tokens': 0, 'token_records': 0,
                'errors': 0, 'new_tokens': 0} for _ in range(2)]
    for row in rows(store.root / 'runs.jsonl'):
        if (row.get('assigned_repo') or assigned.get(row.get('stamp'))) not in pilot:
            continue
        try:
            at = datetime.strptime(row['stamp'], '%Y%m%d-%H%M%S').timestamp()
        except (ValueError, KeyError):
            continue
        if not start - TRIAL_DAYS * DAY <= at < start + TRIAL_DAYS * DAY:
            continue
        m = metrics[int(at >= start)]
        m['records'] += 1
        m['ships'] += row.get('outcome') == 'shipped'
        m['errors'] += row.get('outcome') in {'error', 'failed', 'incomplete'}
        tokens = row.get('tokens') or {}
        m['token_records'] += bool(tokens)
        m['new_tokens'] += tokens.get('total', 0)
        if row.get('outcome') == 'blocked-no-item':
            m['no_work_planning_tokens'] += sum(c.get('total', 0) for c in tokens.get('calls', [])
                                                if c.get('role') == 'orchestrator')
    baseline, trial = metrics
    failures = [json.loads(r[0]) for r in store.db.execute("SELECT value FROM settings WHERE key LIKE 'shadow_failure:%'")]
    unsafe = any(isinstance(f, dict) and f.get('at', 0) >= start for f in failures)
    ready = (now - start >= TRIAL_DAYS * DAY and baseline['token_records'] > 0 and trial['token_records'] > 0
             and trial['no_work_planning_tokens'] <= baseline['no_work_planning_tokens'] * .5
             and trial['ships'] >= baseline['ships'] and trial['errors'] <= baseline['errors'] and not unsafe)
    return {'ready': ready, 'elapsed_days': round((now - start) / DAY, 2), 'baseline': baseline, 'trial': trial,
            'reason': 'requires a full trial week, accounting, fewer no-work tokens, no lost ships or increased errors',
            'limitations': 'New tokens exclude cache reads; changed inputs and provider mix still require review.'}


def advance_rollout(store, decisions, now=None):
    """Deterministic staged rollout; observations cannot be replaced by elapsed time alone."""
    if not store.get('auto_rollout', False):
        return
    now = time.time() if now is None else now
    mode = store.get('mode', 'shadow')
    if mode == 'shadow':
        readiness = promotion_readiness(store, now)
        store.set('rollout_wait', readiness)
        if not readiness['ready']:
            return
        # Pilot the repos with the largest measured no-work planning cost.
        assigned = {r['stamp']: r['assigned_repo'] for r in store.db.execute('SELECT * FROM attempts')}
        waste = {}
        for row in rows(store.root / 'runs.jsonl'):
            repo = row.get('assigned_repo') or assigned.get(row.get('stamp'))
            if row.get('outcome') == 'blocked-no-item':
                waste[repo] = waste.get(repo, 0) + sum(c.get('total', 0) for c in
                    (row.get('tokens') or {}).get('calls', []) if c.get('role') == 'orchestrator')
        eligible = [d['repo'] for d in decisions if not store.hold(d['repo'], now)]
        pilot = sorted(eligible, key=lambda repo: (-waste.get(repo, 0), repo))[:2]
        if not pilot:
            return
        store.set('pilot', pilot)
        store.set('trial_started', now)
        store.set('mode', 'trial')
    elif mode == 'trial':
        evaluation = trial_evaluation(store, now)
        store.set('rollout_wait', evaluation)
        if evaluation['ready']:
            # Keep candidates/ownership filtering in the normal dispatch path.
            store.set('pilot', batch.candidates(Path(batch.shared.code_dir())))
            store.set('mode', 'dynamic')
            store.set('rollout_complete', {'at': now, 'evaluation': evaluation})


def archive_completed_plan(store):
    """Honor Jake's archive request only after measured rollout completion.

    A dirty/different checkout is left alone and retried next tick. No runtime data is committed.
    """
    if not store.get('rollout_complete') or store.get('plan_archived'):
        return
    checkout = store.get('plan_checkout')
    if not checkout:
        return
    checkout = Path(checkout)
    def git(*args):
        return subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, text=True, timeout=60)
    pending = store.get('archive_commit')
    if pending:
        # Retry a failed push without making a second archive commit.
        git('fetch', 'origin')
        if git('merge-base', '--is-ancestor', pending['revision'], 'origin/main').returncode == 0:
            store.set('plan_archived', pending)
            return
        result = git('push', 'origin', pending['revision'] + ':refs/heads/main')
        if result.returncode == 0:
            store.set('plan_archived', pending)
        return
    if (git('branch', '--show-current').stdout.strip() != 'main'
            or git('status', '--porcelain').stdout.strip()):
        store.set('archive_wait', 'Plan checkout is busy or not on main; retry after it is clean')
        return
    origin = git('remote', 'get-url', 'origin').stdout.strip()
    if origin.removesuffix('.git').rstrip('/') not in {'git@github.com:jnelken/ai-tools', 'https://github.com/jnelken/ai-tools'}:
        store.set('archive_wait', 'Plan checkout origin does not match jnelken/ai-tools')
        return
    source = 'docs/plans/roadmap-next-iteration.md'
    target = 'docs/plans/archive/roadmap-next-iteration.md'
    if not (checkout / source).exists() and (checkout / target).exists():
        subject = git('log', '-1', '--format=%s', '--', target).stdout.strip()
        if subject == 'Archive completed roadmap scheduler rollout':
            revision = git('log', '-1', '--format=%H', '--', target).stdout.strip()
            store.set('archive_commit', {'revision': revision, 'path': target})
            archive_completed_plan(store)
            return
    if not (checkout / source).exists() or (checkout / target).exists():
        store.set('archive_wait', 'Plan path changed; retain the rollout completion record for reconciliation')
        return
    if git('fetch', 'origin').returncode or git('rev-list', 'origin/main..HEAD').stdout.strip():
        store.set('archive_wait', 'Unpublished or unavailable checkout history; no archive edits made')
        return
    pull = git('pull', '--ff-only', 'origin', 'main')
    if pull.returncode:
        store.set('archive_wait', 'Could not fast-forward the plan checkout; no archive edits made')
        return
    if git('status', '--porcelain').stdout.strip():
        return
    original = (checkout / source).read_text()
    completed = original + '\n## Rollout completion\n\n' + (
        'The measured shadow and pilot gates passed. Dynamic dispatch is active. '
        'Saved completion evidence is in the local workqueue `rollout_complete` setting.\n')
    completed = re.sub(r'## Status\n\n.*?(?=\n## )',
                       '## Status\n\nComplete. Measured shadow and pilot gates passed; dynamic dispatch is active.\n',
                       completed, count=1, flags=re.S)
    completed = completed.replace('- [ ] Observe seven shadow days;', '- [x] Observe seven shadow days;')
    completed = completed.replace('- [ ] Observe a comparable trial week;', '- [x] Observe a comparable trial week;')
    completed = completed.replace('- [ ] Move this plan to', '- [x] Move this plan to')
    (checkout / source).write_text(completed)
    moved = git('mv', source, target)
    if moved.returncode:
        (checkout / source).write_text(original)
        return
    committed = git('commit', '--only', '-m', 'Archive completed roadmap scheduler rollout', '--', source, target)
    if committed.returncode:
        store.set('archive_wait', 'Archive commit failed; preserve files for inspection')
        return
    revision = git('rev-parse', 'HEAD').stdout.strip()
    store.set('archive_commit', {'revision': revision, 'path': target})
    if git('push', 'origin', revision + ':refs/heads/main').returncode == 0:
        store.set('plan_archived', {'revision': revision, 'path': target})


def refresh_snapshot(root):
    path = root / 'scheduler-snapshot.json'
    if path.exists() and time.time() - path.stat().st_mtime < 900:
        return path
    staged = root / 'scheduler-snapshot.next.json'
    result = batch.shared.py('linearsnap.py', '--out', str(staged), timeout=180)
    if result.returncode or not (read(staged, {}) or {}).get('ok'):
        staged.unlink(missing_ok=True)
        raise RuntimeError('fresh Linear snapshot unavailable; leave all decisions unchanged')
    os.replace(staged, path)
    return path


def daily_admission(store, repos):
    since = datetime.now().strftime('%Y%m%d')
    counts = {}
    for row in store.db.execute('SELECT assigned_repo,record,pid FROM attempts WHERE stamp>=?', (since,)):
        record = json.loads(row['record']) if row['record'] else {}
        if (not row['record'] and row['pid'] is not None) or record.get('orchestrator') not in {None, 'none', 'pending-plan'}:
            counts[row['assigned_repo']] = counts.get(row['assigned_repo'], 0) + 1
    return [repo for repo in repos if counts.get(repo, 0) < MAX_NEW_PER_REPO_DAY]


def dispatch(store, decisions, snapshot, pilot):
    eligible = [d for d in decisions if d['repo'] in pilot and d['decision'] != 'wait']
    allowed = set(daily_admission(store, [d['repo'] for d in eligible if d['decision'] == 'plan']))
    eligible = [d for d in eligible if d['decision'] == 'resume' or d['repo'] in allowed]
    if not eligible:
        return
    # The same kernel lock excludes both legacy batches and other ticks for the entire dispatch.
    directory = store.root / 'batches' / ('dynamic-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir()
    import shutil
    shutil.copyfile(snapshot, directory / 'linear-snapshot.json')
    job = batch.Batch(directory, [d['repo'] for d in sorted(eligible, key=lambda d: d['decision'] != 'resume')],
                      min(5, int(os.environ.get('ADVANCE_ROADMAP_LANES', '5'))), threads=1)
    marker = store.root / 'run.lock'
    marker.mkdir(exist_ok=True)
    (marker / 'pid').write_text(str(os.getpid()))
    try:
        job.run(streaming=True, summary=False)
    finally:
        marker.joinpath('pid').unlink(missing_ok=True)
        try:
            marker.rmdir()
        except OSError:
            pass
        if job.budget:
            for item in job.state['queue']:
                if item['status'] not in {'planning', 'running', 'canceling'}:
                    job.budget.release(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=batch.ROOT)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--promote', nargs='+', metavar='REPO')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--expand', action='store_true')
    parser.add_argument('--restart-shadow', action='store_true')
    parser.add_argument('--enable-rollout', type=Path, metavar='AI_TOOLS_CHECKOUT')
    args = parser.parse_args()
    batch.ROOT = batch.shared.ROOT = args.root
    args.root.mkdir(parents=True, exist_ok=True)
    marker = Path.home() / 'dotfiles/bin/is-personal-machine'
    if marker.exists() and subprocess.run([str(marker)]).returncode == 1:
        return 0
    code = batch.shared.code_dir()
    if not code:
        return 0
    if args.status:
        with Store(args.root) as store:
            print(json.dumps({'mode': store.get('mode', 'shadow'), 'readiness': promotion_readiness(store),
                              'pilot': store.get('pilot', []), 'trial_evaluation': trial_evaluation(store),
                              'last_tick': store.get('last_tick'), 'auto_rollout': store.get('auto_rollout', False),
                              'rollout_wait': store.get('rollout_wait'), 'plan_archived': store.get('plan_archived'),
                              'archive_wait': store.get('archive_wait'), 'work': store.list()}, indent=2))
        return 0
    with open(args.root / 'run.lock.f', 'a') as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if args.promote or args.expand or args.rollback or args.restart_shadow or args.enable_rollout:
                parser.error('a controller is active; retry this policy change after it finishes')
            return 0  # live controller retains authority; next tick reconciles
        with Store(args.root) as store:
            if args.enable_rollout:
                store.set('plan_checkout', str(args.enable_rollout.resolve()))
                store.set('auto_rollout', True)
                return 0
            if args.restart_shadow:
                store.set('shadow_started', time.time())
                store.set('mode', 'shadow')
                store.set('pilot', [])
                return 0
            if args.rollback:
                store.set('auto_rollout', False)
                store.set('mode', 'shadow')
                store.set('pilot', [])
                return 0
            repos = batch.candidates(Path(code))
            if args.expand:
                if store.get('mode') != 'trial' or not trial_evaluation(store)['ready']:
                    parser.error('a successful measured seven-day trial is required before expansion')
                store.set('pilot', repos)
                store.set('mode', 'dynamic')
                return 0
            if args.promote:
                if not promotion_readiness(store)['ready']:
                    parser.error('seven days of shadow observations without false skips/errors are required')
                if not 1 <= len(set(args.promote)) <= 2 or not set(args.promote) <= set(repos):
                    parser.error('choose one or two eligible repos for the trial')
                store.set('pilot', sorted(set(args.promote)))
                store.set('trial_started', time.time())
                store.set('mode', 'trial')
                return 0
            live = sync(store, code) | batch.shared.inflight_repos(args.root)
            observe_completed(store, args.root)
            try:
                snapshot = refresh_snapshot(args.root)
                decisions = policy(store, [r for r in repos if r not in live], snapshot)
                record_shadow(store, decisions)
                store.set('last_error', None)
                advance_rollout(store, decisions)
                archive_completed_plan(store)
                if store.get('mode', 'shadow') in {'trial', 'dynamic'}:
                    dispatch(store, decisions, snapshot, store.get('pilot', []))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                store.set('last_error', {'at': time.time(), 'error': str(error)})
                store.set('shadow_failure:tick:' + str(int(time.time())), {'at': time.time(), 'error': str(error)})
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
