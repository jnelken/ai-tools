import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('workspaces', Path(__file__).parents[1] / 'lib/workspaces.py')
workspaces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspaces)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs' / 'one'
        self.run.mkdir(parents=True)
        (self.root / 'runs.jsonl').write_text(json.dumps({'stamp': 'one', 'worker_exit': 0}) + '\n')
        (self.run / 'workspace.json').write_text(json.dumps({'workspace_id': 'ws', 'terminal_id': 'term'}))
        (self.run / 'worker.pid').write_text('123')

    def test_terminal_requires_settlement_and_detachment(self):
        calls = []
        def cli(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('terminals', 'list'):
                return {'sessions': [{'terminalId': 'term', 'attached': False}]}
            return {'ok': True}
        with patch.object(workspaces, 'cli', side_effect=cli), patch.object(workspaces, 'process_exists', return_value=False):
            workspaces.cleanup_terminals(self.root, False)
            self.assertFalse(calls)
            (self.run / 'worker.complete').touch()
            workspaces.cleanup_terminals(self.root, False)
        self.assertEqual(calls[-1], ('terminals', 'close', '--local', '--workspace', 'ws', '--terminal', 'term'))
        self.assertTrue((self.run / 'terminal.cleaned').exists())

    def test_live_or_attached_worker_is_kept(self):
        (self.run / 'worker.complete').touch()
        with patch.object(workspaces, 'cli', return_value={'sessions': [{'terminalId': 'term', 'attached': True}]}) as cli:
            with patch.object(workspaces, 'process_exists', return_value=True):
                workspaces.cleanup_terminals(self.root, False)
                cli.assert_not_called()
            with patch.object(workspaces, 'process_exists', return_value=False):
                workspaces.cleanup_terminals(self.root, False)
                self.assertEqual(cli.call_count, 1)
        self.assertFalse((self.run / 'terminal.cleaned').exists())

    def test_unfinished_branch_is_kept(self):
        with sqlite3.connect(self.root / 'workqueue.sqlite3') as db:
            db.execute('create table work (plan text, state text)')
            db.execute('insert into work values (?, ?)', (json.dumps({'branch': 'feature'}), 'eligible'))
        workspace = {'id': 'ws', 'branch': 'feature', 'worktreePath': str(self.root)}
        def cli(*args, **kwargs):
            if args[:2] == ('workspaces', 'list'):
                return [workspace]
            self.fail('unfinished workspace should not be inspected or deleted')
        with patch.object(workspaces, 'cli', side_effect=cli), patch.object(workspaces, 'merged_workspaces', return_value={'ws': {'busy': 0, 'open_prs': 0, 'merged_at': 1, 'last_event_at': 0}}):
            self.assertEqual(workspaces.cleanup_worktrees(SimpleNamespace(min_idle_minutes=0, dry_run=False), self.root), 0)

    def test_only_clean_merged_idle_workspace_is_deleted(self):
        with sqlite3.connect(self.root / 'workqueue.sqlite3') as db:
            db.execute('create table work (plan text, state text)')
        workspace = {'id': 'ws', 'branch': 'feature', 'worktreePath': str(self.root)}
        calls = []
        def cli(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('workspaces', 'list'):
                return [workspace]
            if args[:2] == ('terminals', 'list'):
                return {'sessions': []}
            return {'ok': True}
        completed = SimpleNamespace(returncode=0, stdout='')
        info = {'ws': {'busy': 0, 'open_prs': 0, 'merged_at': 1, 'last_event_at': 0}}
        with patch.object(workspaces, 'cli', side_effect=cli), patch.object(workspaces, 'merged_workspaces', return_value=info), patch.object(workspaces.subprocess, 'run', return_value=completed):
            workspaces.cleanup_worktrees(SimpleNamespace(min_idle_minutes=0, dry_run=False), self.root)
        self.assertEqual(calls[-1], ('workspaces', 'delete', '--local', 'ws'))


if __name__ == '__main__':
    unittest.main()
