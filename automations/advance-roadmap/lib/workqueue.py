#!/usr/bin/env python3
"""Durable work identity, phase checkpoints and restart recovery (no model calls)."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

WORK_ACTIONS = {'dispatch_worker', 'resume_worker'}
PHASES = {'ready', 'implementing', 'reviewing', 'shipping', 'verifying', 'done'}
PACKET_BYTES = 8192
ROOT = Path(os.environ.get('ADVANCE_ROADMAP_ROOT', Path.home() / '.claude/automations/advance-roadmap'))


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def rows(path):
    try:
        with Path(path).open() as stream:
            for line in stream:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        return


def identity(plan):
    repo = plan.get('repo')
    item = plan.get('linear_id') or plan.get('item')
    if not repo or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', repo) or not item or plan.get('action') not in WORK_ACTIONS:
        return None
    return hashlib.sha256(json.dumps([repo, item], ensure_ascii=False).encode()).hexdigest()[:32]


def git(path, *args):
    try:
        result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def packet(value):
    """The packet contains bounded descriptions and pointers, never full transcripts."""
    allowed = {'summary', 'completed', 'next_action', 'tests', 'findings', 'workspace', 'head', 'branch',
               'dirty_paths', 'session_ids', 'artifacts', 'acceptance', 'evidence_head', 'evidence_valid'}
    out = {k: v for k, v in value.items() if k in allowed}
    if len(json.dumps(out, ensure_ascii=False).encode()) > PACKET_BYTES:
        raise ValueError('continuation packet exceeds 8192 bytes; use artifact paths for large evidence')
    return out


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'workqueue.sqlite3', timeout=15)
        os.chmod(self.root / 'workqueue.sqlite3', 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS work (
            id TEXT PRIMARY KEY, repo TEXT NOT NULL, item TEXT NOT NULL,
            plan TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'ready',
            state TEXT NOT NULL DEFAULT 'eligible', reason TEXT, not_before REAL NOT NULL DEFAULT 0,
            checkpoint TEXT NOT NULL DEFAULT '{}', latest_stamp TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts (
            stamp TEXT PRIMARY KEY, work_id TEXT, assigned_repo TEXT NOT NULL,
            reported_repo TEXT, pid INTEGER, pid_start TEXT, outcome TEXT, record TEXT,
            created REAL NOT NULL, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS checkpoints (
            seq INTEGER PRIMARY KEY, work_id TEXT NOT NULL, stamp TEXT NOT NULL,
            phase TEXT NOT NULL, packet TEXT NOT NULL, created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS decisions (
            tick TEXT NOT NULL, repo TEXT NOT NULL, decision TEXT NOT NULL,
            reason TEXT NOT NULL, fingerprint TEXT, created REAL NOT NULL,
            PRIMARY KEY(tick, repo));
          CREATE INDEX IF NOT EXISTS work_repo ON work(repo);
        ''')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def list(self, repo=None):
        query = 'SELECT * FROM work' + (' WHERE repo=?' if repo else '') + ' ORDER BY created,id'
        result = []
        for row in self.db.execute(query, (repo,) if repo else ()):
            obj = dict(row)
            for field in ('plan', 'checkpoint'):
                obj[field] = json.loads(obj[field])
            result.append(obj)
        return result

    def register(self, plan, stamp, source=None):
        wid = identity(plan)
        if not wid:
            return None
        plan = dict(plan)
        for marker in ('\n\nContinuation checkpoint (', '\nSaved assignment needs revalidation'):
            if marker in (plan.get('worker_brief') or ''):
                plan['worker_brief'] = plan['worker_brief'].split(marker, 1)[0]
        now = time.time()
        with self.db:
            self.db.execute('''INSERT INTO work(id,repo,item,plan,latest_stamp,created,updated)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                plan=CASE WHEN excluded.latest_stamp >= latest_stamp THEN excluded.plan ELSE plan END,
                latest_stamp=MAX(latest_stamp,excluded.latest_stamp),updated=excluded.updated''',
                (wid, plan['repo'], plan.get('linear_id') or plan['item'], json.dumps(plan), stamp, now, now))
            if source:
                self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                                ('source:' + str(source), json.dumps({'work_id': wid, 'plan': plan})))
        return wid

    def attempt(self, stamp, repo, plan=None, pid=None):
        wid = self.register(plan, stamp) if plan else None
        now = time.time()
        started = process_start(pid) if pid else None
        with self.db:
            self.db.execute('''INSERT INTO attempts(stamp,work_id,assigned_repo,pid,pid_start,created,updated)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(stamp) DO UPDATE SET
                work_id=COALESCE(excluded.work_id,work_id),pid=COALESCE(excluded.pid,pid),
                pid_start=COALESCE(pid_start,excluded.pid_start),updated=excluded.updated''',
                (stamp, wid, repo, pid, started, now, now))
        return wid

    def checkpoint(self, wid, stamp, phase, value):
        if phase not in PHASES:
            raise ValueError('unknown continuation phase: ' + phase)
        value = packet(value)
        with self.db:
            row = self.db.execute('SELECT * FROM work WHERE id=?', (wid,)).fetchone()
            if row is None or stamp < row['latest_stamp']:
                return False  # a late worker must not overwrite a newer attempt
            prior = json.loads(row['checkpoint'])
            if 'artifacts' in value:
                pointers = {Path(p).name: p for p in prior.get('artifacts', []) + value['artifacts']}
                value = {**value, 'artifacts': list(pointers.values())[-12:]}
            prior.update(value)
            if 'head' in prior:
                prior['evidence_valid'] = bool(prior.get('head') and prior.get('evidence_head') == prior['head'])
            prior = packet(prior)
            encoded = json.dumps(prior)
            self.db.execute('INSERT INTO checkpoints(work_id,stamp,phase,packet,created) VALUES (?,?,?,?,?)',
                            (wid, stamp, phase, encoded, time.time()))
            self.db.execute('UPDATE work SET phase=?,checkpoint=?,updated=? WHERE id=?',
                            (phase, encoded, time.time(), wid))
        return True

    def finish(self, record, result=None):
        stamp = record['stamp']
        with self.db:
            attempt = self.db.execute('SELECT * FROM attempts WHERE stamp=?', (stamp,)).fetchone()
            if not attempt:
                return
            # Re-imports must not advance cooldowns or resurrect settled work.
            encoded = json.dumps(record, sort_keys=True)
            if attempt['record'] == encoded:
                return
            self.db.execute('UPDATE attempts SET reported_repo=?,outcome=?,record=?,updated=? WHERE stamp=?',
                            (record.get('repo'), record.get('outcome'), encoded, time.time(), stamp))
            if not attempt['work_id']:
                return
            work = self.db.execute('SELECT * FROM work WHERE id=?', (attempt['work_id'],)).fetchone()
            if not work or stamp < work['latest_stamp'] or work['state'] == 'done':
                return
            outcome = record.get('outcome')
            result = result or {}
            reason = record.get('detail') or result.get('summary') or outcome
            try:
                finished = datetime.fromisoformat(record['finished_at']).timestamp()
            except (KeyError, ValueError, TypeError):
                finished = time.time()
            failures = self.db.execute("SELECT COUNT(*) FROM attempts WHERE work_id=? AND outcome IN ('error','failed','incomplete','blocked','blocked-branch-left','limit_hit')", (work['id'],)).fetchone()[0]
            backoff = min(6 * 3600, 900 * 2 ** min(max(failures - 1, 0), 5))
            state, phase, wake = 'retry', work['phase'], finished + backoff
            if outcome == 'shipped' and record.get('worker_exit') == 0 and record.get('worker'):
                state, phase, wake = 'done', 'done', 0
            elif outcome == 'shipped-deploy-failed':
                state, phase = 'retry', 'verifying'
            elif result.get('needs_input') is True and str(result.get('input_question') or '').strip():
                state, reason, wake = 'human', result['input_question'], 0
                snapshot = read(self.root / 'runs' / stamp / 'linear-snapshot-post.json') or read(
                    self.root / 'runs' / stamp / 'linear-snapshot.json', {})
                baseline = human_inputs(json.loads(work['plan']), snapshot)
                if baseline is not None:
                    self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                    ('human_inputs:' + work['id'], json.dumps(baseline)))
            elif outcome in {'quota-paused', 'skipped-quota'}:
                future = [p.get('reset_epoch') for p in (record.get('quota') or {}).get('providers', [])
                          if isinstance(p.get('reset_epoch'), (int, float)) and p['reset_epoch'] > finished]
                wake = min(future) if future else finished + 3600
            self.db.execute('UPDATE work SET phase=?,state=?,reason=?,not_before=?,updated=? WHERE id=?',
                            (phase, state, reason, wake, time.time(), work['id']))

    def hold(self, repo, now=None):
        now = time.time() if now is None else now
        work = [w for w in self.list(repo) if w['state'] != 'done']
        if not work:
            return None
        if any(w['state'] not in {'human'} and w['not_before'] <= now for w in work):
            return None
        human = next((w for w in work if w['state'] == 'human'), None)
        w = human or min(work, key=lambda w: w['not_before'])
        return {'state': w['state'], 'reason': w['reason'], 'not_before': w['not_before']}

    def release_human(self, wid, reason):
        if not reason.strip():
            raise ValueError('record what changed before releasing a human blocker')
        with self.db:
            self.db.execute("UPDATE work SET state='eligible',reason=?,not_before=0,updated=? WHERE id=? AND state='human'",
                            (reason, time.time(), wid))


def eligibility_fingerprint(repo, code, snapshot, skill_root):
    from linearscope import scope_issues
    relevant = scope_issues(snapshot.get('issues', []), repo)
    inputs = [{k: i.get(k) for k in ('id', 'title', 'description', 'state', 'priority', 'labels',
                                     'blocked_by', 'human_comments_sha')} for i in relevant]
    value = {'head': git(Path(code) / repo, 'rev-parse', 'HEAD'),
             'branches': git(Path(code) / repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads'),
             'dirty': git(Path(code) / repo, 'status', '--porcelain'), 'issues': inputs,
             'policy': [(p.name, p.read_text()) for p in sorted(Path(skill_root).glob('*.md'))]}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def human_inputs(plan, snapshot):
    relevant = [i for i in snapshot.get('issues', [])
                if i.get('id') == plan.get('linear_id')
                or ('advance-roadmap:directive:' + plan['repo']) in (i.get('description') or '')]
    if not snapshot.get('ok') or not relevant:
        return None
    return {i['id']: {k: i.get(k) for k in ('state', 'description', 'human_comments_sha')} for i in relevant}


def human_changed(store, work, snapshot):
    baseline = store.get('human_inputs:' + work['id'])
    current = human_inputs(work['plan'], snapshot)
    return baseline is not None and current is not None and baseline != current


def wake_humans(store, snapshot):
    for work in store.list():
        if work['state'] == 'human' and human_changed(store, work, snapshot):
            store.release_human(work['id'], 'Relevant human input changed; revalidate the saved assignment')


def process_start(pid):
    try:
        p = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True, timeout=3)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def alive(attempt):
    # Both fields are required: PID reuse is not worker survival.
    return bool(attempt.get('pid_start') and process_start(attempt.get('pid')) == attempt['pid_start'])


def active_count(store):
    """Count surviving roadmap lanes once, including workers detached from a dead controller."""
    attempts = {}
    for row in store.db.execute('SELECT * FROM attempts WHERE pid IS NOT NULL AND outcome IS NULL'):
        if alive(dict(row)):
            attempts.setdefault(row['assigned_repo'], set()).add(row['pid'])
    detached = {}
    base = store.root / 'inflight'
    for path in [*base.glob('*/*.pid'), *base.glob('*.pid')]:
        repo = path.parent.name if path.parent != base else path.name.split('--', 1)[0]
        try:
            pid = int(path.read_text().strip())
        except (OSError, ValueError):
            continue
        if process_start(pid):
            detached.setdefault(repo, set()).add(pid)
    # A run.sh wrapper and its workspace worker are the same lane, not two agents.
    return sum(max(len(attempts.get(repo, [])), len(detached.get(repo, [])))
               for repo in set(attempts) | set(detached))


def capture(store, stamp, repo, plan=None, phase='implementing', code=None):
    run = store.root / 'runs' / stamp
    plan = plan or read(run / 'orchestrator-result.json', {})
    wid = store.attempt(stamp, repo, plan)
    if not wid:
        return None
    workspace = None
    if code and plan.get('branch'):
        # Find the real existing worktree for the assigned branch, not another checkout's HEAD.
        trees = git(Path(code) / repo, 'worktree', 'list', '--porcelain') or ''
        for block in trees.split('\n\n'):
            fields = dict(line.split(' ', 1) for line in block.splitlines() if ' ' in line)
            if fields.get('branch') == 'refs/heads/' + plan['branch']:
                workspace = fields.get('worktree')
                break
    current = next((w for w in store.list(repo) if w['id'] == wid), None)
    if phase == 'ready' and plan.get('action') == 'resume_worker':
        phase = (current or {}).get('phase', phase)
    phase = phase or (current or {}).get('phase', 'implementing')
    value = {'branch': plan.get('branch'), 'next_action': (current or {}).get('checkpoint', {}).get('next_action')
             or {'ready': 'Implement the saved assignment', 'implementing': 'Continue the assigned implementation',
                 'reviewing': 'Address existing findings and finish review', 'verifying': 'Verify the existing merge/deploy'}
             .get(phase, 'Reconcile existing artifacts before taking the next action'),
             'artifacts': [str(run / n) for n in ('orchestrator-result.json', 'worker-result.json', 'reviews.jsonl',
                                                   'workspace.json', 'worker-status.json') if (run / n).exists()]}
    if workspace:
        value.update(workspace=workspace, head=git(workspace, 'rev-parse', 'HEAD'),
                     dirty_paths=(git(workspace, 'status', '--porcelain') or '')[:1800])
    ws = read(run / 'worker-status.json', {})
    if ws.get('session_id'):
        value['session_ids'] = {ws.get('used') or 'worker': ws['session_id']}
    store.checkpoint(wid, stamp, phase, value)
    return wid


def sync(store, code):
    """Idempotent import: retain source files; never revive a live or completed attempt."""
    records = {r.get('stamp'): r for r in rows(store.root / 'runs.jsonl')}
    # Only existing pending assignments authorize recovery during migration. Old failed
    # runs may be deliberately parked; historical logs alone must never revive those.
    for path in sorted((Path(code) / '.advance-roadmap').glob('pending-plan*.json')):
        saved = read(path, {})
        plan = saved.get('orchestrator') or saved
        stamp = saved.get('last_stamp') or saved.get('planned_stamp') or '00000000-000000'
        store.register(plan, stamp, path)
    authorized = {w['id'] for w in store.list()}
    for path in sorted((store.root / 'batches').glob('*/batch.json')):
        signature = [path.stat().st_mtime_ns, path.stat().st_size]
        if store.get('imported:' + str(path)) == signature:
            continue
        for item in read(path, {}).get('queue', []):
            stamp, repo = item.get('stamp'), item.get('repo')
            if not stamp or not repo:
                continue
            plan = read(store.root / 'runs' / stamp / 'orchestrator-result.json', {})
            store.attempt(stamp, repo, plan if identity(plan) in authorized else None)
        store.set('imported:' + str(path), signature)
    for stamp, record in sorted(records.items()):
        store.finish(record, read(store.root / 'runs' / stamp / 'worker-result.json', {}))
    # Recreate the compatibility projection when a controller died after committing work.
    # SQLite is authoritative; original sources and checkpoint history remain intact.
    pending = Path(code) / '.advance-roadmap'
    for work in store.list():
        if work['state'] == 'done':
            continue
        pending.mkdir(exist_ok=True)
        path = pending / ('pending-plan-' + work['repo'] + '.json')
        if path.exists() and identity((read(path, {}) or {}).get('orchestrator', {})) != work['id']:
            for index in range(1, 100):
                alternative = pending / ('pending-plan-' + work['repo'] + '.t' + str(index) + '.json')
                if not alternative.exists() or identity((read(alternative, {}) or {}).get('orchestrator', {})) == work['id']:
                    path = alternative
                    break
            else:
                raise RuntimeError('too many saved assignments for ' + work['repo'])
        if path.exists():
            continue
        value = {'orchestrator': work['plan'], 'planned_stamp': work['latest_stamp'],
                 'last_stamp': work['latest_stamp'], 'attempts': 0,
                 'planned_at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(work['created']))}
        staged = path.with_suffix('.restore.tmp')
        staged.write_text(json.dumps(value) + '\n')
        os.replace(staged, path)
    live = set()
    for row in store.db.execute('SELECT * FROM attempts WHERE pid IS NOT NULL AND outcome IS NULL'):
        if alive(dict(row)):
            live.add(row['assigned_repo'])
            if row['work_id']:
                with store.db:
                    store.db.execute("UPDATE work SET state='active',not_before=0 WHERE id=? AND state NOT IN ('done','human')",
                                     (row['work_id'],))
    return live


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    checkpoint = sub.add_parser('checkpoint')
    checkpoint.add_argument('--request-file', type=Path, required=True)
    checkpoint.add_argument('--phase', choices=sorted(PHASES), required=True)
    checkpoint.add_argument('--packet-file', type=Path)
    release = sub.add_parser('unblock')
    release.add_argument('work_id')
    release.add_argument('--reason', required=True)
    args = parser.parse_args()
    with Store(args.root) as store:
        if args.command == 'status':
            print(json.dumps(store.list(), indent=2))
        elif args.command == 'unblock':
            store.release_human(args.work_id, args.reason)
        else:
            request = read(args.request_file, {})
            plan = request.get('orchestrator', {})
            wid = store.register(plan, request['stamp'])
            if not wid:
                parser.error('checkpoint requires a saved implementation assignment')
            code = os.environ.get('PERSONAL_CODE_DIR')
            current = next((w for w in store.list(plan['repo']) if w['id'] == wid), None)
            if request.get('mode') == 'resume' and not args.packet_file and args.phase == 'implementing':
                args.phase = (current or {}).get('phase', args.phase)
            capture(store, request['stamp'], plan['repo'], plan, args.phase, code)
            value = read(args.packet_file, {}) if args.packet_file else {}
            store.checkpoint(wid, request['stamp'], args.phase, value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
