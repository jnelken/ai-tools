import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "lib"))
import partialtriage as triage  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "partial-triage"


class PartialTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_jsonl_extracts_commands_reads_interim_outputs_and_redacts_secrets(self):
        records = triage.claude_records(FIXTURES / "claude.jsonl")
        commands, reads, outputs, interim, files, tickets = triage.summarize_claude(records)
        self.assertTrue(any("gh issue view DEV-42" in x for x in commands))
        self.assertTrue(any("Read: /repo/demo/db/schema.sql" in x for x in reads))
        self.assertTrue(any("missing migration" in x for x in interim))
        self.assertIn("/repo/demo/db/schema.sql", files)
        self.assertIn("DEV-42", tickets)
        rendered = json.dumps([commands, reads, outputs, interim])
        self.assertNotIn("topsecret", rendered)
        self.assertNotIn("abc.def", rendered)
        self.assertNotIn("output-secret", rendered)

    def test_codex_rollout_extracts_events_and_redacts_secrets(self):
        records = triage.codex_records(FIXTURES / "codex-rollout.jsonl")
        commands, reads, outputs, interim, files, tickets = triage.summarize_codex(records)
        self.assertTrue(any("rg -n migration DEV-42" in x for x in commands))
        self.assertTrue(any("DEV-42 is blocked" in x for x in outputs))
        self.assertIn("DEV-42", tickets)
        self.assertTrue(any("So far:" in x for x in interim))
        self.assertNotIn("codex-secret", json.dumps(outputs))

    def test_capture_uses_session_id_and_writes_bounded_redacted_digest(self):
        projects = self.dir / "projects"
        transcript = projects / "demo" / "session-1.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_bytes((FIXTURES / "claude.jsonl").read_bytes())
        triage.CLAUDE_PROJECTS = str(projects)
        result = self.dir / "result.json"
        result.write_text(json.dumps({"session_id": "session-1"}))
        out = self.dir / "partial-triage-demo.md"
        args = type("Args", (), {"repo": "demo", "provider": "claude", "ended": "quota-paused",
                                  "stamp": "20261029-103000", "result_json": str(result), "started": "0",
                                  "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        digest = out.read_text()
        self.assertIn("Provider: claude", digest)
        self.assertIn("Ended: quota-paused", digest)
        self.assertIn("DEV-42", digest)
        self.assertNotIn("topsecret", digest)
        self.assertNotIn("abc.def", digest)
        self.assertLessEqual(out.stat().st_size, triage.MAX_BYTES)

    def test_each_output_is_truncated_to_about_400_characters(self):
        output = "x" * 1000
        self.assertLessEqual(len(triage.clipped(output)), 420)
        self.assertIn("truncated", triage.clipped(output))

    def test_redacts_quoted_environment_values(self):
        rendered = triage.redact('PASSWORD="two words"')
        self.assertNotIn("two words", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_digest_total_is_capped_at_20_kilobytes(self):
        digest = triage.encode_bounded("é" * (triage.MAX_BYTES * 2))
        self.assertLessEqual(len(digest.encode("utf-8")), triage.MAX_BYTES)
        self.assertIn("Digest truncated", digest)

    def test_partial_triage_ages_out_after_72_hours(self):
        code = self.dir / "code"
        path = code / ".advance-roadmap" / "partial-triage-demo.md"
        path.parent.mkdir(parents=True)
        path.write_text("old")
        old = time.time() - triage.AGE_SECONDS - 1
        os.utime(path, (old, old))
        args = type("Args", (), {"code_dir": str(code), "repo": "demo", "now": time.time()})
        self.assertEqual(triage.expire(args), 0)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
