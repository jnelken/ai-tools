"""Exercise the real batch controller with fake planners/workers, never live providers."""
import hashlib
import time
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
from types import SimpleNamespace

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
        'reset_at': '2026-10-13T11:50:00-04:00', 'cause': 'codex: weekly 90%; claude: session limit', 'providers': [
            {'provider': 'codex', 'state': 'over', 'cause': 'weekly 90%', 'reset_epoch': None, 'reset_at': None},
            {'provider': 'claude', 'state': 'limited', 'cause': 'session limit',
             'reset_epoch': 1791906600, 'reset_at': '2026-10-13T11:50:00-04:00'}]}))
    event('quota-skip')
    skip_quota('no orchestrator available')
    raise SystemExit(0)
event('plan-start')
time.sleep(float(os.environ.get('FAKE_SLOW_PLAN', '.8')) if repo == os.environ.get('FAKE_SLOW_REPO') else (.15 if repo == 'a' else .05))
if repo == os.environ.get('FAKE_FAILED_REPO'):
    event('plan-failed')
    raise SystemExit(1)
plan = {'action': 'dispatch_worker', 'repo': repo, 'linear_id': f'DEV-{repo}{thread}',
        'item': f'{repo} t{thread}', 'branch': f'roadmap/{repo}-{thread}', 'work_kind': 'feature',
        'touchpoints': [f'src/{repo}{thread}.ts'], 'worker_brief': 'do it', 'summary': 's'}
if os.environ.get('FAKE_NO_WORK'):  # a no-work verdict whose blocker still needs a bookkeeping worker
    plan = {'action': 'blocked_no_item', 'outcome_token': 'blocked-no-item', 'repo': repo, 'summary': 'none',
            'blockers': [{'repo': repo, 'linear_id': 'DEV-9', 'detail': 'needs a decision'}]}
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
yield_marker = root / ('yielded-once-' + repo)
should_yield = bool(os.environ.get('FAKE_YIELD_ONCE') and not yield_marker.exists())
if should_yield:
    yield_marker.touch()
    (run / 'worker-result.json').write_text(json.dumps({'outcome': 'yielded',
        'provider': 'codex', 'next_phase': 'implementing'}))
if not os.environ.get('FAKE_KEEP_PENDING') and not should_yield:
    pending = Path(os.environ['PERSONAL_CODE_DIR']) / '.advance-roadmap'
    for saved in pending.glob(f'pending-plan-{repo}*.json'):
        saved.unlink()
outcome = 'yielded' if should_yield else 'shipped'
if repo == os.environ.get('FAKE_HUMAN_REPO'):
    outcome = 'blocked'
    (run / 'worker-result.json').write_text(json.dumps({'outcome': 'blocked-branch-left',
        'needs_input': True, 'input_question': 'Which account should this use?'}))
