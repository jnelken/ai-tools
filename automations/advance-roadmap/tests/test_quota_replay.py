#!/usr/bin/env python3
"""Offline replay of the Oct 13 batch through the real run.sh, worker.sh and lib/*.py.

On Oct 13 every Claude planner answered only "You've hit your session limit · resets 11:50am
(America/New_York)". run.sh wrapped that as a blocked_no_item plan, released the batch slot,
ran a Cursor bookkeeping worker, saved the banner as a no-work verdict and alerted as an error.
Here a planner failure must stop before all of that, and a real no-work plan must not.
Providers are fakes that log every call; nothing reaches a live CLI, Linear or the live root.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import daily_batch  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oct13"
NY = ZoneInfo("America/New_York")
FUTURE = int(time.time()) + 6 * 3600
NO_WORK = {"action": "blocked_no_item", "outcome_token": "blocked-no-item", "repo": "demo",
           "blockers": [], "archives": [], "considered": [], "summary": "No work qualified for demo."}

FAKE = r'''#!{python}
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if name == "claude" and "-p" in args else (args[-1] if args else "")
role = "worker" if "WORKER.md" in prompt else "orchestrator"
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({{"bin": name, "role": role}}) + "\n")
mode = os.environ.get("FAKE_" + name.upper(), "plan")
fence = "```ORCHESTRATOR_RESULT_JSON\n" + os.environ.get("FAKE_PLAN", "{{}}") + "\n```"
if name == "agent":
    path = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("(raw JSON, no fence) to: "))
    json.dump({{"outcome": "bookkeeping", "provider": "cursor", "repo": "demo", "summary": "bookkeeping done"}},
              open(path, "w"))
    print(json.dumps({{"type": "result", "result": "done", "usage": {{"inputTokens": 1, "outputTokens": 1}}}}))
elif name == "codex":
    if mode == "limit":
        print("You've hit your usage limit · resets in 2h 30m")
        sys.exit(1)
    out = args[args.index("-o") + 1] if "-o" in args else None
    if out:
        open(out, "w").write(fence if mode == "plan" else "no fence here")
    print("codex done")
elif name == "claude":
    if mode == "limit":
        print(json.dumps(json.load(open(os.environ["FAKE_LIMIT_FIXTURE"]))))  # one line, as the CLI prints it
        sys.exit(1)
    result = fence if mode == "plan" else "I looked around and decided nothing."
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": result, "modelUsage": {{}}}}))
'''


class QuotaReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root, self.code, self.bin = self.dir / "root", self.dir / "code", self.dir / "bin"
        self.skills = self.dir / "skills" / "advance-roadmap"
        for d in (self.root / "lib", self.code, self.bin, self.skills, self.root / "batch-verdicts"):
            d.mkdir(parents=True)
        for f in ("run.sh", "worker.sh"):
            (self.root / f).symlink_to(HERE / f)
        for f in ("models.sh", "usage.py", "runrecord.py", "tokens.py", "verdict.py", "pendingplan.py",
                  "deploysnap.py"):
            (self.root / "lib" / f).symlink_to(HERE / "lib" / f)
        (self.root / "lib" / "linearsnap.py").write_text(
            "import json, sys\na = sys.argv\njson.dump({'ok': True, 'issues': []}, open(a[a.index('--out') + 1], 'w'))\n")
        for f in ("ORCHESTRATOR.md", "SAFETY.md", "WORKER.md", "CODE_HEALTH.md"):
            (self.skills / f).write_text("stub\n")
        subprocess.run(["git", "init", "-q", str(self.code / "demo")], check=True)
        subprocess.run(["git", "-C", str(self.code / "demo"), "remote", "add", "origin",
                        "git@github.com:jnelken/demo.git"], check=True)
        for name in ("claude", "codex", "agent"):
            p = self.bin / name
            p.write_text(FAKE.format(python=sys.executable))
            p.chmod(0o755)
        # deploysnap.py's gh: fails like an offline machine, so nothing reaches GitHub. It runs after
        # the planner is picked and before it launches, so it can also stand in for another lane
        # gating planners in between (FAKE_GATE_ON_SNAPSHOT).
        gh = self.bin / "gh"
        gh.write_text("#!/bin/sh\n"
                      "for p in $FAKE_GATE_ON_SNAPSHOT; do\n"
                      f"  {sys.executable} \"$ADVANCE_ROADMAP_ROOT/lib/usage.py\" --root \"$ADVANCE_ROADMAP_ROOT\" "
                      "record-limit --provider \"$p\" --text \"usage limit, resets in 2h 5m\" >/dev/null 2>&1\n"
                      "done\n"
                      "echo \"gh: offline in tests\" >&2\nexit 1\n")
        gh.chmod(0o755)
        self.snapshot = self.dir / "snapshot.json"
        self.snapshot.write_text(json.dumps({"ok": True, "issues": []}))
        self.verdict = self.root / "batch-verdicts" / "demo.json"
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def usage_state(self, codex=None, claude=None):
        providers = {"codex": codex or {}, "claude": claude or {}}
        (self.root / "providers-usage.json").write_text(json.dumps({"providers": providers, "history": []}))

    def env(self, **fake):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ADVANCE_ROADMAP_", "SUPERSET_", "SLACK_"))}
        self.n += 1
        self.stamp = f"20261013-1030{self.n:02d}"
        env.update({
            "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.code),
            "ADVANCE_ROADMAP_SKILL_DIR": str(self.skills), "ADVANCE_ROADMAP_CONDUCTOR_DIR": str(self.skills),
            "ADVANCE_ROADMAP_SECRETS": "/dev/null", "ADVANCE_ROADMAP_NOTIFY": "0", "ADVANCE_ROADMAP_SUPERSET": "0",
            "ADVANCE_ROADMAP_SLACKAGENT_DIR": str(self.dir / "no-slackagent"), "DOTFILES_PERSONAL_MACHINE": "1",
            "ADVANCE_ROADMAP_CLAUDE_BIN": str(self.bin / "claude"), "ADVANCE_ROADMAP_CODEX_BIN": str(self.bin / "codex"),
            "ADVANCE_ROADMAP_AGENT_BIN": str(self.bin / "agent"), "ADVANCE_ROADMAP_USAGE_PROBES": "0",
            "ADVANCE_ROADMAP_USAGE_FILE": str(self.dir / "no-statusline.json"),
            "ADVANCE_ROADMAP_LANE": "1", "ADVANCE_ROADMAP_TARGET_REPO": "demo", "ADVANCE_ROADMAP_THREAD": "1",
            "ADVANCE_ROADMAP_BATCH_PID": str(os.getpid()), "ADVANCE_ROADMAP_BATCH_SNAPSHOT": str(self.snapshot),
            "ADVANCE_ROADMAP_BATCH_POLL_S": "0.05", "ADVANCE_ROADMAP_STAMP": self.stamp,
            "ADVANCE_ROADMAP_LAST_VERDICT": str(self.verdict),
            "ADVANCE_ROADMAP_PENDING_DIR": str(self.root / "pending"),
            "FAKE_CALLS": str(self.dir / "calls.jsonl"), "FAKE_LIMIT_FIXTURE": str(FIXTURES / "claude-planner-limit.json"),
            "FAKE_PLAN": json.dumps(NO_WORK), "PYTHONDONTWRITEBYTECODE": "1",
            "ADVANCE_ROADMAP_GH_BIN": str(self.bin / "gh"),
        })
        env.update(fake)
        return env

    def run_lane(self, **fake):
        """One daily-batch lane: release it at the barrier if it gets there, like daily_batch.py."""
        env = self.env(**fake)
        run = self.root / "runs" / self.stamp
        proc = subprocess.Popen(["/bin/zsh", str(self.root / "run.sh")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.reached_barrier = False
        deadline = time.time() + 90
        while proc.poll() is None and time.time() < deadline:
            if (run / "batch-ready").exists() and not (run / "batch-release").exists():
                self.reached_barrier = True
                (run / "batch-release").touch()
            time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()
            self.fail("run.sh did not finish")
        self.log = (self.root / "logs" / f"run-{self.stamp}.log").read_text()
        return run

    def record(self):
        rows = [json.loads(l) for l in (self.root / "runs.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["stamp"], self.stamp)
        return rows[-1]

    def calls(self):
        p = self.dir / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def alert(self):
        return subprocess.run([sys.executable, str(HERE / "lib/runrecord.py"), "--root", str(self.root), "alert"],
                              capture_output=True, text=True).stdout.strip()

    def assert_nothing_dispatched(self, run):
        self.assertFalse(self.reached_barrier, "a failed plan must never reach batch-ready")
        self.assertFalse((run / "batch-ready").exists())
        for f in ("worker-request.json", "worker-status.json", "worker-result.json"):
            self.assertFalse((run / f).exists(), f)
        self.assertEqual([c for c in self.calls() if c["role"] == "worker"], [])
        self.assertFalse(self.verdict.exists(), "no verdict may be saved from a failed plan")
        self.assertEqual(list((self.root / "pending").glob("*.json")) if (self.root / "pending").exists() else [], [])

    # ── Oct 13 ────────────────────────────────────────────────────────────────

    def test_oct13_planner_limit_dispatches_nothing_and_pauses_for_quota(self):
        # Codex was over its gate that morning, so Claude planned; Claude answered only the banner.
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        before = time.time()
        run = self.run_lane(FAKE_CLAUDE="limit")
        self.assert_nothing_dispatched(run)
        self.assertEqual(self.calls(), [{"bin": "claude", "role": "orchestrator"}])
        rec = self.record()
        self.assertEqual(rec["outcome"], "quota-paused", rec)
        self.assertEqual((rec["orchestrator"], rec["quota"]["provider"], rec["quota"]["role"]),
                         ("claude", "claude", "orchestrator"))
        self.assertEqual(rec["quota"]["cause"], "You've hit your session limit · resets 11:50am (America/New_York)")
        reset = rec["quota"]["reset_epoch"]
        self.assertEqual(datetime.fromtimestamp(reset, NY).strftime("%H:%M"), "11:50")
        self.assertTrue(before < reset <= before + 86400)
        hit = json.loads((self.root / "providers-usage.json").read_text())["providers"]["claude"]["last_limit_hit"]
        self.assertEqual(hit["reset_epoch"], reset)
        # A second identical lane: still not an error streak, so no generic alert.
        self.run_lane(FAKE_CLAUDE="limit")
        self.assertEqual(self.record()["outcome"], "skipped-quota")  # Claude is now gated until its reset
        self.assertEqual(self.alert(), "")
        self.assertNotIn("=== alert:", self.log)

    def test_planner_limit_fails_over_to_the_next_planner_under_its_gate(self):
        self.usage_state()
        run = self.run_lane(FAKE_CODEX="limit", FAKE_CLAUDE="plan")
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "orchestrator"], ["codex", "claude"])
        self.assertTrue(self.reached_barrier)
        rec = self.record()
        self.assertEqual((rec["orchestrator"], rec["outcome"]), ("claude", "blocked-no-item"))
        self.assertIn("failing over to claude", self.log)
        self.assertTrue((run / "orchestrator-limit-codex.json").exists())

    def test_bad_plan_after_a_limit_failover_is_an_error_not_a_quota_pause(self):
        self.usage_state()
        run = self.run_lane(FAKE_CODEX="limit", FAKE_CLAUDE="plan", FAKE_PLAN='{"action": "launch_rockets"}')
        self.assert_nothing_dispatched(run)
        self.assertEqual([c["bin"] for c in self.calls()], ["codex", "claude"])
        rec = self.record()
        self.assertEqual((rec["orchestrator"], rec["outcome"]), ("claude", "error"), rec)
        self.assertIsNone(rec["quota"])

    def test_no_failover_to_a_planner_over_its_gate(self):
        self.usage_state(claude={"five_hour": {"used_pct": 90, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CODEX="limit", FAKE_CLAUDE="plan")
        self.assert_nothing_dispatched(run)
        self.assertEqual([c["bin"] for c in self.calls()], ["codex"])
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["quota"]["provider"]), ("quota-paused", "codex"))
        # The gated planner is out too: the record and the batch summary name it with its reset.
        out = {p["provider"]: p for p in rec["quota"]["providers"]}
        self.assertEqual(sorted(out), ["claude", "codex"])
        self.assertEqual((out["claude"]["state"], out["claude"]["reset_epoch"]), ("over", FUTURE))
        summary = daily_batch.quota_paused({"queue": [{"repo": "demo", "outcome": rec["outcome"],
                                                       "quota": rec["quota"]}]})
        self.assertEqual({q["provider"]: q["reset_epoch"] for q in summary}["claude"], FUTURE)

    def test_both_planners_out_during_failover_are_both_in_the_quota_record(self):
        self.usage_state()
        run = self.run_lane(FAKE_CODEX="limit", FAKE_CLAUDE="limit")
        self.assert_nothing_dispatched(run)
        self.assertEqual([c["bin"] for c in self.calls()], ["codex", "claude"])
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["quota"]["provider"]), ("quota-paused", "claude"))
        out = {p["provider"]: p for p in rec["quota"]["providers"]}
        self.assertEqual(sorted(out), ["claude", "codex"])
        self.assertTrue(out["codex"]["reset_epoch"] and out["claude"]["reset_epoch"])
        self.assertIn("usage limit", out["codex"]["cause"])
        summary = daily_batch.quota_paused({"queue": [{"repo": "demo", "outcome": rec["outcome"],
                                                       "quota": rec["quota"]}]})
        self.assertEqual(sorted((q["provider"], q["repos"]) for q in summary),
                         [("claude", ["demo"]), ("codex", ["demo"])])

    def test_planner_gated_between_pick_and_launch_is_not_called(self):
        self.usage_state()
        self.run_lane(FAKE_GATE_ON_SNAPSHOT="codex", FAKE_CLAUDE="plan")
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "orchestrator"], ["claude"])
        self.assertIn("planner codex went over its gate before launch — using claude", self.log)
        self.assertTrue(self.reached_barrier)
        self.assertEqual(self.record()["orchestrator"], "claude")

    def test_every_planner_gated_before_launch_is_a_quota_skip_naming_them(self):
        self.usage_state()
        run = self.run_lane(FAKE_GATE_ON_SNAPSHOT="codex claude")
        self.assert_nothing_dispatched(run)
        self.assertEqual(self.calls(), [])
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["orchestrator"], rec["quota"]["role"]),
                         ("skipped-quota", None, "orchestrator"))
        self.assertEqual(sorted(p["provider"] for p in rec["quota"]["providers"]), ["claude", "codex"])
        self.assertEqual(self.alert(), "")

    # ── malformed output is an error, still with no worker ───────────────────

    def test_malformed_planner_output_is_an_error_without_a_worker(self):
        for plan_mode, fake_plan in (("garbage", json.dumps(NO_WORK)), ("plan", '{"action": "launch_rockets"}'),
                                     ("plan", '["not", "an", "object"]')):
            with self.subTest(plan_mode=plan_mode, fake_plan=fake_plan):
                self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
                run = self.run_lane(FAKE_CLAUDE=plan_mode, FAKE_PLAN=fake_plan)
                self.assert_nothing_dispatched(run)
                rec = self.record()
                self.assertEqual(rec["outcome"], "error", rec)
                self.assertTrue(rec["detail"].startswith("orchestrator: "), rec["detail"])
                self.assertIsNone(rec["quota"])

    # ── a real no-work plan keeps its bookkeeping ────────────────────────────

    def test_valid_no_work_plan_runs_bookkeeping_and_saves_its_verdict(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertTrue(self.reached_barrier)
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "worker"], ["agent"])
        self.assertEqual(self.record()["outcome"], "blocked-no-item")
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], NO_WORK["summary"])

    def test_offline_deploy_snapshot_is_unknown_and_never_blocks_planning(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CLAUDE="plan")
        snap = json.loads((run / "deploy-snapshot.json").read_text())
        self.assertEqual(snap["repos"]["demo"]["status"], "unknown")
        self.assertIn("gh: offline in tests", snap["repos"]["demo"]["error"])
        self.assertNotIn("deploy snapshot failed", self.log)
        self.assertTrue(self.reached_barrier)
        self.assertEqual(self.record()["outcome"], "blocked-no-item")

    def test_contaminated_verdict_on_disk_is_discarded_and_the_repo_replanned(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        fp, deploys = self.dir / "fp.json", self.dir / "deploys.json"
        subprocess.run([sys.executable, str(self.root / "lib/deploysnap.py"), "--code-dir", str(self.code),
                        "--out", str(deploys)], env=self.env(), check=True, capture_output=True)
        subprocess.run([sys.executable, str(self.root / "lib/verdict.py"), "fingerprint",
                        "--snapshot", str(self.snapshot), "--deploys", str(deploys), "--out", str(fp)],
                       env=self.env(), check=True, capture_output=True)
        bad = json.loads((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        bad.update(decided_at=datetime.now().isoformat(timespec="seconds"), fingerprint=json.loads(fp.read_text()))
        self.verdict.write_text(json.dumps(bad))
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertIn("discarded the carried verdict from 20261013-103004", self.log)
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "orchestrator"], ["claude"])
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], NO_WORK["summary"])


if __name__ == "__main__":
    unittest.main()
