"""Quota wake-ups and two-lane dispatch with no real providers or external APIs."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import quota_scheduler as q
import workqueue


class QuotaSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.code = self.root / 'code'
        self.code.mkdir()
        self.snapshot = self.root / 'snapshot.json'
        self.snapshot.write_text('{"ok": true, "issues": []}')

    def test_next_reset_and_reservation_recheck(self):
        state = q.usage.empty_state()
        state['providers']['codex']['five_hour'] = {'used_pct': 80, 'reset_epoch': 1500}
        state['providers']['codex']['weekly'] = {'used_pct': 20, 'reset_epoch': 3000}
        state['providers']['claude']['five_hour'] = {'used_pct': 90, 'reset_epoch': 1200}
        self.assertEqual(q.next_reset(state, 1000), 1200)
        budget = SimpleNamespace(snapshot=lambda _: {'providers': {'codex': {'windows': {
            'five_hour': {'reserved_pct': 2}}}}})
        self.assertEqual(q.wake_after_no_capacity(state, budget, 1000)['wake_at'], 1060)

    def test_held_and_future_wake_make_no_provider_calls(self):
        (self.root / 'session-hold.json').write_text('{}')
        with patch.object(q.batch.shared, 'py', side_effect=AssertionError('unexpected probe')):
            self.assertEqual(q.tick(self.root), 'held')
        (self.root / 'session-hold.json').unlink()
        with workqueue.Store(self.root) as store:
            store.set('quota_scheduler_enabled', True)
            store.set('quota_schedule', {'wake_at': time.time() + 100, 'provisional': False})
        with patch.object(q.batch.shared, 'code_dir', return_value=str(self.code)), \
                patch.object(q.batch.shared, 'py', side_effect=AssertionError('unexpected probe')):
            self.assertEqual(q.tick(self.root), 'waiting')

    def test_due_tick_dispatches_two_distinct_saved_assignments(self):
        with workqueue.Store(self.root) as store:
            store.set('quota_scheduler_enabled', True)
            for repo in ('a', 'b'):
                store.register({'repo': repo, 'item': repo, 'action': 'resume_worker',
                                'branch': 'roadmap/' + repo}, '20261111-100000')
        calls = []
        root = self.root

        class FakeBudget:
            incremental = True
            planners = ['codex']
            def __init__(self):
                self.forecast = []
            def choose(self, **kwargs):
                return {'leases': [{'id': str(len(self.forecast))}]}
            def release(self, item):
                pass

        class FakeBatch:
            def __init__(self, directory, repos, lanes, threads):
                calls.append((repos, lanes, threads))
                self.state = {'queue': [{'repo': repo, 'status': 'finished', 'outcome': 'yielded',
                                         'released_at': time.time()} for repo in repos]}
                self.budget = FakeBudget()
            def run(self, **kwargs):
                self.kwargs = kwargs
                with workqueue.Store(root) as current:
                    provisional = current.get('quota_schedule')
                calls.append(('provisional', provisional.get('provisional'),
                              os.environ.get('ADVANCE_ROADMAP_CONTINUOUS')))

        success = SimpleNamespace(returncode=0)
        with patch.object(q.batch.shared, 'code_dir', return_value=str(self.code)), \
                patch.object(q.batch.shared, 'inflight_repos', return_value=set()), \
                patch.object(q.batch.shared, 'py', return_value=success), \
                patch.object(q.workqueue, 'sync', return_value=set()), \
                patch.object(q.workqueue, 'active_count', return_value=0), \
                patch.object(q.workqueue, 'wake_humans'), \
                patch.object(q.scheduler, 'observe_completed'), \
                patch.object(q.scheduler, 'refresh_snapshot', return_value=self.snapshot), \
                patch.object(q.scheduler, 'policy', return_value=[]), \
                patch.object(q.batch, 'candidates', return_value=['a', 'b']), \
                patch.object(q.batch, 'RoadmapBudget', FakeBudget), \
                patch.object(q.batch, 'Batch', FakeBatch), \
                patch.object(q.usage, 'load', return_value={}), \
                patch.object(q, 'next_reset', return_value=None), \
                patch.object(q.shutil, 'which', return_value=None):
            self.assertEqual(q.tick(self.root), 'unit-finished')
        self.assertEqual(calls, [(['a', 'b'], 2, 1), ('provisional', True, '1')])
        with workqueue.Store(self.root) as store:
            schedule = store.get('quota_schedule')
            self.assertEqual(schedule['reason'], 'unit completed; recheck headroom')
            self.assertEqual(len(store.get('quota_work_ids')), 2)

    def test_one_surviving_worker_leaves_one_distinct_slot(self):
        with workqueue.Store(self.root) as store:
            for repo in ('a', 'b', 'c'):
                store.register({'repo': repo, 'item': repo, 'action': 'resume_worker',
                                'branch': 'roadmap/' + repo}, '20261111-100000')
            with patch.object(q.scheduler, 'policy', return_value=[]):
                selected = q.choose_repos(store, str(self.code), self.snapshot,
                                          ['a', 'b', 'c'], {'a'}, time.time())
        self.assertEqual([repo for repo, _ in selected], ['b', 'c'])

    def test_unavailable_review_does_not_starve_other_repositories(self):
        class Budget:
            planners = ['codex']
            def __init__(self):
                self.forecast = []
            def choose(self, item, **kwargs):
                return None if item['repo'] == 'a' else {'leases': [{'id': item['repo']}]}
        budget = Budget()
        candidates = [(name, {'phase': 'reviewing' if name == 'a' else 'implementing'})
                      for name in ('a', 'b', 'c')]
        chosen = q.forecast_candidates(budget, candidates, 2)
        self.assertEqual([repo for repo, _ in chosen], ['b', 'c'])
        self.assertEqual([lease['id'] for lease in budget.forecast], ['b', 'c'])


if __name__ == '__main__':
    unittest.main()
