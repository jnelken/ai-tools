"""Exercise the real batch controller with fake planners/workers, never live providers."""
import importlib.util
import json
import os
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
import json, os, time
from pathlib import Path
root = Path(os.environ['ADVANCE_ROADMAP_ROOT'])
stamp = os.environ['ADVANCE_ROADMAP_STAMP']
repo = os.environ['ADVANCE_ROADMAP_TARGET_REPO']
run = root / 'runs' / stamp
def event(kind):
    with open(root / 'trace.jsonl', 'a') as f:
        f.write(json.dumps({'kind': kind, 'repo': repo, 'time': time.time()}) + '\n')
event('plan-start')
time.sleep(.15 if repo == 'a' else .05)
if repo == os.environ.get('FAKE_FAILED_REPO'):
    event('plan-failed')
    raise SystemExit(1)
(run / 'orchestrator-result.json').write_text(json.dumps({
    'action': 'dispatch_worker', 'repo': repo, 'linear_id': 'DEV-' + repo, 'item': repo}))
event('plan-ready')
(run / 'batch-ready').touch()
while not (run / 'batch-release').exists():
    if (run / 'batch-cancel').exists():
        event('deferred')
        raise SystemExit(0)
    time.sleep(.01)
event('work-start')
time.sleep(.2)
event('work-end')
with open(root / 'runs.jsonl', 'a') as f:
    f.write(json.dumps({'stamp': stamp, 'repo': repo, 'outcome': 'shipped'}) + '\n')
PY
'''


class DailyBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.code = self.root / 'code'
        self.code.mkdir()
        (self.root / 'lib').mkdir()
        (self.root / 'run.sh').write_text(FAKE_RUN)
        (self.root / 'lib/usage.py').write_text(
            "import os, sys\n"
            "print('' if os.environ.get('FAKE_NO_QUOTA') else "
            "{'pick-worker-chain': 'cursor', 'pick-orchestrator': 'codex'}.get(sys.argv[-1], ''))\n")
        (self.root / 'lib/linearsnap.py').write_text(
            "import json, sys\njson.dump({'ok': True, 'issues': []}, open(sys.argv[-1], 'w'))\n")
        self.env = {**os.environ, 'ADVANCE_ROADMAP_ROOT': str(self.root),
                    'PERSONAL_CODE_DIR': str(self.code), 'ADVANCE_ROADMAP_BATCH_POLL_S': '.02',
                    'ADVANCE_ROADMAP_SUPERSET': '0', 'DOTFILES_PERSONAL_MACHINE': '1',
                    'ADVANCE_ROADMAP_SKILL_DIR': str(self.root / 'skills'),
                    'FAKE_PYTHON': sys.executable, 'PYTHONDONTWRITEBYTECODE': '1'}

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, name, owner='jnelken'):
        repo = self.code / name
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin',
                        f'git@github.com:{owner}/{name}.git'], check=True)
        return repo

    def run_batch(self, lanes=8, extra=()):
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
        self.run_batch(lanes=0)
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

    def test_quota_defers_without_implementation_or_retry(self):
        self.repo('a')
        self.env['FAKE_NO_QUOTA'] = '1'
        self.run_batch()
        self.assertEqual([e['kind'] for e in self.events()], ['plan-start', 'plan-ready', 'deferred'])
        self.assertEqual(self.state()['phase'], 'done')

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
