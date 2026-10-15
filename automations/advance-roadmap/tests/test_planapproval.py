"""The plan approver's model configuration, mechanical checks and ruling logic, with no live model."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "lib"))
import planapproval as pa


def plan(stamp, thread, touch, **extra):
    return {"stamp": stamp, "thread": thread, "item": f"item{thread}", "linear_id": f"DEV-{thread}",
            "branch": f"roadmap/t{thread}", "touchpoints": touch, **extra}


def request(*plans):
    return {"repo": "r", "plans": list(plans)}


class ModelConfiguration(unittest.TestCase):
    def codex_model(self, env):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(pa.subprocess, "run", return_value=mock.Mock(stdout="")) as run:
            pa.run_codex("prompt", "/code")
        argv = run.call_args.args[0]
        return argv[argv.index("-m") + 1]

    def test_default_matches_models_sh(self):
        assignments = dict(line.split("=", 1) for line in (HERE / "lib/models.sh").read_text().splitlines()
                           if line and not line.startswith("#"))
        self.assertEqual(self.codex_model({}), assignments["CODEX_MODEL_DEFAULT"])

    def test_env_override_wins(self):
        self.assertEqual(self.codex_model({"ADVANCE_ROADMAP_ORCH_CODEX_MODEL": "custom-model"}),
                         "custom-model")

    def test_missing_models_file_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pa, "__file__", str(Path(tmp) / "planapproval.py")):
            with self.assertRaisesRegex(RuntimeError, "cannot read model defaults: .*models.sh"):
                self.codex_model({})

    def test_missing_default_assignment_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pa, "__file__", str(Path(tmp) / "planapproval.py")):
            (Path(tmp) / "models.sh").write_text("# no default assignment\n")
            with self.assertRaisesRegex(RuntimeError, "missing CODEX_MODEL_DEFAULT assignment"):
                self.codex_model({})


class MechanicalChecks(unittest.TestCase):
    def test_overlap_rules(self):
        self.assertTrue(pa.paths_overlap("src/a.ts", "src/a.ts"))
        self.assertTrue(pa.paths_overlap("src/", "src/a/b.ts"))
        self.assertTrue(pa.paths_overlap("src/*.ts", "src/a.ts"))
        self.assertFalse(pa.paths_overlap("src/a.ts", "src/ab.ts"))
        self.assertFalse(pa.paths_overlap("", "src"))

    def test_no_touchpoints_defers(self):
        mech = pa.mechanical_checks([plan("1", 1, ["a"]), plan("2", 2, [])])
        self.assertEqual(mech["2"][0], "defer")

    def test_same_ticket_or_branch_defers(self):
        mech = pa.mechanical_checks([plan("1", 1, ["a"]), plan("2", 2, ["b"], linear_id="DEV-1")])
        self.assertEqual(mech["2"][0], "defer")

    def test_overlap_serializes_after_the_earlier_thread(self):
        mech = pa.mechanical_checks([plan("1", 1, ["src/x"]), plan("2", 2, ["src/x/y.ts"])])
        self.assertEqual(mech["2"][0:1] + mech["2"][2:], ("serialize", "1"))

    def test_disjoint_plans_are_left_to_the_model(self):
        mech = pa.mechanical_checks([plan("1", 1, ["a"]), plan("2", 2, ["b"])])
        self.assertEqual(mech, {})


class Combine(unittest.TestCase):
    def model(self, *decisions, summary=""):
        return {"decisions": list(decisions), "summary": summary}

    def test_first_thread_always_approved(self):
        out = pa.combine(request(plan("1", 1, ["a"]), plan("2", 2, ["b"])),
                         self.model({"stamp": "1", "verdict": "defer"}, {"stamp": "2", "verdict": "approve"}))
        self.assertEqual([d["verdict"] for d in out["decisions"]], ["approve", "approve"])

    def test_mechanical_check_can_only_tighten(self):
        req = request(plan("1", 1, ["src/x"]), plan("2", 2, ["src/x"]))
        out = pa.combine(req, self.model({"stamp": "2", "verdict": "approve", "reason": "looks fine"}))
        self.assertEqual((out["decisions"][1]["verdict"], out["decisions"][1]["after"]), ("serialize", "1"))
        # ...and a stricter model ruling is never loosened by a milder check.
        out = pa.combine(req, self.model({"stamp": "2", "verdict": "defer", "reason": "too risky"}))
        self.assertEqual(out["decisions"][1]["verdict"], "defer")
        self.assertIsNone(out["decisions"][1]["after"])

    def test_silence_or_no_model_defers(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        self.assertEqual(pa.combine(req, self.model())["decisions"][1]["verdict"], "defer")
        self.assertEqual(pa.combine(req, None)["decisions"][1]["verdict"], "defer")

    def test_serialize_after_must_name_an_earlier_thread(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]), plan("3", 3, ["c"]))
        out = pa.combine(req, self.model({"stamp": "3", "verdict": "serialize", "after": "nope"},
                                         {"stamp": "2", "verdict": "approve"}))
        self.assertEqual(out["decisions"][2]["after"], "2")

    def test_standing_conflicts_are_always_listed(self):
        out = pa.combine(request(plan("1", 1, ["a"]), plan("2", 2, ["b"])), self.model())
        areas = " ".join(c["area"] for c in out["standing_conflicts"])
        self.assertIn("ROADMAP.md", areas)
        self.assertIn("lockfiles", areas)
        self.assertIn("deploys", areas)


class Approve(unittest.TestCase):
    def fence(self, body):
        return "noise\n```PLAN_APPROVAL_JSON\n" + json.dumps(body) + "\n```\n"

    def test_fence_parsing_takes_the_last_valid_fence(self):
        text = self.fence({"decisions": [], "summary": "old"}) + "```PLAN_APPROVAL_JSON\nnot json\n```\n"
        self.assertEqual(pa.extract_fence(text)["summary"], "old")
        self.assertIsNone(pa.extract_fence("nothing"))

    def test_falls_back_to_the_second_provider(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        calls = []
        runners = {"codex": lambda prompt, code: (calls.append("codex") or "no fence here", ""),
                   "claude": lambda prompt, code: (calls.append("claude") or self.fence(ruling), "")}
        out = pa.approve("/nowhere", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        self.assertEqual(calls, ["codex", "claude"])
        self.assertEqual(out["decisions"][1]["verdict"], "approve")
        self.assertNotIn("error", out)

    def test_limit_banner_is_recorded_and_fails_over(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        runners = {"codex": lambda prompt, code: ("You've hit your usage limit. Try again at 5:00 PM.\n", ""),
                   "claude": lambda prompt, code: (self.fence(ruling), "")}
        with mock.patch.object(pa, "usage_cli", return_value=mock.Mock(returncode=0)) as usage:
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        args = next(c.args for c in usage.call_args_list if c.args[1] == "record-limit")
        self.assertEqual(args[:4], ("/root", "record-limit", "--provider", "codex"))
        self.assertIn("approver", args)
        self.assertEqual(out["decisions"][1]["verdict"], "approve")

    def test_stderr_only_limit_banner_is_recorded_and_fails_over(self):
        # The real runners, with CLIs that print the banner to stderr alone and answer nothing.
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        banner = "You've hit your usage limit. Try again at 5:00 PM."
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("codex", "claude"):
                cli = Path(tmp) / name
                cli.write_text(f"#!/bin/sh\necho \"{banner}\" >&2\nexit 1\n")
                cli.chmod(0o755)
            env = {"ADVANCE_ROADMAP_CODEX_BIN": str(Path(tmp) / "codex"),
                   "ADVANCE_ROADMAP_CLAUDE_BIN": str(Path(tmp) / "claude")}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(pa, "usage_cli", side_effect=self.gates(set())) as usage:
                out = pa.approve("/root", req, "DOC.md", "/code", providers=["codex", "claude"])
        recorded = [c.args[3] for c in usage.call_args_list if c.args[1] == "record-limit"]
        self.assertEqual(recorded, ["codex", "claude"])
        self.assertTrue(all(banner in c.args for c in usage.call_args_list if c.args[1] == "record-limit"))
        self.assertEqual(out["error"], f"claude: {banner}")
        self.assertEqual(out["decisions"][1]["verdict"], "defer")

    def test_codex_ruling_comes_only_from_its_final_answer_file(self):
        # The real Codex runner, with a CLI that echoes PLAN_APPROVAL.md's valid example ruling to
        # stdout. Without an -o answer that echo is no ruling; a quota banner after it is a limit.
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        echoed = self.fence({"decisions": [{"stamp": "2", "verdict": "approve", "reason": "doc example"}]})
        final = self.fence({"decisions": [{"stamp": "2", "verdict": "serialize", "reason": "real answer"}]})
        banner = "You've hit your usage limit. Try again at 5:00 PM."
        script = ("#!/usr/bin/env python3\nimport os, sys\na = sys.argv[1:]\n"
                  f"print({echoed!r})\n"
                  "if os.environ.get('FAKE_FINAL'):\n"
                  "    open(a[a.index('-o') + 1], 'w').write(os.environ['FAKE_FINAL'])\n"
                  "else:\n"
                  f"    print({banner!r}); sys.exit(1)\n")
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "codex"
            cli.write_text(script)
            cli.chmod(0o755)
            for final_answer, verdict, reason in (("", "defer", None), (final, "serialize", "real answer")):
                with self.subTest(final_answer=bool(final_answer)), \
                        mock.patch.dict(os.environ, {"ADVANCE_ROADMAP_CODEX_BIN": str(cli),
                                                     "FAKE_FINAL": final_answer}), \
                        mock.patch.object(pa, "usage_cli", side_effect=self.gates(set())) as usage:
                    out = pa.approve("/root", req, "DOC.md", "/code", providers=["codex"])
                    self.assertEqual(out["decisions"][1]["verdict"], verdict)
                    recorded = [c.args[3] for c in usage.call_args_list if c.args[1] == "record-limit"]
                    if reason:
                        self.assertEqual(out["decisions"][1]["reason"], reason)
                        self.assertNotIn("error", out)
                        self.assertEqual(recorded, [])
                    else:
                        self.assertEqual(out["error"], f"codex: {banner}")
                        self.assertEqual(recorded, ["codex"])

    def test_fence_is_read_from_the_answer_not_the_log(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        runners = {"codex": lambda prompt, code: ("no fence here", self.fence({"decisions": []}))}
        with mock.patch.object(pa, "usage_cli", side_effect=self.gates(set())):
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex"])
        self.assertIn("codex: no PLAN_APPROVAL_JSON fence", out["error"])

    @staticmethod
    def gates(over):
        """A usage_cli stand-in whose `gate` exits 3 for each provider in `over` at call time."""
        def cli(root, *args):
            if args[0] == "gate":
                return mock.Mock(returncode=3 if args[-1] in over else 0)
            return mock.Mock(returncode=0, stdout="")
        return cli

    def test_fallback_gated_during_the_first_attempt_is_never_called(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        calls, over = [], set()
        runners = {"codex": lambda prompt, code: (calls.append("codex") or over.add("claude") or "no fence here", ""),
                   "claude": lambda prompt, code: (calls.append("claude") or self.fence({"decisions": []}), "")}
        with mock.patch.object(pa, "usage_cli", side_effect=self.gates(over)) as usage:
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        self.assertEqual(calls, ["codex"])
        self.assertEqual([c.args for c in usage.call_args_list if c.args[1] == "gate"][-1],
                         ("/root", "gate", "--role", "approver", "--provider", "claude"))
        self.assertEqual(out["decisions"][1]["verdict"], "defer")
        self.assertIn("claude: over its usage gate", out["error"])
        self.assertIn("codex: no", out["error"])

    def test_empty_ruling_then_gated_fallback_keeps_error_and_quota(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        quota = {"provider": "claude", "reset_at": "2026-10-15T17:00:00Z"}
        for empty in ({}, [], "approve"):
            with self.subTest(fence=empty):
                runners = {"codex": lambda prompt, code: (self.fence(empty), "")}
                with mock.patch.object(pa, "usage_cli", side_effect=self.gates({"claude"})), \
                        mock.patch.object(pa, "quota_record", return_value=quota) as record:
                    out = pa.approve("/root", req, "DOC.md", "/code", runners=runners,
                                     providers=["codex", "claude"])
                self.assertEqual(out["decisions"][1]["verdict"], "defer")
                self.assertIn("claude: over its usage gate", out["error"])
                self.assertEqual(out["quota"], quota)
                record.assert_called_once_with("/root", [], None)

    def test_fallback_whose_gate_reopens_during_the_first_attempt_is_tried(self):
        # Claude is over its gate when the approval starts and resets while Codex runs: the
        # default order still lists it, and its gate is read only when its turn comes.
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        calls, over = [], {"claude"}
        runners = {"codex": lambda prompt, code: (calls.append("codex") or over.discard("claude") or "no fence", ""),
                   "claude": lambda prompt, code: (calls.append("claude") or self.fence(ruling), "")}
        with mock.patch.object(pa, "usage_cli", side_effect=self.gates(over)), \
                mock.patch.dict(pa.RUNNERS, runners):
            out = pa.approve("/root", req, "DOC.md", "/code")
        self.assertEqual(calls, ["codex", "claude"])
        self.assertEqual(out["decisions"][1]["verdict"], "approve")
        self.assertNotIn("error", out)

    def test_first_provider_over_its_gate_is_skipped_not_called(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        calls = []
        runners = {"codex": lambda prompt, code: (calls.append("codex") or self.fence(ruling), ""),
                   "claude": lambda prompt, code: (calls.append("claude") or self.fence(ruling), "")}
        with mock.patch.object(pa, "usage_cli", side_effect=self.gates({"codex"})):
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        self.assertEqual(calls, ["claude"])
        self.assertEqual(out["decisions"][1]["verdict"], "approve")

    def test_unreadable_usage_never_blocks_the_fallback(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        runners = {"codex": lambda prompt, code: ("no fence here", ""),
                   "claude": lambda prompt, code: (self.fence(ruling), "")}
        with mock.patch.object(pa, "usage_cli", side_effect=subprocess.TimeoutExpired("usage.py", 120)):
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        self.assertEqual(out["decisions"][1]["verdict"], "approve")

    def test_provider_order_is_the_full_orchestrator_preference_order(self):
        with mock.patch.object(pa, "usage_cli") as usage:
            self.assertEqual(pa.provider_order(), ["codex", "claude"])
        usage.assert_not_called()

    def test_no_provider_defers_and_says_why(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        out = pa.approve("/nowhere", req, "DOC.md", "/code", runners={}, providers=[])
        self.assertEqual(out["decisions"][1]["verdict"], "defer")
        self.assertIn("no orchestrator provider", out["error"])

    def test_prompt_names_the_doc_and_every_plan(self):
        prompt = pa.build_prompt(request(plan("1", 1, ["a"]), plan("2", 2, ["b"])), "/x/PLAN_APPROVAL.md", "/code/r")
        self.assertIn("/x/PLAN_APPROVAL.md", prompt)
        self.assertIn("roadmap/t2", prompt)


if __name__ == "__main__":
    unittest.main()
