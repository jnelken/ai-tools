#!/usr/bin/env python3
"""verdict.py: planner failures are never saved as no-work verdicts, and old ones are discarded."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "lib" / "verdict.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oct13"
STAMP = "20261013-103004"


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "root"
        (self.root / "runs").mkdir(parents=True)
        (self.dir / "code").mkdir()
        self.verdict = self.root / "batch-verdicts" / "apt-sqft.json"
        self.verdict.parent.mkdir()
        self.fp = self.dir / "fingerprint.json"
        self.fp.write_text(json.dumps({"repos": {}, "issues": {}, "linear_ok": True}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_verdict(self, *args):
        env = dict(os.environ, PERSONAL_CODE_DIR=str(self.dir / "code"), ADVANCE_ROADMAP_ROOT=str(self.root))
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def check(self):
        return self.run_verdict("check", "--file", str(self.verdict), "--fingerprint", str(self.fp),
                                "--changes", str(self.dir / "changes.json"), "--root", str(self.root))

    def write_verdict(self, **over):
        v = {"stamp": "20261014-103004", "decided_at": datetime.now().isoformat(timespec="seconds"),
             "outcome_token": "blocked-no-item", "blockers": [], "considered": [],
             "summary": "No work qualified for apt-sqft.", "fingerprint": json.loads(self.fp.read_text())}
        v.update(over)
        self.verdict.write_text(json.dumps(v))

    def save(self, orch, worker):
        o, w = self.dir / "orch.json", self.dir / "worker.json"
        o.write_text(json.dumps(orch))
        w.write_text(json.dumps(worker))
        return self.run_verdict("save", "--file", str(self.verdict), "--fingerprint", str(self.fp),
                                "--orch-result", str(o), "--worker-result", str(w), "--stamp", "20261014-103004")

    # ── save ──────────────────────────────────────────────────────────────────

    def test_valid_no_work_verdict_is_saved(self):
        p = self.save({"action": "blocked_no_item", "outcome_token": "blocked-no-item", "summary": "nothing"},
                      {"outcome": "bookkeeping"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], "nothing")

    def test_oct13_synthesized_plan_is_not_saved(self):
        orch = json.loads((FIXTURES / "orchestrator-result.json").read_text())
        self.write_verdict()
        p = self.save(orch, {"outcome": "bookkeeping"})
        self.assertIn("not carried forward", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_planner_errors_are_never_saved(self):
        cases = [
            ({"action": "blocked_no_item", "summary": "You've hit your session limit · resets 11:50am"}, {"outcome": "bookkeeping"}),
            ({"action": None, "planner_error": {"kind": "quota", "provider": "claude"}}, {"outcome": "bookkeeping"}),
            ({"action": "blocked_no_item", "parse_error": "missing ORCHESTRATOR_RESULT_JSON"}, {"outcome": "bookkeeping"}),
            ({"action": "blocked_no_item", "summary": "nothing"},
             {"outcome": "bookkeeping", "limit_text": "Orchestrator: You've hit your session limit"}),
        ]
        for orch, worker in cases:
            with self.subTest(orch=orch, worker=worker):
                self.write_verdict()
                self.save(orch, worker)
                self.assertFalse(self.verdict.exists())

    # ── check ─────────────────────────────────────────────────────────────────

    def test_unchanged_valid_verdict_still_skips(self):
        self.write_verdict()
        p = self.check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.verdict.exists())

    def test_oct13_contaminated_verdict_is_discarded_and_stays_gone(self):
        v = json.loads((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        v.update(decided_at=datetime.now().isoformat(timespec="seconds"), fingerprint=json.loads(self.fp.read_text()))
        self.verdict.write_text(json.dumps(v))
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("discarded", p.stdout)
        self.assertFalse(self.verdict.exists())
        self.assertFalse((self.dir / "changes.json").exists(), "the planner must not be handed it to carry forward")
        p = self.check()  # idempotent: nothing left to discard
        self.assertEqual(p.returncode, 1)
        self.assertIn("no carried verdict", p.stdout)

    def test_clean_summary_is_discarded_on_its_runs_orchestrator_evidence(self):
        self.write_verdict(stamp=STAMP)
        (self.root / "runs" / STAMP).mkdir()
        shutil.copy(FIXTURES / "orchestrator-result.json", self.root / "runs" / STAMP / "orchestrator-result.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("planner output unusable", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_clean_summary_is_discarded_on_its_run_record(self):
        self.write_verdict(stamp=STAMP)
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        with open(self.root / "runs.jsonl", "w") as f:
            f.write(json.dumps({"stamp": "20261013-103003", "outcome": "shipped"}) + "\n")
            f.write(json.dumps(row) + "\n")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("ended error", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_a_good_run_record_keeps_the_verdict(self):
        self.write_verdict(stamp=STAMP)
        (self.root / "runs.jsonl").write_text(json.dumps({"stamp": STAMP, "outcome": "blocked-no-item"}) + "\n")
        self.assertEqual(self.check().returncode, 0)
        self.assertTrue(self.verdict.exists())


if __name__ == "__main__":
    unittest.main()
