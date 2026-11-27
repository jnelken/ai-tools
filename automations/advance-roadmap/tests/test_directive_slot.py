import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import directive_slot as ds


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


if __name__ == '__main__':
    unittest.main()
