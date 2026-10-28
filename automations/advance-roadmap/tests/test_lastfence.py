#!/usr/bin/env python3
"""lastfence.py: the planner's fence, from its transcript, when the final answer holds none."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "oct27" / "claude-planner-transcript.jsonl"
LABEL = "ORCHESTRATOR_RESULT_JSON"


class LastFenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.projects = self.dir / "projects" / "-some-cwd"
        self.projects.mkdir(parents=True)
        self.out = self.dir / "fence.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, result_json_text, label=LABEL):
        rj = self.dir / "result.json"
        rj.write_text(result_json_text)
        env = dict(os.environ, ADVANCE_ROADMAP_CLAUDE_PROJECTS=str(self.dir / "projects"))
        return subprocess.run([sys.executable, str(HERE / "lib/lastfence.py"), "--result-json", str(rj),
                               "--label", label, "--out", str(self.out)], env=env, capture_output=True, text=True)

    def transcript(self, sid, text=None):
        (self.projects / f"{sid}.jsonl").write_text(FIXTURE.read_text() if text is None else text)

    def test_takes_the_newest_assistant_message_with_a_fence_not_the_prompt_echo(self):
        self.transcript("s1")
        p = self.run_it(json.dumps({"session_id": "s1", "result": "💬 done"}))
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.out.read_text()
        self.assertIn("No work qualified for demo.", text)
        self.assertNotIn("ECHOED EXAMPLE", text)   # the user entry
        self.assertNotIn("in passing", text)       # the sidechain entry

    def test_stderr_noise_around_the_json_line_is_fine(self):
        self.transcript("s1")
        self.assertEqual(self.run_it("warning: x\n" + json.dumps({"session_id": "s1", "result": "r"}) + "\n").returncode, 0)

    def test_fails_without_a_session_a_transcript_or_a_fence(self):
        self.transcript("s1")
        self.assertEqual(self.run_it(json.dumps({"result": "r"})).returncode, 1)
        self.assertEqual(self.run_it(json.dumps({"session_id": "missing"})).returncode, 1)
        self.assertEqual(self.run_it("not json at all").returncode, 1)
        self.assertEqual(self.run_it(json.dumps({"session_id": "s1"}), label="OTHER_LABEL").returncode, 1)
        self.assertFalse(self.out.exists())

    def test_a_session_id_cannot_escape_the_projects_directory(self):
        (self.dir / "evil.jsonl").write_text(FIXTURE.read_text())
        self.assertEqual(self.run_it(json.dumps({"session_id": "../../evil"})).returncode, 1)


if __name__ == "__main__":
    unittest.main()