with open(root / 'runs.jsonl', 'a') as f:
    f.write(json.dumps({'stamp': stamp, 'repo': repo, 'outcome': outcome}) + '\n')
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
                         {'provider': 'codex', 'state': 'over', 'cause': 'weekly 90%', 'reset_epoch': 1792000000,
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
            "    role = sys.argv[sys.argv.index('--role') + 1]\n"
            "    print(os.environ.get('FAKE_QUOTA_' + role.upper()) or json.dumps({'role': 'worker', 'provider': 'cursor', 'reset_epoch': 1792000000,\n"
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
            "if 'budget-input' in sys.argv:\n"
            "    gates = json.loads(os.environ.get('FAKE_REVIEWER_GATES') or '{}').get('gates', [])\n"
            "    print(json.dumps({'workers': [] if dry else os.environ.get('FAKE_WORKER_CHAIN', 'codex').split(),\n"
            "                      'planners': ['codex'], 'reviewer_gates': gates}))\n"
            "    raise SystemExit(0)\n"
            "print('' if dry else {'pick-worker-chain': os.environ.get('FAKE_WORKER_CHAIN', 'codex'), 'pick-orchestrator': 'codex'}.get(sys.argv[-1], ''))\n")
        (self.root / 'lib/workspaces.py').write_text(
            "import os, sys\nsys.exit(1 if os.environ.get('FAKE_SUPERSET_DOWN') and 'available' in sys.argv else 0)\n")
        (self.root / 'lib/planapproval.py').write_text(FAKE_APPROVER)
        (self.root / 'lib/linearsnap.py').write_text(
            "import json, sys\njson.dump({'ok': True, 'issues': []}, open(sys.argv[-1], 'w'))\n")
        state = daily_batch.usage.empty_state()
        now = time.time()
        state['providers']['codex'].update(observed_epoch=now,
            five_hour=dict(used_pct=0, reset_epoch=now+86400), weekly=dict(used_pct=0, reset_epoch=now+86400))
        state['providers']['cursor']['included'].update(observed_at=now, auto_pct=0, api_pct=0, reset_epoch=now+86400)
        (self.root / 'providers-usage.json').write_text(json.dumps(state))
        quota_dir = self.root / '.claude/state'
        quota_dir.mkdir(parents=True)
        (quota_dir / 'quota-snapshot-cache.json').write_text(json.dumps(dict(
            identity=hashlib.sha256(b'no-subscription').hexdigest(), attempted_at=now+3600,
            observed_at=now, source='oauth_usage', windows={
                'five_hour': {'used_percentage': 0}, 'seven_day': {'used_percentage': 0}})))
        self.env = {**os.environ, 'HOME': str(self.root),
                    'AGENT_BUDGET_CLAUDE_HOME': str(self.root / '.claude'),
                    'ADVANCE_ROADMAP_CODEX_SESSIONS': str(self.root / '.codex/sessions'),
                    'ADVANCE_ROADMAP_USAGE_PROBES': '0', 'ANTHROPIC_API_KEY': 'test-disable-oauth',
                    'CLAUDE_CODE_OAUTH_TOKEN': '', 'CLAUDE_CONFIG_DIR': str(self.root / '.claude'), 'ADVANCE_ROADMAP_ROOT': str(self.root),
                    'PERSONAL_CODE_DIR': str(self.code), 'ADVANCE_ROADMAP_BATCH_POLL_S': '.02',
                    'ADVANCE_ROADMAP_INCREMENTAL': '0',
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

    def test_review_fallback_note_tracks_each_reservation_refresh_in_both_directions(self):
        class ChangingBudget:
            def __init__(self):
                self.decisions = [
                    {'strategy': 'cursor-then-codex', 'reason': None},
                    {'strategy': 'cross-provider', 'reason': 'cursor gated until reset'},
                    {'strategy': 'cursor-then-codex', 'reason': None},
                ]
                self.review_strategy = None

            def refresh(self):
                self.review_strategy = self.decisions.pop(0)

        batch = daily_batch.Batch.__new__(daily_batch.Batch)
        batch.budget = ChangingBudget()
        batch.state = {'review_fallback': 'review fallback: cross-provider (stale)'}

        batch.refresh_budget()
        self.assertNotIn('review_fallback', batch.state)
        batch.refresh_budget()
        self.assertEqual(batch.state['review_fallback'], 'review fallback: cross-provider (cursor gated until reset)')
        batch.refresh_budget()
        self.assertNotIn('review_fallback', batch.state)

    def test_continuous_budget_refresh_is_throttled(self):
        budget = SimpleNamespace(review_strategy={'strategy': 'cross-provider', 'reason': None})
        with patch.dict(os.environ, {'ADVANCE_ROADMAP_CONTINUOUS': '1'}), \
                patch.object(daily_batch.time, 'monotonic', return_value=110), \
                patch.object(daily_batch, 'ROOT', self.root):
            batch = daily_batch.Batch(self.root, [], lanes=1)
            batch.budget = budget
            batch._budget_refreshed_at = 100
            batch.refresh_budget()  # A second quota probe would fail: this budget has no refresh method.

    def events(self):
        return [json.loads(line) for line in (self.root / 'trace.jsonl').read_text().splitlines()]

    def state(self):
        return json.loads(next((self.root / 'batches').glob('*/batch.json')).read_text())

    def trace_exists(self):
        return (self.root / 'trace.jsonl').exists()

    def rows(self):
        return [json.loads(line) for line in (self.root / 'runs.jsonl').read_text().splitlines()]

    # ── a no-work verdict survives a gated worker pool (F1a) ─────────────────

    def test_no_work_plans_leave_the_barrier_when_the_worker_pool_runs_dry(self):
        self.repo('a')
        self.env.update(FAKE_NO_QUOTA='1', FAKE_NO_WORK='1')
        self.run_batch()  # a lane left `ready` forever would hit this call's 30s timeout
        self.assertEqual([e['kind'] for e in self.events()], ['plan-start', 'plan-ready', 'deferred'])
        self.assertEqual([i['outcome'] for i in self.state()['queue']], ['skipped-quota'])

    def test_no_work_plans_are_released_when_a_worker_is_available(self):
        self.repo('a')
        self.env['FAKE_NO_WORK'] = '1'
        self.run_batch()
        self.assertEqual([e['kind'] for e in self.events()], ['plan-start', 'plan-ready', 'work-start', 'work-end'])

    def test_defer_ready_cancels_only_dispatch_and_resume_plans(self):
        batch = daily_batch.Batch.__new__(daily_batch.Batch)
        actions = ['dispatch_worker', 'resume_worker', 'blocked_no_item', 'nothing_qualified']
        batch.state = {'queue': [{'stamp': f'20261013-10300{n}', 'status': 'ready', 'action': a}
                                 for n, a in enumerate(actions)]}
        for item in batch.state['queue']:
            (self.root / 'runs' / item['stamp']).mkdir(parents=True)
        with patch.object(daily_batch, 'ROOT', self.root):
            batch.defer_ready('no worker quota')
            self.assertEqual([i['status'] for i in batch.state['queue']], ['canceling', 'canceling', 'ready', 'ready'])
            batch.defer_bookkeeping('no worker quota')
        self.assertEqual([i['status'] for i in batch.state['queue']], ['canceling'] * 4)

    # ── A4: plan only what can be implemented ────────────────────────────────

    def test_no_worker_headroom_plans_nothing_and_touches_no_carried_state(self):
        for name in 'ab':
            self.repo(name)
        verdicts, pending = self.root / 'batch-verdicts', self.code / '.advance-roadmap'
        verdicts.mkdir(), pending.mkdir()
        (verdicts / 'a.json').write_text('{"stamp": "carried"}')
        (pending / 'pending-plan-b.json').write_text('{"plan": "kept"}')
        self.env['FAKE_NO_QUOTA_AT_START'] = '1'
        self.run_batch()
        self.assertFalse(self.trace_exists(), 'no planner may launch')
        state = self.state()
        self.assertEqual((state['phase'], [i['outcome'] for i in state['queue']]), ('skipped', ['skipped-quota'] * 2))
        self.assertIn('no worker provider is under its quota gate', state['skipped_reason'])
        self.assertEqual([r['outcome'] for r in self.rows()], ['skipped-quota', 'skipped-quota'])
        self.assertEqual([r['repo'] for r in self.rows()], ['a', 'b'])
        self.assertIn('planning skipped', self.rows()[0]['detail'])
        self.assertEqual(self.rows()[0]['quota']['provider'], 'cursor', 'quota-record names who is out')
        self.assertEqual((verdicts / 'a.json').read_text(), '{"stamp": "carried"}')
        self.assertEqual((pending / 'pending-plan-b.json').read_text(), '{"plan": "kept"}')
        self.assertEqual([q['provider'] for q in state['quota_paused']], ['cursor'])

    def test_a_gated_required_reviewer_plans_nothing(self):
        self.repo('a')
        self.env['FAKE_REVIEWER_GATES'] = json.dumps({'role': 'reviewer', 'gates': [
            {'provider': 'codex', 'state': 'over', 'reason': '5h 100% >= 70%'},
            {'provider': 'cursor', 'state': 'ok', 'reason': None}, {'provider': 'claude', 'state': 'ok'}]})
        self.run_batch()
        self.assertFalse(self.trace_exists())
        self.assertIn('required codex reviewer is gated (5h 100% >= 70%)', self.state()['skipped_reason'])

    def test_cursor_gate_automatically_admits_codex_with_claude_review(self):
        self.repo('a')
        self.env.update(FAKE_WORKER_CHAIN='codex', FAKE_REVIEWER_GATES=json.dumps({'gates': [
            {'provider': 'cursor', 'state': 'over', 'reason': 'auto pool 100%',
             'reset_at': '2026-11-03T10:30:00-05:00'},
            {'provider': 'codex', 'state': 'ok'}, {'provider': 'claude', 'state': 'ok'}]}))
        self.run_batch()
        state = self.state()
        self.assertTrue(self.trace_exists(), 'Codex work can be reviewed by Claude')
        self.assertEqual(state['review_fallback'],
                         'review fallback: cross-provider (cursor gated until 2026-11-03T10:30:00-05:00)')

    def test_cursor_gate_does_not_admit_claude_when_it_is_its_only_reviewer(self):
        self.repo('a')
        self.env.update(FAKE_WORKER_CHAIN='claude', FAKE_REVIEWER_GATES=json.dumps({'gates': [
            {'provider': 'cursor', 'state': 'over', 'reason': 'auto pool 100%',
             'reset_at': '2026-11-03T10:30:00-05:00'},
            {'provider': 'codex', 'state': 'over'}, {'provider': 'claude', 'state': 'ok'}]}))
        self.run_batch()
        state = self.state()
        self.assertFalse(self.trace_exists(), 'Claude cannot review its own work')
        self.assertEqual(state['phase'], 'skipped')
        self.assertIn('cross-provider reviewer', state['skipped_reason'])

    def test_cursor_available_keeps_default_two_stage_admission(self):
        self.repo('a')
        self.env['FAKE_REVIEWER_GATES'] = json.dumps({'gates': [
            {'provider': 'cursor', 'state': 'ok'}, {'provider': 'codex', 'state': 'ok'},
            {'provider': 'claude', 'state': 'ok'}]})
        self.run_batch()
        state = self.state()
        self.assertTrue(self.trace_exists())
        self.assertNotIn('review_fallback', state)

    def test_reviewer_admission_skip_records_the_reviewer_gate(self):
        self.repo('a')
        self.env.update(FAKE_REVIEWER_GATES=json.dumps({'gates': [
            {'provider': 'codex', 'state': 'over', 'reason': '5h 99% >= 70%'},
            {'provider': 'cursor', 'state': 'ok'}, {'provider': 'claude', 'state': 'ok'}]}))
        # Healthy worker pool; the reviewer role alone names the exhausted provider.
        self.env.update(FAKE_QUOTA_WORKER=json.dumps({'role': 'worker', 'provider': None}),
                        FAKE_QUOTA_REVIEWER=json.dumps({
            'role': 'reviewer', 'provider': 'codex', 'reset_epoch': 1792000000,
            'reset_at': 'reset', 'cause': '5h 99% >= 70%'}))
        self.run_batch()
        self.assertFalse(self.trace_exists())
        row = self.rows()[0]
        self.assertEqual((row['quota']['role'], row['quota']['provider']), ('reviewer', 'codex'))
        self.assertEqual(row['quota']['reset_at'], 'reset')
        self.assertIn('5h 99%', row['quota']['cause'])

    def test_cross_provider_cannot_admit_a_worker_as_its_own_only_reviewer(self):
        self.repo('a')
        self.env.update(ADVANCE_ROADMAP_REVIEW_STRATEGY='cross-provider',
                        FAKE_WORKER_CHAIN='cursor', FAKE_REVIEWER_GATES=json.dumps({
            'gates': [{'provider': 'cursor', 'state': 'ok'}, {'provider': 'codex', 'state': 'ok'},
                      {'provider': 'claude', 'state': 'ok'}]}))
        self.run_batch()
        self.assertFalse(self.trace_exists(), 'a Cursor-only provider chain is filtered')
        self.assertIn('no worker provider is under its quota gate', self.state()['skipped_reason'])

    def test_cross_provider_review_needs_only_one_reviewer(self):
        self.repo('a')
        self.env.update(ADVANCE_ROADMAP_REVIEW_STRATEGY='cross-provider', FAKE_REVIEWER_GATES=json.dumps({
            'gates': [{'provider': 'codex', 'state': 'over'}, {'provider': 'cursor', 'state': 'limited'},
                      {'provider': 'claude', 'state': 'ok'}]}))
        self.run_batch()
        self.assertTrue(self.trace_exists())
        self.env['FAKE_REVIEWER_GATES'] = json.dumps({'gates': [{'provider': p, 'state': 'over'}
                                                                for p in ('codex', 'cursor', 'claude')]})
        (self.root / 'trace.jsonl').unlink()
        shutil.rmtree(self.root / 'batches')
        self.run_batch()
        self.assertFalse(self.trace_exists())
        self.assertEqual(self.state()['skipped_reason'], 'every reviewer is gated')

    def test_unreadable_reviewer_gates_never_block_planning(self):
        self.repo('a')  # the fake prints nothing for `gates`
        self.run_batch()
        self.assertTrue(self.trace_exists())

    def budget_context(self):
        # Enter with patch.dict/patch.object so the real in-process budget uses
        # exactly the same isolated account as the subprocess batch tests.
        return patch.dict(os.environ, self.env)

    def test_live_budget_limits_planning_and_waits_for_released_jobs_to_finish(self):
        for repo in 'abcde':
            self.repo(repo)
        state_path = self.root / 'providers-usage.json'
        state = json.loads(state_path.read_text())
        state['providers']['codex']['five_hour']['used_pct'] = 64
        state_path.write_text(json.dumps(state))
        self.run_batch(lanes=5)
        active, maximum = 0, 0
        for event in self.events():
            if event['kind'] == 'plan-start':
                active += 1
                maximum = max(maximum, active)
            elif event['kind'] in {'plan-ready', 'plan-failed'}:
                active -= 1
        self.assertEqual(maximum, 1)
        self.assertLessEqual(len([e for e in self.events() if e['kind'] == 'work-start']), 5)
        self.assertTrue(self.state()['budget']['held'])
        self.assertEqual(self.state()['budget']['providers']['codex']['reserved_pct'], 0)

    def test_fractional_expectations_at_barrier_cannot_deadlock_the_batch(self):
        for repo in 'abcde':
            self.repo(repo)
        state_path = self.root / 'providers-usage.json'
        state = json.loads(state_path.read_text())
        state['providers']['codex']['five_hour']['used_pct'] = 64
        state_path.write_text(json.dumps(state))
        self.run_batch(lanes=5)
        state = self.state()
        self.assertEqual(state['phase'], 'done')
        self.assertTrue(any(i['outcome'] == 'shipped' for i in state['queue']))
        self.assertTrue(state['budget']['held'])
        self.assertTrue(all(i['status'] == 'finished' for i in state['queue']))

    def test_pending_plan_can_launch_without_planner_budget(self):
        b = daily_batch.agent_budget
        (self.root / 'logs').mkdir()
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch.shared, 'LOG', None):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.dry_run = False
            budget.workers, budget.planners, budget.gates = ['claude'], ['codex'], {}
            budget.review_strategy = {'strategy': 'cross-provider', 'reason': None}
            # The raw planner gate is open, but the two remaining Codex points
            # fit its review only, not the additional three-point planner.
            budget.readings['codex']['windows']['five_hour']['live'] = 68
            batch = daily_batch.Batch(self.root, ['saved'], lanes=1)
            batch.budget = budget
            item = batch.state['queue'][0]
            item['verdict_check'] = {'priority': 0, 'result': 'pending', 'action': 'resume_worker'}
            try:
                self.assertIsNone(budget.choose(item=item, planner='codex', commit=False))
                self.assertIsNotNone(budget.choose(item=item, commit=False))
                self.assertTrue(budget.admit(item))
                self.assertIsNone(item['allocation']['planner'])
                with b.lock():
                    self.assertTrue(all('planner' not in lease['roles'] for lease in b.reap()))
                # Exercise launch's real environment construction without a model.
                started = b.process_start(os.getpid())
                with patch.object(daily_batch.subprocess, 'Popen') as spawn, patch.object(
                        b, 'process_start', return_value=started), patch.object(
                        daily_batch.workqueue, 'process_start', return_value=str(started)), patch.object(
                        daily_batch.workqueue, 'git', return_value='head'):
                    spawn.return_value.pid = os.getpid()
                    batch.launch(item)
                env = spawn.call_args.kwargs['env']
                self.assertEqual(env['ADVANCE_ROADMAP_BUDGET_PENDING'], '1')
                self.assertEqual(env['ADVANCE_ROADMAP_BUDGET_PLANNER'], 'none')
            finally:
                budget.release(item)

    def test_pending_dispatch_does_not_expand_into_fresh_threads(self):
        self.repo('a')
        pending = self.code / '.advance-roadmap'
        pending.mkdir()
        (pending / 'pending-plan-a.json').write_text(json.dumps({
            'orchestrator': {'repo': 'a', 'action': 'dispatch_worker', 'work_kind': 'feature'},
            'planned_at': datetime.now().isoformat(), 'attempts': 0}))
        state_path = self.root / 'providers-usage.json'
        state = json.loads(state_path.read_text())
        # Recovery needs only its saved implementation and review, even with extra threads enabled.
        state['providers']['codex']['five_hour']['used_pct'] = 69
        state_path.write_text(json.dumps(state))
        self.env.update(ADVANCE_ROADMAP_REVIEW_STRATEGY='cross-provider', FAKE_WORKER_CHAIN='claude')
        self.run_batch(lanes=1, extra=['--threads', '2'])
        by_thread = {i['thread']: i for i in self.state()['queue']}
        self.assertEqual(set(by_thread), {1})
        self.assertIsNone(by_thread[1]['allocation']['planner'])
        self.assertEqual(by_thread[1]['outcome'], 'shipped')
        self.assertEqual([e['thread'] for e in self.events() if e['kind'] == 'plan-start'], [1])

    def test_single_lane_barrier_preserves_changed_repo_over_all_reservations(self):
        for repo in 'abcde':
            self.repo(repo)
        state_path = self.root / 'providers-usage.json'
        state = json.loads(state_path.read_text())
        state['providers']['codex']['five_hour']['used_pct'] = 64
        state_path.write_text(json.dumps(state))
        verdicts = self.root / 'batch-verdicts'
        verdicts.mkdir()
        (verdicts / 'a.json').write_text('{"stamp":"changed"}')
        (self.root / 'lib/deploysnap.py').write_text('')
        (self.root / 'lib/verdict.py').write_text("""import json,sys
from pathlib import Path
a=sys.argv
if 'fingerprint' in a:
    Path(a[a.index('--out')+1]).write_text('{}')
else:
    Path(a[a.index('--changes')+1]).write_text(json.dumps({'changed':['repo:a']}))
    raise SystemExit(1)
""")
        self.run_batch(lanes=1)
        by_repo = {i['repo']: i for i in self.state()['queue']}
        self.assertEqual(by_repo['a']['verdict_check']['result'], 'changed')
        self.assertEqual(by_repo['a']['outcome'], 'shipped')
        self.assertEqual([e['repo'] for e in self.events() if e['kind'] == 'work-start'][0], 'a')
        self.assertTrue(any(i.get('outcome') == 'skipped-quota' for r, i in by_repo.items() if r != 'a'))
        self.assertTrue(all(i['status'] == 'finished' for i in by_repo.values()))

    def test_controller_failure_keeps_live_planner_leases_until_process_exit(self):
        self.repo('a')
        (self.root / 'run.sh').write_text(r'''#!/bin/zsh
exec "$FAKE_PYTHON" - <<'PYTHON'
import os,time
from pathlib import Path
root = Path(os.environ['ADVANCE_ROADMAP_ROOT'])
(root / 'planner-alive').touch()
while not (root / 'planner-exit').exists():
    time.sleep(.01)
PYTHON
''')
        launched = {}
        original_launch = daily_batch.Batch.launch
        original_refresh = daily_batch.RoadmapBudget.refresh

        def launch(batch, item):
            original_launch(batch, item)
            launched['proc'] = batch.procs[item['stamp']]
            launched['leases'] = set(item['budget_leases'])
            deadline = time.monotonic() + 5
            while not (self.root / 'planner-alive').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((self.root / 'planner-alive').exists())

        def refresh(budget):
            if launched:
                raise subprocess.TimeoutExpired('usage.py budget-input', 120)
            return original_refresh(budget)

        b = daily_batch.agent_budget
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch.shared, 'ROOT', self.root), patch.object(
                daily_batch.shared, 'LIB', self.root / 'lib'), patch.object(
                daily_batch.shared, 'RUN_SH', self.root / 'run.sh'), patch.object(
                daily_batch.shared, 'LOG', None), patch.object(
                daily_batch, 'POLL_S', .01), patch.object(
                daily_batch.shutil, 'which', return_value=None), patch.object(
                daily_batch.Batch, 'launch', launch), patch.object(
                daily_batch.RoadmapBudget, 'refresh', refresh), patch.object(
                sys, 'argv', ['daily_batch.py', '--lanes', '1', '--threads', '1']):
            try:
                self.assertEqual(daily_batch.main(), 1)
                proc = launched['proc']
                self.assertIsNone(proc.poll(), 'the planner is still making its fake model call')
                self.assertEqual(self.state()['queue'][0]['status'], 'planning')
                with b.lock():
                    leases = b.reap()
                self.assertTrue(launched['leases'])
                self.assertEqual({lease['id'] for lease in leases}, launched['leases'])
                self.assertTrue(all(lease['pid'] == proc.pid for lease in leases))
            finally:
                (self.root / 'planner-exit').touch()
                if launched:
                    proc = launched['proc']
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            with b.lock():
                self.assertEqual(b.reap(), [], 'the dead lane PID must free its reservations')

    def test_cursor_is_never_allocated_as_worker(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.dry_run = False
            budget.workers, budget.planners = ['cursor', 'codex', 'claude'], ['codex']
            budget.gates = {'cursor': {'provider': 'cursor', 'state': 'ok'},
                            'codex': {'provider': 'codex', 'state': 'ok'},
                            'claude': {'provider': 'claude', 'state': 'ok'}}
            routes = list(budget.routes())
            self.assertTrue(routes)
            self.assertTrue(all(worker != 'cursor' for worker, _ in routes))

    def test_expected_cost_and_conversion_follow_the_first_fitting_worker(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.object(daily_batch, 'dispatch_rates', return_value={'a': .6}):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.dry_run = False
            budget.workers, budget.planners, budget.gates = ['cursor', 'codex', 'claude'], ['codex'], {}
            item = {'repo': 'a', 'stamp': 'one'}
            self.assertTrue(budget.admit(item))
            allocation = item['allocation']
            self.assertEqual(allocation['worker'], 'codex')
            self.assertEqual([r['scale'] for r in allocation['requests'] if r['role'] != 'planner'], [.6, .6, .6])
            self.assertTrue(any(r['role'] == 'planner' for r in allocation['requests']))
            budget.planned(item)
            self.assertTrue(all(r['role'] != 'planner' for r in item['allocation']['requests']))
            # Cursor still has quota by its raw gate, but a full worker+review
            # no longer fits. The next worker in chain must be tried.
            budget.readings['cursor']['windows']['auto']['live'] = 94
            converted = budget.choose(item=item)
            self.assertIn(converted['worker'], ('codex', 'claude'))
            self.assertEqual(converted['reviewers'], ['cursor', 'codex'])
            self.assertEqual({r.get('scale', 1) for r in converted['requests']}, {1})
            with daily_batch.agent_budget.lock():
                leases = daily_batch.agent_budget.reap()
            self.assertEqual({l['id'] for l in leases}, set(item['budget_leases']))
            budget.release(item)

    def test_incremental_admission_reserves_only_current_phase(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.workers = ['claude']
            budget.review_strategy = {'strategy': 'cross-provider', 'reason': None}
            budget.gates = {'codex': {'state': 'ok'}, 'cursor': {'state': 'over'}}
            budget.models['claude']['roles']['worker']['pct']['five_hour'] = 82.12
            budget.readings['claude']['windows']['five_hour']['live'] = 7
            with patch.object(daily_batch.agent_budget, 'reserve_bundle', return_value=[]) as reserve:
                implementation = budget.choose(item={'repo': 'a', 'stamp': 'one',
                                                      'work_phase': 'implementing'}, commit=False)
                self.assertEqual(implementation['reviewers'], [])
                requests = reserve.call_args.args[0]
                self.assertAlmostEqual(requests[0]['scale'] * 82.12, 10)
                self.assertEqual([r['role'] for r in requests], ['worker'])
                review = budget.choose(item={'repo': 'a', 'stamp': 'two',
                                             'work_phase': 'reviewing'}, commit=False)
                self.assertEqual(review['reviewers'], ['codex'])
                requests = reserve.call_args.args[0]
                self.assertEqual([r['role'] for r in requests], ['worker', 'reviewer'])
                self.assertLessEqual(requests[0]['scale'] * 82.12, 10)
                self.assertLess(requests[0]['scale'], 1)

    def test_incremental_batch_releases_implementation_with_reviewers_gated(self):
        self.repo('a')
        self.env['ADVANCE_ROADMAP_INCREMENTAL'] = '1'
        self.env['FAKE_REVIEWER_GATES'] = json.dumps({'gates': [
            {'provider': 'cursor', 'state': 'over'}, {'provider': 'codex', 'state': 'over'},
            {'provider': 'claude', 'state': 'over'}]})
        self.run_batch(lanes=1)
        row = next(item for item in self.state()['queue'] if item['repo'] == 'a')
        allocation = json.loads((self.root / 'runs' / row['stamp'] / 'batch-budget.json').read_text())
        self.assertEqual(allocation['phase'], 'implementing')
        self.assertEqual(allocation['reviewers'], [])
        self.assertTrue(any(event['kind'] == 'work-start' for event in self.events()))
        self.assertEqual(self.state()['starvation']['admitted'], 1)
        self.assertEqual(self.state()['starvation']['consecutive_zero_admission'], 0)
    def test_incremental_implementation_does_not_require_available_reviewers(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.workers = ['claude']
            budget.review_strategy = {'strategy': 'cross-provider', 'reason': None}
            budget.gates = {'codex': {'state': 'over'}, 'cursor': {'state': 'over'}}
            with patch.object(daily_batch.agent_budget, 'reserve_bundle', return_value=[]):
                self.assertIsNotNone(budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'implementing'}, commit=False))
                self.assertIsNone(budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'reviewing'}, commit=False))

    def test_continuous_mode_never_admits_on_stale_or_unknown_usage(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.dict(
                os.environ, {'ADVANCE_ROADMAP_CONTINUOUS': '1'}):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            now = time.time()
            def reading(provider, **kwargs):
                return {'observed_at': now - 121 if provider == 'codex' else now,
                        'windows': {'five_hour': {'live': None if provider == 'claude' else 1, 'cap': 70}}}
            with patch.object(daily_batch.agent_budget, 'readings', side_effect=reading):
                budget.refresh()
            self.assertEqual(budget.workers, [])
            self.assertEqual(budget.planners, [])
            with patch.object(daily_batch.agent_budget, 'readings', return_value={
                    'observed_at': now, 'error': 'http_429',
                    'windows': {'five_hour': {'live': 1, 'cap': 70}}}):
                budget.refresh()
            self.assertEqual(budget.workers, [])
            self.assertEqual(budget.planners, [])

    def test_incremental_replay_admits_ten_point_unit_not_eighty_two_point_worker(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.workers = ['claude']
            budget.models['claude']['roles']['worker']['pct'] = {'five_hour': 82.12, 'seven_day': 1}
            budget.readings['claude']['windows'] = {'five_hour': {'live': 7, 'cap': 70},
                                                    'seven_day': {'live': 20, 'cap': 90}}
            whole = daily_batch.agent_budget.reserve_bundle([{'provider': 'claude', 'role': 'worker'}],
                reading_by_provider=budget.readings, models=budget.models, commit=False)
            self.assertIsNone(whole)
            selected = budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'implementing'},
                                     commit=False)
            self.assertIsNotNone(selected)
            self.assertAlmostEqual(selected['leases'][0]['pct']['five_hour'], 10)

    def test_incremental_forecast_uses_mature_mode_cohorts_conservatively(self):
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch, 'worker_configuration', return_value={'model': 'm', 'effort': 'high'}):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.workers = ['claude']
            budget.models['claude']['worker_cohorts'] = [
                {'model': 'm', 'effort': 'high', 'phase': 'implementing', 'continuation_mode': mode,
                 'sample_count': 5, 'tokens_p80': tokens, 'newest_at': time.time()}
                for mode, tokens in [('fresh', 10000), ('native', 20000), ('fallback', 30000)]]
            with patch.object(daily_batch.agent_budget, 'reserve_bundle', return_value=[]) as reserve:
                selected = budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'implementing'},
                                         commit=False)
                self.assertEqual(selected['worker_estimate']['roles']['worker']['tokens_p80'], 30000)
                self.assertEqual(reserve.call_args.kwargs['models']['claude']['roles']['worker']['source'],
                                 'phase-mode-cohorts')

    def test_job_override_requires_fresh_reading_and_keeps_shared_caps(self):
        b = daily_batch.agent_budget
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.workers = []
            now = time.time()
            fresh = {'observed_at': now, 'source': 'fake', 'windows': {
                'five_hour': {'live': 85, 'cap': 70}, 'seven_day': {'live': 20, 'cap': 90}}}
            budget.readings['claude'] = fresh
            budget.models['claude']['roles']['worker']['pct'] = {'five_hour': 82.12, 'seven_day': 1}
            gate = [{'provider': 'claude', 'state': 'over', 'reason': '5h 85% >= 70%'},
                    {'provider': 'codex', 'state': 'limited', 'reason': 'actual limit'}]
            fake_result = type('Result', (), {'returncode': 0})()
            with patch.object(daily_batch.shared, 'py', return_value=fake_result), \
                    patch.object(daily_batch.usage, 'load', return_value={}), \
                    patch.object(daily_batch.usage, 'role_gates', return_value=gate), \
                    patch.object(b, 'readings', return_value=fresh):
                self.assertTrue(budget.prepare_override())
                self.assertEqual(budget.readings['claude']['windows']['five_hour']['cap'], 70)
                self.assertEqual(budget.override_readings['claude']['windows']['five_hour']['cap'], 70)
                chosen = budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'implementing',
                                              'job_override': True}, commit=False)
                self.assertIsNotNone(chosen)
                self.assertEqual(chosen['worker'], 'claude')
                self.assertEqual(chosen['baseline']['windows']['five_hour']['cap'], 100)
                fresh['error'] = 'http_429'
                self.assertFalse(budget.prepare_override())
                fresh.pop('error')
                fresh['observed_at'] = now - 61
                self.assertFalse(budget.prepare_override())

    def test_job_override_charges_existing_reservations(self):
        b = daily_batch.agent_budget
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.incremental = True
            budget.override_workers = ['claude']
            budget.models['claude']['roles']['worker']['pct'] = {'five_hour': 82.12, 'seven_day': 1}
            budget.override_readings = dict(budget.readings, claude={'observed_at': time.time(),
                'windows': {'five_hour': {'live': 85, 'cap': 100}, 'seven_day': {'live': 20, 'cap': 100}}})
            budget.forecast = [{'id': 'other', 'provider': 'claude', 'role': 'worker',
                                'pct': {'five_hour': 6, 'seven_day': 0}}]
            chosen = budget.choose(item={'repo': 'a', 'stamp': 'one', 'work_phase': 'implementing',
                                          'job_override': True}, commit=False)
            self.assertIsNone(chosen)

    def test_idle_override_only_once_and_only_for_saved_continuation(self):
        class Budget:
            incremental = True

            def prepare_override(self):
                return True

            def choose(self, **kwargs):
                return {'worker': 'claude'}

        with patch.object(daily_batch, 'ROOT', self.root):
            batch = daily_batch.Batch(self.root, ['a', 'b'], lanes=2)
            batch.budget = Budget()
            fresh, saved = batch.state['queue']
            fresh['verdict_check'] = {'result': 'no-verdict'}
            saved['verdict_check'] = {'result': 'pending', 'action': 'resume_worker'}
            with patch.object(batch, 'launch') as launch:
                self.assertTrue(batch.try_idle_override())
                self.assertEqual(launch.call_args.args[0]['repo'], 'b')
                self.assertFalse(batch.try_idle_override())
                self.assertEqual(launch.call_count, 1)
            batch.state.pop('idle_override_used')
            batch.state['normal_work_admitted'] = True
            self.assertFalse(batch.try_idle_override())

    def test_interactive_leases_reduce_planner_lanes_and_refresh_blocks_conversion(self):
        b = daily_batch.agent_budget
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root):
            budget = daily_batch.RoadmapBudget(dry_run=True)
            budget.dry_run = False
            budget.workers, budget.planners, budget.gates = ['claude'], ['codex'], {}
            budget.review_strategy = {'strategy': 'cross-provider', 'reason': None}
            budget.readings['codex']['windows']['five_hour']['live'] = 61
            interactive = b.reserve_bundle([{'provider': 'codex', 'role': 'planner'}],
                models=budget.models, reading_by_provider=budget.readings)
            admitted = []
            for n in range(5):
                item = {'repo': str(n), 'stamp': str(n)}
                if budget.admit(item):
                    admitted.append(item)
            self.assertEqual(len(admitted), 1, 'the interactive lease leaves room for only one compound planner')
            before = set(admitted[0]['budget_leases'])
            budget.readings['codex']['windows']['five_hour']['live'] = 100
            budget.readings['cursor']['windows']['auto']['live'] = 100
            self.assertIsNone(budget.choose(item=admitted[0]))
            with b.lock():
                self.assertTrue(before <= {l['id'] for l in b.reap()})
            budget.release(admitted[0])
            b.release_many([l['id'] for l in interactive])

    def test_dispatch_share_ignores_skips_and_defaults_to_point_three(self):
        (self.root / 'runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in [
            {'repo': 'a', 'action': 'dispatch_worker'}, {'repo': 'a', 'action': 'blocked_no_item'},
            {'repo': 'a', 'outcome': 'skipped-quota'}, {'repo': 'b', 'action': 'resume_worker'}]))
        with patch.object(daily_batch, 'ROOT', self.root):
            self.assertEqual(daily_batch.dispatch_rates(), {'a': .5, 'b': 0})

    def test_budget_holds_are_visible_and_do_not_leak_leases(self):
        self.repo('a')
        self.env['FAKE_NO_QUOTA'] = '1'
        self.run_batch()
        state = self.state()
        self.assertEqual(state['budget']['held'][0]['repo'], 'a')
        self.assertIn('Budget hold: a:', daily_batch.quota_line(state))
        self.assertEqual(state['budget']['providers']['codex']['reserved_pct'], 0)
        self.assertIn('headroom', state['budget']['providers']['codex']['windows']['five_hour'])
        leases = json.loads((self.root / '.claude/state/agent-budget/leases.json').read_text())
        self.assertEqual(leases, [])

    def test_priority_reports_pending_changes_and_no_verdict_without_mutating_carried_state(self):
        for name in ('pending', 'changed', 'new', 'carried'):
            self.repo(name)
        pending = self.code / '.advance-roadmap'
        pending.mkdir()
        (pending / 'pending-plan-pending.json').write_text(json.dumps({'orchestrator': {'action': 'resume_worker'}}))
        verdicts = self.root / 'batch-verdicts'
        verdicts.mkdir()
        for repo in ('changed', 'carried'):
            (verdicts / f'{repo}.json').write_text('{"stamp":"kept"}')
        snapshot = self.root / 'snapshot.json'
        snapshot.write_text('{"ok":true}')
        (self.root / 'lib/deploysnap.py').write_text('')
        (self.root / 'lib/verdict.py').write_text("""import json,sys
from pathlib import Path
a=sys.argv
if 'fingerprint' in a:
    Path(a[a.index('--out')+1]).write_text('{}')
else:
    changed = 'changed' in Path(a[a.index('--file')+1]).read_text()
    # The copied file is intentionally destructive, like contamination recovery.
    Path(a[a.index('--file')+1]).unlink()
    Path(a[a.index('--changes')+1]).write_text(json.dumps({'changed':['repo:changed'] if changed else []}))
    raise SystemExit(1 if changed else 0)
""")
        (verdicts / 'changed.json').write_text('{"stamp":"changed"}')
        with self.budget_context(), patch.object(daily_batch, 'ROOT', self.root), patch.object(daily_batch.shared, 'LIB', self.root / 'lib'):
            results = {repo: daily_batch.repo_priority(repo, snapshot) for repo in ('pending','changed','new','carried')}
        self.assertEqual([results[r]['priority'] for r in ('pending','changed','new','carried')], [0,1,2,3])
        self.assertTrue((verdicts / 'changed.json').exists())
        self.assertTrue((verdicts / 'carried.json').exists())

    # ── A5: an outage must not use up the day ────────────────────────────────

    def test_a_superset_outage_creates_no_batch_directory_and_a_later_run_proceeds(self):
        self.repo('a')
        self.env.update(ADVANCE_ROADMAP_SUPERSET='1', FAKE_SUPERSET_DOWN='1')
        p = subprocess.run([sys.executable, str(HERE / 'daily_batch.py')], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertFalse((self.root / 'batches').exists(), "the date directory is what says 'already started'")
        self.assertFalse(list(self.root.glob('linear-snapshot.preflight.*')))
        self.assertFalse(list((self.root / 'runs').glob('*')) if (self.root / 'runs').exists() else [])
        del self.env['FAKE_SUPERSET_DOWN']
        self.run_batch()
        self.assertTrue(self.trace_exists())

    def test_a_linear_outage_also_leaves_the_day_unclaimed(self):
        self.repo('a')
        (self.root / 'lib/linearsnap.py').write_text(
            "import json, sys\njson.dump({'ok': False, 'issues': []}, open(sys.argv[-1], 'w'))\n")
        p = subprocess.run([sys.executable, str(HERE / 'daily_batch.py')], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertFalse((self.root / 'batches').exists())

    def test_the_snapshot_lands_in_the_batch_directory_after_preflight(self):
        self.repo('a')
        self.run_batch()
        directory = next((self.root / 'batches').iterdir())
        self.assertTrue((directory / 'linear-snapshot.json').exists())

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

    def test_recovery_work_finishes_before_any_new_planning(self):
        self.repo('a'); self.repo('z')
        pending = self.code / '.advance-roadmap'; pending.mkdir()
        (pending / 'pending-plan-z.json').write_text(json.dumps({'action': 'resume_worker'}))
        self.run_batch(lanes=2)
        events = self.events()
        finished = next(e['time'] for e in events if e['repo'] == 'z' and e['kind'] == 'work-end')
        fresh = next(e['time'] for e in events if e['repo'] == 'a' and e['kind'] == 'plan-start')
        self.assertGreaterEqual(fresh, finished)
        self.assertEqual(self.state()['phase'], 'done')

    def test_failed_recovery_does_not_block_an_unrelated_repo(self):
        self.repo('a'); self.repo('z')
        pending = self.code / '.advance-roadmap'; pending.mkdir()
        (pending / 'pending-plan-z.json').write_text(json.dumps({'action': 'resume_worker'}))
        self.env['FAKE_PLANNER_GATED'] = 'z'
        self.run_batch(lanes=2)
        self.assertTrue(any(e['repo'] == 'a' and e['kind'] == 'work-start' for e in self.events()))
        fresh = next(i for i in self.state()['queue'] if i['repo'] == 'a')
        self.assertEqual(fresh['outcome'], 'shipped')

    def test_remaining_pending_plan_does_not_block_an_unrelated_repo(self):
        self.repo('a'); self.repo('z')
        pending = self.code / '.advance-roadmap'; pending.mkdir()
        (pending / 'pending-plan-z.json').write_text(json.dumps({'action': 'dispatch_worker'}))
        self.env['FAKE_KEEP_PENDING'] = '1'
        self.run_batch(lanes=2)
        self.assertTrue(any(e['repo'] == 'a' and e['kind'] == 'work-start' for e in self.events()))

    def test_human_blocked_recovery_allows_fresh_repo_to_continue(self):
        self.repo('a'); self.repo('z')
        pending = self.code / '.advance-roadmap'; pending.mkdir()
        (pending / 'pending-plan-z.json').write_text(json.dumps({'action': 'resume_worker'}))
        self.env['FAKE_HUMAN_REPO'] = 'z'
        self.run_batch(lanes=2)
        fresh = next(i for i in self.state()['queue'] if i['repo'] == 'a')
        self.assertEqual(fresh['outcome'], 'shipped')
        with daily_batch.workqueue.Store(self.root) as store:
            self.assertEqual(store.hold('z')['state'], 'human')

    def test_streaming_releases_ready_work_before_unrelated_planner_and_caps_all_phases(self):
        self.repo('a'); self.repo('b')
        self.env.update(FAKE_SLOW_REPO='b', ADVANCE_ROADMAP_THREADS_PER_REPO='1')
        script = """import sys
sys.path.insert(0, sys.argv[1])
import daily_batch
original = daily_batch.Batch.run
def streaming(self):
    return original(self, streaming=True, summary=False)
daily_batch.Batch.run = streaming
sys.argv = ['daily_batch.py', '--lanes', '2', '--threads', '1']
raise SystemExit(daily_batch.main())
"""
        proc = subprocess.run([sys.executable, '-c', script, str(HERE)], env=self.env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        events = self.events()
        start = next(e['time'] for e in events if e['repo'] == 'a' and e['kind'] == 'work-start')
        ready = next(e['time'] for e in events if e['repo'] == 'b' and e['kind'] == 'plan-ready')
        self.assertLess(start, ready)
        active = peak = 0
        for e in sorted(events, key=lambda e: e['time']):
            if e['kind'] in {'plan-start', 'work-start'}:
                active += 1
                peak = max(peak, active)
            elif e['kind'] in {'plan-ready', 'work-end'}:
                active -= 1
        self.assertLessEqual(peak, 2)
        self.assertEqual(len([e for e in events if e['kind'] == 'work-start']), 2)

    def test_continuous_stream_refills_a_yielded_assignment(self):
        self.repo('a')
        pending = self.code / '.advance-roadmap'
        pending.mkdir()
        (pending / 'pending-plan-a.json').write_text(json.dumps({'orchestrator': {
            'action': 'resume_worker', 'repo': 'a', 'linear_id': 'DEV-a1', 'item': 'a t1',
            'branch': 'roadmap/a-1'}}))
        self.env.update(ADVANCE_ROADMAP_INCREMENTAL='1', ADVANCE_ROADMAP_CONTINUOUS='1', FAKE_YIELD_ONCE='1')
        script = """import sys
sys.path.insert(0, sys.argv[1])
import daily_batch
original = daily_batch.Batch.run
def streaming(self):
    return original(self, streaming=True, summary=False)
daily_batch.Batch.run = streaming
sys.argv = ['daily_batch.py', '--lanes', '1', '--threads', '1']
raise SystemExit(daily_batch.main())
"""
        result = subprocess.run([sys.executable, '-c', script, str(HERE)], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len([event for event in self.events() if event['kind'] == 'work-start']), 2)
        self.assertEqual([item['outcome'] for item in self.state()['queue']], ['yielded', 'shipped'])

    def test_slots_cover_morning_afternoon_and_wake_up_without_extra_runs(self):
        for now, expected in [
            (datetime(2026, 11, 10, 10, 29), datetime(2026, 11, 9, 15, 30)),
            (datetime(2026, 11, 10, 10, 30), datetime(2026, 11, 10, 10, 30)),
            (datetime(2026, 11, 10, 15, 29), datetime(2026, 11, 10, 10, 30)),
            (datetime(2026, 11, 10, 15, 30), datetime(2026, 11, 10, 15, 30)),
            (datetime(2026, 11, 10, 23), datetime(2026, 11, 10, 15, 30)),
        ]:
            self.assertEqual(daily_batch.scheduled_slot(now), expected)

    def test_each_scheduled_slot_can_start_once_and_legacy_morning_is_respected(self):
        self.repo('a')
        def preflight(snapshot):
            snapshot.write_text('{"ok":true}')
        with patch.dict(os.environ, self.env), patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch.shared, 'code_dir', return_value=str(self.code)), patch.object(
                daily_batch.shared, 'log'), patch.object(daily_batch.shared, 'LOG', None), patch.object(daily_batch, 'preflight', side_effect=preflight), patch.object(
                daily_batch, 'Batch') as batch, patch.object(daily_batch, 'datetime') as clock, patch.object(
                daily_batch.shared.Supervisor, 'busy_repos', return_value={}), patch.object(
                daily_batch.shutil, 'which', return_value=None), patch.object(sys, 'argv', ['daily_batch.py']):
            batch.return_value.run.return_value = 0
            batch.return_value.budget = None
            batch.return_value.state = {'queue': []}
            clock.now.return_value = datetime(2026, 11, 10, 10, 30)
            self.assertEqual(daily_batch.main(), 0)
            self.assertEqual(daily_batch.main(), 0)
            self.assertEqual(batch.call_count, 1)
            clock.now.return_value = datetime(2026, 11, 10, 15, 30)
            self.assertEqual(daily_batch.main(), 0)
            self.assertEqual(daily_batch.main(), 0)
            self.assertEqual(batch.call_count, 2)
            (self.root / 'batches' / '2026-11-11').mkdir()
            clock.now.return_value = datetime(2026, 11, 11, 10, 30)
            self.assertEqual(daily_batch.main(), 0)
            self.assertEqual(batch.call_count, 2)

    def test_session_pause_preserves_unclaimed_slot_without_preflight_or_models(self):
        (self.root / 'session-hold.json').write_text('{}')
        with patch.dict(os.environ, self.env), patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch.shared, 'code_dir', return_value=str(self.code)), patch.object(
                daily_batch, 'preflight', side_effect=AssertionError('paused')), patch.object(
                daily_batch, 'Batch', side_effect=AssertionError('paused')), patch.object(
                daily_batch.shared, 'LOG', None), patch.object(
                sys, 'argv', ['daily_batch.py']):
            self.assertEqual(daily_batch.main(), 0)
        self.assertFalse((self.root / 'batches').exists())

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
                         [('codex', 'orchestrator', ['b', 'c'], None, 'weekly 90%'),
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
        output = json.loads(p.stdout)
        self.assertEqual(output['repos'], ['a'])
        self.assertEqual(output['verdict_checks'][0]['result'], 'no-verdict')
        self.assertEqual(output['lanes'], 1)
        self.assertIn('headroom', output['budget']['providers']['codex']['windows']['five_hour'])
        self.assertFalse((self.root / '.claude/state/agent-budget').exists())
        self.assertFalse((self.root / 'batches').exists())

    def test_empty_queue_finishes(self):
        self.run_batch()
        self.assertEqual(self.state()['queue'], [])
        self.assertEqual(self.state()['phase'], 'done')

    def test_live_prior_worker_only_defers_its_own_repo(self):
        self.repo('a'); self.repo('b')
        (self.root / 'inflight').mkdir()
        (self.root / 'inflight/a--old.pid').write_text(str(os.getpid()))
        self.run_batch()
        queue = {i['repo']: i for i in self.state()['queue']}
        self.assertEqual(queue['a']['status'], 'deferred')
        self.assertEqual(queue['b']['outcome'], 'shipped')
        self.assertEqual([e['repo'] for e in self.events() if e['kind'] == 'work-start'], ['b'])

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

    def test_bot_summary_does_not_append_context_after_the_bulletin(self):
        if not shutil.which('node'):
            self.skipTest('node is not installed')
        slack = self.root / 'slackagent'
        (slack / 'scripts').mkdir(parents=True)
        posts = self.root / 'bot-posts.jsonl'
        (slack / 'scripts/post-roadmap-summary.mjs').write_text(
            "import { appendFileSync } from 'node:fs';\n"
            f"appendFileSync({json.dumps(str(posts))}, JSON.stringify({{text:'Batch summary'}})+'\\n');\n")
        directory = self.root / 'summary'
        directory.mkdir()
        (directory / 'batch.json').write_text(json.dumps({'queue': [],
            'review_fallback': 'review fallback: cross-provider (cursor gated until reset)', 'budget': {
            'held': [{'repo':'a', 'reason':'shared headroom'}]}}))
        with patch.object(daily_batch, 'ROOT', self.root), patch.object(
                daily_batch, 'slackagent_script', return_value=slack / 'scripts/post-roadmap-summary.mjs'):
            self.assertTrue(daily_batch.post_slack_summary(directory))
        records = [json.loads(line) for line in posts.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['text'], 'Batch summary')
        self.assertIn('review fallback: cross-provider (cursor gated until reset)',
                      daily_batch.quota_line(json.loads((directory / 'batch.json').read_text())))

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


    def test_dashboard_schedule_preserves_history_and_uses_two_current_slots(self):
        with patch.dict(os.environ, self.env):
            spec = importlib.util.spec_from_file_location('batch_dashboard', HERE / 'gen-dashboard.py')
            dashboard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dashboard)
        with patch.object(dashboard, 'base_cadence', return_value=1):
            self.assertEqual(len(dashboard.slots_on(datetime(2026, 10, 8))), 10)
            self.assertEqual(dashboard.slots_on(datetime(2026, 10, 10)), [datetime(2026, 10, 10, 10, 30)])
            self.assertEqual(dashboard.slots_on(datetime(2026, 11, 10)),
                             [datetime(2026, 11, 10, 10, 30), datetime(2026, 11, 10, 15, 30)])
        import plistlib
        schedule = plistlib.loads((HERE / 'com.jake.advance-roadmap.plist').read_bytes())
        self.assertEqual(schedule['StartCalendarInterval'], [{'Hour': 10, 'Minute': 30}, {'Hour': 15, 'Minute': 30}])


if __name__ == '__main__':
    unittest.main()
