import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('dashboard', Path(__file__).parents[1] / 'gen-dashboard.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class DashboardWorktreeTest(unittest.TestCase):
    def test_dirty_primary_checkout_is_informational_for_isolated_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'demo'
            (repo / '.git').mkdir(parents=True)
            def git(args, cwd=None):
                return {('remote', 'get-url', 'origin'): 'git@github.com:jnelken/demo.git',
                        ('status', '--porcelain'): ' M other-work.txt',
                        ('rev-parse', '--verify', 'origin/main'): 'abc',
                        ('rev-list', '--count', 'origin/main..HEAD'): '1'}.get(tuple(args))
            with patch.object(dashboard, 'CODE_DIR', tmp), patch.object(dashboard, 'git', side_effect=git), \
                    patch.object(dashboard, 'find_roadmap', return_value='ROADMAP.md'), \
                    patch.object(dashboard, 'blockers', return_value=(None, 0, '')):
                row, = dashboard.survey_repos()
            self.assertTrue(row['eligible'])
            self.assertEqual(row['blocks'], [])
            self.assertEqual(len(row['notes']), 2)


if __name__ == '__main__':
    unittest.main()
