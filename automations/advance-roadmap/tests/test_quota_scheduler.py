"""Quota wake-ups and two-lane dispatch with no real providers or external APIs."""
import json
from concurrent.futures import ThreadPoolExecutor
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

    def decision(self, number, repo='a', source='planner'):
        key = f'{number:064x}'
        marker = '<!-- slackagent:unblock:00000000-0000-0000-0000-000000000001 -->'
        directory = self.root / 'slack-decisions'
        directory.mkdir(exist_ok=True)
        (directory / f'C{number}-1.0.json').write_text(json.dumps({
            'status': 'recorded', 'answer': 'Proceed', 'dispatchKey': key, 'linearMarker': marker,
            'decision': {'repo': repo, 'linear_id': f'DEV-{number}', 'stamp': '20261110-103001', 'source': source}}))
        return key, marker

    def test_answer_queue_requires_verified_record_and_is_idempotent(self):
        with workqueue.Store(self.root) as store:
            store.set('quota_scheduler_enabled', True)
        key, _ = self.decision(1)
        with self.assertRaises(ValueError):
            q.queue_decision(self.root, 'other', 'DEV-1', '20261110-103001', key, wake=False)
        self.assertEqual(q.queue_decision(self.root, 'a', 'DEV-1', '20261110-103001', key, wake=False)['disposition'], 'queued')
        self.assertEqual(q.queue_decision(self.root, 'a', 'DEV-1', '20261110-103001', key, wake=False)['disposition'], 'already-queued')
        with workqueue.Store(self.root) as store:
            self.assertEqual(len(store.get('decision_requests')), 1)

    def test_parallel_answer_enqueues_are_not_lost(self):
        with workqueue.Store(self.root) as store:
            store.set('quota_scheduler_enabled', True)
        keys = [self.decision(n)[0] for n in range(1, 9)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda pair: q.queue_decision(self.root, 'a', f'DEV-{pair[0]}',
                     '20261110-103001', pair[1], wake=False), enumerate(keys, 1)))
        with workqueue.Store(self.root) as store:
            self.assertEqual({r['key'] for r in store.get('decision_requests')}, set(keys))

    def test_active_tick_does_not_lose_new_answer_wake(self):
        with workqueue.Store(self.root) as store:
            store.set('decision_requests', [{'status': 'queued', 'created': 101}])
            with patch.object(q.time, 'time', return_value=102):
                self.assertEqual(q.set_schedule(store, tick_started=100, wake_at=900)['wake_at'], 104)
                self.assertEqual(q.set_schedule(store, tick_started=102, wake_at=900)['wake_at'], 900)

    def test_worker_decision_selects_exact_saved_assignment_without_starving_later_request(self):
        pending = self.code / '.advance-roadmap'
        pending.mkdir()
        first = {'key': '1', 'repo': 'missing', 'ticket': 'DEV-9', 'source': 'planner', 'status': 'queued'}
        second = {'key': '2', 'repo': 'a', 'ticket': 'DEV-1', 'source': 'worker',
                  'source_stamp': '20261110-103001', 'linear_marker': 'verified', 'status': 'queued'}
        plan = {'action': 'resume_worker', 'repo': 'a', 'linear_id': 'DEV-1', 'item': 'DEV-1', 'branch': 'roadmap/a'}
        other = {**plan, 'linear_id': 'DEV-2', 'item': 'DEV-2'}
        (pending / 'pending-plan-a.json').write_text(json.dumps({'orchestrator': other}))
        (pending / 'pending-plan-a.t1.json').write_text(json.dumps({'orchestrator': plan}))
        issue = {'id': 'DEV-1', 'state_type': 'unstarted', 'state': 'Todo', 'labels': ['repo/a'],
                 'description': '<!-- advance-roadmap:directive:a -->\n\n## Directive\n\nverified\n'}
        self.snapshot.write_text(json.dumps({'ok': True, 'issues': [issue]}))
        with workqueue.Store(self.root) as store:
            store.attempt('20261110-103001', 'a', plan)
            store.attempt('20261110-103002', 'a', other)
            store.set('decision_requests', [first, second])
            request, target = q.target_decision(store, self.code, self.snapshot, ['a'], set(), time.time())
            self.assertEqual(request['key'], '2')
            self.assertEqual(target[1]['plan']['linear_id'], 'DEV-1')
            self.assertEqual(target[1]['decision_plan_path'], str(pending / 'pending-plan-a.t1.json'))
            self.snapshot.write_text(json.dumps({'ok': True, 'truncated': True,
                                                 'issues': [issue]}))
            self.assertIsNone(q.decision_candidate(store, self.code, self.snapshot, ['a'], set(), time.time(), second))
            self.snapshot.write_text(json.dumps({'ok': True, 'issues': [issue]}))
            # A second plan for the same ticket with a different branch must
            # never replace the exact saved assignment or create ambiguity.
            (pending / 'pending-plan-a.t2.json').write_text(json.dumps({'orchestrator': {**plan, 'branch': 'roadmap/other'}}))
            self.assertEqual(q.decision_candidate(store, self.code, self.snapshot, ['a'], set(), time.time(), second)[1]['decision_plan_path'],
                             str(pending / 'pending-plan-a.t1.json'))
            (pending / 'pending-plan-a.t2.json').write_text(json.dumps({'orchestrator': plan}))
            self.assertIsNone(q.decision_candidate(store, self.code, self.snapshot, ['a'], set(), time.time(), second))
            (pending / 'pending-plan-a.t2.json').unlink()
            second['source_stamp'] = '20261110-103002'
            self.assertIsNone(q.decision_candidate(store, self.code, self.snapshot, ['a'], set(), time.time(), second))

    def test_only_current_pending_directive_authorizes_continuation(self):
        marker = '<!-- slackagent:unblock:00000000-0000-0000-0000-000000000001 -->'
        issue = {'state_type': 'unstarted', 'state': 'Todo', 'labels': ['repo/a'],
                 'description': f'<!-- advance-roadmap:directive:a -->\n\n## Directive\n\n{marker}\n\n## Other\n'}
        self.assertTrue(q.pending_decision_directive(issue, 'a', marker))
        cases = [
            {**issue, 'description': issue['description'].replace('## Directive\n', '## Directive (consumed)\n')},
            {**issue, 'description': issue['description'].replace('## Directive\n', '## Directive (archived)\n')},
            {**issue, 'description': issue['description'].replace(marker, '') + marker},
            {**issue, 'state_type': 'completed'},
            {**issue, 'state': 'Needs Input'},
            {**issue, 'labels': ['repo/a', 'human-only']},
            {**issue, 'labels': ['repo/other']},
        ]
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertFalse(q.pending_decision_directive(changed, 'a', marker))

    def test_decision_is_not_consumed_by_planning_or_release_without_worker(self):
        directory = self.root / 'batch'
        directory.mkdir()
        item = {'repo': 'a', 'decision_target': 'key', 'linear_id': 'DEV-1', 'stamp': 's',
                'started': 100, 'released_at': 101, 'status': 'running'}
        (directory / 'batch.json').write_text(json.dumps({'queue': [item]}))
        run = self.root / 'runs' / 's'; run.mkdir(parents=True)
        (run / 'worker-request.json').write_text(json.dumps({'orchestrator': {'repo': 'a', 'linear_id': 'DEV-1'}}))
        with workqueue.Store(self.root) as store:
            store.set('decision_requests', [{'status': 'queued', 'repo': 'a', 'ticket': 'DEV-1',
                                             'key': 'key', 'batch': str(directory)}])
            q.reconcile_decision_requests(store)
            self.assertEqual(store.get('decision_requests')[0]['status'], 'queued')
            (run / 'worker.pid').write_text('123')
            q.reconcile_decision_requests(store)
            self.assertEqual(store.get('decision_requests')[0]['status'], 'started')

    def test_verified_targeted_no_work_records_explanation_for_thread(self):
        directory = self.root / 'batch'; directory.mkdir()
        run = self.root / 'runs' / 's'; run.mkdir(parents=True)
        item = {'repo': 'a', 'decision_target': 'key', 'stamp': 's', 'status': 'finished', 'outcome': 'blocked-no-item'}
        (directory / 'batch.json').write_text(json.dumps({'queue': [item]}))
        (run / 'orchestrator-result.json').write_text(json.dumps({'action': 'blocked_no_item', 'summary': 'Still needs a prerequisite.'}))
        with workqueue.Store(self.root) as store:
            store.set('decision_requests', [{'status': 'queued', 'repo': 'a', 'ticket': 'DEV-1',
                                             'key': 'key', 'batch': str(directory)}])
            q.reconcile_decision_requests(store)
            row = store.get('decision_requests')[0]
            self.assertEqual((row['status'], row['result_stamp'], row['summary']),
                             ('no-work', 's', 'Still needs a prerequisite.'))

    def test_ticket_first_ranking_excludes_blocked_human_and_unrouted(self):
        def issue(id, repo, priority=0, **kw):
            return {'id': id, 'state': 'Todo', 'state_type': 'unstarted', 'priority': priority,
                    'labels': ['repo/' + repo] if repo else [], **kw}
        snap = {'ok': True, 'issues': [
            issue('DEV-2', 'z', 2), issue('DEV-1', 'a', 1),
            issue('DEV-3', 'a', 0), issue('DEV-4', None, 1),
            issue('DEV-5', 'b', 1, blocked_by=['DEV-2']),
            issue('DEV-6', 'c', 1, blocked_by=['DEV-404']),
            {**issue('DEV-7', 'd', 1), 'state': 'Needs Input'},
            {**issue('DEV-8', 'e', 1), 'labels': ['repo/e', 'human-only']},
            {**issue('DEV-9', 'f', 1), 'state_type': 'completed'},
            {**issue('DEV-10', 'health', 1), 'labels': ['repo/health', 'code-health']},
            {**issue('DEV-11', 'urgent', 1), 'labels': ['repo/urgent', 'CODE-HEALTH', 'DO-NEXT']}]}
        self.assertEqual(q.eligible_tickets(snap), [(1, 'DEV-1', 'a'), (1, 'DEV-11', 'urgent'), (2, 'DEV-2', 'z'), (5, 'DEV-3', 'a')])
        self.assertEqual(q.tickets_by_repo(snap), {'a': ['DEV-1', 'DEV-3'], 'urgent': ['DEV-11'], 'z': ['DEV-2']})
        self.assertEqual(q.eligible_tickets({**snap, 'truncated': True}), [])
        self.assertEqual(q.eligible_tickets({'ok': False, 'issues': snap['issues']}), [])

    def test_new_candidates_start_from_tickets_and_keep_priority(self):
        self.snapshot.write_text(json.dumps({'ok': True, 'issues': [
            {'id': 'DEV-20', 'state': 'Todo', 'state_type': 'unstarted', 'priority': 2, 'labels': ['repo/z']},
            {'id': 'DEV-10', 'state': 'Todo', 'state_type': 'unstarted', 'priority': 1, 'labels': ['repo/a']}]}))
        def policy(store, repos, snapshot, **kwargs):
            return [{'repo': r, 'decision': 'plan'} for r in repos]
        with workqueue.Store(self.root) as store, patch.object(q.scheduler, 'policy', side_effect=policy), \
                patch.object(q.batch, 'pending_action', return_value=None):
            chosen = q.choose_repos(store, str(self.code), self.snapshot, ['z', 'a', 'empty'], set(), time.time())
        self.assertEqual(chosen, [('a', None), ('z', None)])

    def test_repo_with_only_code_health_is_not_selected(self):
        self.snapshot.write_text(json.dumps({'ok': True, 'issues': [
            {'id': 'DEV-276', 'state': 'Backlog', 'state_type': 'backlog',
             'labels': ['repo/tidbyt-api', 'code-health']}]}))
        with workqueue.Store(self.root) as store, patch.object(q.batch, 'pending_action', return_value=None):
            self.assertEqual(q.choose_repos(store, str(self.code), self.snapshot,
                                            ['tidbyt-api'], set(), time.time()), [])

    def test_known_blocked_candidates_skip_repo_until_ticket_changes(self):
        now = time.time()
        snapshot = {'ok': True, 'issues': [
            {'id': 'DEV-276', 'state': 'Backlog', 'state_type': 'backlog',
             'labels': ['repo/tidbyt-api', 'code-health']},
            {'id': 'DEV-227', 'state': 'Todo', 'state_type': 'unstarted',
             'labels': ['repo/tidbyt-api'], 'human_comments_sha': 'before'},
            {'id': 'DEV-1', 'state': 'Todo', 'state_type': 'unstarted',
             'labels': ['repo/other']} ]}
        self.snapshot.write_text(json.dumps(snapshot))
        skill = q.batch.shared.HERE.parents[1] / 'skills/advance-roadmap'
        with workqueue.Store(self.root) as store, \
                patch.object(q.batch, 'pending_action', return_value=None), \
                patch.object(q.batch.shared, 'code_dir', return_value=str(self.code)), \
                patch.object(workqueue, 'git', return_value='stable'):
            store.set('no_item_yield:tidbyt-api', {
                'input_fingerprint': workqueue.no_item_fingerprint('tidbyt-api', snapshot, self.code, skill),
                'not_before': now + 86400})
            self.assertEqual(q.choose_repos(store, str(self.code), self.snapshot,
                                            ['tidbyt-api', 'other'], set(), now), [('other', None)])
            snapshot['issues'][1]['human_comments_sha'] = 'after'
            self.snapshot.write_text(json.dumps(snapshot))
            self.assertIn(('tidbyt-api', None), q.choose_repos(store, str(self.code), self.snapshot,
                                                                 ['tidbyt-api', 'other'], set(), now))

    def test_saved_plan_survives_no_eligible_tickets(self):
        def policy(store, repos, snapshot, **kwargs):
            return [{'repo': r, 'decision': 'resume'} for r in repos]
        with workqueue.Store(self.root) as store, patch.object(q.scheduler, 'policy', side_effect=policy), \
                patch.object(q.batch, 'pending_action', side_effect=lambda repo: 'resume_worker' if repo == 'a' else None):
            chosen = q.choose_repos(store, str(self.code), self.snapshot, ['a', 'b'], set(), time.time())
        self.assertEqual(chosen, [('a', None)])

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
                patch.object(q.scheduler, 'refresh_snapshot', return_value=self.snapshot) as refresh, \
                patch.object(q.scheduler, 'policy', return_value=[]), \
                patch.object(q.batch, 'candidates', return_value=['a', 'b']), \
                patch.object(q.batch, 'RoadmapBudget', FakeBudget), \
                patch.object(q.batch, 'Batch', FakeBatch), \
                patch.object(q.usage, 'load', return_value={}), \
                patch.object(q, 'next_reset', return_value=None), \
                patch.object(q.shutil, 'which', return_value=None):
            self.assertEqual(q.tick(self.root), 'unit-finished')
            refresh.assert_called_once_with(self.root, force=True)
        self.assertEqual(calls, [(['a', 'b'], 2, 1), ('provisional', True, '1')])
        with workqueue.Store(self.root) as store:
            schedule = store.get('quota_schedule')
            self.assertEqual(schedule['reason'], 'unit completed; recheck headroom')
            self.assertEqual(len(store.get('quota_work_ids')), 2)

    def test_linear_failure_continues_saved_work_but_never_selects_fresh_work(self):
        with workqueue.Store(self.root) as store:
            store.set('quota_scheduler_enabled', True)
            store.register({'repo': 'a', 'item': 'DEV-1', 'action': 'resume_worker',
                            'branch': 'roadmap/a'}, '20261110-100000')
        captured = []
        class Budget:
            planners = ['codex']
            def __init__(self): self.forecast = []
            def choose(self, **kwargs): return {'leases': []}
            def release(self, item): pass
        class Batch:
            def __init__(self, directory, repos, lanes, threads):
                captured.append(repos)
                self.state = {'queue': [{'repo': repo, 'status': 'finished', 'outcome': 'yielded',
                                         'released_at': time.time()} for repo in repos]}
                self.budget = Budget()
            def run(self, **kwargs):
                captured.append(os.environ.get('ADVANCE_ROADMAP_CONTINUATION_ONLY'))
        with patch.object(q.batch.shared, 'code_dir', return_value=str(self.code)), \
                patch.object(q.batch.shared, 'inflight_repos', return_value=set()), \
                patch.object(q.batch.shared, 'py', return_value=SimpleNamespace(returncode=0)), \
                patch.object(q.workqueue, 'sync', return_value=set()), \
                patch.object(q.workqueue, 'active_count', return_value=0), \
                patch.object(q.workqueue, 'wake_humans') as wake, \
                patch.object(q.scheduler, 'observe_completed'), \
                patch.object(q.scheduler, 'refresh_snapshot', side_effect=RuntimeError('offline')), \
                patch.object(q.batch, 'candidates', return_value=['a', 'b']), \
                patch.object(q.batch, 'pending_action', return_value=None), \
                patch.object(q.batch, 'RoadmapBudget', Budget), \
                patch.object(q.batch, 'Batch', Batch), \
                patch.object(q.usage, 'load', return_value={}), \
                patch.object(q, 'next_reset', return_value=None), \
                patch.object(q.shutil, 'which', return_value=None):
            self.assertEqual(q.tick(self.root), 'unit-finished')
            wake.assert_not_called()
        self.assertEqual(captured, [['a'], '1'])

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
