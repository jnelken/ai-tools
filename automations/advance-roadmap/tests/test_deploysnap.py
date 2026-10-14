#!/usr/bin/env python3

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
spec = importlib.util.spec_from_file_location("deploysnap", LIB / "deploysnap.py")
deploysnap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploysnap)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def env(name, sha, state="success", age=timedelta(hours=1)):
    return {"env": name, "sha": sha, "state": state, "created_at": iso(NOW - age)}


class ClassifyTest(unittest.TestCase):
    def classify(self, production, head="head", pushed=timedelta(hours=2)):
        return deploysnap.classify(head, NOW - pushed, production, NOW)

    def test_no_production_deployments_is_none(self):
        self.assertEqual(self.classify([]), "none")

    def test_head_deployed_successfully_is_green(self):
        self.assertEqual(self.classify([env("Production", "head")]), "green")

    def test_head_deploy_failed(self):
        self.assertEqual(self.classify([env("Production", "head", "failure")]), "failed")
        self.assertEqual(self.classify([env("Production", "head", "error")]), "failed")

    def test_head_still_building_is_pending(self):
        self.assertEqual(self.classify([env("Production", "head", "in_progress")]), "pending")

    def test_recent_push_not_yet_deployed_is_pending(self):
        self.assertEqual(self.classify([env("Production", "old")], pushed=timedelta(minutes=5)), "pending")

    def test_old_push_never_deployed_is_stale(self):
        # jakenelken.com on 2026-10-08: merge commit pushed, production stayed on the previous one.
        self.assertEqual(self.classify([env("Production – a", "old"), env("Production – b", "old")]), "stale")

    def test_worst_live_environment_wins(self):
        self.assertEqual(self.classify([env("Production – a", "head"),
                                        env("Production – b", "head", "failure")]), "failed")

    def test_abandoned_environment_is_ignored(self):
        # mailcrush: a renamed Vercel project left two environments frozen weeks back.
        production = [env("Production", "head"),
                      env("Production – mailcrush", "old", age=timedelta(days=25)),
                      env("Production – mailcruxh", "old", "failure", age=timedelta(days=25))]
        self.assertEqual(self.classify(production), "green")
        self.assertEqual([d["env"] for d in deploysnap.live_environments(production)], ["Production"])


class VerdictDeploysTest(unittest.TestCase):
    def test_deploy_status_flip_is_a_change(self):
        src = (LIB / "verdict.py").read_text()
        # diff() is pure; exec just it rather than the module (which needs a personal code dir).
        ns = {}
        start = src.index("def diff(")
        exec(src[start:src.index("\n\n\n", start)], ns)
        old = {"repos": {}, "issues": {}, "deploys": {"jakenelken.com": "stale", "typey.site": "green"}}
        new = {"repos": {}, "issues": {}, "deploys": {"jakenelken.com": "green", "typey.site": "green"}}
        self.assertEqual(ns["diff"](old, new), ["deploy:jakenelken.com"])


if __name__ == "__main__":
    unittest.main()
