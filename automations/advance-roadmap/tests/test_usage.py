#!/usr/bin/env python3

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "usage.py"

os.environ["ADVANCE_ROADMAP_USAGE_PROBES"] = "0"
_spec = importlib.util.spec_from_file_location("usage", SCRIPT)
usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage)

FUTURE = int(time.time()) + 3600
PAST = int(time.time()) - 3600


def app_server_result(five=12, weekly=72, reached=None, five_reset=FUTURE, weekly_reset=FUTURE):
    codex = {
        "limitId": "codex", "limitName": None,
        "primary": {"usedPercent": five, "windowDurationMins": 300, "resetsAt": five_reset},
        "secondary": {"usedPercent": weekly, "windowDurationMins": 10080, "resetsAt": weekly_reset},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "planType": "plus", "rateLimitReachedType": reached,
    }
    return {
        "rateLimits": codex,
        "rateLimitsByLimitId": {
            "base_model_inference": {
                "limitId": "base_model_inference",
                "primary": {"usedPercent": 99, "windowDurationMins": 10080, "resetsAt": FUTURE},
            },
            "codex": codex,
        },
        "rateLimitResetCredits": {"credits": [{"id": "RateLimitResetCredit_secret"}]},
    }


def session_line(ts, five=11, weekly=71, limit_id="codex"):
    return json.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "token_count", "info": None, "rate_limits": {
            "limit_id": limit_id, "limit_name": None,
            "primary": {"used_percent": five, "window_minutes": 300, "resets_at": FUTURE},
            "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": FUTURE},
        }},
    })


def cursor_response(auto=10.2, api=11.3, end_ms=None):
    return {
        "billingCycleStart": "1788411623000",
        "billingCycleEnd": str(end_ms if end_ms is not None else FUTURE * 1000),
        "planUsage": {
            "totalSpend": 5099, "includedSpend": 2000, "bonusSpend": 3099, "limit": 2000,
            "autoPercentUsed": auto, "apiPercentUsed": api, "totalPercentUsed": 10.3,
        },
        "displayMessage": "You've hit your usage limit",
        "email": "someone@example.com",
    }


def probed(parsed, source):
    parsed = dict(parsed)
    parsed["source"] = source
    parsed["observed_epoch"] = time.time()
    return parsed


