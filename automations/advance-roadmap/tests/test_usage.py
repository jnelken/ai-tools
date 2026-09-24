#!/usr/bin/env python3

import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


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
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=96, api=5)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "auto"}):
            self.assertFalse(self.flags()["cursor"]["available_for_worker"])
        self.assertEqual(usage.pick_worker_chain(self.state), ["codex", "claude"])

    def test_cursor_pinned_model_gates_on_api_pool(self):
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=96, api=5)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "claude-sonnet-4-6"}):
            self.assertTrue(self.flags()["cursor"]["available_for_worker"])
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=5, api=97)), "dashboard_api"))
        with mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CURSOR_WORKER_MODEL": "claude-sonnet-4-6"}):
            self.assertFalse(self.flags()["cursor"]["available_for_worker"])

    def test_cursor_available_after_billing_cycle_end(self):
        usage.apply_cursor(self.state, probed(usage.parse_cursor_usage(cursor_response(auto=99, end_ms=PAST * 1000)), "dashboard_api"))
        self.assertTrue(self.flags()["cursor"]["available_for_worker"])

    def test_dollar_pool_and_display_message_do_not_gate(self):
        # includedSpend == limit and "You've hit your usage limit" while pools are ~10%.
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

    def test_probe_command_reports_missing_data(self):
        result = self.run_usage("probe")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"codex": None, "cursor": None})


if __name__ == "__main__":
    unittest.main()
