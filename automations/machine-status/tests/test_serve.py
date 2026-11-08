#!/usr/bin/env python3

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "serve.py"
spec = importlib.util.spec_from_file_location("machine_status", SCRIPT)
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

RC_OK = """launchd: loaded (running
pid 10879 )
plist:   /Users/jake/Library/LaunchAgents/com.jake.claude-remote-control.plist
tmux:    session 'claude-rc' running
--- last 40 lines ---
·✔︎· Connected · code
"""


class ParseTest(unittest.TestCase):
    def test_remote_control(self):
        self.assertEqual(ms.parse_rc(RC_OK)[0], "ok")
        self.assertEqual(ms.parse_rc(RC_OK.replace("Connected", "Reconnecting"))[0], "problem")
        down = "launchd: loaded\ntmux:    no session\n"
        state, summary = ms.parse_rc(down)
        self.assertEqual(state, "problem")
        self.assertIn("tmux session", summary)
        self.assertEqual(ms.parse_rc("launchd: not loaded\nplist:   (none)\n")[0], "absent")

    def test_superset_host_and_crash_check(self):
        self.assertEqual(ms.parse_superset_host("launchd: not loaded\n")[0], "absent")
        self.assertEqual(ms.parse_superset_host("launchd: loaded (running\npid 4 )\n")[0], "ok")
        self.assertEqual(ms.parse_superset_host("launchd: loaded\n")[0], "problem")
        self.assertEqual(ms.parse_crash_check("launchd: loaded\nRESUME candidates found: 0\n")[0], "ok")
        state, summary = ms.parse_crash_check("launchd: loaded\nRESUME candidates found: 2\n")
        self.assertEqual((state, "2 dead" in summary), ("warn", True))

    def test_host_parsers(self):
        self.assertEqual(ms.parse_boottime("{ sec = 1793579792, usec = 671044 } Sun Nov  1"), 1793579792)
        self.assertEqual(ms.parse_batt("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t24%; charging; 2:50"),
                         ("AC Power", "24% (charging)"))
        log = ("2026-11-07 21:30:14 -0500 Sleep               \tEntering Sleep state due to 'Maintenance'\n"
               "2026-11-07 21:31:14 -0500 Wake                \tDarkWake to FullWake\n")
        sl = ms.parse_sleep_log(log)
        self.assertEqual(sl["Sleep"][0], "2026-11-07 21:30:14")
        self.assertIn("FullWake", sl["Wake"][1])
        held = ms.parse_assertions('   pid 84196(Maschine 3): [0x1] 01:11:21 PreventUserIdleSystemSleep named: "Avoiding audio dropouts"')
        self.assertEqual(held, ["Avoiding audio dropouts (Maschine 3)"])


class CheckTest(unittest.TestCase):
    def test_missing_binary_and_timeout(self):
        with mock.patch.object(ms.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(ms.run(["nope"]), (None, "not installed"))
            self.assertEqual(ms.check_daemon("claude-rc-daemon", "RC", ms.parse_rc)["state"], "absent")
        with mock.patch.object(ms.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 8)):
            rc, out = ms.run(["slow"])
            self.assertIsNone(rc)
            self.assertIn("timed out", out)
            self.assertEqual(ms.check_daemon("claude-rc-daemon", "RC", ms.parse_rc)["state"], "problem")

    def test_tailscale_online_and_offline(self):
        def fake(argv, **kw):
            if "serve" in argv:
                return mock.Mock(returncode=0, stdout=json.dumps({"Web": {"h:443": {"Handlers": {"/": {}, "/x": {}}}}}), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps({"Self": {"Online": True, "DNSName": "jmiv.c.ts.net.",
                                                                       "TailscaleIPs": ["100.1.2.3"]}}), stderr="")
        with mock.patch.object(ms.subprocess, "run", side_effect=fake):
            c = ms.check_tailscale()
        self.assertEqual(c["state"], "ok")
        self.assertIn(("served pages", "2"), c["lines"])
        self.assertIn(("name", "jmiv.c.ts.net"), c["lines"])
        off = mock.Mock(returncode=0, stdout=json.dumps({"Self": {"Online": False}}), stderr="")
        with mock.patch.object(ms.subprocess, "run", return_value=off):
            self.assertEqual(ms.check_tailscale()["state"], "problem")

    def test_providers_file_absent_and_gated(self):
        with mock.patch.object(ms, "USAGE", "/nonexistent/providers-usage.json"):
            self.assertEqual(ms.check_providers()["state"], "absent")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"providers": {"codex": {"gate": {"state": "quota-paused"}, "five_hour": {"used_pct": 91.0}},
                                     "claude": {"gate": {"state": "ok"}}}}, f)
        try:
            with mock.patch.object(ms, "USAGE", f.name):
                c = ms.check_providers()
            self.assertEqual(c["state"], "warn")
            self.assertIn(("codex", "gate quota-paused · 5 h 91%"), c["lines"])
        finally:
            os.unlink(f.name)

    def test_one_broken_check_does_not_take_the_page_down(self):
        boom = mock.Mock(side_effect=RuntimeError("kaput"), __name__="check_host")
        with mock.patch.object(ms, "check_host", boom), \
             mock.patch.object(ms, "run", return_value=(None, "not installed")):
            cards = ms.collect()
        self.assertTrue(any(c["state"] == "problem" and "kaput" in str(c["lines"]) for c in cards))

    def test_page_banner_reflects_the_worst_state(self):
        ok = [ms.card("A", "ok", [("k", "v")]), ms.card("B", "absent")]
        self.assertIn("All good", ms.build(ok))
        page = ms.build(ok + [ms.card("C", "warn", [("disk free", "9%")], raw="<b>raw</b>")])
        self.assertIn("Needs a look", page)
        self.assertIn("&lt;b&gt;raw&lt;/b&gt;", page)        # raw output escaped
        self.assertIn("<title>Machine status</title>", page)


if __name__ == "__main__":
    unittest.main()
