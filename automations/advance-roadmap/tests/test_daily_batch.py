"""Exercise the real batch controller with fake planners/workers, never live providers."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import daily_batch

FAKE_RUN = r'''#!/bin/zsh
exec "$FAKE_PYTHON" - <<'PY'
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['ADVANCE_ROADMAP_ROOT'])
stamp = os.environ['ADVANCE_ROADMAP_STAMP']
repo = os.environ['ADVANCE_ROADMAP_TARGET_REPO']
thread = int(os.environ.get('ADVANCE_ROADMAP_THREAD', '1'))
run = root / 'runs' / stamp
def event(kind):
    with open(root / 'trace.jsonl', 'a') as f:
        f.write(json.dumps({'kind': kind, 'repo': repo, 'thread': thread, 'stamp': stamp,
                            'time': time.time()}) + '\n')
def skip_quota(detail):
    # As run.sh's record(): the real runrecord.py, with any gates saved beside the run.
    gates = run / 'quota-gates.json'
    subprocess.run([sys.executable, str(root / 'lib/runrecord.py'), '--root', str(root), 'append',
                    '--stamp', stamp, '--status', 'skipped-quota', '--detail', detail,
                    *(['--quota-gates', str(gates)] if gates.exists() else [])], check=True)
if repo in os.environ.get('FAKE_PLANNER_GATED', '').split():
    # An earlier lane's planner limit already gated every planner: run.sh skips before planning.
    (run / 'quota-gates.json').write_text(json.dumps({
        'role': 'orchestrator', 'provider': 'claude', 'reset_epoch': 1791906600,
        'reset_at': '2026-10-13T11:50:00-04:00', 'cause': 'codex: weekly 85%; claude: session limit', 'providers': [
            {'provider': 'codex', 'state': 'over', 'cause': 'weekly 85%', 'reset_epoch': None, 'reset_at': None},
            {'provider': 'claude', 'state': 'limited', 'cause': 'session limit',
             'reset_epoch': 1791906600, 'reset_at': '2026-10-13T11:50:00-04:00'}]}))
    event('quota-skip')
    skip_quota('no orchestrator available')
    raise SystemExit(0)
event('plan-start')
time.sleep(.15 if repo == 'a' else .05)
if repo == os.environ.get('FAKE_FAILED_REPO'):
    event('plan-failed')
    raise SystemExit(1)
plan = {'action': 'dispatch_worker', 'repo': repo, 'linear_id': f'DEV-{repo}{thread}',
        'item': f'{repo} t{thread}', 'branch': f'roadmap/{repo}-{thread}', 'work_kind': 'feature',
        'touchpoints': [f'src/{repo}{thread}.ts'], 'worker_brief': 'do it', 'summary': 's'}
if thread > 1 and os.environ.get('FAKE_NO_SECOND'):
    plan = {'action': 'blocked_no_item', 'repo': None, 'summary': 'no second thread'}
(run / 'orchestrator-result.json').write_text(json.dumps(plan))
event('plan-ready')
(run / 'batch-ready').touch()
while not (run / 'batch-release').exists():
    if (run / 'batch-cancel').exists():
        event('deferred')
        skip_quota('daily batch deferred before worker launch; pending plan retained')
        raise SystemExit(0)
    time.sleep(.01)
event('work-start')
time.sleep(.2)
event('work-end')
with open(root / 'runs.jsonl', 'a') as f:
    f.write(json.dumps({'stamp': stamp, 'repo': repo, 'outcome': 'shipped'}) + '\n')
PY
'''

FAKE_APPROVER = '''import json, os, sys
a = sys.argv
req = json.load(open(a[a.index('--request') + 1]))
verdict = os.environ.get('FAKE_VERDICT', 'approve')
import time
with open(os.path.join(os.environ['ADVANCE_ROADMAP_ROOT'], 'approver.jsonl'), 'a') as f:
    f.write(json.dumps({'kind': 'start', 'repo': req['repo'], 'time': time.time()}) + '\\n')
time.sleep(float(os.environ.get('FAKE_APPROVER_SLEEP', '0')))
with open(os.path.join(os.environ['ADVANCE_ROADMAP_ROOT'], 'approver.jsonl'), 'a') as f:
    f.write(json.dumps({'kind': 'end', 'repo': req['repo'], 'time': time.time()}) + '\\n')
if verdict == 'fail':
    sys.stderr.write('approver exploded')
    raise SystemExit(1)
plans = req['plans']
if verdict == 'quota':
    out = {'ok': True, 'repo': req['repo'], 'error': 'claude: session limit', 'standing_conflicts': [],
           'decisions': [{'stamp': p['stamp'], 'thread': p['thread'], 'verdict': 'approve' if n == 0 else 'defer',
                          'reason': 'no approver ruling was obtained', 'after': None} for n, p in enumerate(plans)],
           'quota': {'role': 'approver', 'provider': 'claude', 'reset_epoch': 1791906600, 'reset_at': 'r1',
                     'cause': 'session limit', 'providers': [
                         {'provider': 'codex', 'state': 'over', 'cause': 'weekly 85%', 'reset_epoch': 1792000000,
                          'reset_at': 'r2'},
                         {'provider': 'claude', 'state': 'limited', 'cause': 'session limit',
                          'reset_epoch': 1791906600, 'reset_at': 'r1'}]}}
    json.dump(out, open(a[a.index('--out') + 1], 'w'))
    raise SystemExit(0)
decisions = [{'stamp': p['stamp'], 'thread': p['thread'], 'verdict': 'approve' if n == 0 else verdict,
              'reason': 'fake', 'after': plans[0]['stamp'] if verdict == 'serialize' else None,
              'acknowledged_conflicts': [{'area': 'ROADMAP.md', 'handling': 'rebase'}]}
             for n, p in enumerate(plans)]
json.dump({'ok': True, 'repo': req['repo'], 'decisions': decisions,
           'standing_conflicts': [{'area': 'lockfiles', 'handling': 'retake'}]},
          open(a[a.index('--out') + 1], 'w'))
'''


class DailyBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.code = self.root / 'code'
        self.code.mkdir()
        (self.root / 'lib').mkdir()
        for name in ('models.sh', 'runrecord.py', 'tokens.py'):
            shutil.copyfile(HERE / 'lib' / name, self.root / 'lib' / name)
        (self.root / 'run.sh').write_text(FAKE_RUN)
        (self.root / 'lib/usage.py').write_text(
            "import json, os, sys\n"
            "if 'quota-record' in sys.argv:\n"
            "    print(json.dumps({'role': 'worker', 'provider': 'cursor', 'reset_epoch': 1792000000,\n"
            "        'reset_at': '2026-10-24T00:00:00-04:00', 'cause': 'cursor: auto pool 96%', 'providers': [\n"
            "        {'provider': 'cursor', 'state': 'over', 'cause': 'auto pool 96%',\n"
            "         'reset_epoch': 1792000000, 'reset_at': '2026-10-24T00:00:00-04:00'},\n"
            "        {'provider': 'codex', 'state': 'ok', 'cause': None, 'reset_epoch': None, 'reset_at': None}]}))\n"
            "    raise SystemExit(0)\n"
            "if 'gates' in sys.argv:\n"
            "    print(os.environ.get('FAKE_REVIEWER_GATES', ''))\n"
            "    raise SystemExit(0)\n"
            "# FAKE_NO_QUOTA: the worker pool runs dry once planning has started (the headroom check before\n"
            "# any planner still passes); FAKE_NO_QUOTA_AT_START: it is dry before the batch plans anything.\n"
            "dry = os.environ.get('FAKE_NO_QUOTA_AT_START') or (\n"
            "    os.environ.get('FAKE_NO_QUOTA') and os.path.exists(os.path.join(os.environ['ADVANCE_ROADMAP_ROOT'], 'trace.jsonl')))\n"
            "print('' if dry else {'pick-worker-chain': 'cursor', 'pick-orchestrator': 'codex'}.get(sys.argv[-1], ''))\n")
        (self.root / 'lib/planapproval.py').write_text(FAKE_APPROVER)
        (self.root / 'lib/linearsnap.py').write_text(
            "import json, sys\njson.dump({'ok': True, 'issues': []}, open(sys.argv[-1], 'w'))\n")
        self.env = {**os.environ, 'ADVANCE_ROADMAP_ROOT': str(self.root),
                    'PERSONAL_CODE_DIR': str(self.code), 'ADVANCE_ROADMAP_BATCH_POLL_S': '.02',
                    'ADVANCE_ROADMAP_SUPERSET': '0', 'DOTFILES_PERSONAL_MACHINE': '1',
                    'ADVANCE_ROADMAP_SKILL_DIR': str(self.root / 'skills'),
                    'FAKE_PYTHON': sys.executable, 'PYTHONDONTWRITEBYTECODE': '1',
                    'ADVANCE_ROADMAP_THREADS_PER_REPO': '1', 'ADVANCE_ROADMAP_BATCH_SUMMARY': '0',
                    'ADVANCE_ROADMAP_THREAD_RELEASE_GAP_S': '0'}

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, name, owner='jnelken'):
        repo = self.code / name
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin',
                        f'git@github.com:{owner}/{name}.git'], check=True)
        return repo

    def run_batch(self, lanes=5, extra=()):
        p = subprocess.run([sys.executable, str(HERE / 'daily_batch.py'), '--lanes', str(lanes), *extra],
                           env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def events(self):
        return [json.loads(line) for line in (self.root / 'trace.jsonl').read_text().splitlines()]

    def state(self):
        return json.loads(next((self.root / 'batches').glob('*/batch.json')).read_text())

    def test_barrier_and_one_item_per_repo(self):
        for name in 'abc':
            self.repo(name)
        self.run_batch(lanes=5)
        events = self.events()
        ready = [e['time'] for e in events if e['kind'] == 'plan-ready']
        starts = [e for e in events if e['kind'] == 'work-start']
        ends = [e['time'] for e in events if e['kind'] == 'work-end']
        self.assertEqual(sorted(e['repo'] for e in starts), list('abc'))
        self.assertLess(max(ready), min(e['time'] for e in starts))
        self.assertLess(max(e['time'] for e in starts), min(ends))
        self.assertEqual(self.state()['phase'], 'done')
        self.assertTrue(all(i['outcome'] == 'shipped' for i in self.state()['queue']))
        self.assertFalse((self.root / 'run.lock').exists())

    def test_cap_smaller_than_queue_does_not_deadlock_or_refill(self):
        for name in 'abcde':
            self.repo(name)
        self.run_batch(lanes=2)
        active, peak, starts = 0, 0, []
        for e in self.events():
            if e['kind'] == 'work-start':
                active += 1
                peak = max(peak, active)
                starts.append(e['repo'])
            elif e['kind'] == 'work-end':
                active -= 1
        self.assertEqual(peak, 2)
        self.assertEqual(sorted(starts), list('abcde'))

    def test_duplicate_tick_cannot_start_second_batch(self):
        self.repo('a')
        self.run_batch()
        before = self.events()
        self.run_batch()
        self.assertEqual(before, self.events())

    def test_concurrency_cannot_exceed_five_or_be_unlimited(self):
        for lanes in (0, 6, 8):
            for override in ('argument', 'environment'):
                with self.subTest(lanes=lanes, override=override):
                    cmd = [sys.executable, str(HERE / 'daily_batch.py')]
                    env = dict(self.env)
                    if override == 'argument':
                        cmd += ['--lanes', str(lanes)]
                    else:
                        env['ADVANCE_ROADMAP_LANES'] = str(lanes)
                    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(p.returncode, 2)
                    self.assertIn('between 1 and 5 agents', p.stderr)
        self.assertFalse((self.root / 'batches').exists())

    def test_quota_defers_without_implementation_or_retry(self):
        self.repo('a')
        self.env['FAKE_NO_QUOTA'] = '1'
        self.run_batch()
        self.assertEqual([e['kind'] for e in self.events()], ['plan-start', 'plan-ready', 'deferred'])
        self.assertEqual(self.state()['phase'], 'done')

    def test_quota_deferred_plans_are_in_the_quota_summary(self):
        for name in 'ab':
            self.repo(name)
        self.env['FAKE_NO_QUOTA'] = '1'
        self.run_batch()
        state = self.state()
        self.assertEqual([i['outcome'] for i in state['queue']], ['skipped-quota', 'skipped-quota'])
        self.assertEqual([(q['provider'], q['role'], sorted(q['repos']), q['reset_epoch'])
                          for q in state['quota_paused']], [('cursor', 'worker', ['a', 'b'], 1792000000)])
        self.assertIn('cursor out of quota for 2 repo(s)', daily_batch.quota_line(state))

    def test_lanes_gated_by_an_earlier_planner_limit_are_in_the_quota_summary(self):
        for name in 'abc':
            self.repo(name)
        self.env['FAKE_PLANNER_GATED'] = 'b c'
        self.run_batch()
        state = self.state()
        self.assertEqual([(i['repo'], i['outcome']) for i in state['queue']],
                         [('a', 'shipped'), ('b', 'skipped-quota'), ('c', 'skipped-quota')])
        self.assertEqual([(q['provider'], q['role'], sorted(q['repos']), q['reset_at'], q['cause'])
                          for q in state['quota_paused']],
                         [('codex', 'orchestrator', ['b', 'c'], None, 'weekly 85%'),
                          ('claude', 'orchestrator', ['b', 'c'], '2026-10-13T11:50:00-04:00', 'session limit')])

    def test_quota_summary_merges_pauses_and_skips_per_provider(self):
        q = lambda epoch: {'provider': 'claude', 'role': 'orchestrator', 'reset_epoch': epoch,
                           'reset_at': f'at-{epoch}', 'cause': 'limit'}
        state = {'queue': [{'repo': 'a', 'outcome': 'quota-paused', 'quota': q(100)},
                           {'repo': 'b', 'outcome': 'skipped-quota', 'quota': q(200)},
                           {'repo': 'c', 'outcome': 'skipped-quota'},
                           {'repo': 'd', 'outcome': 'error', 'quota': q(300)}]}
        self.assertEqual(daily_batch.quota_paused(state),
                         [{'provider': 'claude', 'role': 'orchestrator', 'repos': ['a', 'b'],
                           'reset_epoch': 200, 'reset_at': 'at-200', 'cause': 'limit'}])

    def test_quota_summary_counts_every_listed_provider_and_each_repo_once(self):
        # Two threads of repo a, each stopped with both workers out: one repo per provider.
        row = {'role': 'worker', 'provider': 'codex', 'reset_epoch': 100, 'cause': 'joined', 'providers': [
            {'provider': 'cursor', 'state': 'over', 'reason': 'auto pool 96%', 'reset_epoch': 500, 'reset_at': 'r500'},
            {'provider': 'codex', 'state': 'limited', 'cause': 'usage limit', 'reset_epoch': 100, 'reset_at': 'r100'}]}
        state = {'queue': [{'repo': 'a', 'thread': 1, 'outcome': 'quota-paused', 'quota': row},
                           {'repo': 'a', 'thread': 2, 'outcome': 'skipped-quota', 'quota': row}]}
        summary = daily_batch.quota_paused(state)
        self.assertEqual([(q['provider'], q['repos'], q['reset_at'], q['cause']) for q in summary],
                         [('cursor', ['a'], 'r500', 'auto pool 96%'), ('codex', ['a'], 'r100', 'usage limit')])
        self.assertEqual(daily_batch.quota_line({'quota_paused': summary}),
                         'cursor out of quota for 1 repo(s), resets r500; '
                         'codex out of quota for 1 repo(s), resets r100.')

    def test_failed_planner_does_not_hold_barrier(self):
        for name in 'ab':
            self.repo(name)
        self.env['FAKE_FAILED_REPO'] = 'a'
        self.run_batch()
        self.assertEqual([e['repo'] for e in self.events() if e['kind'] == 'work-start'], ['b'])

    def test_candidate_filter_and_dry_run_have_no_side_effects(self):
        self.repo('a')
        self.repo('company', owner='Concentro-Inc')
        (self.repo('disabled') / '.noroadmap').touch()
        self.repo('spoof', owner='not-jnelken')
        (self.code / 'alias').symlink_to(self.code / 'a')
        p = self.run_batch(extra=['--dry-run'])
        self.assertEqual(json.loads(p.stdout)['repos'], ['a'])
        self.assertFalse((self.root / 'batches').exists())

    def test_empty_queue_finishes(self):
        self.run_batch()
        self.assertEqual(self.state()['queue'], [])
        self.assertEqual(self.state()['phase'], 'done')

    def test_live_prior_worker_repo_is_deferred(self):
        self.repo('a')
        (self.root / 'inflight').mkdir()
        (self.root / 'inflight/a--old.pid').write_text(str(os.getpid()))
        self.run_batch()
        self.assertEqual(self.state()['queue'][0]['status'], 'deferred')
        self.assertFalse((self.root / 'trace.jsonl').exists())

    # ── threads ───────────────────────────────────────────────────────────────

    def threaded(self, **extra):
        self.env.update({'ADVANCE_ROADMAP_THREADS_PER_REPO': '2', **extra})
        self.repo('a')
        self.run_batch(lanes=5)
        return self.events()

    def starts(self, events):
        return sorted((e['repo'], e['thread']) for e in events if e['kind'] == 'work-start')

    def test_approved_second_thread_runs_beside_the_first(self):
        events = self.threaded(FAKE_VERDICT='approve')
        self.assertEqual(self.starts(events), [('a', 1), ('a', 2)])
        ready = max(e['time'] for e in events if e['kind'] == 'plan-ready')
        self.assertLess(ready, min(e['time'] for e in events if e['kind'] == 'work-start'))
        queue = self.state()['queue']
        self.assertEqual([(i['thread'], i['outcome']) for i in queue], [(1, 'shipped'), (2, 'shipped')])
        self.assertEqual(queue[1]['parent'], queue[0]['stamp'])
        request = json.loads(next((self.root / 'batches').glob('*/approval-a-request.json')).read_text())
        self.assertEqual([p['thread'] for p in request['plans']], [1, 2])
        note = json.loads((self.root / 'runs' / queue[1]['stamp'] / 'batch-approval.json').read_text())
        self.assertEqual((note['verdict'], note['siblings'][0]['thread']), ('approve', 1))
        self.assertEqual(note['acknowledged_conflicts'][0]['area'], 'ROADMAP.md')
        self.assertEqual(note['standing_conflicts'][0]['area'], 'lockfiles')

    def test_approvals_for_different_repos_run_side_by_side(self):
        self.env.update({'ADVANCE_ROADMAP_THREADS_PER_REPO': '2', 'FAKE_VERDICT': 'approve',
                         'FAKE_APPROVER_SLEEP': '.4'})
        self.repo('a')
        self.repo('b')
        self.run_batch(lanes=5)
        rows = [json.loads(line) for line in (self.root / 'approver.jsonl').read_text().splitlines()]
        starts = [r['time'] for r in rows if r['kind'] == 'start']
        ends = [r['time'] for r in rows if r['kind'] == 'end']
        self.assertEqual(len(starts), 2)
        self.assertLess(max(starts), min(ends))

    def test_second_thread_sees_the_first_threads_plan(self):
        self.threaded(FAKE_VERDICT='approve')
        stamp = self.state()['queue'][1]['stamp']
        peers = json.loads((self.root / 'runs' / stamp / 'peer-plans.json').read_text())
        self.assertEqual([(p['thread'], p['touchpoints']) for p in peers], [(1, ['src/a1.ts'])])

    def test_serialized_second_thread_starts_after_the_first_finishes(self):
        events = self.threaded(FAKE_VERDICT='serialize')
        self.assertEqual(self.starts(events), [('a', 1), ('a', 2)])
        end_one = next(e['time'] for e in events if e['kind'] == 'work-end' and e['thread'] == 1)
        start_two = next(e['time'] for e in events if e['kind'] == 'work-start' and e['thread'] == 2)
        self.assertLessEqual(end_one, start_two)

    def test_deferred_second_thread_never_starts_work(self):
        events = self.threaded(FAKE_VERDICT='defer')
        self.assertEqual(self.starts(events), [('a', 1)])
        self.assertIn(('deferred', 2), [(e['kind'], e['thread']) for e in events])
        self.assertEqual(self.state()['queue'][0]['outcome'], 'shipped')

    def test_failed_approver_defers_extra_threads_but_not_the_first(self):
        events = self.threaded(FAKE_VERDICT='fail')
        self.assertEqual(self.starts(events), [('a', 1)])
        self.assertEqual(self.state()['phase'], 'done')

    def test_approver_out_of_quota_puts_every_approver_in_the_quota_summary(self):
        events = self.threaded(FAKE_VERDICT='quota')
        self.assertEqual(self.starts(events), [('a', 1)])
        state = self.state()
        self.assertEqual([(i['thread'], i['outcome']) for i in state['queue']], [(1, 'shipped'), (2, 'skipped-quota')])
        self.assertEqual([(q['provider'], q['role'], q['repos'], q['reset_epoch']) for q in state['quota_paused']],
                         [('codex', 'approver', ['a'], 1792000000), ('claude', 'approver', ['a'], 1791906600)])

    def test_missing_approver_defers_extra_threads(self):
        (self.root / 'lib/planapproval.py').unlink()
        events = self.threaded()
        self.assertEqual(self.starts(events), [('a', 1)])

    def test_repo_with_no_second_item_needs_no_approval(self):
        # Thread 2 ran, but only to find nothing: no approver call, no second piece of work.
        events = self.threaded(FAKE_NO_SECOND='1')
        self.assertEqual(self.starts(events), [('a', 1), ('a', 2)])
        self.assertFalse(list((self.root / 'batches').glob('*/approval-a-request.json')))

    def test_thread_cap_is_respected(self):
        events = self.threaded(ADVANCE_ROADMAP_THREADS_PER_REPO='3', FAKE_VERDICT='approve')
        self.assertEqual(self.starts(events), [('a', 1), ('a', 2), ('a', 3)])
        events = [e for e in events if e['kind'] == 'plan-start']
        self.assertEqual(len(events), 3)

    def test_lane_cap_bounds_threads_not_just_repos(self):
        self.env.update({'ADVANCE_ROADMAP_THREADS_PER_REPO': '2', 'FAKE_VERDICT': 'approve'})
        self.repo('a')
        self.repo('b')
        self.run_batch(lanes=2)
        active, peak = 0, 0
        for e in self.events():
            if e['kind'] == 'work-start':
                active += 1
                peak = max(peak, active)
            elif e['kind'] == 'work-end':
                active -= 1
        self.assertEqual(peak, 2)
        self.assertEqual(len(self.starts(self.events())), 4)

    def test_one_summary_after_every_thread_has_stopped(self):
        if not shutil.which('node'):
            self.skipTest('node is not installed')
        slack = self.root / 'slackagent'
        (slack / 'scripts').mkdir(parents=True)
        (slack / 'scripts/post-roadmap-summary.mjs').write_text(
            "import { appendFileSync, readFileSync } from 'node:fs';\n"
            "const i = process.argv.indexOf('--batch');\n"
            "const state = JSON.parse(readFileSync(process.argv[i + 1], 'utf8'));\n"
            f"appendFileSync({json.dumps(str(self.root / 'slack-posts.jsonl'))}, "
            "JSON.stringify({time: Date.now() / 1000, phase: state.phase, lanes: state.queue.length}) + '\\n');\n")
        events = self.threaded(FAKE_VERDICT='approve', ADVANCE_ROADMAP_BATCH_SUMMARY='1',
                               ADVANCE_ROADMAP_SLACKAGENT_DIR=str(slack))
        posts = [json.loads(line) for line in (self.root / 'slack-posts.jsonl').read_text().splitlines()]
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0]['phase'], posts[0]['lanes']), ('done', 2))
        self.assertGreaterEqual(posts[0]['time'], max(e['time'] for e in events if e['kind'] == 'work-end'))
        self.assertTrue(self.state()['summary_posted'])

    def test_failed_summary_never_fails_the_batch(self):
        if not shutil.which('node'):
            self.skipTest('node is not installed')
        slack = self.root / 'slackagent'
        (slack / 'scripts').mkdir(parents=True)
        (slack / 'scripts/post-roadmap-summary.mjs').write_text("process.exit(3);\n")
        self.repo('a')
        self.env.update({'ADVANCE_ROADMAP_BATCH_SUMMARY': '1', 'ADVANCE_ROADMAP_SLACKAGENT_DIR': str(slack),
                         'ADVANCE_ROADMAP_SLACK_WEBHOOK': '', 'SLACK_CCUSAGE_WEBHOOK_URL': '',
                         'ADVANCE_ROADMAP_SECRETS': str(self.root / 'none.env')})
        self.run_batch()
        self.assertFalse(self.state()['summary_posted'])
        self.assertEqual(self.state()['phase'], 'done')


    def test_dashboard_schedule_preserves_history_and_uses_one_daily_slot(self):
        with patch.dict(os.environ, self.env):
            spec = importlib.util.spec_from_file_location('batch_dashboard', HERE / 'gen-dashboard.py')
            dashboard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dashboard)
        with patch.object(dashboard, 'base_cadence', return_value=1):
            self.assertEqual(len(dashboard.slots_on(datetime(2026, 10, 8))), 10)
            self.assertEqual(dashboard.slots_on(datetime(2026, 10, 10)), [datetime(2026, 10, 10, 10, 30)])


if __name__ == '__main__':
    unittest.main()
