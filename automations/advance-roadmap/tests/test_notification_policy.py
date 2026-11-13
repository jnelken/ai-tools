import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from notification_policy import routine_checkpoint


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


if __name__ == '__main__':
    unittest.main()