class ParseTests(unittest.TestCase):
    def test_app_server_shape_reads_codex_pool_only(self):
        got = usage.parse_codex_rate_limits(app_server_result())
        self.assertEqual(got["five_hour"]["used_pct"], 12)
        self.assertEqual(got["weekly"]["used_pct"], 72)
        self.assertEqual(got["weekly"]["reset_epoch"], FUTURE)
        self.assertEqual(got["plan"], "plus")
        self.assertIsNone(got["reached"])

    def test_session_log_shape(self):
        payload = json.loads(session_line("2026-09-24T02:51:11.809Z"))["payload"]
        got = usage.parse_codex_rate_limits({"rate_limits": payload["rate_limits"]})
        self.assertEqual(got["five_hour"]["used_pct"], 11)
        self.assertEqual(got["weekly"]["window_minutes"], 10080)

    def test_other_limit_id_is_ignored(self):
        payload = json.loads(session_line("2026-09-24T02:51:11Z", limit_id="base_model_inference"))
        self.assertIsNone(usage.parse_codex_rate_limits({"rate_limits": payload["payload"]["rate_limits"]}))

    def test_missing_secondary(self):
        result = app_server_result()
        result["rateLimitsByLimitId"]["codex"]["secondary"] = None
        got = usage.parse_codex_rate_limits(result)
        self.assertIsNotNone(got["five_hour"])
        self.assertIsNone(got["weekly"])

    def test_garbage_returns_none(self):
        for bad in (None, [], {}, {"rateLimits": {"primary": {"usedPercent": "x"}}}):
            self.assertIsNone(usage.parse_codex_rate_limits(bad))
        for bad in (None, {}, {"planUsage": {}}, {"planUsage": "nope"}):
            self.assertIsNone(usage.parse_cursor_usage(bad))

    def test_cursor_parse_converts_ms_and_drops_sensitive_fields(self):
        got = usage.parse_cursor_usage(cursor_response(end_ms=1791003623000))
        self.assertEqual(got["reset_epoch"], 1791003623)
        self.assertAlmostEqual(got["auto_pct"], 10.2)
        self.assertNotIn("email", json.dumps(got))
        self.assertNotIn("displayMessage", json.dumps(got))
        self.assertFalse(any("spend" in k or "limit" in k for k in got))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.state = usage.empty_state()

    def flags(self):
        usage.recompute_flags(self.state)
        return self.state["providers"]

    def test_codex_under_thresholds_is_available(self):
        usage.apply_codex(self.state, probed(usage.parse_codex_rate_limits(app_server_result()), "app_server"))
        self.assertTrue(self.flags()["codex"]["available_for_orchestrator"])

    def test_codex_weekly_over_threshold_is_gated(self):
        usage.apply_codex(self.state, probed(usage.parse_codex_rate_limits(app_server_result(weekly=85)), "app_server"))
        self.assertFalse(self.flags()["codex"]["available_for_orchestrator"])
        self.assertEqual(usage.pick_orchestrator(self.state), "claude")

    def test_claude_high_usage_gates_before_the_hard_limit(self):
        self.state["providers"]["claude"]["five_hour"] = {"used_pct": 75, "reset_epoch": FUTURE}
        providers = self.flags()
        self.assertFalse(providers["claude"]["available_for_orchestrator"])
        self.assertFalse(providers["claude"]["available_for_worker"])
        self.assertEqual(providers["claude"]["gate"]["state"], "over")
        self.assertEqual(providers["claude"]["gate"]["reset_epoch"], FUTURE)
        self.assertEqual(usage.pick_orchestrators(self.state), ["codex"])

    def test_claude_observed_limit_hit_still_gates(self):
        self.state["providers"]["claude"]["last_limit_hit"] = {"reset_epoch": FUTURE}
        self.assertFalse(self.flags()["claude"]["available_for_orchestrator"])

    def test_codex_past_reset_counts_as_zero(self):
        parsed = usage.parse_codex_rate_limits(app_server_result(five=99, weekly=99, five_reset=PAST, weekly_reset=PAST))
        usage.apply_codex(self.state, probed(parsed, "app_server"))
        providers = self.flags()
        self.assertEqual(providers["codex"]["weekly"]["used_pct"], 0)
        self.assertTrue(providers["codex"]["available_for_worker"])

    def test_stored_window_expires_without_new_probe(self):
        self.state["providers"]["codex"]["weekly"] = {"used_pct": 99, "reset_epoch": PAST}
        self.assertTrue(self.flags()["codex"]["available_for_worker"])

    def test_codex_reached_is_gated(self):
        parsed = usage.parse_codex_rate_limits(app_server_result(five=10, weekly=10, reached="primary"))
        usage.apply_codex(self.state, probed(parsed, "app_server"))
        self.assertFalse(self.flags()["codex"]["available_for_worker"])

    def test_codex_reached_clears_at_earliest_reset(self):
        parsed = usage.parse_codex_rate_limits(
            app_server_result(five=10, weekly=10, reached="primary", five_reset=PAST, weekly_reset=FUTURE))
        usage.apply_codex(self.state, probed(parsed, "app_server"))
        self.assertTrue(self.flags()["codex"]["available_for_worker"])

    def test_older_session_log_does_not_overwrite_fresher_reading(self):
        usage.apply_codex(self.state, probed(usage.parse_codex_rate_limits(app_server_result(weekly=72)), "app_server"))
        stale = usage.parse_codex_rate_limits(app_server_result(weekly=10))
        stale["source"] = "session_log"
        stale["observed_epoch"] = time.time() - 7200
        self.assertFalse(usage.apply_codex(self.state, stale))
        self.assertEqual(self.state["providers"]["codex"]["weekly"]["used_pct"], 72)
        self.assertEqual(self.state["providers"]["codex"]["source"], "app_server")

    def test_cursor_auto_pool_gates(self):
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=100, api=5)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "auto"}):
            self.assertFalse(self.flags()["cursor"]["available_for_worker"])
        self.assertEqual(usage.pick_worker_chain(self.state), ["codex", "claude"])

    def test_cursor_pinned_model_gates_on_api_pool(self):
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=100, api=5)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "claude-sonnet-4-6"}):
            self.assertTrue(self.flags()["cursor"]["available_for_worker"])
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=5, api=100)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "claude-sonnet-4-6"}):
            self.assertFalse(self.flags()["cursor"]["available_for_worker"])

    def test_cursor_available_after_billing_cycle_end(self):
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=99, end_ms=PAST * 1000)), "dashboard_api"))
        self.assertTrue(self.flags()["cursor"]["available_for_worker"])

    def test_extra_usage_budget_does_not_gate(self):
        # $0 extra-usage budget reads as includedSpend == limit and "You've hit
        # your usage limit" while the subscription quota is ~10%.
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response()), "dashboard_api"))
        self.assertTrue(self.flags()["cursor"]["available_for_worker"])

    def test_failed_probe_keeps_limit_hit_fallback(self):
        self.state["providers"]["cursor"]["last_limit_hit"] = {
            "observed_at": usage.now_iso(), "reset_epoch": FUTURE}
        self.assertFalse(usage.apply_cursor(self.state, None))
        self.assertFalse(self.flags()["cursor"]["available_for_worker"])

    def test_failed_probe_keeps_stored_values(self):
        usage.apply_codex(self.state, probed(usage.parse_codex_rate_limits(app_server_result(weekly=85)), "app_server"))
        self.assertFalse(usage.apply_codex(self.state, None))
        self.assertEqual(self.state["providers"]["codex"]["weekly"]["used_pct"], 85)
        self.assertFalse(self.flags()["codex"]["available_for_worker"])


