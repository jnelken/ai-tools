import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import directive_store as store
from tests.test_directive_slot import DIRTY, directive

SCRIPT = Path(store.__file__).resolve()


class DirectiveStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        env = patch.dict(os.environ, DIRECTIVE_STORE_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, '-I', str(SCRIPT), *args],
                                capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def upsert_cli(self, answer='yes'):
        return self.cli('upsert', '--repo', 'repo', '--ticket', 'DEV-1', '--item', 'Title',
                        '--question', 'q?', '--answer', answer)

    def test_create_update_unchanged_and_reopen(self):
        created = self.upsert_cli()
        ident = hashlib.sha256(b'DEV-1\nq?').hexdigest()[:16]
        self.assertEqual(created, {'id': ident, 'status': 'created'})
        path = store.store_path('repo')
        original = path.read_bytes()
        self.assertEqual(self.upsert_cli(), {'id': ident, 'status': 'unchanged'})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.upsert_cli('no'), {'id': ident, 'status': 'updated'})
        decision = self.cli('list', '--repo', 'repo')[0]
        self.assertEqual(decision['answer'], 'no')
        self.assertIsNotNone(decision['recorded_at'])
        self.assertIsNone(decision['consumed_at'])
        self.assertIsNone(decision['consumed_note'])
        self.assertEqual(self.cli('consume', '--repo', 'repo', '--ids', ident,
                                  '--note', 'done'), 1)
        consumed = path.read_bytes()
        self.assertEqual(self.upsert_cli('no')['status'], 'unchanged')
        self.assertEqual(path.read_bytes(), consumed)
        self.assertEqual(self.upsert_cli('revised')['status'], 'updated')
        decision = store.list_pending('repo')[0]
        self.assertEqual(decision['status'], 'pending')
        self.assertIsNone(decision['consumed_at'])
        self.assertIsNone(decision['consumed_note'])

    def test_list_consume_and_pending_repos(self):
        self.assertEqual(self.cli('list', '--repo', 'missing'), [])
        self.assertFalse(store.has_store('missing'))
        first = store.upsert('repo', 'DEV-1', 'a', 'q1', 'yes')['id']
        second = store.upsert('repo', 'DEV-2', 'b', 'q2', 'no')['id']
        store.upsert('other', 'DEV-3', 'c', 'q3', 'later')
        self.assertEqual(self.cli('pending-repos'), {'other': 1, 'repo': 2})
        self.assertEqual(len(self.cli('list', '--repo', 'repo')), 2)
        self.assertEqual(self.cli('consume', '--repo', 'repo', '--ids', first, second,
                                  'missing-id', '--note', 'acted on'), 2)
        self.assertEqual(self.cli('consume', '--repo', 'repo', '--ids', first,
                                  '--note', 'again'), 0)
        self.assertEqual(self.cli('list', '--repo', 'repo'), [])
        self.assertEqual(self.cli('pending-repos'), {'other': 1})
        for decision in json.loads(store.store_path('repo').read_text())['decisions']:
            self.assertEqual(decision['status'], 'consumed')
            self.assertTrue(decision['consumed_at'])
            self.assertEqual(decision['consumed_note'], 'acted on')

    def test_concurrent_upserts_preserve_every_decision(self):
        code = (
            'import sys; sys.path.insert(0, sys.argv[1]); import directive_store as s\n'
            'for i in range(50):\n'
            ' s.upsert("repo", "DEV-" + sys.argv[2] + str(i), "item", "q?", "yes")\n'
            ' s.upsert("repo", "DEV-shared", "shared", "same?", "yes")\n'
        )
        processes = [subprocess.Popen([sys.executable, '-I', '-c', code, str(SCRIPT.parent), prefix],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for prefix in ('1', '2')]
        try:
            for process in processes:
                _, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        decisions = store.list_pending('repo')
        expected = {f'DEV-{prefix}{i}' for prefix in ('1', '2') for i in range(50)} | {'DEV-shared'}
        self.assertEqual({d['ticket'] for d in decisions}, expected)
        self.assertEqual(len(decisions), 101)

    def test_corrupt_store_is_never_overwritten(self):
        path = store.store_path('repo')
        for raw in (b'{broken', b'[]', b'{"repo": "repo", "decisions": "wrong"}', b'\xff'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                for args in (
                    ('upsert', '--repo', 'repo', '--ticket', 'DEV-1', '--item', 'x',
                     '--question', 'q?', '--answer', 'yes'),
                    ('consume', '--repo', 'repo', '--ids', 'x', '--note', 'done'),
                    ('list', '--repo', 'repo'),
                    ('pending-repos',),
                ):
                    result = self.cli(*args, success=False)
                    self.assertIn('corrupt directive store', result.stderr)
                    self.assertEqual(path.read_bytes(), raw)

    def test_unknown_fields_survive_update_and_consume(self):
        ident = store.upsert('repo', 'DEV-1', 'item', 'q?', 'yes')['id']
        path = store.store_path('repo')
        data = json.loads(path.read_text())
        data['future'] = {'nested': [1, 2]}
        data['decisions'][0]['extra'] = {'nested': 'keep'}
        path.write_text(json.dumps(data))
        store.upsert('repo', 'DEV-1', 'item', 'q?', 'no')
        store.consume('repo', [ident], 'done')
        updated = json.loads(path.read_text())
        self.assertEqual(updated['future'], data['future'])
        self.assertEqual(updated['decisions'][0]['extra'], data['decisions'][0]['extra'])

    def test_import_snapshot_is_idempotent(self):
        first = directive('repo', 'first', 'yes\n  and keep going', '* go')
        first = first.replace('  **Recorded in:** not yet',
                              '  **Recorded in:** not yet\n\n'
                              '* **Item:** inline **Question:** another? **Answer:** later')
        # A consumed section is ignored, and a dirty-tree section is explicitly skipped.
        snapshot = {'ok': True, 'issues': [
            {'identifier': 'DEV-1', 'description': first},
            {'id': 'DEV-2', 'description': directive('repo', 'second', 'no', '* go')},
            {'identifier': 'DEV-3', 'description': DIRTY},
            {'identifier': 'DEV-4', 'description': directive('other', 'other', 'a', '* go')},
            {'identifier': 'DEV-5', 'description': directive('repo', 'old', 'a', '* go').replace(
                '## Directive\n', '## Directive (consumed yesterday)\n')},
        ]}
        fixture = self.directory / 'snapshot.fixture'
        fixture.write_text(json.dumps(snapshot))
        summary = self.cli('import', '--repo', 'repo', '--snapshot', str(fixture))
        self.assertEqual({k: summary[k] for k in ('created', 'updated', 'unchanged')},
                         {'created': 3, 'updated': 0, 'unchanged': 0})
        self.assertEqual(len(summary['skipped']), 1)
        self.assertIn('DEV-3: dirty-tree', summary['skipped'][0])
        decisions = store.list_pending('repo')
        multiline = next(d for d in decisions if d['item'] == 'first')
        self.assertIn('and keep going', multiline['answer'])
        self.assertNotIn('Recorded in', multiline['answer'])
        self.assertEqual(multiline['ticket'], 'DEV-1')
        self.assertEqual(multiline['answer'], 'yes\nand keep going')
        again = self.cli('import', '--repo', 'repo', '--snapshot', str(fixture))
        self.assertEqual((again['created'], again['updated'], again['unchanged']), (0, 0, 3))
        self.assertEqual(len(store.list_pending('repo')), 3)
        snapshot['issues'][1]['description'] = directive('repo', 'second', 'changed', '* go')
        summary = store.import_snapshot('repo', snapshot)
        self.assertEqual((summary['created'], summary['updated'], summary['unchanged']), (0, 1, 2))

    def test_import_reports_malformed_bullets_and_unavailable_snapshot(self):
        text = directive('repo', 'item', 'yes', '* go').replace('**Question:**', '**Missing:**')
        snapshot = {'ok': True, 'issues': [{'id': 'DEV-1', 'description': text}]}
        result = store.import_snapshot('repo', snapshot)
        self.assertEqual(result['created'], 0)
        self.assertIn('incomplete', result['skipped'][0])
        with self.assertRaisesRegex(store.StoreError, 'snapshot unavailable'):
            store.import_snapshot('repo', {'ok': False, 'error': 'offline'})

    def test_empty_import_still_rejects_corrupt_store(self):
        path = store.store_path('repo')
        path.write_bytes(b'{broken')
        fixture = self.directory / 'empty.fixture'
        fixture.write_text(json.dumps({'ok': True, 'issues': []}))
        result = self.cli('import', '--repo', 'repo', '--snapshot', str(fixture), success=False)
        self.assertIn('corrupt directive store', result.stderr)
        self.assertEqual(path.read_bytes(), b'{broken')

    def test_import_sections_are_bounded(self):
        text = directive('repo', 'item', 'yes', '* go') + DIRTY
        snapshot = {'ok': True, 'issues': [{'id': 'DEV-1', 'description': text}]}
        result = store.import_snapshot('repo', snapshot)
        self.assertEqual(result['created'], 1)
        self.assertEqual(len(result['skipped']), 1)

    def test_repo_names_cannot_escape_store(self):
        for repo in ('', '.', '..', '../outside', 'a/b', 'a\\b'):
            with self.subTest(repo=repo):
                result = self.cli('list', '--repo', repo, success=False)
                self.assertIn('invalid repo name', result.stderr)

    def test_render_status(self):
        first = store.upsert('repo', 'DEV-1', 'first', 'q?', 'yes')['id']
        store.upsert('repo', 'DEV-2', 'second', 'q2?', 'no')
        store.consume('repo', [first], 'done')
        rendered = store.render_status('repo')
        self.assertIn('1 pending decision(s); 1 consumed', rendered)
        self.assertIn('DEV-2 — second', rendered)
        self.assertIn('Question: q2?', rendered)
        self.assertIn('Answer: no', rendered)
        self.assertNotIn('DEV-1', rendered)
        self.assertIn('0 pending', store.render_status('missing'))
        self.assertFalse(store.has_store('missing'))


if __name__ == '__main__':
    unittest.main()
