#!/usr/bin/env python3
"""linearscope.py: one repo's slice of the Linear snapshot, for its planner and its fingerprint."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "lib"))
import linearscope  # noqa: E402


def issue(id_, labels=(), state_type="unstarted", blocked_by=(), description=""):
    return {"id": id_, "labels": list(labels), "state_type": state_type, "blocked_by": list(blocked_by),
            "description": description}


ISSUES = [
    issue("DEV-1", ["repo/a"]),
    issue("DEV-2", ["repo/b"]),
    issue("DEV-3"),
    issue("DEV-4", ["repo/a"], "completed"),
    issue("DEV-5", ["repo/a"], "canceled", description="## Directive\nship it"),
    issue("DEV-6", ["repo/a", "area/x"], blocked_by=["DEV-7"]),
    issue("DEV-7", ["repo/b"], "completed"),
    issue("DEV-8", ["area/x"]),
]


def ids(issues):
    return [i["id"] for i in issues]


class ScopeTests(unittest.TestCase):
    def test_repo_label_or_no_repo_label(self):
        self.assertEqual(ids(linearscope.scope_issues(ISSUES, "b")), ["DEV-2", "DEV-3", "DEV-7", "DEV-8"])

    def test_fingerprint_scope_keeps_closed_issues_and_blockers_of_any_repo(self):
        self.assertEqual(ids(linearscope.scope_issues(ISSUES, "a")),
                         ["DEV-1", "DEV-3", "DEV-4", "DEV-5", "DEV-6", "DEV-7", "DEV-8"])

    def test_planner_scope_drops_closed_work_but_not_directives_or_blockers(self):
        self.assertEqual(ids(linearscope.scope_issues(ISSUES, "a", open_only=True)),
                         ["DEV-1", "DEV-3", "DEV-5", "DEV-6", "DEV-7", "DEV-8"])

    def test_an_unknown_blocker_id_is_ignored(self):
        self.assertEqual(ids(linearscope.scope_issues([issue("DEV-1", ["repo/a"], blocked_by=["DEV-99"])], "a")),
                         ["DEV-1"])

    def test_scoped_snapshot_keeps_the_shape_and_counts_what_it_omitted(self):
        snap = {"ok": True, "fetched_at": "t", "count": 8, "issues": ISSUES}
        out = linearscope.scoped_snapshot(snap, "b")
        self.assertEqual((out["ok"], out["scoped_to"], out["count"], out["omitted"]), (True, "b", 3, 5))
        self.assertEqual(ids(out["issues"]), ["DEV-2", "DEV-3", "DEV-8"])

    def test_cli_writes_the_planner_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap, out = Path(tmp) / "s.json", Path(tmp) / "o.json"
            snap.write_text(json.dumps({"ok": True, "issues": ISSUES}))
            subprocess.run([sys.executable, str(HERE / "lib/linearscope.py"), "--snapshot", str(snap),
                            "--repo", "a", "--out", str(out)], check=True)
            self.assertNotIn("DEV-4", ids(json.loads(out.read_text())["issues"]))
            bad = subprocess.run([sys.executable, str(HERE / "lib/linearscope.py"), "--snapshot", str(snap) + "x",
                                  "--repo", "a", "--out", str(out)])
            self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
