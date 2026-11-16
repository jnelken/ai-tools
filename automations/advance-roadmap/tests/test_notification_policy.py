import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from notification_policy import main, routine_checkpoint


class NotificationPolicyTest(unittest.TestCase):
    def test_only_routine_yields_are_suppressed(self):
        for outcome, reason, needs_input, quiet in [
            ('yielded', 'provider-exit', False, True),
            ('yielded', 'provider-exit', True, False),
            ('yielded', 'quota-ceiling', False, False),
            ('yielded', 'watchdog', False, False),
            ('shipped', 'provider-exit', False, False),
            ('error', '', False, False),
            ('skipped-unchanged', '', False, True),
        ]:
            with self.subTest(outcome=outcome, reason=reason, needs_input=needs_input):
                row = {'outcome': outcome, 'incremental': {'lifecycle': {'reason': reason}}}
                self.assertEqual(routine_checkpoint(row, {'needs_input': needs_input}), quiet)
        row = {'outcome': 'yielded'}
        self.assertFalse(routine_checkpoint(row, {}, {'verdict': 'unavailable', 'gated': ['claude']}))
        self.assertTrue(routine_checkpoint(row, {}, {'verdict': 'clean', 'gated': ['claude']}))

    def test_only_completed_unassigned_repo_searches_are_quiet(self):
        row = {'outcome': 'blocked-no-item', 'assigned_repo': 'mailcrush', 'item': None,
               'linear_id': None, 'orchestrator_exit': 0, 'worker_exit': 0, 'worker': 'none'}
        result = {'outcome': 'bookkeeping'}
        planner = {'action': 'blocked_no_item', 'blockers': [], 'archives': [], 'closeouts': []}
        self.assertTrue(routine_checkpoint(row, result, planner=planner))
        for field, value in [('blockers', [{'detail': 'needs input'}]), ('directive_ticket', 'DEV-1'),
                             ('closeouts', ['DEV-2']), ('archives', ['plan.md']),
                             ('parse_error', 'invalid output'), ('planner_error', {'kind': 'quota'}),
                             ('action', 'dispatch_worker')]:
            with self.subTest(planner_field=field):
                self.assertFalse(routine_checkpoint(row, result, planner={**planner, field: value}))
        for field, value in [('assigned_repo', None), ('item', 'DEV-1'), ('linear_id', 'DEV-1'),
                             ('orchestrator_exit', 1), ('worker_exit', 1), ('worker', 'codex'),
                             ('outcome', 'error')]:
            with self.subTest(row_field=field):
                self.assertFalse(routine_checkpoint({**row, field: value}, result, planner=planner))
        self.assertFalse(routine_checkpoint(row, {**result, 'needs_input': True}, planner=planner))
        self.assertFalse(routine_checkpoint(row, result, planner=None))
        self.assertFalse(routine_checkpoint(row, result, planner=planner, dispatch_rejected=True))
        self.assertTrue(routine_checkpoint({**row, 'outcome': 'nothing-qualified'}, result,
                                           planner={**planner, 'action': 'nothing_qualified'}))

    def test_main_requires_a_readable_planner_and_respects_dispatch_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = {'stamp': 's1', 'outcome': 'blocked-no-item', 'assigned_repo': 'mailcrush',
                   'orchestrator_exit': 0, 'worker_exit': 0, 'worker': 'none'}
            (root / 'runs.jsonl').write_text(json.dumps(row) + '\n')
            (root / 'worker-result.json').write_text(json.dumps({'outcome': 'bookkeeping'}))
            self.assertEqual(main(root / 'runs.jsonl', 's1', root), 1)
            (root / 'orchestrator-result.json').write_text(json.dumps({'action': 'blocked_no_item'}))
            self.assertEqual(main(root / 'runs.jsonl', 's1', root), 0)
            (root / 'dispatch-rejected').write_text('invalid ticket')
            self.assertEqual(main(root / 'runs.jsonl', 's1', root), 1)
            (root / 'orchestrator-result.json').write_text('{invalid')
            self.assertEqual(main(root / 'runs.jsonl', 's1', root), 1)


if __name__ == '__main__':
    unittest.main()
