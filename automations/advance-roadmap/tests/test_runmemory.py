#!/usr/bin/env python3

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "runmemory.py"


class RunMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Path(self.tmp.name)
        self.runs = self.mem / "advance-roadmap-runs"
        self.env = {**os.environ, "ADVANCE_ROADMAP_RUNS_DIR": str(self.runs)}

    def tearDown(self):
        self.tmp.cleanup()

    def rm(self, *args, stdin=None, check=True):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env, input=stdin,
                              capture_output=True, text=True, check=check)

    def append(self, repo, stamp, outcome="shipped", detail=None, check=True):
        args = ["append-ledger", "--repo", repo, "--stamp", stamp, "--outcome", outcome, "--date", "2026-10-04"]
        if detail:
            args += ["--detail", detail]
        return self.rm(*args, check=check)

    def test_rows_land_in_per_repo_files(self):
        self.append("mailcrush", "20261004-130533", detail="DEV-31, ff merge `6fcff93`")
        self.append("typey.site", "20261004-200754", "shipped-deploy-failed")
        self.assertIn("20261004-130533", (self.runs / "mailcrush.md").read_text())
        self.assertNotIn("20261004-130533", (self.runs / "typey.site.md").read_text())
        self.assertIn("DEV-31, ff merge `6fcff93`", (self.runs / "mailcrush.md").read_text())

    def test_no_repo_goes_to_none_file(self):
        self.append("none", "20261003-134501", "blocked-no-item", detail="bookkeeping")
        self.assertTrue((self.runs / "_none.md").exists())

    def test_same_stamp_replaces_not_duplicates(self):
        self.append("mailcrush", "20261004-130533", "shipped")
        self.append("mailcrush", "20261004-130533", "shipped-deploy-failed")
        text = (self.runs / "mailcrush.md").read_text()
        self.assertEqual(text.count("20261004-130533"), 1)
        self.assertIn("shipped-deploy-failed", text)

    def test_trims_to_ten_per_repo_and_keeps_newest(self):
        for i in range(12):
            self.append("mailcrush", f"20261004-1{i:05d}")
        text = (self.runs / "mailcrush.md").read_text()
        self.assertEqual(text.count("| `2026"), 10)
        self.assertNotIn("20261004-100000", text)
        self.assertIn("20261004-100011", text)

    def test_notes_replace_without_touching_ledger(self):
        self.append("mailcrush", "20261004-130533")
        self.rm("write-notes", "--repo", "mailcrush", stdin="first notes")
        self.rm("write-notes", "--repo", "mailcrush", stdin="second notes")
        text = (self.runs / "mailcrush.md").read_text()
        self.assertIn("second notes", text)
        self.assertNotIn("first notes", text)
        self.assertIn("20261004-130533", text)
        self.append("mailcrush", "20261004-150000")   # appending keeps the notes
        self.assertIn("second notes", (self.runs / "mailcrush.md").read_text())

    def test_has_stamp_searches_every_repo(self):
        self.append("mailcrush", "20261004-130533")
        self.append("ableton-catalog", "20261004-160312")
        found = self.rm("has-stamp", "--stamp", "20261004-160312")
        self.assertEqual(found.stdout.strip(), "ableton-catalog")
        self.assertEqual(self.rm("has-stamp", "--stamp", "20261004-999999", check=False).returncode, 1)

    def test_ledger_json_is_sorted_by_stamp(self):
        self.append("b", "20261004-200000")
        self.append("a", "20261004-100000")
        out = self.rm("ledger").stdout
        self.assertLess(out.index("20261004-100000"), out.index("20261004-200000"))

    def test_bad_stamp_rejected_and_repo_name_sanitized(self):
        self.assertNotEqual(self.append("mailcrush", "not-a-stamp", check=False).returncode, 0)
        self.append("../evil", "20261004-130533")
        self.assertEqual([p.name for p in self.runs.glob("*.md")], ["evil.md"])

    def test_concurrent_appends_to_different_repos_lose_nothing(self):
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "append-ledger", "--repo", f"repo{i % 4}",
                                   "--stamp", f"20261004-1{i:05d}", "--outcome", "shipped"],
                                  env=self.env, stdout=subprocess.DEVNULL) for i in range(24)]
        self.assertTrue(all(p.wait() == 0 for p in procs))
        stamps = {r.split("`")[1] for p in self.runs.glob("*.md") for r in p.read_text().splitlines() if r.startswith("| `")}
        self.assertEqual(len(stamps), 24)

    def test_concurrent_appends_to_same_repo_lose_nothing(self):
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "append-ledger", "--repo", "mailcrush",
                                   "--stamp", f"20261004-1{i:05d}", "--outcome", "shipped"],
                                  env=self.env, stdout=subprocess.DEVNULL) for i in range(10)]
        self.assertTrue(all(p.wait() == 0 for p in procs))
        self.assertEqual((self.runs / "mailcrush.md").read_text().count("| `2026"), 10)

    def test_migrate_splits_legacy_ledger_and_keeps_prose(self):
        legacy = self.mem / "project_advance-roadmap-runs.md"
        legacy.write_text(
            "---\nname: x\n---\n\nIntro.\n\n## Run ledger\n\nOld how-to prose.\n\n"
            "| Stamp | Date | Repo | Outcome |\n|---|---|---|---|\n"
            "| `20261004-130533` | 2026-10-04 | mailcrush (DEV-31, ff merge `6fcff93`) | shipped |\n"
            "| `20261003-134501` | 2026-10-03 | none (bookkeeping; nothing pushed) | blocked-no-item |\n\n"
            "Stamps absent on purpose, and they must stay absent:\n\n- quota skip `20260913-164505`\n\n"
            "## Run 2026-10-04\n\nBody of a run.\n")
        self.rm("migrate")
        self.assertIn("DEV-31, ff merge `6fcff93`", (self.runs / "mailcrush.md").read_text())
        self.assertIn("bookkeeping; nothing pushed", (self.runs / "_none.md").read_text())
        text = legacy.read_text()
        self.assertNotIn("| `20261004-130533`", text)
        self.assertIn("Stamps absent on purpose", text)
        self.assertIn("Body of a run.", text)
        self.assertNotIn("Old how-to prose", text)
        self.assertTrue((self.mem / "project_advance-roadmap-runs.md.pre-split").exists())
        self.rm("migrate")  # idempotent
        self.assertEqual((self.runs / "mailcrush.md").read_text().count("20261004-130533"), 1)
        self.assertEqual(self.rm("has-stamp", "--stamp", "20261003-134501").stdout.strip(), "none")


if __name__ == "__main__":
    unittest.main()
