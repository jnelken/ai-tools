import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import tokens  # noqa: E402


class Args:
    def __init__(self, **kw):
        self.role, self.model, self.round = "worker", "", None
        self.__dict__.update(kw)


class TokensTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = str(self.dir / "model-usage.jsonl")

    def rows(self):
        return [json.loads(l) for l in open(self.log)] if os.path.exists(self.log) else []

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text)
        return str(p)

    def test_claude_json_logs_each_model_and_restores_text(self):
        body = {"result": "final text", "total_cost_usd": 0.5, "modelUsage": {
            "claude-opus-5": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 1000,
                              "cacheCreationInputTokens": 30, "costUSD": 0.4},
            "claude-haiku-4-5": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 0,
                                 "cacheCreationInputTokens": 0, "costUSD": 0.1}}}
        src = self.write("in.json", "stderr warning\n" + json.dumps(body) + "\n")
        out = str(self.dir / "out.txt")
        tokens.cmd_claude_json(Args(input=src, out_text=out, log=self.log, round=2, role="reviewer"))
        self.assertEqual(Path(out).read_text(), "stderr warning\nfinal text\n")
        opus, haiku = self.rows()
        self.assertEqual((opus["model"], opus["round"], opus["role"]), ("claude-opus-5", 2, "reviewer"))
        self.assertEqual(opus["total"], 10 + 30 + 20)       # cache reads excluded
        self.assertEqual(opus["cache_read"], 1000)
        self.assertEqual(haiku["total"], 3)

    def test_non_json_output_passes_through_and_logs_nothing(self):
        src = self.write("in.txt", "You've hit your limit · resets in 2h\n")
        out = str(self.dir / "out.txt")
        tokens.cmd_claude_json(Args(input=src, out_text=out, log=self.log))
        self.assertEqual(Path(out).read_text(), "You've hit your limit · resets in 2h\n")
        self.assertEqual(self.rows(), [])

    def test_cursor_json(self):
        src = self.write("in.json", json.dumps({"result": "hi", "usage": {
            "inputTokens": 4, "outputTokens": 6, "cacheReadTokens": 9000, "cacheWriteTokens": 100}}))
        tokens.cmd_cursor_json(Args(input=src, out_text=str(self.dir / "o"), log=self.log, model="auto"))
        (r,) = self.rows()
        self.assertEqual((r["provider"], r["model"], r["total"], r["cache_read"]), ("cursor", "auto", 110, 9000))

    def test_codex_prefers_rollout_over_total_line(self):
        sid = "01a109bc-cc7b-7343-a406-81827e2d26d5"
        day = self.dir / "sessions" / "2026" / "10" / "05"
        day.mkdir(parents=True)
        ev = {"payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50, "cache_write_input_tokens": 0}}}}
        (day / f"rollout-2026-10-05T09-20-35-{sid}.jsonl").write_text(
            '{"type":"x"}\n' + json.dumps(ev) + "\n")
        src = self.write("codex.txt", f"session id: {sid}\nprompt echo\ntokens used\n1,050\n")
        old, tokens.CODEX_SESSIONS = tokens.CODEX_SESSIONS, str(self.dir / "sessions")
        try:
            tokens.cmd_codex(Args(input=src, log=self.log, model="gpt-5.6-sol", role="orchestrator"))
        finally:
            tokens.CODEX_SESSIONS = old
        (r,) = self.rows()
        self.assertEqual((r["input"], r["cache_read"], r["output"], r["total"]), (200, 800, 50, 250))
        self.assertEqual(r["source"], "codex-rollout")

    def test_codex_falls_back_to_total_line(self):
        src = self.write("codex.txt", "no header here\ntokens used\n111,291\n")
        tokens.cmd_codex(Args(input=src, log=self.log, model="m", role="orchestrator"))
        (r,) = self.rows()
        self.assertEqual((r["total"], r["source"]), (111291, "codex-total"))

    def test_summarize_groups_review_rounds_per_reviewer(self):
        for rnd in (1, 1, 2):
            tokens.append(self.log, [tokens.record("reviewer", "codex", "m", 10, 0, 0, 5, None, "s", rnd)])
        tokens.append(self.log, [tokens.record("worker", "cursor", "auto", 100, 0, 0, 1, None, "s")])
        s = tokens.summarize(self.log)
        self.assertEqual(s["total"], 3 * 15 + 101)
        self.assertEqual(len(s["calls"]), 3)  # round 1 (merged), round 2, worker
        self.assertIsNone(s["cost_usd"])
        self.assertIsNone(tokens.summarize(str(self.dir / "missing.jsonl")))
        self.assertIsNone(tokens.summarize(""))


if __name__ == "__main__":
    unittest.main()
