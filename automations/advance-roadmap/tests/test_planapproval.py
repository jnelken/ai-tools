"""The plan approver's model configuration, mechanical checks and ruling logic, with no live model."""
import json
import os
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
        runners = {"codex": lambda prompt, code: calls.append("codex") or "no fence here",
                   "claude": lambda prompt, code: calls.append("claude") or self.fence(ruling)}
        out = pa.approve("/nowhere", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        self.assertEqual(calls, ["codex", "claude"])
        self.assertEqual(out["decisions"][1]["verdict"], "approve")
        self.assertNotIn("error", out)

    def test_limit_banner_is_recorded_and_fails_over(self):
        req = request(plan("1", 1, ["a"]), plan("2", 2, ["b"]))
        ruling = {"decisions": [{"stamp": "2", "verdict": "approve", "reason": "disjoint"}]}
        runners = {"codex": lambda prompt, code: "You've hit your usage limit. Try again at 5:00 PM.\n",
                   "claude": lambda prompt, code: self.fence(ruling)}
        with mock.patch.object(pa, "usage_cli") as usage:
            out = pa.approve("/root", req, "DOC.md", "/code", runners=runners, providers=["codex", "claude"])
        args = usage.call_args.args
        self.assertEqual(args[:3], ("/root", "record-limit", "--provider"))
        self.assertEqual(args[3], "codex")
        self.assertIn("approver", args)
        self.assertEqual(out["decisions"][1]["verdict"], "approve")

    def test_provider_order_is_the_gated_orchestrator_list(self):
        with mock.patch.object(pa, "usage_cli", return_value=mock.Mock(stdout="claude other\n")) as usage:
            self.assertEqual(pa.provider_order("/root"), ["claude"])
        self.assertEqual(usage.call_args.args, ("/root", "pick-orchestrators"))

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
