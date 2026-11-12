"""Model-free scheduler decisions and rollout guards."""
import json
import importlib.util
import os
import sys
import tempfile
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import scheduler as s
import workqueue as q


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = q.Store(self.root)
        self.addCleanup(self.store.close)
        self.snapshot = self.root / 'snapshot.json'
        self.write([])
        self.patches = [patch.object(s.batch.shared, 'code_dir', return_value=str(self.root / 'code')),
                        patch.object(q, 'git', return_value='stable'),
                        patch.object(s.batch, 'pending_action', return_value=None)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def write(self, issues):
        self.snapshot.write_text(json.dumps({'ok': True, 'issues': issues}))

    def decide(self):
        return s.policy(self.store, ['a'], self.snapshot, now=10000)[0]

    def cached(self):
        d = self.decide()
        self.store.set('eligibility:a', {'fingerprint': d['fingerprint'], 'checked': 9999})
        return d

    def test_idle_ticks_invoke_no_models_or_budget_probes(self):
        self.cached()
        with patch.object(s.batch.shared, 'py', side_effect=AssertionError('no external calls')), patch.object(
                s.batch, 'RoadmapBudget', side_effect=AssertionError('no quota probes')):
            for _ in range(4):
                self.assertEqual(self.decide()['decision'], 'wait')

    def test_unrelated_and_bot_only_updates_do_not_invalidate_cached_verdict(self):
        self.write([{'id': 'DEV-1', 'labels': ['repo/a'], 'title': 'Task', 'updated_at': 'old'}])
        self.cached()
        self.write([{'id': 'DEV-1', 'labels': ['repo/a'], 'title': 'Task', 'updated_at': 'new'},
                    {'id': 'DEV-2', 'labels': ['repo/b'], 'title': 'Changed'},
                    {'id': 'DEV-3', 'labels': [], 'title': 'Unrouted'}])
        self.assertEqual(self.decide()['decision'], 'wait')

    def test_relevant_human_change_reopens_planning(self):
        self.write([{'id': 'DEV-1', 'labels': ['repo/a'], 'human_comments_sha': 'one'}])
        self.cached()
        self.write([{'id': 'DEV-1', 'labels': ['repo/a'], 'human_comments_sha': 'two'}])
        self.assertEqual(self.decide()['decision'], 'plan')

    def test_dependency_from_other_repo_can_wake_this_repo(self):
        first = {'id': 'DEV-1', 'labels': ['repo/a'], 'blocked_by': ['DEV-2']}
        self.write([first, {'id': 'DEV-2', 'labels': ['repo/b'], 'state': 'Started'}])
        self.cached()
        self.write([first, {'id': 'DEV-2', 'labels': ['repo/b'], 'state': 'Done'}])
        self.assertEqual(self.decide()['decision'], 'plan')

    def test_interrupted_work_resumes_without_new_planner(self):
        p = {'repo': 'a', 'action': 'resume_worker', 'item': 'DEV-1'}
        self.store.register(p, '20261110-103001')
        self.assertEqual(self.decide()['decision'], 'resume')

    def test_human_blocker_stays_parked_while_other_repo_is_eligible(self):
        p = {'repo': 'a', 'action': 'resume_worker', 'item': 'DEV-1'}
        self.store.attempt('20261110-103001', 'a', p)
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'blocked'},
                          {'needs_input': True, 'input_question': 'Which account?'})
        ds = s.policy(self.store, ['a', 'b'], self.snapshot, now=time.time())
        self.assertEqual([d['decision'] for d in ds], ['wait', 'plan'])

    def test_cannot_promote_without_seven_days_or_with_false_skip(self):
        start = 1800000000
        for day in range(7):
            s.record_shadow(self.store, [self.decide()], now=start + day * s.DAY)
        self.assertFalse(s.promotion_readiness(self.store, start + 6 * s.DAY)['ready'])
        s.record_shadow(self.store, [self.decide()], now=start + 7 * s.DAY)
        self.assertTrue(s.promotion_readiness(self.store, start + 7 * s.DAY)['ready'])
        self.store.set('shadow_failure:test', {'at': start + s.DAY, 'reason': 'false skip'})
        self.assertFalse(s.promotion_readiness(self.store, start + 7 * s.DAY)['ready'])

    def test_old_observations_do_not_satisfy_new_shadow_period(self):
        start = 1800000000
        for day in range(7):
            s.record_shadow(self.store, [self.decide()], now=start + day * s.DAY)
        self.store.set('shadow_started', start + 8 * s.DAY)
        self.assertFalse(s.promotion_readiness(self.store, start + 15 * s.DAY)['ready'])

    def test_late_older_completion_is_observed_using_its_own_launch_fingerprint(self):
        self.store.attempt('20261110-100000', 'a')
        self.store.attempt('20261110-110000', 'a')
        self.store.set('eligibility_at_start:20261110-100000', 'older-inputs')
        self.store.set('eligibility_at_start:20261110-110000', 'newer-inputs')
        path = self.root / 'runs.jsonl'
        path.write_text(json.dumps({'stamp': '20261110-110000', 'outcome': 'error'}) + '\n')
        s.observe_completed(self.store, self.root)
        with path.open('a') as stream:
            stream.write(json.dumps({'stamp': '20261110-100000', 'outcome': 'blocked-no-item'}) + '\n')
        s.observe_completed(self.store, self.root)
        self.assertEqual(self.store.get('eligibility:a')['fingerprint'], 'older-inputs')
        self.assertEqual(self.decide()['decision'], 'plan')

    def test_daily_planning_cap_counts_unrecorded_attempts(self):
        stamp = time.strftime('%Y%m%d')
        for sec in ('103001', '153001'):
            self.store.attempt(stamp + '-' + sec, 'a', pid=os.getpid())
        self.assertEqual(s.daily_admission(self.store, ['a', 'b']), ['b'])
        decision = self.decide()
        self.assertEqual(decision['decision'], 'wait')
        self.assertIn('midnight', decision['reason'])

    def test_automatic_rollout_cannot_skip_observation_or_trial_gates(self):
        self.store.set('auto_rollout', True)
        decisions = [{'repo': repo, 'decision': 'plan'} for repo in 'abc']
        with patch.object(s, 'promotion_readiness', return_value={'ready': False}):
            s.advance_rollout(self.store, decisions)
        self.assertEqual(self.store.get('mode', 'shadow'), 'shadow')
        with patch.object(s, 'promotion_readiness', return_value={'ready': True}):
            s.advance_rollout(self.store, decisions)
        self.assertEqual(self.store.get('mode'), 'trial')
        self.assertEqual(len(self.store.get('pilot')), 2)
        with patch.object(s, 'trial_evaluation', return_value={'ready': False}):
            s.advance_rollout(self.store, decisions)
        self.assertIsNone(self.store.get('rollout_complete'))
        with patch.object(s, 'trial_evaluation', return_value={'ready': True}), patch.object(
                s.batch, 'candidates', return_value=['a', 'b', 'c']):
            s.advance_rollout(self.store, decisions)
        self.assertEqual(self.store.get('mode'), 'dynamic')
        self.assertEqual(self.store.get('pilot'), ['a', 'b', 'c'])

    def test_shadow_main_never_calls_dispatch_or_a_model(self):
        with patch.object(sys, 'argv', ['scheduler.py', '--root', str(self.root)]), patch.object(
                s.batch, 'ROOT', self.root), patch.object(s.batch.shared, 'ROOT', self.root), patch.object(
                s.batch, 'candidates', return_value=['a']), patch.object(
                s, 'refresh_snapshot', return_value=self.snapshot), patch.object(
                s, 'dispatch', side_effect=AssertionError('shadow cannot dispatch')), patch.object(
                s.batch.shared, 'py', side_effect=AssertionError('no model or probe calls')):
            self.assertEqual(s.main(), 0)
        self.assertEqual(self.store.get('mode', 'shadow'), 'shadow')
        self.assertIsNotNone(self.store.get('shadow_started'))

    def test_plan_is_archived_only_after_completion_and_pushed_from_clean_checkout(self):
        checkout = self.root / 'dev'
        remote = self.root / 'remote.git'
        run = subprocess.run
        def git(*args):
            return run(['git', *args], capture_output=True, text=True, check=True)
        git('init', '--bare', '-q', str(remote))
        git('init', '-q', '-b', 'main', str(checkout))
        git('-C', str(checkout), 'config', 'user.email', 'test@example.invalid')
        git('-C', str(checkout), 'config', 'user.name', 'Test')
        source = checkout / 'docs/plans/roadmap-next-iteration.md'
        source.parent.mkdir(parents=True)
        source.write_text('# Plan\n\n## Status\n\nIn progress.\n\n## Open threads\n\n- [ ] Move this plan to archive.\n')
        (checkout / 'docs/plans/archive').mkdir()
        git('-C', str(checkout), 'add', '.')
        git('-C', str(checkout), 'commit', '-qm', 'Initial plan')
        git('-C', str(checkout), 'remote', 'add', 'origin', str(remote))
        git('-C', str(checkout), 'push', '-u', 'origin', 'main')
        self.store.set('plan_checkout', str(checkout))
        s.archive_completed_plan(self.store)
        self.assertTrue(source.exists())
        self.store.set('rollout_complete', {'at': time.time()})
        self.store.set('mode', 'dynamic')
        dirty = checkout / 'unrelated.txt'
        dirty.write_text('keep this')
        s.archive_completed_plan(self.store)
        self.assertTrue(source.exists())
        dirty.unlink()
        def local_git_only(argv, **kwargs):
            if argv[-3:] == ['remote', 'get-url', 'origin']:
                return subprocess.CompletedProcess(argv, 0, stdout='git@github.com:jnelken/ai-tools.git\n')
            return run(argv, **kwargs)
        with patch.object(s.subprocess, 'run', side_effect=local_git_only):
            s.archive_completed_plan(self.store)
        archived = checkout / 'docs/plans/archive/roadmap-next-iteration.md'
        self.assertTrue(archived.exists())
        self.assertFalse(source.exists())
        self.assertIn('Complete. Measured shadow', archived.read_text())
        self.assertIsNotNone(self.store.get('plan_archived'))
        head = git('-C', str(checkout), 'rev-parse', 'HEAD').stdout
        published = git('--git-dir', str(remote), 'rev-parse', 'refs/heads/main').stdout
        self.assertEqual(head, published)
        self.assertEqual(git('-C', str(checkout), 'status', '--porcelain').stdout, '')
        # Simulate interruption after the Git action but before its durable completion marker.
        with self.store.db:
            self.store.db.execute("DELETE FROM settings WHERE key IN ('plan_archived','archive_commit')")
        with patch.object(s.subprocess, 'run', side_effect=local_git_only):
            s.archive_completed_plan(self.store)
        self.assertEqual(git('-C', str(checkout), 'rev-parse', 'HEAD').stdout, head)
        self.assertIsNotNone(self.store.get('plan_archived'))

    def test_expansion_requires_measured_trial_and_preserves_ship_count(self):
        start = time.mktime(time.strptime('2026-11-10 10:00:00', '%Y-%m-%d %H:%M:%S'))
        self.store.set('trial_started', start)
        self.store.set('pilot', ['a'])
        records = []
        for stamp, tokens in [('20261109-103001', 100), ('20261111-103001', 40)]:
            self.store.attempt(stamp, 'a')
            records.append({'stamp': stamp, 'outcome': 'blocked-no-item', 'tokens': {
                'total': tokens, 'calls': [{'role': 'orchestrator', 'total': tokens}]}})
        (self.root / 'runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        self.assertFalse(s.trial_evaluation(self.store, start + 6 * s.DAY)['ready'])
        self.assertTrue(s.trial_evaluation(self.store, start + 7 * s.DAY)['ready'])
        records[0]['outcome'] = 'shipped'
        (self.root / 'runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        self.assertFalse(s.trial_evaluation(self.store, start + 7 * s.DAY)['ready'])

    def test_dashboard_explains_waiting_and_keeps_unrouted_issues_visible(self):
        (self.root / 'code').mkdir()
        self.store.attempt('20261110-103001', 'a', {'repo': 'a', 'action': 'resume_worker', 'item': 'DEV-1'})
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'blocked'},
                          {'needs_input': True, 'input_question': '<script>Which account?</script>'})
        self.store.set('incremental_starvation', {'consecutive_zero_admission': 3,
            'oldest_eligible_age_seconds': 7200, 'job_override': {'repo': 'a', 'outcome': 'yielded'}})
        (self.root / 'scheduler-snapshot.json').write_text(json.dumps({'issues': [
            {'id': 'DEV-unrouted', 'title': 'Assign me', 'labels': [], 'url': 'https://linear.app/jnelken/issue/DEV-unrouted'}]}))
        with patch.dict(os.environ, ADVANCE_ROADMAP_ROOT=str(self.root), PERSONAL_CODE_DIR=str(self.root / 'code')):
            spec = importlib.util.spec_from_file_location('queue_dashboard_test', HERE / 'gen-dashboard.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        result = module.queue_panel()
        self.assertIn('&lt;script&gt;Which account?', result)
        self.assertNotIn('<script>Which account?', result)
        self.assertIn('DEV-unrouted', result)
        self.assertIn('repo/* label', result)
        self.assertIn('3 consecutive batches admitted no worker', result)
        self.assertIn('job-only override: a (yielded)', result)

    def test_resumed_workers_also_consume_the_daily_attempt_cap(self):
        day = time.strftime('%Y%m%d')
        for sec in ('103001', '153001'):
            stamp = day + '-' + sec
            self.store.attempt(stamp, 'a')
            self.store.finish({'stamp': stamp, 'orchestrator': 'pending-plan',
                               'worker': 'codex', 'outcome': 'quota-paused'})
        self.assertEqual(s.daily_admission(self.store, ['a', 'b']), ['b'])

    def test_quota_driven_policy_does_not_apply_twice_daily_attempt_cap(self):
        day = time.strftime('%Y%m%d')
        for sec in ('103001', '153001'):
            stamp = day + '-' + sec
            self.store.attempt(stamp, 'a')
            self.store.finish({'stamp': stamp, 'orchestrator': 'pending-plan',
                               'worker': 'codex', 'outcome': 'quota-paused'})
        with patch.object(s.batch.shared, 'code_dir', return_value=str(self.root)), \
                patch.object(s, 'eligibility_fingerprint', return_value='same'):
            decisions = s.policy(self.store, ['a'], self.snapshot, daily_limit=False)
        self.assertNotIn('daily attempt budget used', decisions[0]['reason'])

    def test_waiting_item_does_not_dispatch_or_create_batch(self):
        s.dispatch(self.store, [{'repo': 'a', 'decision': 'wait'}], self.snapshot, ['a'])
        self.assertFalse((self.root / 'batches').exists())


if __name__ == '__main__':
    unittest.main()
