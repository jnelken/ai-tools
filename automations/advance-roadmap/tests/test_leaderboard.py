#!/usr/bin/env python3

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "leaderboard.py"


def load(root):
    os.environ["ADVANCE_ROADMAP_ROOT"] = str(root)
    spec = importlib.util.spec_from_file_location("leaderboard_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api(period, rank, total=100, day="2026-10-07"):
    return {"handle": "jnelken", "lastPublishedAt": "2026-10-07T03:30:27Z", "period": period,
            "range": {"from": day, "to": day} if period == "day" else None,
            "tokens": "1", "rank": rank, "total": total, "approximate": True}


class LeaderboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lb = load(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, *args):
        return self.lb.main(["snapshot", *args])

    def ranks(self, all_time=400, rolling=500, today=300, day="2026-10-07"):
        table = {"all": api("all", all_time), "7d": api("7d", rolling), "day": api("day", today, day=day)}
        return lambda period, tok: table[period]

    def test_best_7d_takes_each_days_latest_rank(self):
        rows = [
            {"day": "2026-10-05", "today": {"rank": 50, "total": 90}},   # early-day rank…
            {"day": "2026-10-05", "today": {"rank": 200, "total": 500}},  # …superseded by the latest
            {"day": "2026-10-06", "today": {"rank": 120, "total": 480}},
            {"day": "2026-09-29", "today": {"rank": 1, "total": 10}},     # outside the 7 days
        ]
        best = self.lb.best_7d(rows, "2026-10-07")
        self.assertEqual((best["rank"], best["day"], best["days_recorded"]), (120, "2026-10-06", 2))

    def test_best_7d_window_includes_six_days_back(self):
        rows = [{"day": "2026-10-01", "today": {"rank": 5, "total": 9}}]
        self.assertEqual(self.lb.best_7d(rows, "2026-10-07")["rank"], 5)
        self.assertIsNone(self.lb.best_7d(rows, "2026-10-08"))

    def test_snapshot_writes_ranks_and_history(self):
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks()):
            self.snapshot()
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertTrue(out["ok"])
        self.assertEqual((out["all_time"]["rank"], out["rolling_7d"]["rank"], out["today"]["rank"]), (400, 500, 300))
        self.assertEqual(out["best_7d"]["rank"], 300)
        self.assertEqual(len((self.root / "leaderboard-history.jsonl").read_text().splitlines()), 1)
        self.assertNotIn('"t"', (self.root / "leaderboard.json").read_text())

    def test_fresh_snapshot_is_reused_unless_forced(self):
        me = mock.Mock(side_effect=self.ranks())
        with mock.patch.object(self.lb, "token", return_value="t"), mock.patch.object(self.lb, "me", me):
            self.snapshot()
            self.snapshot()
            self.assertEqual(me.call_count, 3)  # second call within 10 minutes: no fetch
            self.snapshot("--force")
            self.assertEqual(me.call_count, 6)

    def test_failed_fetch_keeps_last_ranks_and_says_why(self):
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks()):
            self.snapshot()
        boom = urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b""))
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=boom):
            self.snapshot("--force")
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertFalse(out["ok"])
        self.assertIn("503", out["error"])
        self.assertEqual(out["all_time"]["rank"], 400)  # the last good ranks still render

    def test_401_refreshes_the_token_once(self):
        calls = []

        def me(period, tok):
            calls.append(tok)
            if tok == "stale":
                raise urllib.error.HTTPError("u", 401, "expired", {}, io.BytesIO(b""))
            return self.ranks()(period, tok)

        tokens = mock.Mock(side_effect=lambda refresh=False: "fresh" if refresh else "stale")
        with mock.patch.object(self.lb, "token", tokens), mock.patch.object(self.lb, "me", me):
            self.snapshot()
        self.assertTrue(json.loads((self.root / "leaderboard.json").read_text())["ok"])
        self.assertEqual(calls[:2], ["stale", "fresh"])
        tokens.assert_any_call(refresh=True)

    def test_no_login_is_a_recorded_failure(self):
        with mock.patch.object(self.lb, "token", return_value=None):
            self.assertEqual(self.snapshot(), 0)
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertFalse(out["ok"])
        self.assertIn("login", out["error"])


if __name__ == "__main__":
    unittest.main()
