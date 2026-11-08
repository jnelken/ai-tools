#!/usr/bin/env python3

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def load(module, **env):
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location(f"{module}_under_test", HERE / f"{module}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.code = Path(self.tmp.name) / "code"
        files = {
            "slackagent/var/reports/finance/2026-09-19-burn/report.md": "# Burn-rate audit\n\nBody.",
            "slackagent/var/reports/finance/2026-09-19-burn/data.json": '{"x": 1}',
            "brand/.logo-gestalt/board.html": "<html><head><title>Logo board</title></head></html>",
            "brand/.logo-gestalt/previews/hero.png": "PNG",
            ".worktrees/mailcrush-dev-91/.impeccable/mocks/decision/canon.html": "<title>Canon</title>",
            "app/node_modules/pkg/.impeccable/mocks/x.html": "<title>should be pruned</title>",
            "secret.txt": "nope",
        }
        for rel, body in files.items():
            p = self.code / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        self.cfg = Path(self.tmp.name) / "library.json"
        self.cfg.write_text(json.dumps({
            "reports": [{"glob": f"{self.code}/slackagent/var/reports/**/report.md", "group": "Finance"}],
            "sketchpad": [{"glob": f"{self.code}/**/.logo-gestalt/board.html"},
                          {"glob": f"{self.code}/**/.impeccable/mocks/**/*.html"}],
            "links": [{"title": "Brew Radar", "url": "https://claude.ai/artifact/abc", "updated": "2026-10-13"},
                      {"title": "Bike handoff", "url": "https://claude.ai/artifact/def", "section": "sketchpad"},
                      {"title": "no url"}],
        }))
        self.lib = load("library", TAILNET_INDEX_LIBRARY=str(self.cfg), TAILNET_INDEX_CODE_DIR=str(self.code))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sections_titles_and_groups(self):
        lib = self.lib.build_library()
        self.assertEqual([(e["title"], e["group"]) for e in lib["reports"]], [("Burn-rate audit", "Finance")])
        sketches = sorted((e["title"], e["group"]) for e in lib["sketchpad"])
        self.assertEqual(sketches, [("Canon", "mailcrush-dev-91"), ("Logo board", "brand")])
        self.assertEqual([l["title"] for l in lib["claude"]], ["Brew Radar"])
        self.assertEqual([l["title"] for l in lib["sketch_links"]], ["Bike handoff"])

    def test_globs_match_by_segment(self):
        m = self.lib.match_segments
        self.assertTrue(m(["a", "b", ".logo-gestalt", "board.html"], ["**", ".logo-gestalt", "board.html"]))
        self.assertTrue(m([".logo-gestalt", "board.html"], ["**", ".logo-gestalt", "board.html"]))
        self.assertFalse(m(["not.logo-gestalt", "board.html"], ["**", ".logo-gestalt", "board.html"]))
        self.assertTrue(m(["prototype", "index.html"], ["prototype", "*.html"]))
        self.assertFalse(m(["prototype", "deep", "index.html"], ["prototype", "*.html"]))   # * stays in one folder
        self.assertTrue(m(["mocks", "x", "y", "z.html"], ["mocks", "**", "*.html"]))

    def test_link_scheme_check_sees_through_control_characters(self):
        out = self.lib.md_to_html("[a](\x01javascript:alert(1)) [b]( JaVaScRiPt:alert(2)) [c](https://ok.example/x) [d](notes.md)")
        import re
        hrefs = re.findall(r'href="([^"]*)"', out)
        self.assertEqual(hrefs, ["https://ok.example/x", "notes.md"])   # no javascript: link survives
        self.assertIn('href="notes.md"', out)

    def test_relative_globs_are_refused(self):
        self.assertEqual(self.lib.expand("**/report.md"), [])

    def test_a_dangling_symlink_is_skipped_not_fatal(self):
        dead = self.code / "brand" / ".logo-gestalt" / "size-ladder.html"
        os.symlink(self.code / "gone.html", dead)
        cfg = json.loads(self.cfg.read_text())
        cfg["sketchpad"].append({"glob": f"{self.code}/**/.logo-gestalt/size-ladder.html"})
        lib = self.lib.build_library(cfg)
        self.assertEqual(len(lib["sketchpad"]), 2)   # the two real sketches, without the dead link

    def test_link_urls_and_code_keep_markdown_punctuation(self):
        out = self.lib.md_to_html("[download](exports/_draft_/report.md) and `a_b_c` and _em_")
        self.assertIn('href="exports/_draft_/report.md"', out)
        self.assertIn("<code>a_b_c</code>", out)
        self.assertIn("<em>em</em>", out)
        self.assertNotIn("<em>draft</em>", out)

    def test_code_in_a_link_label_and_parentheses_in_a_url(self):
        out = self.lib.md_to_html("[`report.md`](report.md) and [doc](https://example.com/a_(b))")
        self.assertIn('<a href="report.md"><code>report.md</code></a>', out)
        self.assertIn('href="https://example.com/a_(b)"', out)
        self.assertNotIn("\x00", out)

    def test_globs_prune_dependency_folders(self):
        found = self.lib.expand(f"{self.code}/**/.impeccable/mocks/**/*.html")
        self.assertTrue(found and not any("node_modules" in p for p in found))

    def test_resolve_serves_the_entry_and_its_folder_only(self):
        lib = self.lib.library()
        report = lib["reports"][0]
        board = next(e for e in lib["sketchpad"] if e["title"] == "Logo board")
        self.assertEqual(self.lib.resolve(report["id"]), report["path"])
        self.assertTrue(self.lib.resolve(report["id"], "data.json").endswith("data.json"))
        self.assertTrue(self.lib.resolve(board["id"], "previews/hero.png").endswith("hero.png"))
        self.assertIsNone(self.lib.resolve(board["id"], "../../secret.txt"))
        self.assertIsNone(self.lib.resolve(board["id"], "/etc/passwd"))
        self.assertIsNone(self.lib.resolve("0" * 12))

    def test_markdown_renders_and_escapes(self):
        out = self.lib.md_to_html(
            "# Title\n\nSome **bold** and `code` and [data](data.json) and <script>x</script>.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n- one\n- two\n  wrapped\n\n1. first\n\n> quote\n\n"
            "```\n<b>raw</b>\n```\n\n[bad](javascript:alert(1))")
        for frag in ("<h1>Title</h1>", "<strong>bold</strong>", "<code>code</code>", '<a href="data.json">data</a>',
                     "&lt;script&gt;", "<th>A</th>", "<td>2</td>", "<li>two wrapped</li>", "<ol><li>first</li></ol>",
                     "<blockquote><p>quote</p></blockquote>", "&lt;b&gt;raw&lt;/b&gt;"):
            self.assertIn(frag, out)
        self.assertNotIn("<script>", out)
        self.assertNotIn("javascript:", out)

    def test_index_page_lists_the_sections_with_sketchpad_last(self):
        serve = load("serve")
        page = serve.build([], lib=self.lib.build_library())
        self.assertLess(page.index("Reports"), page.index("On claude.ai"))
        self.assertLess(page.index("On claude.ai"), page.index("Sketchpad"))
        self.assertIn('href="https://claude.ai/artifact/abc"', page)
        self.assertIn("/lib/", page)


if __name__ == "__main__":
    unittest.main()
