import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from slack_fallback import notice, run_notice


class SlackFallbackTest(unittest.TestCase):
    def test_run_uses_matching_record_and_bulleted_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runs.jsonl'
            path.write_text('bad json\n' + json.dumps({'stamp': 'one', 'assigned_repo': 'demo',
                'outcome': 'blocked-no-item', 'summary': 'A decision is needed.'}) + '\n')
            self.assertEqual(run_notice(path, 'one'), '*advance-roadmap · Run update*\n\n• Detailed summary unavailable. Check the roadmap dashboard.')
            self.assertEqual(run_notice(path, 'missing'), '')

    def test_batch_or_alert_facts_are_separate_bullets(self):
        self.assertEqual(notice('Batch', 'one', '', 'two\nmore'), '*advance-roadmap · Run update*\n\n• Detailed summary unavailable. Check the roadmap dashboard.')

    def test_untrusted_secrets_never_reach_webhook(self):
        self.assertNotIn('example-token-123', notice('Batch example-token-123', 'example-token-123'))


if __name__ == '__main__':
    unittest.main()