class GateMatrixTests(unittest.TestCase):
    """70% five-hour / 80% weekly for Claude and Codex, 100% of the role's Cursor pool — in every role."""

    def gate(self, provider, role, prov=None, env=None):
        state = usage.empty_state()
        state["providers"][provider].update(prov or {})
        with mock.patch.dict(os.environ, env or {}):
            return usage.provider_gate(state, provider, role)

    def windows(self, provider, five=None, weekly=None, reset=FUTURE):
        key = "seven_day" if provider == "claude" else "weekly"
        return {"five_hour": {"used_pct": five, "reset_epoch": reset},
                key: {"used_pct": weekly, "reset_epoch": reset}}

    def test_window_boundaries_for_claude_and_codex_in_every_role(self):
        cases = [((69.9, None), True), ((70, None), False), ((None, 79.9), True), ((None, 80), False),
                 ((69, 79), True), ((100, 100), False)]
        for provider in ("claude", "codex"):
            for role in ("orchestrator", "worker", "reviewer"):
                for (five, weekly), available in cases:
                    with self.subTest(provider=provider, role=role, five=five, weekly=weekly):
                        got = self.gate(provider, role, self.windows(provider, five, weekly))
                        self.assertEqual(got["available"], available, got)
                        self.assertEqual(got["state"], "ok" if available else "over")

    def test_cursor_pool_boundary_per_role(self):
        for role, env_key in (("worker", "ADVANCE_ROADMAP_CURSOR_WORKER_MODEL"),
                              ("reviewer", "ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL")):
            for model, pool in (("auto", "auto_pct"), ("claude-sonnet-4-6", "api_pct")):
                for pct, available in ((99.9, True), (100, False)):
                    with self.subTest(role=role, model=model, pct=pct):
                        inc = {"auto_pct": 0, "api_pct": 0, "reset_epoch": FUTURE, pool: pct}
                        got = self.gate("cursor", role, {"included": inc}, {env_key: model})
                        self.assertEqual(got["available"], available, got)

    def test_cursor_never_plans(self):
        self.assertFalse(self.gate("cursor", "orchestrator")["available"])

    def test_expired_windows_read_as_zero(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.assertTrue(self.gate(provider, "worker", self.windows(provider, 99, 99, reset=PAST))["available"])
        inc = {"auto_pct": 99, "reset_epoch": PAST}
        self.assertTrue(self.gate("cursor", "worker", {"included": inc})["available"])

    def test_missing_readings_are_available_but_reported_unknown(self):
        for provider, role in (("claude", "orchestrator"), ("codex", "reviewer"), ("cursor", "worker")):
            with self.subTest(provider=provider):
                got = self.gate(provider, role)
                self.assertTrue(got["available"])
                self.assertEqual(got["state"], "unknown")
        state = usage.empty_state()
        usage.recompute_flags(state)
        self.assertEqual({p["gate"]["state"] for p in state["providers"].values()}, {"unknown"})

    def test_limit_hit_is_reported_as_limited_with_its_reset(self):
        got = self.gate("codex", "reviewer", {"last_limit_hit": {"reset_epoch": FUTURE}})
        self.assertEqual((got["available"], got["state"], got["reset_epoch"]), (False, "limited", FUTURE))

    def test_over_gate_reports_when_every_over_window_resets(self):
        later = FUTURE + 3600
        prov = {"five_hour": {"used_pct": 90, "reset_epoch": FUTURE},
                "seven_day": {"used_pct": 90, "reset_epoch": later}}
        self.assertEqual(self.gate("claude", "worker", prov)["reset_epoch"], later)

    def test_thresholds_are_overridable_by_env(self):
        env = dict(os.environ, ADVANCE_ROADMAP_MAX_FIVE_HOUR_PCT="50", ADVANCE_ROADMAP_MAX_WEEKLY_PCT="60",
                   ADVANCE_ROADMAP_MAX_CURSOR_PCT="40")
        code = ("import importlib.util, json, sys\n"
                f"spec = importlib.util.spec_from_file_location('u', {str(SCRIPT)!r})\n"
                "u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)\n"
                "print(json.dumps([u.MAX_FIVE_HOUR_PCT, u.MAX_SEVEN_DAY_PCT, u.MAX_CURSOR_PCT]))\n")
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), [50, 60, 40])


