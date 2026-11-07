#!/usr/bin/env python3

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "gen-page.py"


def load(root):
    os.environ["PARADEV_ROOT"] = str(root)
    spec = importlib.util.spec_from_file_location("gen_page_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LB = {
    "ok": True, "handle": "jnelken", "fetched_at": "2026-11-07T18:12:00+00:00", "day": "2026-11-07",
    "all_time": {"rank": 527, "total": 2762}, "rolling_7d": {"rank": 386, "total": 1572},
    "today": {"rank": 809, "total": 894},
    "best_7d": {"rank": 178, "total": 1415, "day": "2026-11-03", "days_recorded": 7},
    "records": {
        "best_rank": {"day": "2026-09-10", "rank": 124, "total": 1931, "tokens": 1161675798, "usd": 678.7,
                      "sessions": 125, "top_pct": 6.4, "final": True},
        "busiest": {"day": "2026-11-03", "rank": 178, "total": 1415, "tokens": 1400181221, "usd": 1051.9,
                    "sessions": 1661, "top_pct": 12.6, "final": True},
    },
}
DAYS = [
    {"day": "2026-11-06", "rank": 300, "total": 1200, "tokens": 536016880, "usd": 265.8, "sessions": 90, "final": False},
    {"day": "2026-11-03", "rank": 178, "total": 1415, "tokens": 1400181221, "usd": 1051.9, "sessions": 1661, "final": True},
    {"day": "2026-10-25", "rank": None, "below": 1100, "total": 1500, "tokens": None, "usd": None, "sessions": None, "final": True},
]


class GenPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gp = load(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_shows_ranks_records_and_every_day(self):
        page = self.gp.build(LB, DAYS)
        for text in ("#527", "/ 2,762", "all-time rank", "#124", "best day rank", "top 6.4%",
                     "1.40B", "busiest day", "Tue Nov 3", "provisional", "below #1,100",
                     "3 days recorded", 'href="https://paradev.com/jnelken"'):
            self.assertIn(text, page)
        # Top days lists ranked days only, busiest first; the history lists every day, newest first.
        history = page[page.index("Every recorded day"):]
        self.assertLess(history.index("2026-11-06"), history.index("2026-10-25"))

    def test_fetch_failure_is_visible(self):
        page = self.gp.build({**LB, "ok": False, "error": "HTTP 503"}, DAYS)
        self.assertIn("last fetch failed: HTTP 503", page)

    def test_empty_store_explains_how_to_backfill(self):
        page = self.gp.build({"handle": "jnelken"}, [])
        self.assertIn("backfill --top 10", page)

    def test_a_crashed_snapshot_still_renders_but_fails(self):
        from unittest import mock
        (self.root / "leaderboard.json").write_text(json.dumps(LB))
        crashed = mock.Mock(returncode=1, stdout="", stderr="Traceback: boom")
        with mock.patch.object(self.gp.subprocess, "run", return_value=crashed):
            self.assertEqual(self.gp.main([]), 1)
        self.assertIn("#527", (self.root / "paradev.html").read_text())

    def test_main_writes_the_page_from_disk(self):
        (self.root / "leaderboard.json").write_text(json.dumps(LB))
        (self.root / "leaderboard-daily.jsonl").write_text("".join(json.dumps(d) + "\n" for d in DAYS))
        self.assertEqual(self.gp.main(["--no-snapshot"]), 0)
        self.assertIn("#527", (self.root / "paradev.html").read_text())


if __name__ == "__main__":
    unittest.main()
