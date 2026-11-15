"""Ownership records preserve handoffs and concurrent sessions."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import punchin


class PunchInTests(unittest.TestCase):
    def test_preserves_notes_updates_one_session_and_keeps_others(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            handoff = root / 'IN_PROGRESS.md'
            handoff.write_text('# Existing\n- [ ] Preserve this\n')
            punchin.punch_in(root, 'DEV-7', 'session-1', 'gpt-test', 'codex')
            punchin.punch_in(root, 'DEV-8', 'session-2', 'sonnet', 'claude')
            punchin.punch_in(root, 'DEV-9', 'session-1', 'gpt-new', 'codex')
            text = handoff.read_text()
            self.assertIn('- [ ] Preserve this', text)
            self.assertEqual(text.count('<!-- agent-work:session-1 -->'), 1)
            self.assertIn('Ticket: DEV-9 | Session: session-1 | Provider: codex | Model: gpt-new', text)
            self.assertIn('Ticket: DEV-8 | Session: session-2', text)
            self.assertNotIn('Ticket: DEV-7', text)

    def test_existing_claude_handoff_and_symlink_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            (root / '.claude').mkdir()
            handoff = root / '.claude' / 'IN_PROGRESS.md'
            handoff.write_text('# Notes\n')
            self.assertEqual(punchin.punch_in(root, None, 's1', 'auto', 'cursor'), handoff.resolve())
            handoff.unlink()
            handoff.symlink_to(root / 'elsewhere')
            with self.assertRaises(ValueError):
                punchin.punch_in(root, None, 's1', 'auto', 'cursor')
            self.assertFalse((root / 'elsewhere').exists())


if __name__ == '__main__':
    unittest.main()