class ResetParseTests(unittest.TestCase):
    NY = ZoneInfo("America/New_York")

    def at(self, *args, tz=None):
        return datetime(*args, tzinfo=tz or self.NY).timestamp()

    def parse(self, text, observed, tz=None):
        epoch = usage.parse_reset_epoch_from_text(text, observed, tz)
        return None if epoch is None else datetime.fromtimestamp(epoch, tz or self.NY)

    def test_relative_formats_are_kept(self):
        obs = 1_800_000_000
        self.assertEqual(usage.parse_reset_epoch_from_text("usage limit, resets in 2h 30m", obs), obs + 9000)
        self.assertEqual(usage.parse_reset_epoch_from_text("Resets in 45m", obs), obs + 2700)
        self.assertEqual(usage.parse_reset_epoch_from_text("resets in 2 hours", obs), obs + 7200)

    def test_oct13_banner_resets_later_the_same_morning(self):
        text = "You've hit your session limit · resets 11:50am (America/New_York)"
        got = self.parse(text, self.at(2026, 10, 13, 10, 35))
        self.assertEqual(got, datetime(2026, 10, 13, 11, 50, tzinfo=self.NY))

    def test_clock_already_past_rolls_to_tomorrow(self):
        text = "You've hit your session limit · resets 11:50am (America/New_York)"
        got = self.parse(text, self.at(2026, 10, 13, 12, 0))
        self.assertEqual(got, datetime(2026, 10, 14, 11, 50, tzinfo=self.NY))
        # Exactly at the reset is already past too.
        got = self.parse(text, self.at(2026, 10, 13, 11, 50))
        self.assertEqual(got.date().day, 14)

    def test_bare_pm_hour_uses_the_given_zone(self):
        got = self.parse("You've hit your usage limit. Resets 3pm.", self.at(2026, 10, 13, 9, 0), self.NY)
        self.assertEqual(got, datetime(2026, 10, 13, 15, 0, tzinfo=self.NY))

    def test_try_again_at_clock_time(self):
        observed = self.at(2026, 10, 13, 9, 0)
        got = self.parse("You've hit your usage limit. Try again at 5:00 PM.", observed, self.NY)
        self.assertEqual(got, datetime(2026, 10, 13, 17, 0, tzinfo=self.NY))
        # ...and the recorded hit gates the provider until then, past the one-hour fallback.
        with tempfile.TemporaryDirectory() as tmp:
            state = usage.record_limit(Path(tmp), "codex", "You've hit your usage limit. Try again at 11:59 PM.",
                                       "approver")
        hit = state["providers"]["codex"]["last_limit_hit"]
        self.assertIsNotNone(hit["reset_epoch"])
        gate = usage.provider_gate(state, "codex", "orchestrator", now=hit["reset_epoch"] - 60)
        self.assertEqual(gate["state"], "limited")

    def test_midnight_and_noon(self):
        london = ZoneInfo("Europe/London")
        got = self.parse("resets 12:05 am (Europe/London)", self.at(2026, 10, 13, 23, 50, tz=london), london)
        self.assertEqual(got, datetime(2026, 10, 14, 0, 5, tzinfo=london))
        got = self.parse("resets 12pm (America/New_York)", self.at(2026, 10, 13, 9, 0))
        self.assertEqual(got, datetime(2026, 10, 13, 12, 0, tzinfo=self.NY))
        got = self.parse("resets 12am", self.at(2026, 10, 13, 9, 0), self.NY)
        self.assertEqual(got, datetime(2026, 10, 14, 0, 0, tzinfo=self.NY))

    def test_zone_converts_to_the_observers_epoch(self):
        # 11:50am in New York is 16:50 in London (both on summer time on Oct 13).
        london = ZoneInfo("Europe/London")
        epoch = usage.parse_reset_epoch_from_text("resets 11:50am (America/New_York)",
                                                  self.at(2026, 10, 13, 15, 0, tz=london), london)
        self.assertEqual(datetime.fromtimestamp(epoch, london), datetime(2026, 10, 13, 16, 50, tzinfo=london))

    def parse_in_local_ny(self, text, observed):
        """Parse with no zone given and the system zone set to New York."""
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            return self.parse(text, observed)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_local_next_day_reset_across_fall_back(self):
        # Observed in EDT on Oct 31; 3pm on Nov 1 2026 is already EST.
        got = self.parse_in_local_ny("Resets 3pm.", self.at(2026, 10, 31, 16, 0))
        self.assertEqual(got, datetime(2026, 11, 1, 15, 0, tzinfo=self.NY))

    def test_local_next_day_reset_across_spring_forward(self):
        # Observed in EST on Mar 7; 3pm on Mar 8 2026 is already EDT.
        got = self.parse_in_local_ny("Resets 3pm.", self.at(2026, 3, 7, 16, 0))
        self.assertEqual(got, datetime(2026, 3, 8, 15, 0, tzinfo=self.NY))

    def parse_both_ways(self, text, observed):
        """The same banner read with an explicit New York zone and with local time forced to it."""
        explicit = self.parse(text + " (America/New_York)", observed)
        return explicit, self.parse_in_local_ny(text, observed)

    def test_repeated_fall_back_hour_resets_the_same_night(self):
        # Nov 1 2026, 01:10 EST — the second pass through 1am. 1:30am EDT is past; 1:30am EST is not.
        observed = datetime(2026, 11, 1, 1, 10, fold=1, tzinfo=self.NY).timestamp()
        want = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=self.NY).timestamp()
        for got in self.parse_both_ways("resets 1:30am", observed):
            self.assertEqual(got.timestamp(), want)
        # On the first pass (EDT) the earlier 1:30 is next.
        observed = datetime(2026, 11, 1, 1, 10, fold=0, tzinfo=self.NY).timestamp()
        want = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=self.NY).timestamp()
        for got in self.parse_both_ways("resets 1:30am", observed):
            self.assertEqual(got.timestamp(), want)

    def test_skipped_spring_forward_hour_normalizes_forward(self):
        # Mar 8 2026: 2:30am never happens in New York; the clock reads 3:30 EDT then.
        observed = self.at(2026, 3, 8, 1, 0)
        for got in self.parse_both_ways("resets 2:30am", observed):
            self.assertEqual(got, datetime(2026, 3, 8, 3, 30, tzinfo=self.NY))
        # Already past the jump: tomorrow's real 2:30.
        for got in self.parse_both_ways("resets 2:30am", self.at(2026, 3, 8, 3, 45)):
            self.assertEqual(got, datetime(2026, 3, 9, 2, 30, tzinfo=self.NY))

    def test_unknown_zone_falls_back_to_the_given_one(self):
        got = self.parse("resets 3pm (Mars/Olympus)", self.at(2026, 10, 13, 9, 0), self.NY)
        self.assertEqual(got, datetime(2026, 10, 13, 15, 0, tzinfo=self.NY))

    def test_non_clock_text_is_not_a_reset(self):
        for text in ("resets 2026 plans", "resets 13pm", "resets 9:75am", "You've hit your session limit"):
            with self.subTest(text=text):
                self.assertIsNone(usage.parse_reset_epoch_from_text(text, self.at(2026, 10, 13, 9, 0), self.NY))

    def test_expired_recorded_reset_no_longer_gates(self):
        state = usage.empty_state()
        state["providers"]["claude"]["last_limit_hit"] = {"reset_epoch": PAST, "observed_at": usage.now_iso()}
        self.assertTrue(usage.provider_gate(state, "claude", "orchestrator")["available"])


