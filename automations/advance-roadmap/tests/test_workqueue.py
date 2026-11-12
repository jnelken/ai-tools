"""Recovery invariants: no models, external APIs, or real personal repositories."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'lib'))
import workqueue as q
import pendingplan


def plan(repo='a', item='DEV-1'):
    return {'action': 'dispatch_worker', 'repo': repo, 'linear_id': item, 'item': item,
            'branch': 'roadmap/test', 'worker_brief': 'Implement the acceptance criteria'}


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / 'code'
        (self.code / 'a').mkdir(parents=True)
        self.store = q.Store(self.root)
        self.addCleanup(self.store.close)

    def register(self, stamp='20261110-103001'):
        return self.store.attempt(stamp, 'a', plan())

    def test_restart_preserves_identity_checkpoints_and_attempts(self):
        wid = self.register()
        self.store.checkpoint(wid, '20261110-103001', 'reviewing', {'next_action': 'Fix finding 2'})
        with q.Store(self.root) as restarted:
            self.assertEqual(restarted.list()[0]['id'], wid)
            self.assertEqual(restarted.list()[0]['checkpoint']['next_action'], 'Fix finding 2')
            self.assertEqual(restarted.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)

    def test_clean_yield_advances_phase_but_forced_yield_does_not(self):
        self.register()
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'yielded', 'worker_exit': 0},
                          {'next_phase': 'reviewing'})
        self.assertEqual((self.store.list()[0]['phase'], self.store.list()[0]['state']),
                         ('reviewing', 'eligible'))
        self.register('20261110-153001')
        self.store.finish({'stamp': '20261110-153001', 'outcome': 'yielded', 'worker_exit': 124},
                          {'next_phase': 'shipping'})
        self.assertEqual(self.store.list()[0]['phase'], 'reviewing')
        self.assertEqual(self.store.list()[0]['state'], 'eligible')

    def test_interrupted_worker_cannot_clear_pending_assignment_with_partial_ship_result(self):
        saved, result, request = (self.root / name for name in ('pending.json', 'result.json', 'request.json'))
        saved.write_text(json.dumps({'orchestrator': plan(), 'last_stamp': '20261110-103001'}))
        result.write_text(json.dumps({'outcome': 'shipped', 'provider': 'codex'}))
        request.write_text(json.dumps({'stamp': '20261110-103001', 'orchestrator': plan()}))
        store_type = q.Store
        with patch.object(pendingplan.workqueue, 'Store', side_effect=lambda: store_type(self.root)):
            pendingplan.cmd_settle(SimpleNamespace(file=str(saved), worker_result=str(result),
                                                   request_file=str(request), worker_rc=124))
        self.assertTrue(saved.exists())
        self.assertNotEqual(self.store.list()[0]['state'], 'done')

    def test_reimport_migrates_old_retry_backoff_for_a_yield(self):
        wid = self.register()
        row = {'stamp': '20261110-103001', 'outcome': 'yielded', 'worker_exit': 143}
        self.store.finish(row)
        with self.store.db:
            self.store.db.execute("UPDATE work SET state='retry',not_before=9999999999 WHERE id=?", (wid,))
        self.store.finish(row)
        self.assertEqual((self.store.list()[0]['state'], self.store.list()[0]['not_before']), ('eligible', 0))

    def test_late_older_attempt_cannot_replace_checkpoint_or_close_newer_work(self):
        wid = self.register()
        self.register('20261110-153001')
        self.store.checkpoint(wid, '20261110-153001', 'reviewing', {'next_action': 'Current findings'})
        self.assertFalse(self.store.checkpoint(wid, '20261110-103001', 'done', {'next_action': 'Stale'}))
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'shipped', 'worker_exit': 0, 'worker': 'codex'})
        work = self.store.list()[0]
        self.assertEqual(work['phase'], 'reviewing')
        self.assertNotEqual(work['state'], 'done')

    def test_explicit_human_question_is_distinct_from_retryable_failure(self):
        self.register()
        r = {'stamp': '20261110-103001', 'outcome': 'error'}
        self.store.finish(r, {'summary': 'test failed'})
        self.assertEqual(self.store.list()[0]['state'], 'retry')
        self.register('20261110-153001')
        self.store.finish({'stamp': '20261110-153001', 'outcome': 'blocked'},
                          {'needs_input': True, 'input_question': 'Which account should this use?'})
        work = self.store.list()[0]
        self.assertEqual(self.store.hold('a')['state'], 'human')
        self.store.release_human(work['id'], 'Use the existing personal account')
        self.assertIsNone(self.store.hold('a'))

    def test_quota_wait_honors_reset_and_reimport_does_not_extend_it(self):
        self.register()
        r = {'stamp': '20261110-103001', 'outcome': 'quota-paused',
             'quota': {'providers': [{'reset_epoch': 10000}]}}
        with patch.object(q.time, 'time', return_value=1000):
            self.store.finish(r)
        with patch.object(q.time, 'time', return_value=2000):
            self.store.finish(r)
        self.assertEqual(self.store.list()[0]['not_before'], 10000)
        self.assertIsNotNone(self.store.hold('a', 9999))
        self.assertIsNone(self.store.hold('a', 10001))

    def test_changed_head_invalidates_old_tests_but_preserves_artifact_pointers(self):
        wid = self.register()
        self.store.checkpoint(wid, '20261110-103001', 'reviewing',
                              {'head': 'one', 'evidence_head': 'one', 'tests': ['passed'],
                               'findings': ['old'], 'artifacts': ['reviews.jsonl']})
        self.store.checkpoint(wid, '20261110-103001', 'implementing', {'head': 'two'})
        cp = self.store.list()[0]['checkpoint']
        self.assertFalse(cp['evidence_valid'])
        self.assertEqual(cp['tests'], ['passed'])
        self.assertEqual(cp['findings'], ['old'])
        self.assertEqual(cp['artifacts'], ['reviews.jsonl'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 2)

    def test_changed_requirements_invalidate_evidence_even_at_same_head(self):
        wid = self.register()
        self.store.checkpoint(wid, '20261110-103001', 'reviewing', {'head': 'one', 'evidence_head': 'one'})
        self.assertTrue(self.store.list()[0]['checkpoint']['evidence_valid'])
        self.store.register({**plan(), 'worker_brief': 'Changed acceptance criteria'}, '20261110-153001')
        checkpoint = self.store.list()[0]['checkpoint']
        self.assertFalse(checkpoint['evidence_valid'])
        self.assertIsNone(checkpoint['evidence_head'])
        self.assertEqual(self.store.list()[0]['phase'], 'implementing')

    def test_oversized_checkpoint_does_not_destroy_prior_state(self):
        wid = self.register()
        self.store.checkpoint(wid, '20261110-103001', 'ready', {'summary': 'valid'})
        with self.assertRaises(ValueError):
            self.store.checkpoint(wid, '20261110-103001', 'reviewing', {'summary': 'x' * 9000})
        self.assertEqual(self.store.list()[0]['phase'], 'ready')

    def test_failed_write_rolls_back_phase_and_checkpoint_together(self):
        wid = self.register()
        self.store.db.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON work BEGIN SELECT RAISE(ABORT,'disk full'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.checkpoint(wid, '20261110-103001', 'reviewing', {'summary': 'new'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 0)
        self.assertEqual(self.store.list()[0]['phase'], 'ready')

    def test_completed_work_is_not_resurrected_by_late_abort(self):
        self.register()
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'shipped', 'worker_exit': 0, 'worker': 'codex'})
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'incomplete'})
        self.assertEqual(self.store.list()[0]['state'], 'done')

    def test_importing_old_failure_does_not_start_a_new_cooldown(self):
        self.register()
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'error',
                           'finished_at': '2020-01-01T00:00:00'})
        self.assertIsNone(self.store.hold('a'))

    def test_repeated_failure_backs_off_without_requesting_human_input(self):
        self.register()
        with patch.object(q.time, 'time', return_value=1000):
            self.store.finish({'stamp': '20261110-103001', 'outcome': 'error'})
        self.register('20261110-153001')
        with patch.object(q.time, 'time', return_value=2000):
            self.store.finish({'stamp': '20261110-153001', 'outcome': 'error'})
        work = self.store.list()[0]
        self.assertEqual(work['state'], 'retry')
        self.assertEqual(work['not_before'], 2000 + 1800)

    def test_pid_reuse_is_not_live_worker_adoption(self):
        with patch.object(q, 'process_start', return_value='old birth'):
            self.store.attempt('s', 'a', plan(), pid=12)
        a = dict(self.store.db.execute('SELECT * FROM attempts').fetchone())
        with patch.object(q, 'process_start', return_value='new birth'):
            self.assertFalse(q.alive(a))
        with patch.object(q, 'process_start', return_value='old birth'):
            self.assertTrue(q.alive(a))

    def test_import_is_idempotent_retains_sources_and_never_revives_historical_failure(self):
        pending = self.code / '.advance-roadmap'
        pending.mkdir()
        source = pending / 'pending-plan-a.json'
        source.write_text(json.dumps({'orchestrator': plan(), 'planned_stamp': '20261110-103001'}))
        old = self.root / 'runs/20260901-000000'
        old.mkdir(parents=True)
        (old / 'orchestrator-result.json').write_text(json.dumps(plan(item='DEV-old-parked')))
        manifest = self.root / 'batches/2026-09-01'
        manifest.mkdir(parents=True)
        (manifest / 'batch.json').write_text(json.dumps({'queue': [{'repo': 'a', 'stamp': old.name}]}))
        q.sync(self.store, self.code)
        q.sync(self.store, self.code)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0]['item'], 'DEV-1')
        self.assertTrue(source.exists())
        source.unlink()
        q.sync(self.store, self.code)
        self.assertEqual(json.loads(source.read_text())['orchestrator']['linear_id'], 'DEV-1')

    def test_human_reply_revalidates_without_waking_on_unrelated_changes(self):
        self.register()
        run = self.root / 'runs/20261110-103001'
        run.mkdir(parents=True)
        snap = {'ok': True, 'issues': [{'id': 'DEV-1', 'human_comments_sha': 'one'}]}
        (run / 'linear-snapshot.json').write_text(json.dumps(snap))
        self.store.finish({'stamp': '20261110-103001', 'outcome': 'blocked'},
                          {'needs_input': True, 'input_question': 'Which account?'})
        q.wake_humans(self.store, {**snap, 'issues': snap['issues'] + [{'id': 'DEV-other'}]})
        self.assertEqual(self.store.hold('a')['state'], 'human')
        q.wake_humans(self.store, {'ok': True, 'issues': [{'id': 'DEV-1', 'human_comments_sha': 'two'}]})
        self.assertIsNone(self.store.hold('a'))

    def test_ready_barrier_does_not_erase_saved_review_phase(self):
        wid = self.register()
        self.store.checkpoint(wid, '20261110-103001', 'reviewing', {'next_action': 'Finish review'})
        q.capture(self.store, '20261110-103002', 'a', {**plan(), 'action': 'resume_worker'}, 'ready')
        self.assertEqual(self.store.list()[0]['phase'], 'reviewing')
        self.assertEqual(self.store.list()[0]['checkpoint']['next_action'], 'Finish review')

    def test_surviving_wrappers_and_detached_workers_share_the_same_agent_cap(self):
        with patch.object(q, 'process_start', return_value='birth'):
            self.store.attempt('20261110-103001', 'a', plan(), pid=12)
            folder = self.root / 'inflight/a'
            folder.mkdir(parents=True)
            (folder / 'one.pid').write_text('13')
            self.assertEqual(q.active_count(self.store), 1)
            (folder / 'two.pid').write_text('14')
            self.assertEqual(q.active_count(self.store), 2)

    def test_saved_plan_can_resume_after_age_and_retry_limits_with_checkpoint(self):
        self.register()
        self.store.checkpoint(q.identity(plan()), '20261110-103001', 'reviewing', {'next_action': 'Fix finding 2'})
        saved = self.root / 'pending.json'
        saved.write_text(json.dumps({'orchestrator': plan(), 'planned_stamp': '20261110-103001',
                                     'planned_at': '2020-01-01T00:00:00', 'attempts': 12}))
        out = self.root / 'out.json'
        store_class = q.Store
        with patch.object(pendingplan, 'CODE_DIR', str(self.code)), patch.object(
                pendingplan, 'branch_exists', return_value=True), patch.object(
                pendingplan, 'merged_assignment', return_value=None), patch.object(
                q, 'Store', side_effect=lambda: store_class(self.root)):
            rc = pendingplan.cmd_reuse(SimpleNamespace(file=str(saved), out=str(out), max_age_hours=36, max_attempts=2))
        result = json.loads(out.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(result['action'], 'resume_worker')
        self.assertIn('Fix finding 2', result['worker_brief'])
        self.assertIn('revalidation', result['worker_brief'])
        self.assertTrue(saved.exists())

    def test_merged_recovery_checks_exact_branch_revision_and_time(self):
        pr = {'headRefName': 'roadmap/test', 'headRefOid': 'abc', 'mergedAt': '2026-11-11T10:00:00Z',
              'mergeCommit': {'oid': 'merge'}, 'url': 'https://github.com/jnelken/a/pull/1'}
        def command(args, **kwargs):
            return SimpleNamespace(stdout='abc\n' if args[0] == 'git' else json.dumps([pr]))
        with patch.object(pendingplan.subprocess, 'run', side_effect=command):
            self.assertEqual(pendingplan.merged_assignment(str(self.code / 'a'), 'roadmap/test',
                             '2026-11-10T00:00:00+00:00')['merge_commit'], 'merge')
            pr['headRefOid'] = 'different'
            self.assertIsNone(pendingplan.merged_assignment(str(self.code / 'a'), 'roadmap/test',
                                                            '2026-11-10T00:00:00+00:00'))

    def test_merged_saved_assignment_resumes_verification_without_another_merge(self):
        self.register()
        saved, out = self.root / 'pending.json', self.root / 'out.json'
        saved.write_text(json.dumps({'orchestrator': plan(), 'planned_stamp': '20261110-103001',
                                    'planned_at': '2026-11-10T00:00:00', 'attempts': 0}))
        merged = {'url': 'https://github.com/jnelken/a/pull/1', 'head': 'abc', 'merge_commit': 'merge'}
        store_class = q.Store
        with patch.object(pendingplan, 'CODE_DIR', str(self.code)), patch.object(
                pendingplan, 'branch_exists', return_value=True), patch.object(
                pendingplan, 'merged_assignment', return_value=merged), patch.object(
                q, 'Store', side_effect=lambda: store_class(self.root)):
            self.assertEqual(pendingplan.cmd_reuse(SimpleNamespace(file=str(saved), out=str(out),
                                                                   max_age_hours=36, max_attempts=2)), 0)
        self.assertEqual(self.store.list()[0]['phase'], 'verifying')
        self.assertIn('never merge again', json.loads(out.read_text())['worker_brief'])


if __name__ == '__main__':
    unittest.main()
