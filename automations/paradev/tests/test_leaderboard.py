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

SCRIPT = Path(__file__).resolve().parents[1] / "leaderboard.py"


def load(root):
    os.environ["PARADEV_ROOT"] = str(root)
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
        self.lb.PAGE_SLEEP_S = 0
        # No test reaches the network: the public standings default to an empty board.
        self.standings = mock.patch.object(
            self.lb, "standings", return_value={"rows": [], "hasMore": False, "total": 0})
        self.standings.start()

    def tearDown(self):
        mock.patch.stopall()
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

    # ── final single-day ranks ────────────────────────────────────────────────

    def board(self, me_rank, total=250, me_tokens="900", me_usd="5.5"):
        """standings(day, offset) for a 100-per-page board with jnelken at me_rank (None = absent)."""
        def page(day, offset, timeout=None):
            rows = []
            for rank in range(offset + 1, min(offset + 100, total) + 1):
                handle = "jnelken" if rank == me_rank else f"user{rank}"
                rows.append({"handle": handle, "rank": rank, "tokens": me_tokens if handle == "jnelken" else "1",
                             "usd": me_usd, "sessions": 42})
            return {"total": total, "rows": rows, "hasMore": offset + 100 < total}
        return mock.Mock(side_effect=page)

    def test_day_rank_pages_until_it_finds_the_handle(self):
        board = self.board(me_rank=231)
        with mock.patch.object(self.lb, "standings", board):
            rec = self.lb.day_rank("2026-11-03", "jnelken")
        self.assertEqual((rec["rank"], rec["total"], rec["tokens"], rec["usd"], rec["sessions"]),
                         (231, 250, 900, 5.5, 42))
        self.assertEqual([c.args[1] for c in board.call_args_list], [0, 100, 200])  # found on page 3

    def test_day_rank_absent_is_unranked(self):
        with mock.patch.object(self.lb, "standings", self.board(me_rank=None)):
            rec = self.lb.day_rank("2026-11-03", "jnelken")
        self.assertIsNone(rec["rank"])
        self.assertEqual(rec["total"], 250)

    def at(self, iso):
        now = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        return mock.patch.object(self.lb, "utc_now", return_value=now)

    def test_record_days_skips_settled_days_and_today(self):
        board = self.board(me_rank=7)
        with mock.patch.object(self.lb, "standings", board), self.at("2026-11-07T12:00:00"):
            self.assertEqual(self.lb.record_days(["2026-10-01", "2026-11-07"], "jnelken", log=lambda m: None), 1)
            self.assertEqual(self.lb.record_days(["2026-10-01", "2026-10-02"], "jnelken", log=lambda m: None), 1)
        store = self.lb.daily()
        self.assertEqual(sorted(store), ["2026-10-01", "2026-10-02"])   # 10-01 once; today never
        self.assertTrue(store["2026-10-01"]["final"])

    def test_recent_days_stay_provisional_until_settled(self):
        with mock.patch.object(self.lb, "standings", self.board(me_rank=50)), self.at("2026-11-07T12:00:00"):
            self.lb.record_days(["2026-11-06"], "jnelken", log=lambda m: None)
        self.assertFalse(self.lb.daily()["2026-11-06"]["final"])
        # A day later the late publishes moved the rank; the provisional record is refreshed.
        with mock.patch.object(self.lb, "standings", self.board(me_rank=45)), self.at("2026-11-08T12:00:00"):
            self.assertEqual(self.lb.record_days(["2026-11-06"], "jnelken", log=lambda m: None), 1)
        self.assertEqual(self.lb.daily()["2026-11-06"]["rank"], 45)
        # Once 48 h past the day's end it settles, and is never fetched again.
        with mock.patch.object(self.lb, "standings", self.board(me_rank=40)), self.at("2026-11-09T01:00:00"):
            self.lb.record_days(["2026-11-06"], "jnelken", log=lambda m: None)
        board = self.board(me_rank=1)
        with mock.patch.object(self.lb, "standings", board), self.at("2026-11-10T12:00:00"):
            self.assertEqual(self.lb.record_days(["2026-11-06"], "jnelken", log=lambda m: None), 0)
        self.assertEqual(self.lb.daily()["2026-11-06"]["rank"], 40)
        board.assert_not_called()

    def test_day_below_the_reachable_range_is_not_absent(self):
        board = self.board(me_rank=None, total=2000)
        with mock.patch.object(self.lb, "standings", board):
            rec = self.lb.day_rank("2026-11-03", "jnelken")
        self.assertEqual((rec["rank"], rec["below"], rec["total"]), (None, 1100, 2000))
        self.assertEqual(max(c.args[1] for c in board.call_args_list), 1000)   # never past the API's cap

    def test_deadline_mid_day_stores_nothing_for_that_day(self):
        clock = iter([0.0, 0.0, 0.0, 5.0, 5.0, 5.0])
        with mock.patch.object(self.lb, "standings", self.board(me_rank=250, total=300)), \
             mock.patch.object(self.lb.time, "monotonic", side_effect=lambda: next(clock)), \
             self.at("2026-11-07T12:00:00"):
            self.assertEqual(self.lb.record_days(["2026-11-06"], "jnelken", log=lambda m: None, budget_s=1), 0)
        self.assertEqual(self.lb.daily(), {})

    def test_best_7d_prefers_final_ranks(self):
        rows = [{"day": "2026-11-05", "today": {"rank": 50, "total": 90}},    # mid-day snapshot
                {"day": "2026-11-06", "today": {"rank": 10, "total": 30}},    # mid-day snapshot
                {"day": "2026-11-07", "today": {"rank": 400, "total": 800}}]  # today: snapshot only
        final = {"2026-11-05": {"day": "2026-11-05", "rank": 300, "total": 1000},
                 "2026-11-06": {"day": "2026-11-06", "rank": None, "total": 1000}}  # wasn't on the board
        best = self.lb.best_7d(rows, "2026-11-07", final)
        self.assertEqual((best["rank"], best["day"], best["days_recorded"]), (300, "2026-11-05", 2))

    def test_records_best_rank_and_busiest_day(self):
        store = {
            "2026-11-03": {"day": "2026-11-03", "rank": 178, "total": 1415, "tokens": 1400181221, "usd": 1051.9, "sessions": 1661},
            "2026-09-10": {"day": "2026-09-10", "rank": 124, "total": 1931, "tokens": 1161675798, "usd": 678.7, "sessions": 125},
            "2026-09-03": {"day": "2026-09-03", "rank": 126, "total": 1906, "tokens": 1282000000, "usd": 913.0, "sessions": 127},
            "2026-10-25": {"day": "2026-10-25", "rank": None, "total": 900, "tokens": 0, "usd": 0, "sessions": 0},
        }
        rec = self.lb.records(store)
        self.assertEqual((rec["best_rank"]["day"], rec["best_rank"]["top_pct"]), ("2026-09-10", 6.4))
        self.assertEqual(rec["busiest"]["day"], "2026-11-03")
        self.assertEqual([r["day"] for r in rec["top_days"]], ["2026-11-03", "2026-09-03", "2026-09-10"])
        self.assertEqual(rec["days_recorded"], 4)

    def test_records_mark_provisional_days(self):
        store = {"2026-11-06": {"day": "2026-11-06", "rank": 9, "total": 100, "tokens": 5, "usd": 1, "final": False},
                 "2026-10-01": {"day": "2026-10-01", "rank": 50, "total": 100, "tokens": 4, "usd": 1, "final": True}}
        rec = self.lb.records(store)
        self.assertEqual((rec["best_rank"]["day"], rec["best_rank"]["final"]), ("2026-11-06", False))
        self.assertTrue([r for r in rec["top_days"] if r["day"] == "2026-10-01"][0]["final"])

    def test_concurrent_writers_keep_each_others_days(self):
        self.lb.store_day({"day": "2026-10-01", "rank": 1, "fetched_at": "2026-10-05T00:00:00+00:00"})
        stale = self.lb.daily()                    # a writer that read before the next store…
        self.lb.store_day({"day": "2026-10-02", "rank": 2, "fetched_at": "2026-10-05T00:00:00+00:00"})
        stale["2026-10-03"] = {"day": "2026-10-03", "rank": 3, "fetched_at": "2026-10-05T00:00:00+00:00"}
        self.lb.store_day(stale["2026-10-03"])     # …still merges into the current file
        self.assertEqual(sorted(self.lb.daily()), ["2026-10-01", "2026-10-02", "2026-10-03"])

    def test_profile_days_reads_escaped_and_plain_payloads(self):
        html = (r'..{\"daily\":[{\"day\":\"2026-11-03\",\"tokens\":\"1400181221\",\"usd\":\"1051.8\"},'
                r'{\"day\":\"2026-09-10\",\"tokens\":\"1161675798\",\"usd\":\"678.6\"}]}..'
                '{"day":"2026-08-04","tokens":"397340434","usd":"369.0"}').encode()
        with mock.patch.object(self.lb, "get", return_value=html):
            days = self.lb.profile_days("jnelken")
        self.assertEqual(days, {"2026-11-03": 1400181221, "2026-09-10": 1161675798, "2026-08-04": 397340434})
        with mock.patch.object(self.lb, "get", return_value=b"<html>redesigned</html>"):
            with self.assertRaises(RuntimeError):
                self.lb.profile_days("jnelken")

    def test_backfill_top_records_busiest_days_and_updates_records(self):
        (self.root / "leaderboard.json").write_text(json.dumps({"ok": True, "handle": "jnelken", "day": "2026-11-07"}))
        profile = {"2026-11-03": 1400, "2026-09-10": 1100, "2026-08-04": 10}
        with mock.patch.object(self.lb, "profile_days", return_value=profile), \
             mock.patch.object(self.lb, "standings", self.board(me_rank=12)), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.lb.main(["backfill", "--top", "2"])
        days = sorted(json.loads(l)["day"] for l in (self.root / "leaderboard-daily.jsonl").read_text().splitlines())
        self.assertEqual(days, ["2026-09-10", "2026-11-03"])
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertEqual(out["records"]["best_rank"]["rank"], 12)

    def test_snapshot_finalizes_the_past_week(self):
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks(day="2026-11-07")), \
             mock.patch.object(self.lb, "standings", self.board(me_rank=33)), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.snapshot()
        days = sorted(json.loads(l)["day"] for l in (self.root / "leaderboard-daily.jsonl").read_text().splitlines())
        self.assertEqual(days, ["2026-10-31"] + [f"2026-11-0{d}" for d in range(1, 7)])
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertEqual(out["records"]["best_rank"]["rank"], 33)
        self.assertNotIn("finalize_error", out)

    def test_backfill_top_ignores_today(self):
        (self.root / "leaderboard.json").write_text(json.dumps({"ok": True, "handle": "jnelken", "day": "2026-11-07"}))
        profile = {"2026-11-07": 9999, "2026-11-03": 1400, "2026-09-10": 1100}
        with mock.patch.object(self.lb, "profile_days", return_value=profile), \
             mock.patch.object(self.lb, "standings", self.board(me_rank=12)), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.lb.main(["backfill", "--top", "1"])
        days = [json.loads(l)["day"] for l in (self.root / "leaderboard-daily.jsonl").read_text().splitlines()]
        self.assertEqual(days, ["2026-11-03"])   # the busiest *finished* day, not today

    def test_snapshot_saves_today_before_finalizing(self):
        seen = []

        def slow(day, offset, timeout=None):  # by the time past days are fetched, today's ranks are on disk
            seen.append(json.loads((self.root / "leaderboard.json").read_text())["today"]["rank"])
            return {"rows": [], "hasMore": False, "total": 0}
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks(day="2026-11-07")), \
             mock.patch.object(self.lb, "standings", side_effect=slow), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.snapshot()
        self.assertTrue(seen and all(r == 300 for r in seen))

    def test_finalize_budget_stops_starting_new_days(self):
        self.lb.FINALIZE_BUDGET_S = 10
        clock = iter([0.0, 0.0] + [20.0] * 50)   # deadline at 10 s; the first day fits, then time's up
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks(day="2026-11-07")), \
             mock.patch.object(self.lb, "standings", self.board(me_rank=3)), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"), \
             mock.patch.object(self.lb.time, "monotonic", side_effect=lambda: next(clock)):
            self.snapshot()
        self.assertEqual(sorted(self.lb.daily()), ["2026-10-31"])   # oldest first, then out of budget

    def test_budget_limited_runs_never_starve_older_days(self):
        week = [f"2026-10-{d}" for d in (31,)] + [f"2026-11-0{d}" for d in range(1, 7)]
        for run in range(len(week)):           # each run has time for exactly one day
            clock = iter([0.0, 0.0] + [20.0] * 50)
            with mock.patch.object(self.lb, "standings", self.board(me_rank=3)), \
                 mock.patch.object(self.lb.time, "monotonic", side_effect=lambda: next(clock)), \
                 self.at("2026-11-07T12:00:00"):
                self.lb.record_days(week, "jnelken", log=lambda m: None, budget_s=10)
        self.assertEqual(sorted(self.lb.daily()), week)   # every day reached, oldest to newest

    def test_backfill_keeps_a_snapshot_written_meanwhile(self):
        (self.root / "leaderboard.json").write_text(json.dumps({"ok": True, "handle": "jnelken", "day": "2026-11-07",
                                                                "fetched_at": "old"}))

        def page(day, offset, timeout=None):  # a snapshot lands while backfill is fetching
            (self.root / "leaderboard.json").write_text(json.dumps(
                {"ok": True, "handle": "jnelken", "day": "2026-11-07", "fetched_at": "new"}))
            return {"total": 1, "hasMore": False, "rows": [{"handle": "jnelken", "rank": 1, "tokens": "1", "usd": "1"}]}
        with mock.patch.object(self.lb, "standings", side_effect=page), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.lb.main(["backfill", "--days", "2026-10-01"])
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertEqual(out["fetched_at"], "new")
        self.assertEqual(out["records"]["best_rank"]["day"], "2026-10-01")

    def test_snapshot_survives_a_standings_outage(self):
        with mock.patch.object(self.lb, "token", return_value="t"), \
             mock.patch.object(self.lb, "me", side_effect=self.ranks(day="2026-11-07")), \
             mock.patch.object(self.lb, "standings", side_effect=OSError("down")), \
             mock.patch.object(self.lb, "utc_today", return_value="2026-11-07"):
            self.snapshot()
        out = json.loads((self.root / "leaderboard.json").read_text())
        self.assertTrue(out["ok"])                       # today's ranks still recorded
        self.assertIn("down", out["finalize_error"])


if __name__ == "__main__":
    unittest.main()