class QuotaRecordTests(unittest.TestCase):
    def state(self, **providers):
        state = usage.empty_state()
        for name, prov in providers.items():
            state["providers"][name].update(prov)
        return state

    def test_lists_the_whole_role_even_when_the_lane_tried_one(self):
        state = self.state(cursor={"included": {"auto_pct": 100, "reset_epoch": FUTURE}})
        rec = usage.quota_record(state, "worker", [{"provider": "claude", "state": "limited", "cause": "usage limit",
                                                    "reset_epoch": FUTURE + 60}], primary="claude")
        self.assertEqual([(p["provider"], p["state"], p["reset_epoch"]) for p in rec["providers"]],
                         [("cursor", "over", FUTURE), ("codex", "unknown", None), ("claude", "limited", FUTURE + 60)])
        self.assertEqual((rec["provider"], rec["reset_epoch"], rec["cause"]), ("claude", FUTURE + 60, "usage limit"))

    def test_without_a_primary_the_first_reset_leads_and_every_reason_is_joined(self):
        state = self.state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE + 60}},
                           claude={"five_hour": {"used_pct": 90, "reset_epoch": FUTURE}})
        rec = usage.quota_record(state, "orchestrator")
        self.assertEqual((rec["provider"], rec["reset_epoch"]), ("claude", FUTURE))
        self.assertEqual(rec["cause"], "codex: weekly 85% >= 80%; claude: 5h 90% >= 70%")

    def test_the_live_gate_fills_a_reset_the_attempt_lacks(self):
        state = self.state(claude={"last_limit_hit": {"reset_epoch": FUTURE, "observed_at": usage.now_iso()}})
        rec = usage.quota_record(state, "approver", [{"provider": "claude", "cause": "session limit"}], "claude")
        claude = rec["providers"][1]
        self.assertEqual((claude["state"], claude["cause"], claude["reset_epoch"]), ("limited", "session limit", FUTURE))
        self.assertTrue(claude["reset_at"])

    def test_nobody_out_has_no_provider(self):
        rec = usage.quota_record(usage.empty_state(), "reviewer")
        self.assertIsNone(rec["provider"])
        self.assertEqual([p["provider"] for p in rec["providers"]], ["codex", "cursor", "claude"])


