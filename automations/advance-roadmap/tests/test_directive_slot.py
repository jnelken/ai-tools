from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import directive_slot as ds
import directive_store as store


def directive(repo, item, answer, go, trailer=''):
    return (
        f"Some work description.\n\n<!-- advance-roadmap:directive:{repo} -->\n## Directive\n\n"
        f"**Recorded:** 2026-11-25 by /unblock-roadmap · **Repo:** `{repo}`\n\n"
        "### Answered decisions\n\n"
        f"* **Item:** {item}\n  **Question:** q?\n  **Answer:** {answer}\n  **Recorded in:** not yet\n\n"
        f"### Go-ahead\n\n{go}\n"
        + (trailer + "\n" if trailer else "")
    )


DIRTY = (
    "<!-- advance-roadmap:directive:repo -->\n## Directive\n\n**Recorded:** x\n\n"
    "### Dirty-tree resolution\n\n```\n-M | a.py\n```\n\n**Instruction (verbatim):** Commit it all\n\n"
    "### Go-ahead\n\nDone.\n"
)


class DirectiveSlotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        env = patch.dict(os.environ, DIRECTIVE_STORE_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)

    def duplicate_issues(self, repo='repo'):
        return [
            {'identifier': 'DEV-1', 'description': directive(repo, 'first', 'yes', '* go')},
            {'identifier': 'DEV-2', 'description': directive(repo, 'second', 'no', '* go')},
        ]

    def run_main(self, command, snapshot, writer=None):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(ds, 'load_snapshot', return_value=snapshot), \
                redirect_stdout(output), redirect_stderr(errors):
            code = ds.main(command, writer=writer)
        return code, output.getvalue(), errors.getvalue()

    def test_single_pending_directive_needs_no_merge(self):
        issues = [{'identifier': 'DEV-1', 'description': directive('mailcrush', 'DEV-1 x', 'yes', '* go')}]
        plans, refusals = ds.merge_plan(issues, today='2026-11-27')
        self.assertEqual((plans, refusals), ([], []))
        self.assertEqual(ds.duplicate_repos(issues), [])

    def test_consumed_headings_are_not_pending(self):
        text = '<!-- advance-roadmap:directive:mailcrush -->\n## Directive (consumed 2026-11-01)\n'
        self.assertEqual(ds.pending_entries(text), [])

    def test_duplicates_merge_onto_lowest_dev_number(self):
        issues = [
            {'identifier': 'DEV-332', 'description': directive('mailcrush', 'DEV-332 b', 'combined set', '* proceed b')},
            {'identifier': 'DEV-330', 'description': directive('mailcrush', 'DEV-330 a', 'combined actionable', '* proceed a')},
        ]
        self.assertEqual(ds.duplicate_repos(issues), ['mailcrush'])
        plans, refusals = ds.merge_plan(issues, today='2026-11-27')
        self.assertEqual(refusals, [])
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan['carrier'], 'DEV-330')
        carrier = plan['updates']['DEV-330']
        self.assertIn('DEV-330 a', carrier)
        self.assertIn('DEV-332 b', carrier)
        self.assertIn('* DEV-330: proceed a', carrier)
        self.assertIn('* DEV-332: proceed b', carrier)
        # The non-carrier heading is consumed, not deleted, and keeps its answers.
        consumed = plan['updates']['DEV-332']
        self.assertIn('## Directive (consumed 2026-11-27, merged into DEV-330)', consumed)
        self.assertNotRegex(consumed, r'(?m)^## Directive$')
        self.assertIn('combined set', consumed)
        # The merged carrier has exactly one pending heading.
        self.assertEqual(len(ds.pending_entries(carrier)), 1)

    def test_slack_trailer_survives_on_carrier(self):
        trailer = '<!-- slackagent:unblock:abc -->'
        issues = [
            {'identifier': 'DEV-214', 'description': directive('slackagent', 'DEV-214 q', 'a1', '* go', trailer)},
            {'identifier': 'DEV-216', 'description': directive('slackagent', 'DEV-216 q', 'a2', '* go2')},
        ]
        carrier = ds.merge_plan(issues, today='2026-11-27')[0][0]['updates']['DEV-214']
        self.assertTrue(carrier.rstrip().endswith(trailer))

    def test_dirty_tree_directive_is_refused_not_merged(self):
        issues = [
            {'identifier': 'DEV-1', 'description': directive('repo', 'DEV-1 q', 'yes', '* go')},
            {'identifier': 'DEV-2', 'description': DIRTY},
        ]
        plans, refusals = ds.merge_plan(issues, today='2026-11-27')
        self.assertEqual(plans, [])
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]['repo'], 'repo')
        self.assertIn('dirty-tree', refusals[0]['reason'])

    def test_different_repos_are_never_merged_together(self):
        issues = [
            {'identifier': 'DEV-1', 'description': directive('mailcrush', 'DEV-1 q', 'a', '* go')},
            {'identifier': 'DEV-2', 'description': directive('slackagent', 'DEV-2 q', 'b', '* go')},
        ]
        self.assertEqual(ds.merge_plan(issues, today='2026-11-27'), ([], []))
        self.assertEqual(ds.duplicate_repos(issues), [])

    def test_check_uses_store_without_failing_on_multiple_decisions(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        store.import_snapshot('repo', snapshot)
        code, output, _ = self.run_main(['check'], snapshot)
        self.assertEqual(code, 0)
        self.assertIn('store: repo 2 pending decision(s)', output)
        self.assertNotIn('duplicate pending directives', output)
        self.assertNotIn('falling back', output)

    def test_check_empty_store_still_overrides_linear(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        store.import_snapshot('repo', snapshot)
        store.consume('repo', [d['id'] for d in store.list_pending('repo')], 'done')
        code, output, _ = self.run_main(['check'], snapshot)
        self.assertEqual(code, 0)
        self.assertIn('store: repo 0 pending decision(s)', output)

    def test_check_falls_back_per_repo(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues() + self.duplicate_issues('legacy')}
        store.import_snapshot('repo', snapshot)
        code, output, _ = self.run_main(['check'], snapshot)
        self.assertEqual(code, 1)
        self.assertIn('store: repo 2 pending decision(s)', output)
        self.assertIn('store absent for legacy; falling back to Linear scan', output)
        self.assertIn('duplicate pending directives: legacy', output)
        self.assertNotIn('duplicate pending directives: repo', output)

    def test_check_cli_snapshot_works_in_isolated_python(self):
        path = self.directory / 'snapshot.fixture'
        path.write_text(json.dumps({'ok': True, 'issues': self.duplicate_issues()}))
        result = subprocess.run([sys.executable, '-I', ds.__file__, 'check', '--snapshot', str(path)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('store absent for repo; falling back to Linear scan', result.stdout)

    def test_check_fallback_single_section_succeeds(self):
        code, output, _ = self.run_main(['check'], {'ok': True, 'issues': self.duplicate_issues()[:1]})
        self.assertEqual(code, 0)
        self.assertIn('store absent for repo; falling back to Linear scan', output)

    def test_check_corrupt_store_fails_without_linear_fallback(self):
        store.store_path('repo').write_bytes(b'{broken')
        code, output, errors = self.run_main(['check'], {'ok': True, 'issues': self.duplicate_issues()})
        self.assertEqual(code, 2)
        self.assertIn('corrupt directive store', errors)
        self.assertNotIn('falling back', output)

    def test_check_unavailable_snapshot_with_store(self):
        store.upsert('repo', 'DEV-1', 'first', 'q?', 'yes')
        for snapshot in ({'ok': False, 'error': 'offline'},
                         {'ok': False, 'error': 'offline', 'issues': self.duplicate_issues()}):
            code, output, _ = self.run_main(['check'], snapshot)
            self.assertEqual(code, 0)
            self.assertIn('store: repo 1 pending decision(s)', output)

    def test_check_unavailable_snapshot_needing_fallback(self):
        snapshot = {'ok': False, 'error': 'offline'}
        self.assertEqual(self.run_main(['check'], snapshot)[0], 2)
        store.upsert('repo', 'DEV-1', 'first', 'q?', 'yes')
        snapshot['issues'] = self.duplicate_issues('legacy')
        code, output, _ = self.run_main(['check'], snapshot)
        self.assertEqual(code, 2)
        self.assertIn('store absent for legacy; falling back to Linear scan', output)

    def test_check_missing_snapshot_path_still_reports_store(self):
        store.upsert('repo', 'DEV-1', 'first', 'q?', 'yes')
        output = io.StringIO()
        with redirect_stdout(output):
            code = ds.main(['check', '--snapshot', str(self.directory / 'absent.fixture')])
        self.assertEqual(code, 0)
        self.assertIn('store: repo 1 pending decision(s)', output.getvalue())

    def test_merge_apply_imports_and_consumes_only_merged_decisions(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        unrelated = store.upsert('repo', 'DEV-99', 'other', 'another?', 'later')['id']
        writes = []

        def writer(identifier, description):
            self.assertEqual([d['id'] for d in store.list_pending('repo')], [unrelated])
            writes.append((identifier, description))
            return True

        code, output, _ = self.run_main(['merge', '--apply'], snapshot, writer)
        self.assertEqual(code, 0)
        self.assertEqual([ident for ident, _ in writes], ['DEV-1', 'DEV-2'])
        self.assertIn('consumed 1 decision(s)', output)
        carrier = store.decision_id('DEV-1', 'q?')
        self.assertEqual({d['id'] for d in store.list_pending('repo')}, {unrelated, carrier})
        decisions = json.loads(store.store_path('repo').read_text())['decisions']
        folded = [d for d in decisions if d['ticket'] == 'DEV-2']
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]['status'], 'consumed')
        self.assertEqual(folded[0]['consumed_note'], 'merged into DEV-1')
        # The carrier keeps its decision pending: its Linear description is still the live directive.
        carrier_decision = [d for d in decisions if d['ticket'] == 'DEV-1'][0]
        self.assertEqual(carrier_decision['status'], 'pending')

    def test_merge_apply_consumes_preexisting_store_decisions(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        store.import_snapshot('repo', snapshot)
        code, _, _ = self.run_main(['merge', '--apply'], snapshot, lambda *_: True)
        self.assertEqual(code, 0)
        self.assertEqual([d['ticket'] for d in store.list_pending('repo')], ['DEV-1'])

    def test_merge_consumes_each_repo_only_after_its_own_linear_success(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues('alpha') + self.duplicate_issues('beta')}
        store.import_snapshot('alpha', snapshot)
        store.import_snapshot('beta', snapshot)
        writes = iter([True, True, True, False])
        code, _, _ = self.run_main(['merge', '--apply'], snapshot, lambda *_: next(writes))
        self.assertEqual(code, 1)
        self.assertEqual([d['ticket'] for d in store.list_pending('alpha')], ['DEV-1'])
        self.assertEqual(len(store.list_pending('beta')), 2)

    def test_merge_dry_run_does_not_touch_store_or_linear(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        with patch.object(store, 'import_snapshot', side_effect=AssertionError('store write')), \
                patch.object(ds, 'write_update', side_effect=AssertionError('Linear write')):
            self.assertEqual(self.run_main(['merge'], snapshot)[0], 0)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_merge_failed_linear_write_does_not_consume_store(self):
        snapshot = {'ok': True, 'issues': self.duplicate_issues()}
        store.import_snapshot('repo', snapshot)
        before = store.store_path('repo').read_bytes()
        writes = iter([True, False])
        code, _, _ = self.run_main(['merge', '--apply'], snapshot, lambda *_: next(writes))
        self.assertEqual(code, 1)
        self.assertEqual(store.store_path('repo').read_bytes(), before)

    def test_merge_store_failure_does_not_mask_linear_success(self):
        store.store_path('repo').write_bytes(b'{broken')
        writes = []

        def writer(*args):
            writes.append(args)
            return True

        code, _, errors = self.run_main(['merge', '--apply'],
                                        {'ok': True, 'issues': self.duplicate_issues()}, writer)
        self.assertEqual(code, 0)
        self.assertEqual(len(writes), 2)
        self.assertIn('store: repo failed: corrupt directive store', errors)
        self.assertEqual(store.store_path('repo').read_bytes(), b'{broken')


if __name__ == '__main__':
    unittest.main()
