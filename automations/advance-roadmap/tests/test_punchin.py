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

    def test_linked_worktree_records_in_primary_checkout_only(self):
        with tempfile.TemporaryDirectory() as temp:
            primary = Path(temp) / 'primary'
            (primary / '.git' / 'worktrees' / 'wt').mkdir(parents=True)
            worktree = Path(temp) / 'wt'
            worktree.mkdir()
            (worktree / '.git').write_text(f'gitdir: {primary / ".git" / "worktrees" / "wt"}\n')
            target = punchin.punch_in(worktree, 'DEV-5', 's5', 'auto', 'cursor')
            self.assertEqual(target, (primary / 'IN_PROGRESS.md').resolve())
            self.assertTrue(target.exists())
            self.assertFalse((worktree / 'IN_PROGRESS.md').exists())
            self.assertFalse((worktree / '.claude').exists())

    def test_release_removes_only_the_finished_session_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            handoff = root / 'IN_PROGRESS.md'
            handoff.write_text('# Notes\n- [ ] Keep me\n')
            punchin.punch_in(root, 'DEV-1', 'done-session', 'm', 'cursor')
            punchin.punch_in(root, 'DEV-2', 'live-session', 'm', 'cursor')
            self.assertTrue(punchin.release(root, 'done-session'))
            text = handoff.read_text()
            self.assertNotIn('done-session', text)
            self.assertIn('live-session', text)
            self.assertIn('- [ ] Keep me', text)
            self.assertFalse(punchin.release(root, 'done-session'))

    def test_release_finds_block_in_either_file_when_both_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            (root / '.claude').mkdir()
            (root / '.claude' / 'IN_PROGRESS.md').write_text('# Notes\n')
            (root / 'IN_PROGRESS.md').write_text(
                '# In Progress\n\n<!-- agent-work:dual -->\n- [ ] Ticket: DEV-3 | Session: dual\n<!-- /agent-work:dual -->\n')
            self.assertTrue(punchin.release(root, 'dual'))
            self.assertNotIn('dual', (root / 'IN_PROGRESS.md').read_text())
            self.assertFalse(punchin.release(root, 'dual'))

    def test_sweep_releases_only_done_work_sessions(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp) / 'code'
            (code / 'repo-a' / '.git').mkdir(parents=True)
            (code / 'repo-b' / '.git').mkdir(parents=True)
            for repo, session in (('repo-a', 'finished'), ('repo-b', 'running')):
                punchin.punch_in(code / repo, 'DEV-9', session, 'm', 'cursor')
            queue = Path(temp) / 'q.sqlite3'
            con = sqlite3.connect(queue)
            con.executescript('''
                create table work (id integer primary key, repo text, state text);
                create table sessions (work_id integer, provider text, binding text);
                insert into work values (1, 'repo-a', 'done'), (2, 'repo-b', 'eligible');
                insert into sessions values (1, 'cursor', '{"session_id": "finished"}'),
                                            (2, 'cursor', '{"session_id": "running"}');
            ''')
            con.commit(); con.close()
            self.assertEqual(punchin.sweep(code, queue), [('repo-a', 'finished')])
            self.assertNotIn('finished', (code / 'repo-a' / 'IN_PROGRESS.md').read_text())
            self.assertIn('running', (code / 'repo-b' / 'IN_PROGRESS.md').read_text())


if __name__ == '__main__':
    unittest.main()