class LimitBannerTests(unittest.TestCase):
    def test_banner_at_line_start_only(self):
        text = "noise\nYou've hit your session limit · resets 11:50am (America/New_York)\nmore"
        self.assertEqual(usage.limit_banner(text), "You've hit your session limit · resets 11:50am (America/New_York)")
        self.assertIsNone(usage.limit_banner("The doc says you've hit your session limit sometimes."))
        self.assertIsNone(usage.limit_banner(""))

    def test_live_limit_ignores_echoed_banner_when_final_answer_succeeded(self):
        banner = "You've hit your usage limit. Resets 5pm."
        transcript = f"echoed from a prior log:\n{banner}\nthen the real answer"
        self.assertIsNone(usage.live_limit_banner(transcript, exit_code=0, final_answer="VERDICT: clean\n"))
        self.assertEqual(usage.live_limit_banner(transcript, exit_code=1, final_answer=""), banner)
        self.assertEqual(usage.live_limit_banner(transcript, exit_code=0, final_answer=""), banner)
        self.assertEqual(
            usage.live_limit_banner("no banner", exit_code=0, final_answer="ok",
                                    provider_result={"outcome": "limit_hit", "limit_text": banner}),
            banner)
        self.assertEqual(
            usage.live_limit_banner("ok\n", exit_code=1, final_answer="",
                                    provider_result={"is_error": True, "result": banner}),
            banner)
        # Zero exit + final answer that is the banner text still counts when is_error is set.
        self.assertEqual(
            usage.live_limit_banner("ok\n", exit_code=0, final_answer=banner,
                                    provider_result={"is_error": True, "subtype": "success",
                                                     "result": banner}),
            banner)
        self.assertIsNone(
            usage.live_limit_banner("ok\n", exit_code=0, final_answer=banner,
                                    provider_result=None))
        # Zero exit + is_error:true + generic nonempty result must not suppress a stderr banner.
        stderr_banner = f"noise\n{banner}\n"
        generic = "An unexpected error occurred. Please try again."
        self.assertEqual(
            usage.live_limit_banner(
                stderr_banner, exit_code=0, final_answer=generic,
                provider_result={"type": "result", "subtype": "success", "is_error": True,
                                 "result": generic}),
            banner)
        self.assertIsNone(
            usage.live_limit_banner(
                stderr_banner, exit_code=0, final_answer=generic,
                provider_result={"type": "result", "is_error": False, "result": generic}))

    def test_load_provider_result_finds_json_amid_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_text(
                'warn: ignore\n'
                '{"type":"result","is_error":true,"result":"You\'ve hit your usage limit. Resets 5pm."}\n')
            obj = usage.load_provider_result(path)
            self.assertTrue(obj["is_error"])
            self.assertEqual(
                usage.live_limit_banner(
                    "noise", exit_code=0, final_answer="You've hit your usage limit. Resets 5pm.",
                    provider_result=obj),
                "You've hit your usage limit. Resets 5pm.")

    def test_provider_is_error_and_is_error_cli(self):
        self.assertTrue(usage.provider_is_error({"is_error": True}))
        self.assertTrue(usage.provider_is_error({"subtype": "error_during_execution"}))
        self.assertFalse(usage.provider_is_error({"is_error": False, "result": "ok"}))
        self.assertFalse(usage.provider_is_error(None))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "err.json"
            path.write_text('{"type":"result","is_error":true,"result":"Selected model is at capacity."}\n')
            ok = Path(tmp) / "ok.json"
            ok.write_text('{"type":"result","is_error":false,"result":"VERDICT: clean"}\n')
            self.assertEqual(
                subprocess.run([sys.executable, str(SCRIPT), "is-error",
                                "--result-json", str(path)], capture_output=True).returncode, 0)
            self.assertEqual(
                subprocess.run([sys.executable, str(SCRIPT), "is-error",
                                "--result-json", str(ok)], capture_output=True).returncode, 1)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_USAGE_PROBES": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write_log(self, day, name, lines, mtime):
        d = self.root / "sessions" / day
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_text("\n".join(lines) + "\n")
        os.utime(f, (mtime, mtime))

    def test_session_log_picks_newest_codex_snapshot(self):
        now = time.time()
        self.write_log("2026/09/22", "rollout-a.jsonl", [session_line("2026-09-22T10:00:00Z", weekly=40)], now - 100)
        self.write_log("2026/09/23", "rollout-b.jsonl", [
            session_line("2026-09-23T10:00:00Z", weekly=60),
            session_line("2026-09-23T11:00:00Z", weekly=65),
            session_line("2026-09-23T12:00:00Z", weekly=99, limit_id="base_model_inference"),
            "not json",
        ], now)
        with mock.patch.object(usage, "CODEX_SESSIONS", self.root / "sessions"):
            got = usage.probe_codex_session_log()
        self.assertEqual(got["source"], "session_log")
        self.assertEqual(got["weekly"]["used_pct"], 65)

    def test_session_log_missing_dir(self):
        with mock.patch.object(usage, "CODEX_SESSIONS", self.root / "nope"):
            self.assertIsNone(usage.probe_codex_session_log())

    def test_codex_app_server_missing_binary(self):
        with mock.patch.object(usage, "CODEX_BIN", "/nonexistent"), \
                mock.patch.object(usage.shutil, "which", return_value=None):
            self.assertIsNone(usage.probe_codex_app_server())

    def test_codex_app_server_parses_stub_reply(self):
        stub = self.root / "codex"
        reply = json.dumps({"id": 2, "result": app_server_result(weekly=33)})
        stub.write_text(
            "#!/bin/sh\n"
            "read a; read b; read c\n"
            "echo '{\"id\":1,\"result\":{}}'\n"
            f"echo '{reply}'\n"
            "sleep 30\n"
        )
        stub.chmod(0o755)
        with mock.patch.object(usage, "CODEX_BIN", str(stub)):
            got = usage.probe_codex_app_server(timeout=10)
        self.assertEqual(got["source"], "app_server")
        self.assertEqual(got["weekly"]["used_pct"], 33)

    def test_codex_app_server_timeout(self):
        stub = self.root / "codex"
        stub.write_text("#!/bin/sh\nsleep 30\n")
        stub.chmod(0o755)
        with mock.patch.object(usage, "CODEX_BIN", str(stub)):
            start = time.monotonic()
            self.assertIsNone(usage.probe_codex_app_server(timeout=1))
            self.assertLess(time.monotonic() - start, 10)

    def test_cursor_probe_without_token(self):
        with mock.patch.object(usage, "read_cursor_token", return_value=None):
            self.assertIsNone(usage.probe_cursor())

    def test_cursor_probe_http_error(self):
        err = usage.urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
        with mock.patch.object(usage, "read_cursor_token", return_value="tok"), \
                mock.patch.object(usage.urllib.request, "urlopen", side_effect=err):
            self.assertIsNone(usage.probe_cursor())

    def test_cursor_probe_success(self):
        body = json.dumps(cursor_response(auto=42)).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(usage, "read_cursor_token", return_value="tok"), \
                mock.patch.object(usage.urllib.request, "urlopen", return_value=resp) as urlopen:
            got = usage.probe_cursor()
        self.assertEqual(got["auto_pct"], 42)
        req = urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("DashboardService/GetCurrentPeriodUsage"))

    def test_saved_state_has_no_secrets(self):
        state = usage.empty_state()
        body = json.dumps(cursor_response()).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(usage, "read_cursor_token", return_value="SECRET-TOKEN-VALUE"), \
                mock.patch.object(usage.urllib.request, "urlopen", return_value=resp):
            usage.apply_cursor(state, usage.probe_cursor())
        usage.apply_codex(state, probed(usage.parse_codex_rate_limits(app_server_result()), "app_server"))
        text = usage.save(self.root, state).read_text()
        for needle in ("SECRET-TOKEN-VALUE", "Bearer", "@example.com", "RateLimitResetCredit"):
            self.assertNotIn(needle, text)


