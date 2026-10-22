import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

PATH = (Path(__file__).resolve().parents[3] / 'bin' / 'linear-pr-archive'
        if len(Path(__file__).resolve().parents) > 3 else Path('/private/tmp/linear-pr-archive'))
if not PATH.exists():
    PATH = Path('/private/tmp/linear-pr-archive')
loader = importlib.machinery.SourceFileLoader('linear_pr_archive', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
a = importlib.util.module_from_spec(spec)
loader.exec_module(a)


def issue():
    connection = lambda: {'nodes': [], 'pageInfo': {'hasNextPage': False}}
    return {'id': 'uuid', 'identifier': 'DEV-1', 'title': 'Requirement', 'url': 'https://linear.app/jnelken/issue/DEV-1',
            'state': {'type': 'completed'}, 'description': 'Original exact requirement', 'updatedAt': 'original',
            'parent': None, 'children': connection(), 'documents': {'nodes': []},
            'attachments': connection(), 'comments': connection()}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.issue = issue()
        self.record = {'issue': self.issue, 'targets': [{'repo': 'jnelken/example', 'number': 1}], 'blockers': [], 'backfill_evidence': 'reviewed create-pr invocation'}
        self.client = Mock()
        self.client.issue.return_value = self.issue
        self.client.pr.return_value = {'merged': True}
        self.client.comments.return_value = []
        self.client.save_comment.return_value = 'https://github.com/jnelken/example/pull/1#issuecomment-1'
        self.sleep = patch.object(a.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def test_delete_only_after_every_target_is_saved(self):
        self.record['targets'].append({'repo': 'jnelken/example', 'number': 2})
        self.client.save_comment.side_effect = ['comment1', RuntimeError('verification failed')]
        with self.assertRaises(RuntimeError):
            a.apply(self.client, self.record, delete=True)
        self.client.delete.assert_not_called()

    def test_source_edit_after_post_retains_ticket(self):
        changed = copy.deepcopy(self.issue)
        changed['updatedAt'] = 'new'
        self.client.issue.side_effect = [self.issue, changed]
        with self.assertRaises(RuntimeError):
            a.apply(self.client, self.record, delete=True)
        self.client.delete.assert_not_called()

    def test_open_and_incomplete_sources_are_blocked(self):
        self.issue['state']['type'] = 'started'
        self.issue['comments']['pageInfo']['hasNextPage'] = True
        self.assertIn('not completed', a.blockers(self.issue))
        self.assertIn('comments export incomplete', a.blockers(self.issue))
        self.record['blockers'] = a.blockers(self.issue)
        a.apply(self.client, self.record, delete=True)
        self.client.save_comment.assert_not_called()
        self.client.delete.assert_not_called()

    def test_document_and_asset_sources_require_separate_export(self):
        self.issue['documents']['nodes'].append({'id': 'doc'})
        self.issue['description'] = 'https://uploads.linear.app/file'
        self.assertEqual(len(a.blockers(self.issue)), 2)

    def test_retry_reuses_existing_comment(self):
        body = a.archive_bodies(self.issue)[0]
        self.client.comments.return_value = [{'id': 42, 'body': body}]
        a.apply(self.client, self.record, delete=True)
        self.assertEqual(self.client.save_comment.call_args.args[3], 42)
        self.client.delete.assert_called_once_with(self.issue)

    def test_unfinished_parent_allows_copy_but_prevents_deletion(self):
        self.record['retention'] = ['unfinished parent']
        a.apply(self.client, self.record, delete=True)
        self.client.save_comment.assert_called_once()
        self.client.delete.assert_not_called()

    def test_original_planning_ticket_is_retained(self):
        self.record.pop('backfill_evidence')
        a.apply(self.client, self.record, delete=True)
        self.client.delete.assert_not_called()

    def test_dry_archive_does_not_delete(self):
        a.apply(self.client, self.record)
        self.client.delete.assert_not_called()

    def test_entire_requirements_and_discussion_survive_chunking(self):
        self.issue['description'] = 'R' * 60000
        self.issue['comments']['nodes'] = [{'id': 'c', 'body': 'Decision: keep it',
                                          'createdAt': '2026-10-22', 'user': {'name': 'Jake'}}]
        bodies = a.archive_bodies(self.issue)
        self.assertGreater(len(bodies), 1)
        joined = ''.join(b.split('Original requirements, decisions and discussion, part ', 1)[1].split('.\n\n', 1)[1] for b in bodies)
        self.assertIn(self.issue['description'], joined)
        self.assertIn('Decision: keep it', joined)
        self.assertTrue(all(len(b) < 60000 for b in bodies))

    def test_failed_readback_is_an_error(self):
        with patch.object(a, 'command', side_effect=[{'id': 1}, {'body': 'different'}]):
            with self.assertRaises(RuntimeError):
                a.Client().save_comment('jnelken/example', 1, 'expected')


if __name__ == '__main__':
    unittest.main()
