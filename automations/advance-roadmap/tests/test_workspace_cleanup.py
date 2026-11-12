import importlib.util
import json
import sqlite3
import subprocess
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

    def test_fresh_workspace_uses_origin_base_not_primary_checkout(self):
        out = self.root / 'workspace.json'
        args = SimpleNamespace(repo=str(self.root), branch='roadmap/new', name='new',
                               command='worker', base=None, tag=None, out=str(out))
        calls = []
        def cli(*values, **kwargs):
            calls.append(values)
            return {'workspace': {'id': 'ws'}, 'terminals': [{'terminalId': 'term'}]}
        with patch.object(workspaces, 'ensure_project', return_value='project'), \
                patch.object(workspaces, 'find_workspace', return_value=None), \
                patch.object(workspaces, 'remote_default', return_value='origin/main'), \
                patch.object(workspaces, 'cli', side_effect=cli):
            self.assertEqual(workspaces.cmd_launch(args), 0)
        self.assertEqual(calls[0][calls[0].index('--base-branch') + 1], 'origin/main')

    def test_remote_base_ignores_local_unpushed_commit(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init', '-q', '-b', 'main')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '--allow-empty', '-m', 'base')
        git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '--allow-empty', '-m', 'local')
        self.assertNotEqual(git('rev-parse', 'HEAD'), git('rev-parse', 'origin/main'))
        self.assertEqual(workspaces.remote_default(str(self.root)), 'origin/main')

    def test_fresh_workspace_fails_closed_without_origin_base(self):
        args = SimpleNamespace(repo=str(self.root), branch='roadmap/new', name='new',
                               command='worker', base=None, tag=None, out=str(self.root / 'out'))
        with patch.object(workspaces, 'ensure_project', return_value='project'), \
                patch.object(workspaces, 'find_workspace', return_value=None), \
                patch.object(workspaces, 'remote_default', return_value=None), \
                patch.object(workspaces, 'cli') as cli:
            self.assertEqual(workspaces.cmd_launch(args), 1)
            cli.assert_not_called()

    def test_resume_requires_automation_owned_workspace(self):
        args = SimpleNamespace(repo=str(self.root), branch='roadmap/old', name='old',
                               command='worker', base=None, tag=None, out=str(self.root / 'out'))
        workspace = {'id': 'ws', 'tags': 'other'}
        with patch.object(workspaces, 'ensure_project', return_value='project'), \
                patch.object(workspaces, 'find_workspace', return_value=workspace), \
                patch.object(workspaces, 'cli', return_value={'terminalId': 'term'}) as cli:
            self.assertEqual(workspaces.cmd_launch(args), 1)
            cli.assert_not_called()
            workspace['tags'] = 'advance-roadmap'
            self.assertEqual(workspaces.cmd_launch(args), 0)
            self.assertEqual(json.loads((self.root / 'out').read_text())['workspace_id'], 'ws')


if __name__ == '__main__':
    unittest.main()