class ClaudeProbeTests(unittest.TestCase):
    """probe_claude: the live OAuth reading first, the statusline file second, stale means unknown."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.statusline = self.dir / "claude-usage.json"
        self.state = usage.empty_state()
        self.patches = [mock.patch.object(usage, "CLAUDE_USAGE", self.statusline),
                        mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_USAGE_PROBES": "1"})]
        for p in self.patches:
            p.start()
        # Never the real OAuth endpoint or Keychain: by default the live probe has nothing to say.
        self.default_quota = self.stub_quota("{'observed_at': None, 'windows': {}}")
        self.default_quota.start()
        self.patches.append(self.default_quota)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def stub_quota(self, body):
        """A claude_quota.py whose quota() returns `body` (a Python expression), as usage.py would import it."""
        lib = self.dir / f"lib{len(list(self.dir.glob('lib*')))}"
        lib.mkdir(exist_ok=True)
        lib.joinpath("claude_quota.py").write_text(f"def quota():\n    return {body}\n")
        return mock.patch.object(usage, "claude_quota_lib", return_value=lib)

    def write_statusline(self, five, age=60, reset=FUTURE):
        self.statusline.write_text(json.dumps({
            "timestamp": time.time() - age,
            "five_hour": {"used_percentage": five, "reset_epoch": reset},
            "seven_day": {"used_percentage": 19, "reset_epoch": reset}}))

    def claude(self):
        usage.probe_claude(self.state)
        return self.state["providers"]["claude"]

    def test_live_reading_wins_over_a_stale_statusline_file(self):
        # 10-28: the statusline cache said 0% while the account was at 16%.
        self.write_statusline(0, age=4 * 86400)
        iso = datetime.fromtimestamp(FUTURE, ZoneInfo("UTC")).isoformat()
        with self.stub_quota(f"{{'observed_at': {time.time()}, 'windows': {{'five_hour': "
                             f"{{'used_percentage': 16.0, 'resets_at': '{iso}'}}, "
                             f"'seven_day': {{'used_percentage': 19.0, 'resets_at': '{iso}'}}}}}}"):
            prov = self.claude()
        self.assertEqual((prov["five_hour"]["used_pct"], prov["seven_day"]["used_pct"]), (16.0, 19.0))
        self.assertEqual(prov["five_hour"]["source"], "probe")
        self.assertEqual(prov["five_hour"]["reset_epoch"], int(FUTURE))  # ISO resets_at normalized to epoch

    def test_extra_seven_day_variants_are_ignored(self):
        with self.stub_quota(f"{{'observed_at': {time.time()}, 'windows': {{"
                             "'five_hour': {'used_percentage': 10, 'resets_at': None}, "
                             "'seven_day': {'used_percentage': 20, 'resets_at': None}, "
                             "'seven_day_opus': {'used_percentage': 99, 'resets_at': None}}}"):
            prov = self.claude()
        self.assertEqual(prov["seven_day"]["used_pct"], 20)

    def test_reading_older_than_15_minutes_is_unknown_not_zero(self):
        with self.stub_quota(f"{{'observed_at': {time.time() - 16 * 60}, 'windows': {{"
                             "'five_hour': {'used_percentage': 5, 'resets_at': None}, "
                             "'seven_day': {'used_percentage': 5, 'resets_at': None}}}"):
            prov = self.claude()
        self.assertIsNone(prov["five_hour"]["used_pct"])
        self.assertEqual(usage.claude_gate(prov, time.time())["state"], "unknown")

    def test_a_14_minute_reading_still_counts(self):
        with self.stub_quota(f"{{'observed_at': {time.time() - 14 * 60}, 'windows': {{"
                             "'five_hour': {'used_percentage': 71, 'resets_at': None}, "
                             "'seven_day': {'used_percentage': 5, 'resets_at': None}}}"):
            prov = self.claude()
        self.assertEqual(usage.claude_gate(prov, time.time())["state"], "over")

    def test_falls_back_to_the_statusline_file_when_the_live_probe_has_nothing(self):
        self.write_statusline(42)
        with self.stub_quota("{'observed_at': None, 'windows': {}}"):
            prov = self.claude()
        self.assertEqual((prov["five_hour"]["used_pct"], prov["five_hour"]["source"]), (42, "statusline"))

    def test_a_broken_live_probe_falls_back_too(self):
        self.write_statusline(33)
        with self.stub_quota("1 / 0"):
            self.assertEqual(self.claude()["five_hour"]["used_pct"], 33)

    def test_stale_statusline_file_is_unknown(self):
        self.write_statusline(0, age=4 * 86400)  # the Oct 24 file: 0% would have been read as headroom
        prov = self.claude()
        self.assertIsNone(prov["five_hour"]["used_pct"])
        self.assertEqual(usage.provider_gate(self.state, "claude", "worker")["state"], "unknown")

    def test_statusline_without_a_timestamp_is_aged_by_its_mtime(self):
        self.statusline.write_text(json.dumps({"five_hour": {"used_percentage": 50, "reset_epoch": FUTURE}}))
        os.utime(self.statusline, (time.time() - 3600, time.time() - 3600))
        self.assertIsNone(self.claude()["five_hour"]["used_pct"])

    def test_probes_off_never_imports_the_live_module(self):
        self.write_statusline(12)
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_USAGE_PROBES": "0"}), \
                mock.patch.object(usage, "claude_quota_lib", side_effect=AssertionError("loaded")):
            self.assertEqual(self.claude()["five_hour"]["used_pct"], 12)

    def test_no_source_keeps_a_recent_stored_reading_and_ages_out_an_old_one(self):
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_USAGE_PROBES": "0"}):
            prov = self.state["providers"]["claude"]
            prov.update(five_hour={"used_pct": 80, "reset_epoch": FUTURE}, observed_epoch=time.time() - 300)
            self.assertEqual(self.claude()["five_hour"]["used_pct"], 80)
            prov["observed_epoch"] = time.time() - 3600
            self.assertIsNone(self.claude()["five_hour"]["used_pct"])

    def test_the_live_module_resolves_from_this_checkout(self):
        self.default_quota.stop()
        self.patches.remove(self.default_quota)
        lib = usage.claude_quota_lib()
        self.assertTrue((lib / "claude_quota.py").is_file())
        self.assertEqual(lib, Path(__file__).resolve().parents[3] / "lib")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_usage(self, *args):
        env = dict(os.environ, ADVANCE_ROADMAP_USAGE_PROBES="0",
                   ADVANCE_ROADMAP_USAGE_FILE=str(self.root / "missing.json"))
        return subprocess.run(
            ["python3", str(SCRIPT), "--root", str(self.root), *args],
            check=False, text=True, capture_output=True, env=env,
        )

    def test_refresh_without_probes_falls_back_to_defaults(self):
        result = self.run_usage("refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["orchestrator"], "codex")
        self.assertEqual(out["workers"], ["cursor", "codex", "claude"])

    def test_record_limit_still_gates_with_probe_data_absent(self):
        self.run_usage("record-limit", "--provider", "codex", "--text", "usage limit, resets in 2h 5m")
        result = self.run_usage("pick-orchestrator")
        self.assertEqual(result.stdout.strip(), "claude")

    def test_record_limit_writes_the_hit_with_its_reset(self):
        out = self.root / "hit.json"
        self.run_usage("record-limit", "--provider", "claude", "--scope", "orchestrator", "--out", str(out),
                       "--text", "You've hit your session limit · resets 11:50am (America/New_York)")
        hit = json.loads(out.read_text())
        self.assertEqual((hit["provider"], hit["scope"], hit["reset_source"]), ("claude", "orchestrator", "derived"))
        reset = datetime.fromtimestamp(hit["reset_epoch"], ZoneInfo("America/New_York"))
        self.assertEqual((reset.hour, reset.minute), (11, 50))
        self.assertLess(hit["reset_epoch"] - time.time(), 86400)
        self.assertTrue(hit["reset_at"])

    def test_gate_command_exit_codes(self):
        result = self.run_usage("gate", "--role", "worker", "--provider", "cursor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "unknown")
        self.run_usage("record-limit", "--provider", "codex", "--text", "usage limit, resets in 2h 5m")
        result = self.run_usage("gate", "--role", "reviewer", "--provider", "codex")
        self.assertEqual(result.returncode, usage.GATED_EXIT)
        self.assertEqual(json.loads(result.stdout)["state"], "limited")

    def test_pick_orchestrators_lists_only_gated_eligible_planners(self):
        self.assertEqual(self.run_usage("pick-orchestrators").stdout.split(), ["codex", "claude"])
        self.run_usage("record-limit", "--provider", "claude", "--text", "You've hit your session limit · resets in 1h")
        self.assertEqual(self.run_usage("pick-orchestrators").stdout.split(), ["codex"])
        self.run_usage("record-limit", "--provider", "codex", "--text", "usage limit, resets in 2h 5m")
        result = self.run_usage("pick-orchestrators")
        self.assertEqual((result.returncode, result.stdout.strip()), (2, ""))

    def test_gates_command_lists_every_provider_for_the_role_with_its_reset(self):
        self.run_usage("record-limit", "--provider", "claude", "--text", "You've hit your session limit · resets in 1h")
        result = self.run_usage("gates", "--role", "orchestrator")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["role"], "orchestrator")
        self.assertEqual([(g["provider"], g["state"]) for g in out["gates"]],
                         [("codex", "unknown"), ("claude", "limited")])
        claude = out["gates"][1]
        self.assertFalse(claude["available"])
        self.assertAlmostEqual(claude["reset_epoch"], time.time() + 3600, delta=120)
        self.assertTrue(claude["reset_at"])

    def test_quota_record_command_reads_hits_and_attempts(self):
        hit = self.root / "hit.json"
        self.run_usage("record-limit", "--provider", "codex", "--out", str(hit), "--text", "usage limit, resets in 2h 5m")
        attempts = self.root / "attempts.jsonl"
        attempts.write_text(json.dumps({"provider": "cursor", "state": "over", "reason": "auto pool 96%",
                                        "reset_epoch": FUTURE}) + "\nnot json\n")
        result = self.run_usage("quota-record", "--role", "worker", "--attempts", str(attempts),
                                "--hit", f"codex={hit}", "--primary", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual((out["role"], out["provider"], out["cause"]), ("worker", "codex", "usage limit, resets in 2h 5m"))
        self.assertEqual([(p["provider"], p["state"]) for p in out["providers"]],
                         [("cursor", "over"), ("codex", "limited"), ("claude", "unknown")])
        self.assertEqual(out["providers"][0]["reset_epoch"], FUTURE)

    def test_probe_command_reports_missing_data(self):
        result = self.run_usage("probe")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"codex": None, "cursor": None})


if __name__ == "__main__":
    unittest.main()
