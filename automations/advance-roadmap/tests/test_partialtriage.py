import json
import os
import shutil
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

    def test_transcripts_are_attributed_only_by_exact_session_id(self):
        projects = self.dir / "projects"
        (projects / "a").mkdir(parents=True)
        (projects / "b").mkdir(parents=True)
        (projects / "a" / "wanted.jsonl").write_text('{"type":"assistant","message":{"content":"REPO_A_FINDING"}}\n')
        (projects / "b" / "unrelated.jsonl").write_text('{"type":"assistant","message":{"content":"REPO_B_UNRELATED_FINDING"}}\n')
        triage.CLAUDE_PROJECTS = str(projects)
        result = self.dir / "result.json"
        result.write_text(json.dumps({"session_id": "wanted"}))
        out = self.dir / "triage.md"
        args = type("Args", (), {"repo": "demo", "provider": "claude", "ended": "error", "stamp": "s1",
                                  "result_json": str(result), "started": "0", "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        self.assertIn("REPO_A_FINDING", out.read_text())
        self.assertNotIn("REPO_B_UNRELATED_FINDING", out.read_text())

        result.write_text("{}")
        args = type("Args", (), {"repo": "demo", "provider": "claude", "ended": "error", "stamp": "s2",
                                  "result_json": str(result), "started": "0", "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        self.assertIn("no transcript attributable", out.read_text())
        self.assertNotIn("REPO_A_FINDING", out.read_text())
        self.assertNotIn("REPO_B_UNRELATED_FINDING", out.read_text())

    def test_codex_rollout_requires_stdout_session_id(self):
        sessions = self.dir / "sessions"
        sessions.mkdir()
        (sessions / "rollout-wanted.jsonl").write_text(
            '{"type":"event_msg","payload":{"type":"agent_message","message":"CODEX_WANTED"}}\n')
        (sessions / "rollout-unrelated.jsonl").write_text(
            '{"type":"event_msg","payload":{"type":"agent_message","message":"CODEX_UNRELATED"}}\n')
        triage.CODEX_SESSIONS = str(sessions)
        stdout = self.dir / "planner.out"
        stdout.write_text("session id: wanted\n")
        out = self.dir / "triage.md"
        args = type("Args", (), {"repo": "demo", "provider": "codex", "ended": "quota-paused", "stamp": "s1",
                                  "stdout": str(stdout), "session_file": "", "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        self.assertIn("CODEX_WANTED", out.read_text())
        self.assertNotIn("CODEX_UNRELATED", out.read_text())
        stdout.write_text("codex exited without a session header\n")
        self.assertEqual(triage.capture(args), 0)
        self.assertIn("no transcript attributable", out.read_text())
        self.assertNotIn("CODEX_WANTED", out.read_text())

    def test_adversarial_secret_forms_are_absent_from_entire_digest(self):
        transcript = self.dir / "rollout-adversarial.jsonl"
        raw_args = {
            "arguments": json.dumps({"note": "API_KEY=synthetic-secret.txt words\\\""}),
            "command": "curl -H 'Authorization: Basic dXNlcjpzZWNyZXQ=' "
                       "sk-liveSecretToken123456 ghp_ghpSecretToken123456 "
                       "gho_ghoSecretToken123456 github_pat_githubPatSecret123456 "
                       "xoxb-xoxSecretToken123456 api_key=lowercaseSecret123 "
                       "-----BEGIN RSA PRIVATE KEY-----\nprivateKeySecret123\n-----END RSA PRIVATE KEY-----",
        }
        record = {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                                            "arguments": json.dumps(raw_args)}}
        transcript.write_text(json.dumps(record) + "\n")
        sessions = self.dir / "sessions"
        sessions.mkdir()
        rollout = sessions / "rollout-adversarial-id.jsonl"
        shutil.copyfile(transcript, rollout)
        triage.CODEX_SESSIONS = str(sessions)
        stdout = self.dir / "planner.out"
        stdout.write_text("session id: adversarial-id\n")
        out = self.dir / "partial-triage-demo.md"
        args = type("Args", (), {"repo": "demo", "provider": "codex", "ended": "error", "stamp": "s1",
                                  "stdout": str(stdout), "session_file": "", "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        digest = out.read_text()
        for secret in ("dXNlcjpzZWNyZXQ=", "sk-liveSecretToken123456", "ghp_ghpSecretToken123456",
                       "gho_ghoSecretToken123456", "github_pat_githubPatSecret123456", "xoxb-xoxSecretToken123456",
                       "lowercaseSecret123", "privateKeySecret123", "synthetic-secret.txt", "words"):
            self.assertNotIn(secret, digest)
        self.assertNotIn("synthetic-secret.txt", digest.split("## Files and tickets touched", 1)[-1])

    def test_capture_redacts_truncated_pem_multiline_yaml_auth_url_and_aws_secrets(self):
        secrets = {
            "multiline": "one two\nthree",
            "db": "dbPasswordSecret",
            "openai": "openaiKeySecret",
            "auth": "authHeaderSecret",
            "custom_auth": "customSchemeSecret",
            "curl_user": "curlUserSecret",
            "curl_password": "curlPasswordSecret",
            "url_user": "urlUserSecret",
            "url_password": "urlPasswordSecret",
            "aws_id": "AKIA1234567890ABCDEF",
            "aws_secret": "A" * 40,
            "pem": "opensshPrivateBodySecret",
        }
        command = ('export API_KEY="one two\nthree"; DB_PASSWORD: dbPasswordSecret; '
                   'OPENAI_API_KEY: openaiKeySecret; Authorization: token authHeaderSecret; '
                   'Authorization: Custom customSchemeSecret; curl -u curlUserSecret:curlPasswordSecret '
                   'https://urlUserSecret:urlPasswordSecret@example.test; '
                   'AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF aws_secret_access_key: ' + "A" * 40)
        row = {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": command}}]}}
        result_row = {"type": "user", "isSidechain": False, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b",
             "content": "-----BEGIN OPENSSH PRIVATE KEY-----\nopensshPrivateBodySecret"}]}}
        projects = self.dir / "projects"
        transcript = projects / "demo" / "redaction-session.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps(row) + "\n" + json.dumps(result_row) + "\n")
        triage.CLAUDE_PROJECTS = str(projects)
        result = self.dir / "result.json"
        result.write_text(json.dumps({"session_id": "redaction-session"}))
        out = self.dir / "partial-triage-demo.md"
        args = type("Args", (), {"repo": "demo", "provider": "claude", "ended": "error", "stamp": "s1",
                                  "result_json": str(result), "started": "0", "out": str(out)})
        self.assertEqual(triage.capture(args), 0)
        digest = out.read_text()
        for secret in secrets.values():
            self.assertNotIn(secret, digest)
        self.assertNotIn("urlUserSecret:urlPasswordSecret", digest)
        self.assertIn("Authorization=[REDACTED]", digest)

    def test_redaction_preserves_nonsecret_assignments_and_markdown(self):
        research = "count=3 path=src/a.ts DEV-12 and `count=3`"
        self.assertEqual(triage.redact(research), research)
        self.assertEqual(triage.redact('API_KEY="one two"'), "API_KEY=[REDACTED]")

    def test_combine_gives_each_attempt_budget_and_keeps_all_attempt_headers(self):
        attempt_dir = self.dir / "attempts"
        attempt_dir.mkdir()
        (attempt_dir / "partial-triage-attempt-1.md").write_text(
            "- Provider: codex\n- Ended: quota-paused\n- Session ID: codex-large\n- Started: 1\n\n"
            "Source: `rollout-large.jsonl`\n\n## Interim assistant text\n\n" + "C" * triage.MAX_BYTES)
        (attempt_dir / "partial-triage-attempt-2.md").write_text(
            "- Provider: claude\n- Ended: error\n- Session ID: claude-small\n- Started: 2\n\n"
            "Source: `claude-small.jsonl`\n\n## Interim assistant text\n\nCLAUDE_LATER_FINDING")
        out = self.dir / "partial-triage-demo.md"
        args = type("Args", (), {"repo": "demo", "stamp": "new-stamp", "ended": "error",
                                  "attempt_dir": str(attempt_dir), "out": str(out)})
        self.assertEqual(triage.combine(args), 0)
        digest = out.read_text()
        self.assertIn("## Attempt 1: codex", digest)
        self.assertIn("## Attempt 2: claude", digest)
        self.assertIn("CLAUDE_LATER_FINDING", digest)
        self.assertLessEqual(out.stat().st_size, triage.MAX_BYTES)

    def test_unattributable_attempt_preserves_previous_research_and_age(self):
        out = self.dir / "partial-triage-demo.md"
        out.write_text("# Partial triage: demo\n\n- Stamp: original-stamp\n- Repo: demo\n"
                       "- Provider: codex\n- Ended: quota-paused\n\n## Interim assistant text\n\n"
                       "PRESERVED_RESEARCH_FINDING\n")
        old = time.time() - 48 * 60 * 60
        os.utime(out, (old, old))
        attempt_dir = self.dir / "attempts"
        attempt_dir.mkdir()
        (attempt_dir / "partial-triage-attempt-1.md").write_text(
            "- Provider: claude\n- Ended: error\n- Session ID: unavailable\n- Started: 3\n\n"
            "Source: no transcript attributable\n\n## Files and tickets touched\n\n"
            "- Files: none recorded\n- Tickets: none recorded\n")
        args = type("Args", (), {"repo": "demo", "stamp": "later-stamp", "ended": "error",
                                  "attempt_dir": str(attempt_dir), "out": str(out)})
        self.assertEqual(triage.combine(args), 0)
        digest = out.read_text()
        self.assertIn("Stamp: original-stamp", digest)
        self.assertIn("PRESERVED_RESEARCH_FINDING", digest)
        self.assertIn("Later attempt later-stamp unattributable", digest)
        self.assertEqual(out.stat().st_mtime, old)

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
