"""Offline tests for jlin.py's directory↔label mapping and repo inference.

Fixtures are real DEV issues and real label descriptions (2026-09-27).
Run: python3 -m unittest discover -s ~/.ai-tools/skills/jnelken-linear
"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location("jlin", os.path.join(os.path.dirname(__file__), "jlin.py"))
jlin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jlin)

LABELS = [
    {"id": "1", "name": "mailcrush", "description": "jnelken/mailcrush — Next.js/React Gmail triage app."},
    {"id": "2", "name": "mailcrush-2023", "description": 'jnelken/mailcrush-2023 — ARCHIVED. Directory is "[archived]mailcrush-2023".'},
    {"id": "3", "name": "praxis", "description": 'Local-only (no origin). Directory is "praxis (problem -> solution)".'},
    {"id": "4", "name": "openclaw-vps", "description": "Directory is openclaw-vps but origin is jnelken/vena-vps."},
    {"id": "5", "name": "typey.site", "description": "jnelken/typey.site"},
    {"id": "6", "name": "asciimation", "description": 'Local-only (no origin). Directory is "asciimation (unicode spinners)".'},
    {"id": "7", "name": "slackagent", "description": "Local-only (no origin)."},
    {"id": "8", "name": "ai-tools", "description": "jnelken/ai-tools"},
]


def names(text):
    return sorted(x["name"] for x in jlin.candidates(text, LABELS))


class DirMapping(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(jlin.label_for_dir("mailcrush", LABELS)["name"], "mailcrush")

    def test_archived_prefix(self):
        self.assertEqual(jlin.label_for_dir("[archived]mailcrush-2023", LABELS)["name"], "mailcrush-2023")

    def test_parenthetical_suffix(self):
        self.assertEqual(jlin.label_for_dir("praxis (problem -> solution)", LABELS)["name"], "praxis")
        self.assertEqual(jlin.label_for_dir("asciimation (unicode spinners)", LABELS)["name"], "asciimation")

    def test_origin_mismatch_uses_directory_name(self):
        self.assertEqual(jlin.label_for_dir("openclaw-vps", LABELS)["name"], "openclaw-vps")

    def test_unlabelled_directory(self):
        self.assertIsNone(jlin.label_for_dir("tidbyt-api", LABELS))


class Inference(unittest.TestCase):
    def test_names_repo_in_prose(self):  # DEV-91
        self.assertEqual(names("Complete redesign of Mailcrush frontend from the ground up."), ["mailcrush"])

    def test_longer_name_wins(self):
        self.assertEqual(names("port the mailcrush-2023 rules"), ["mailcrush-2023"])

    def test_dotted_name(self):
        self.assertEqual(names("typey.site: fix STT"), ["typey.site"])
        self.assertEqual(names("typey.sites are fun"), [])

    def test_no_substring_matches(self):
        self.assertEqual(names("mailcrushing is not a word"), [])
        self.assertEqual(names("use the ai-toolshed"), [])

    def test_no_repo_named(self):  # DEV-90, DEV-72
        self.assertEqual(names("Make sender info collapsible on inbox"), [])
        self.assertEqual(names("Scope a Git mirror/cache to accelerate repository fetches"), [])

    def test_two_repos_is_ambiguous(self):
        self.assertEqual(names("move slackagent routing into mailcrush"), ["mailcrush", "slackagent"])

    def test_case_insensitive(self):  # DEV-74
        self.assertEqual(names("Slackagent Phase 2 — hosted split"), ["slackagent"])


if __name__ == "__main__":
    unittest.main()
