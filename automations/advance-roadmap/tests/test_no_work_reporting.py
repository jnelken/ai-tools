import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
# fcntl is used only when appending a run, and is not available on Windows.
try:
    import fcntl  # noqa: F401
except ImportError:
    sys.modules["fcntl"] = types.ModuleType("fcntl")
spec = importlib.util.spec_from_file_location("runrecord", LIB / "runrecord.py")
runrecord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runrecord)


class NoWorkReporting(unittest.TestCase):
    def test_record_preserves_planner_reasons_over_worker_summary(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            def save(name, obj):
                path = root / name
                path.write_text(json.dumps(obj), encoding="utf-8")
                return str(path)
            orch = save("orch.json", {"action": "blocked_no_item", "outcome_token": "blocked-no-item",
                "summary": "No actionable candidate", "considered": ["DEV-276 excluded as code-health"]})
            status = save("status.json", {"used": "cursor", "exit": 0})
            worker = save("worker.json", {"outcome": "bookkeeping", "slack_summary": "Nothing left"})
            snapshot = save("snapshot.json", {"ok": True, "truncated": False, "issues": [
                {"labels": ["repo/demo"], "state_type": "unstarted"}]})
            args = ["--root", str(root), "append", "--stamp", "20261118-120000", "--status", "ran",
                    "--orch-result", orch, "--worker-status", status, "--worker-result", worker,
                    "--linear-snapshot", snapshot, "--target-repo", "demo"]
            with patch.object(runrecord.fcntl, "lockf", create=True), patch.object(runrecord.fcntl, "LOCK_EX", 1, create=True):
                runrecord.main(args)
            row = json.loads((root / "runs.jsonl").read_text())
            self.assertEqual(row["outcome"], "blocked-no-item")
            self.assertEqual(row["backlog"]["status"], "unfinished")
            self.assertIn("DEV-276", row["slack_summary"])
            self.assertNotIn("Nothing left", row["slack_summary"])

    def test_unfinished_tickets_and_planner_reasons(self):
        orch = {"repo": "tidbyt-api", "summary": "No actionable selected ticket.",
                "considered": ["DEV-276 excluded: code-health without do-next",
                               "DEV-227 depends on unshipped DEV-223 controls"],
                "blockers": [{"linear_id": "DEV-227", "detail": "Wait for DEV-223 or expand scope"}]}
        snap = {"ok": True, "truncated": False, "issues": [
            {"labels": ["repo/tidbyt-api"], "state_type": "unstarted"},
            {"labels": ["repo/tidbyt-api"], "state_type": "started"},
            {"labels": ["repo/other"], "state_type": "unstarted"},
            {"labels": ["repo/tidbyt-api"], "state_type": "completed"}]}
        backlog, summary = runrecord.no_work_report(orch, snap, "tidbyt-api")
        self.assertEqual(backlog, {"status": "unfinished", "repo": "tidbyt-api", "unfinished_count": 2})
        for text in ("2 unfinished", "DEV-276", "DEV-227", "DEV-223", "•"):
            self.assertIn(text, summary)

    def test_empty_and_unknown_are_distinct(self):
        complete = {"ok": True, "truncated": False, "issues": []}
        self.assertEqual(runrecord.no_work_report({}, complete, "demo")[0]["status"], "empty")
        for snapshot, repo in ((None, "demo"), ({**complete, "truncated": True}, "demo"),
                               ({**complete, "ok": False}, "demo"), (complete, ""),
                               ({**complete, "issues": [{}]}, "demo")):
            backlog, summary = runrecord.no_work_report({}, snapshot, repo)
            self.assertEqual(backlog["status"], "unknown")
            self.assertIn("could not be verified", summary)

    def test_second_thread_is_not_described_as_empty_selection(self):
        backlog, summary = runrecord.no_work_report({"summary": "no second thread"},
            {"ok": True, "truncated": False, "issues": [
                {"labels": ["repo/demo"], "state_type": "unstarted"}]}, "demo")
        self.assertEqual(backlog["status"], "unfinished")
        self.assertIn("no second thread", summary)


if __name__ == "__main__":
    unittest.main()
