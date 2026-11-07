#!/usr/bin/env python3

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "serve.py"
spec = importlib.util.spec_from_file_location("tailnet_index", SCRIPT)
ti = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ti)

STATUS = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {"jmiv.tailc1d3f0.ts.net:443": {"Handlers": {
        "/": {"Proxy": "http://127.0.0.1:8420"},
        "/roadmap": {"Proxy": "http://127.0.0.1:8421"},
        "/paradev": {"Proxy": "http://127.0.0.1:8422"},
        "/notes": {"Path": "/Users/jake/notes"},
    }}},
}


class TailnetIndexTest(unittest.TestCase):
    def test_handlers_skip_the_index_and_keep_every_other_mount(self):
        got = ti.handlers(STATUS)
        self.assertEqual([e["path"] for e in got], ["/notes", "/paradev", "/roadmap"])
        self.assertEqual(got[1], {"host": "jmiv.tailc1d3f0.ts.net", "path": "/paradev",
                                  "target": "http://127.0.0.1:8422", "kind": "proxy"})
        self.assertEqual(got[0]["kind"], "files")

    def test_no_web_config_means_no_entries(self):
        self.assertEqual(ti.handlers({}), [])

    def test_page_shows_titles_links_and_down_targets(self):
        probes = {"http://127.0.0.1:8421": (True, "Roadmap automation"),
                  "http://127.0.0.1:8422": (False, None)}
        with mock.patch.object(ti, "probe", side_effect=lambda t: probes[t]):
            page = ti.build(ti.handlers(STATUS), now=datetime(2026, 11, 7, 13, 0, 0))
        self.assertIn('href="/roadmap/"', page)
        self.assertIn("Roadmap automation", page)
        self.assertIn('href="/paradev/"', page)
        self.assertIn("· down", page)                 # paradev isn't answering
        self.assertIn("3 page(s)", page)
        self.assertIn("jmiv.tailc1d3f0.ts.net", page)

    def test_title_is_read_from_the_target(self):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"<html><head><title> Paradev &amp; friends </title></head></html>"
        with mock.patch.object(ti.urllib.request, "urlopen", return_value=Resp()):
            self.assertEqual(ti.probe("http://127.0.0.1:8422"), (True, "Paradev & friends"))
        with mock.patch.object(ti.urllib.request, "urlopen", side_effect=OSError("refused")):
            self.assertEqual(ti.probe("http://127.0.0.1:9"), (False, None))

    def test_tailscale_failure_is_shown_not_raised(self):
        page = ti.build([], error="tailscale: not running")
        self.assertIn("read Tailscale Serve: tailscale: not running", page)
        self.assertNotIn("·  ·", page)


if __name__ == "__main__":
    unittest.main()
