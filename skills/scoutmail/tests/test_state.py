#!/usr/bin/env python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scoutmail_state.py"


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def run_state(self, *args, check=True):
        result = subprocess.run(
            ["python3", str(SCRIPT), "--state", str(self.state), *args],
            check=False,
            text=True,
            capture_output=True,
        )
        if check and result.returncode:
            self.fail("command failed: {}".format(result.stderr))
        return result

    def begin(self, now="2026-09-14T14:00:00Z"):
        return json.loads(self.run_state("begin", "--now", now).stdout)

    def test_bootstrap_and_successful_cursor(self):
        first = self.begin()
        self.assertTrue(first["bootstrap"])
        self.assertEqual(first["query_after"], "2026/09/06")
        self.run_state(
            "record",
            "--run-id",
            first["run_id"],
            "--message-id",
            "101",
            "--decision",
            "ignore",
        )
        self.run_state(
            "finish",
            "--run-id",
            first["run_id"],
            "--expected-message-id",
            "101",
            "--account",
            "me@example.com",
        )
        second = self.begin("2026-09-14T15:00:00Z")
        self.assertFalse(second["bootstrap"])
        self.assertEqual(second["last_successful_at"], "2026-09-14T14:00:00Z")
        filtered = json.loads(self.run_state("filter-new", "--id", "101", "--id", "102").stdout)
        self.assertEqual(filtered, {"seen": ["101"], "unseen": ["102"]})

    def test_scheduled_window_skips_without_creating_state(self):
        result = json.loads(
            self.run_state(
                "begin",
                "--now",
                "2026-09-14T03:00:00Z",
                "--enforce-window",
            ).stdout
        )
        self.assertTrue(result["skip"])
        self.assertEqual(result["reason"], "outside_run_window")
        self.assertFalse(self.state.exists())

    def test_incomplete_scan_cannot_advance(self):
        run = self.begin()
        result = self.run_state(
            "finish",
            "--run-id",
            run["run_id"],
            "--expected-message-id",
            "missing",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        retry = self.begin("2026-09-14T15:00:00Z")
        self.assertTrue(retry["bootstrap"])

    def test_issue_resolution_and_material_reopen(self):
        run = self.begin()
        key = json.loads(self.run_state("key", "--text", "Vet | Forest | appointment 42").stdout)[
            "issue_key"
        ]
        self.run_state(
            "record",
            "--run-id",
            run["run_id"],
            "--message-id",
            "201",
            "--decision",
            "surface",
            "--issue-key",
            key,
            "--summary",
            "Forest's appointment moved",
            "--action",
            "No action needed",
            "--event-at",
            "2026-09-21T09:00:00-04:00",
            "--signature",
            "moved-to-2026-09-21T09:00-04:00",
        )
        self.run_state(
            "finish",
            "--run-id",
            run["run_id"],
            "--expected-message-id",
            "201",
        )
        self.run_state("resolve", "--issue-key", key)
        resolved = json.loads(self.run_state("issues").stdout)["issues"][0]
        self.assertEqual(resolved["status"], "resolved")

        next_run = self.begin("2026-09-14T15:00:00Z")
        self.run_state(
            "record",
            "--run-id",
            next_run["run_id"],
            "--message-id",
            "202",
            "--decision",
            "surface",
            "--issue-key",
            key,
            "--summary",
            "Forest's appointment moved again",
            "--action",
            "Review the new time",
            "--event-at",
            "2026-09-21T10:00:00-04:00",
            "--signature",
            "moved-to-2026-09-21T10:00-04:00",
        )
        self.run_state(
            "finish",
            "--run-id",
            next_run["run_id"],
            "--expected-message-id",
            "202",
        )
        reopened = json.loads(self.run_state("issues").stdout)["issues"][0]
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened["resolved_at"])


if __name__ == "__main__":
    unittest.main()
