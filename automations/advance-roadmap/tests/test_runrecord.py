#!/usr/bin/env python3
"""runrecord.py: quota pauses keep their provider/reset/cause and never feed the error alert."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import runrecord  # noqa: E402

QUOTA_PLAN = {"action": None, "summary": "Planner claude hit its usage limit",
              "planner_error": {"kind": "quota", "provider": "claude", "tried": ["claude"],
                                "limit_text": "You've hit your session limit · resets 11:50am (America/New_York)",
                                "reset_epoch": 1791906600, "reset_at": "2026-10-13T11:50:00-04:00"}}
INVALID_PLAN = {"action": None, "parse_error": "missing ORCHESTRATOR_RESULT_JSON",
                "planner_error": {"kind": "invalid_output", "provider": "claude", "cause": "missing ORCHESTRATOR_RESULT_JSON"}}


class RunRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, orch=None, wstatus=None, wresult=None):
        self.n += 1
        paths = {}
        for name, value in (("orch", orch), ("status", wstatus), ("result", wresult)):
            p = self.root / f"{name}-{self.n}.json"
            if value is not None:
                p.write_text(json.dumps(value))
            paths[name] = str(p)
        runrecord.main(["--root", str(self.root), "append", "--stamp", f"20261013-1030{self.n:02d}",
                        "--status", "ran", "--orch-provider", "claude", "--orch-result", paths["orch"],
                        "--worker-status", paths["status"], "--worker-result", paths["result"]])
        return runrecord.read_records(str(self.root))[-1]

    def test_planner_quota_is_a_quota_pause_with_its_details(self):
        rec = self.append(QUOTA_PLAN)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertIn("claude", rec["detail"])
        self.assertEqual(rec["quota"]["provider"], "claude")
        self.assertEqual(rec["quota"]["reset_epoch"], 1791906600)
        self.assertIn("11:50am", rec["quota"]["cause"])
        self.assertEqual(rec["quota"]["role"], "orchestrator")

    def test_malformed_plan_is_still_an_error(self):
        rec = self.append(INVALID_PLAN)
        self.assertEqual(rec["outcome"], "error")
        self.assertIsNone(rec["quota"])

    def test_every_worker_over_its_gate_is_a_quota_pause(self):
        status = {"used": None, "exit": 3, "fatal": None, "missing": [], "limited": [
            {"provider": "cursor", "state": "over", "reason": "auto pool 96% >= 95%", "reset_epoch": 1792000000},
            {"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000}]}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertEqual((rec["quota"]["provider"], rec["quota"]["reset_epoch"]), ("codex", 1791910000))
        self.assertEqual(len(rec["quota"]["providers"]), 2)

    def test_a_missing_worker_binary_is_still_an_error(self):
        status = {"used": None, "exit": 3, "fatal": None, "missing": ["claude"],
                  "limited": [{"provider": "cursor", "state": "over"}]}
        self.assertEqual(self.append({"action": "dispatch_worker"}, status)["outcome"], "error")

    def test_worker_limit_hit_is_a_quota_pause(self):
        rec = self.append({"action": "dispatch_worker"}, {"used": "codex", "exit": 0},
                          {"outcome": "limit_hit", "provider": "codex", "limit_text": "usage limit"})
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertEqual(rec["quota"]["cause"], "usage limit")

    def test_quota_pauses_never_alert_and_never_count_toward_the_error_streak(self):
        for _ in range(4):
            self.append(QUOTA_PLAN)
        self.assertEqual(runrecord.alert_text(runrecord.read_records(str(self.root))), "")
        self.append(INVALID_PLAN)
        self.assertEqual(runrecord.error_streak(runrecord.read_records(str(self.root))), 1)
        self.append(QUOTA_PLAN)
        records = runrecord.read_records(str(self.root))
        self.assertEqual(runrecord.error_streak(records), 1)
        self.assertEqual(runrecord.alert_text(records), "")
        self.append(INVALID_PLAN)
        self.assertIn("failed 2 runs in a row", runrecord.alert_text(runrecord.read_records(str(self.root))))


if __name__ == "__main__":
    unittest.main()
