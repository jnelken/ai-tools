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
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import daily_batch  # noqa: E402
import planapproval  # noqa: E402
import runrecord  # noqa: E402
from usage import ROLE_PROVIDERS  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oct13"
NY = ZoneInfo("America/New_York")
FUTURE = int(time.time()) + 6 * 3600
LATER = FUTURE + 3 * 86400
BANNER = "You've hit your session limit · resets 11:50am (America/New_York)"
NO_WORK = {"action": "blocked_no_item", "outcome_token": "blocked-no-item", "repo": "demo",
           "blockers": [], "archives": [], "considered": [], "summary": "No work qualified for demo."}

FAKE = r'''#!{python}
import json, os, sys, time
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if name == "claude" and "-p" in args else (args[-1] if args else "")
role = "worker" if "WORKER.md" in prompt else "orchestrator"
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps({{"bin": name, "role": role, "args": args, "prompt": prompt,
                         "headless": os.environ.get("ADVANCE_ROADMAP_HEADLESS")}}) + "\n")
mode = os.environ.get(f"FAKE_{{name.upper()}}_{{role.upper()}}") or os.environ.get("FAKE_" + name.upper(), "plan")
session_id = os.environ.get(f"FAKE_{{name.upper()}}_SESSION_ID") or os.environ.get("FAKE_SESSION_ID")
if mode == "wait":
    if name == "codex" and session_id:
        print("session id: " + session_id, flush=True)
    time.sleep(30)
    sys.exit(1)
fence = "```ORCHESTRATOR_RESULT_JSON\n" + os.environ.get("FAKE_PLAN", "{{}}") + "\n```"
# What Codex echoes while it reads ORCHESTRATOR.md / WORKER.md: their examples, which are valid JSON.
label = "WORKER_RESULT_JSON" if role == "worker" else "ORCHESTRATOR_RESULT_JSON"
echo = "```" + label + "\n" + json.dumps({{**json.loads({no_work!r}), "outcome": "shipped",
                                          "summary": "ECHOED EXAMPLE"}}) + "\n```"
final = fence if role == "orchestrator" else "```WORKER_RESULT_JSON\n" + json.dumps(
    {{"outcome": "failed", "provider": "codex", "repo": "demo", "summary": "FINAL ANSWER"}}) + "\n```"
if name == "agent" and "You are reviewing a change" in prompt:
    # Cursor's review round under the default cursor-then-codex strategy: clean, so Codex gets the final pass.
    print(json.dumps({{"type": "result", "result": "No findings.\nVERDICT: clean",
                       "usage": {{"inputTokens": 1, "outputTokens": 1}}}}))
elif name == "agent":
    path = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("(raw JSON, no fence) to: "))
    json.dump({{"outcome": "bookkeeping", "provider": "cursor", "repo": "demo", "summary": "bookkeeping done"}},
              open(path, "w"))
    print(json.dumps({{"type": "result", "result": "done", "usage": {{"inputTokens": 1, "outputTokens": 1}}}}))
elif name == "codex":
    if session_id:
        print("session id: " + session_id, flush=True)
    out = args[args.index("-o") + 1] if "-o" in args else None
    if mode.startswith("echo-"):
        print("user\n" + prompt + "\n" + echo)
        if mode == "echo-limit":
            print("You've hit your usage limit · resets in 2h 30m")
            sys.exit(1)
        if mode == "echo-final" and out:
            open(out, "w").write(final)
        print("codex done")
        sys.exit(0)
    if mode == "limit":
        print("You've hit your usage limit · resets in 2h 30m")
        sys.exit(1)
    if out:
        open(out, "w").write(fence if mode == "plan" else "no fence here")
    print("codex done")
elif name == "claude":
    if mode == "raw":  # a fence printed outside the CLI's JSON is not its final answer
        print(fence)
        sys.exit(1)
    if mode == "limit":
        # Real Claude exits 0 with is_error:true; detection needs --result-json (not exit≠0).
        # Optional FAKE_UNGATE: clear named providers' gates so failover can reach them mid-run
        # (Codex became eligible while Claude was planning).
        for p in os.environ.get("FAKE_UNGATE", "").split():
            path = os.path.join(os.environ["ADVANCE_ROADMAP_ROOT"], "providers-usage.json")
            try:
                state = json.load(open(path))
            except (OSError, ValueError):
                continue
            prov = state.setdefault("providers", {{}}).setdefault(p, {{}})
            for window in ("weekly", "five_hour"):
                if isinstance(prov.get(window), dict):
                    prov[window]["used_pct"] = 0
            prov["last_limit_hit"] = None
            prov["available_for_orchestrator"] = True
            prov["available_for_worker"] = True
            json.dump(state, open(path, "w"))
        reply = json.load(open(os.environ["FAKE_LIMIT_FIXTURE"]))
        if session_id:
            reply["session_id"] = session_id
        print(json.dumps(reply))  # one line, as the CLI prints it
        sys.exit(0)
    if mode == "result-limit":  # the limit only in the worker's result file, no banner on stdout
        path = next(l.split(": ", 1)[1] for l in prompt.splitlines() if l.startswith("(raw JSON, no fence) to: "))
        json.dump({{"outcome": "limit_hit", "provider": "claude", "repo": "demo",
                    "limit_text": os.environ["FAKE_RESULT_LIMIT"]}}, open(path, "w"))
    result = fence if mode == "plan" else "I looked around and decided nothing."
    reply = {{"type": "result", "subtype": "success", "is_error": False, "result": result, "modelUsage": {{}}}}
    if session_id:
        reply["session_id"] = session_id
    if os.environ.get("FAKE_FINAL"):  # what the final answer says when it holds no fence (a sign-off turn)
        reply["result"] = os.environ["FAKE_FINAL"]
    print(json.dumps(reply))
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
                  "deploysnap.py", "linearscope.py", "lastfence.py", "runmemory.py", "partialtriage.py"):
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
            p.write_text(FAKE.format(python=sys.executable, no_work=json.dumps(NO_WORK)))
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

    def usage_state(self, codex=None, claude=None, cursor=None):
        providers = {"codex": codex or {}, "claude": claude or {}, "cursor": cursor or {}}
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
            # Ledger rows and planner transcripts stay in the sandbox, never in ~/.claude.
            "ADVANCE_ROADMAP_RUNS_DIR": str(self.dir / "memory" / "advance-roadmap-runs"),
            "ADVANCE_ROADMAP_CLAUDE_PROJECTS": str(self.dir / "claude-projects"),
            "ADVANCE_ROADMAP_CODEX_SESSIONS": str(self.dir / "codex-sessions"),
        })
        env.update(fake)
        return env

    def run_lane(self, cancel=False, **fake):
        """One daily-batch lane: release it at the barrier if it gets there, like daily_batch.py
        (or cancel it there, as a batch does when no worker has quota)."""
        env = self.env(**fake)
        run = self.root / "runs" / self.stamp
        proc = subprocess.Popen(["/bin/zsh", str(self.root / "run.sh")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.reached_barrier = False
        deadline = time.time() + 90
        while proc.poll() is None and time.time() < deadline:
            if (run / "batch-ready").exists() and not (run / "batch-release").exists():
                self.reached_barrier = True
                (run / ("batch-cancel" if cancel else "batch-release")).write_text("test\n")
            time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()
            self.fail("run.sh did not finish")
        self.log = (self.root / "logs" / f"run-{self.stamp}.log").read_text()
        return run

    def plan_accepted(self, run):
        """The lane took the planner's plan: it reached the batch barrier, or (a no-work result with
        nothing to post) skipped the barrier because no worker was waiting on it."""
        return self.reached_barrier or (run / "bookkeeping-skipped").exists()

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
        self.assertEqual([(c["bin"], c["role"]) for c in self.calls()], [("claude", "orchestrator")])
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
        self.assertTrue(self.plan_accepted(run))
        rec = self.record()
        self.assertEqual((rec["orchestrator"], rec["outcome"]), ("claude", "blocked-no-item"))
        self.assertIn("failing over to claude", self.log)
        self.assertTrue((run / "orchestrator-limit-codex.json").exists())

    def test_quota_failure_leaves_partial_triage_and_next_prompt_reuses_then_deletes_it(self):
        transcript = self.dir / "claude-projects" / "demo" / "partial-session.jsonl"
        transcript.parent.mkdir(parents=True)
        shutil.copyfile(HERE / "tests/fixtures/partial-triage/claude.jsonl", transcript)
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="limit", FAKE_SESSION_ID="partial-session")
        self.assertEqual(self.record()["outcome"], "quota-paused")
        triage = self.code / ".advance-roadmap" / "partial-triage-demo.md"
        self.assertTrue(triage.exists())
        self.assertIn("DEV-42", triage.read_text())
        self.assertIn("Ended: quota-paused", triage.read_text())

        self.usage_state()
        self.run_lane(FAKE_CODEX="plan")
        next_planner = [c for c in self.calls() if c["role"] == "orchestrator"][-1]
        self.assertIn("Partial triage from", next_planner["prompt"])
        self.assertIn("ended quota-paused", next_planner["prompt"])
        self.assertIn("don't redo those reads unless the fingerprint says they changed", next_planner["prompt"])
        self.assertFalse(triage.exists())

    def test_completed_repo_null_result_deletes_digest_by_target_repo(self):
        triage = self.code / ".advance-roadmap" / "partial-triage-demo.md"
        triage.parent.mkdir(parents=True)
        triage.write_text("old findings\n")
        plan = {**NO_WORK, "repo": None}
        run = self.run_lane(FAKE_CODEX="plan", FAKE_PLAN=json.dumps(plan))
        self.assertTrue(self.plan_accepted(run))
        self.assertFalse(triage.exists(), "a valid targeted no-work result owns this repo's digest")

    def test_thread_two_neither_prompts_with_nor_overwrites_partial_triage(self):
        triage = self.code / ".advance-roadmap" / "partial-triage-demo.md"
        triage.parent.mkdir(parents=True)
        triage.write_text("thread one findings\n")
        self.run_lane(FAKE_CODEX="plan", ADVANCE_ROADMAP_THREAD="2")
        planner = [c for c in self.calls() if c["role"] == "orchestrator"][-1]
        self.assertNotIn("Partial triage from", planner["prompt"])
        self.assertEqual(triage.read_text(), "thread one findings\n")

    def test_failover_digest_combines_each_failed_attempt(self):
        codex = self.dir / "codex-sessions" / "2026" / "10" / "rollout-codex-attempt.jsonl"
        claude = self.dir / "claude-projects" / "demo" / "claude-attempt.jsonl"
        codex.parent.mkdir(parents=True)
        claude.parent.mkdir(parents=True)
        codex.write_text((HERE / "tests/fixtures/partial-triage/codex-rollout.jsonl").read_text()
                         .replace("DEV-42", "DEV-901"))
        claude.write_text((HERE / "tests/fixtures/partial-triage/claude.jsonl").read_text()
                          .replace("DEV-42", "DEV-902"))
        self.usage_state()
        self.run_lane(FAKE_CODEX="limit", FAKE_CLAUDE="nofence",
                      FAKE_CODEX_SESSION_ID="codex-attempt", FAKE_CLAUDE_SESSION_ID="claude-attempt")
        digest = (self.code / ".advance-roadmap" / "partial-triage-demo.md").read_text()
        self.assertIn("Attempt 1: codex (ended quota-paused)", digest)
        self.assertIn("Attempt 2: claude (ended error)", digest)
        self.assertIn("DEV-901", digest)
        self.assertIn("DEV-902", digest)
        self.assertLessEqual(len(digest.encode()), 20 * 1024)

    def test_terminated_planner_leaves_codex_rollout_as_planning_timeout(self):
        env = self.env(FAKE_CODEX="wait", FAKE_CODEX_SESSION_ID="codex-timeout-id")
        proc = subprocess.Popen(["/bin/zsh", str(self.root / "run.sh")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        calls = self.dir / "calls.jsonl"
        deadline = time.time() + 20
        while not calls.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(calls.exists(), "fake Codex did not start")
        stdout = self.root / "runs" / self.stamp / "orchestrator-stdout.txt"
        while (not stdout.exists() or "session id: codex-timeout-id" not in stdout.read_text()) and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn("session id: codex-timeout-id", stdout.read_text())
        rollout = self.dir / "codex-sessions" / "2026" / "10" / "rollout-fixture-codex-timeout-id.jsonl"
        rollout.parent.mkdir(parents=True)
        shutil.copyfile(HERE / "tests/fixtures/partial-triage/codex-rollout.jsonl", rollout)
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=20)
        triage = self.code / ".advance-roadmap" / "partial-triage-demo.md"
        self.assertTrue(triage.exists())
        self.assertIn("Ended: planning-timeout", triage.read_text())
        self.assertIn("DEV-42", triage.read_text())

    def test_claude_json_limit_does_not_poison_failover_codex_via_stale_json(self):
        # Codex gated → Claude plans and hits is_error JSON; FAKE_UNGATE opens Codex for failover.
        # Codex returns no fence (exit 0). Stale Claude .json must not make Codex look limited.
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CLAUDE="limit", FAKE_UNGATE="codex", FAKE_CODEX="nofence")
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "orchestrator"],
                         ["claude", "codex"])
        self.assert_nothing_dispatched(run)
        rec = self.record()
        self.assertEqual(rec["outcome"], "error", rec)
        self.assertIsNone(rec.get("quota"))
        self.assertTrue((run / "orchestrator-limit-claude.json").exists())
        self.assertFalse((run / "orchestrator-limit-codex.json").exists(),
                         "stale Claude .json must not gate the Codex failover attempt")

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
        run = self.run_lane(FAKE_GATE_ON_SNAPSHOT="codex", FAKE_CLAUDE="plan")
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "orchestrator"], ["claude"])
        self.assertIn("planner codex went over its gate before launch — using claude", self.log)
        self.assertTrue(self.plan_accepted(run))
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

    # ── one quota record for every role ──────────────────────────────────────

    def planner_row(self):
        self.run_lane(FAKE_CLAUDE="limit")
        return self.record()

    def worker_row(self):
        plan = {"action": "dispatch_worker", "outcome_token": None, "repo": "demo", "item": "DEV-1 thing",
                "branch": "roadmap/thing", "worker_mode": "standard", "work_kind": "feature",
                "archives": [], "blockers": [], "considered": [], "summary": "do the thing"}
        self.run_lane(FAKE_CLAUDE="plan", FAKE_CLAUDE_WORKER="limit", FAKE_PLAN=json.dumps(plan))
        self.assertEqual([c["bin"] for c in self.calls()], ["claude", "claude"])
        return self.record()

    def approver_row(self):
        req = {"repo": "demo", "plans": [{"stamp": "p1", "thread": 1, "touchpoints": ["a"]},
                                         {"stamp": "p2", "thread": 2, "touchpoints": ["b"]}]}
        runners = {"codex": lambda prompt, code: self.fail("a gated approver was called"),
                   "claude": lambda prompt, code: ("", BANNER)}
        with mock.patch.dict(os.environ, self.env(), clear=True):
            result = planapproval.approve(str(self.root), req, "DOC.md", str(self.code), runners=runners)
        self.assertEqual(result["decisions"][1]["verdict"], "defer")
        # daily_batch.py saves it beside the deferred thread; run.sh records that thread's skip.
        run = self.root / "runs" / self.stamp
        run.mkdir(parents=True)
        (run / "quota-gates.json").write_text(json.dumps(result["quota"]))
        runrecord.main(["--root", str(self.root), "append", "--stamp", self.stamp, "--status", "skipped-quota",
                        "--detail", "plan approval deferred", "--quota-gates", str(run / "quota-gates.json")])
        return self.record()

    def test_every_provider_in_the_role_is_in_the_record_when_the_last_one_runs_out(self):
        # Every provider but the last is over its gate; the last one is tried and hits its limit.
        # Whatever chain the lane was handed, the record and the batch summary name them all.
        over_codex = {"weekly": {"used_pct": 85, "reset_epoch": FUTURE}}
        over_cursor = {"included": {"auto_pct": 100, "reset_epoch": LATER}}
        cases = [("orchestrator", dict(codex=over_codex), self.planner_row, {"codex": FUTURE}),
                 ("worker", dict(codex=over_codex, cursor=over_cursor), self.worker_row,
                  {"cursor": LATER, "codex": FUTURE}),
                 ("approver", dict(codex=over_codex), self.approver_row, {"codex": FUTURE})]
        for role, gated, row, gated_resets in cases:
            with self.subTest(role=role):
                (self.dir / "calls.jsonl").unlink(missing_ok=True)
                self.usage_state(**gated)
                rec = row()
                self.assertIn(rec["outcome"], ("quota-paused", "skipped-quota"), rec)
                self.assertEqual((rec["quota"]["role"], rec["quota"]["provider"]), (role, "claude"))
                by = {p["provider"]: p for p in rec["quota"]["providers"]}
                self.assertEqual(list(by), list(ROLE_PROVIDERS[role]))
                self.assertEqual({n: (p["state"], p["reset_epoch"]) for n, p in by.items() if n != "claude"},
                                 {n: ("over", r) for n, r in gated_resets.items()})
                self.assertEqual(by["claude"]["state"], "limited")
                self.assertEqual(by["claude"]["cause"], BANNER)
                self.assertEqual(datetime.fromtimestamp(by["claude"]["reset_epoch"], NY).strftime("%H:%M"), "11:50")
                summary = daily_batch.quota_paused({"queue": [{"repo": "demo", "outcome": rec["outcome"],
                                                               "quota": rec["quota"]}]})
                self.assertEqual({q["provider"]: (q["repos"], q["reset_epoch"]) for q in summary},
                                 {n: (["demo"], p["reset_epoch"]) for n, p in by.items()})

    # ── a failing usage.py helper never loses an observed limit ──────────────

    FAILING_USAGE = '''import sys
from pathlib import Path
if __name__ == "__main__" and {{"record-limit", "quota-record"}} & set(sys.argv[1:]):
    with open(Path(__file__).with_name("failed-helpers.log"), "a") as f:
        f.write(next(a for a in sys.argv[1:] if a in ("record-limit", "quota-record")) + "\\n")
    sys.stderr.write("usage.py: simulated helper failure\\n")
    sys.exit(1)
__file__ = {real!r}
exec(compile(open(__file__).read(), __file__, "exec"))
'''

    def fail_usage_helpers(self):
        """record-limit and quota-record exit 1; every other usage.py call (and an import) is real."""
        stub = self.root / "lib" / "usage.py"
        stub.unlink()
        stub.write_text(self.FAILING_USAGE.format(real=str(HERE / "lib" / "usage.py")))
        return self.root / "lib" / "failed-helpers.log"

    def worker_plan(self):
        return json.dumps({"action": "dispatch_worker", "outcome_token": None, "repo": "demo", "item": "DEV-1 thing",
                           "branch": "roadmap/thing", "worker_mode": "standard", "work_kind": "feature",
                           "archives": [], "blockers": [], "considered": [], "summary": "do the thing"})

    def reviewer_row(self):
        (self.root / "review.sh").symlink_to(HERE / "review.sh")
        repo = self.dir / "reviewed"
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
        # Fixture commits must not depend on the user's signing setup or hooks.
        hermetic = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], env=hermetic, check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], env=hermetic, check=True)
        subprocess.run([*git, "checkout", "-q", "-b", "roadmap/x"], env=hermetic, check=True)
        (repo / "x.py").write_text("x = 1\n")
        subprocess.run([*git, "add", "x.py"], env=hermetic, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "feat"], env=hermetic, check=True)
        env = self.env(FAKE_CODEX="limit")
        run = self.root / "runs" / self.stamp
        run.mkdir(parents=True)
        # Codex (Cursor's reviewer) answers only its banner; Claude reviews instead.
        subprocess.run(["/bin/zsh", str(self.root / "review.sh"), "--repo", str(repo), "--worker", "cursor",
                        "--round", "1", "--log", str(run / "reviews.jsonl"), "--base", "main"],
                       env=env, capture_output=True, check=False)
        (run / "worker-result.json").write_text(json.dumps({"outcome": "failed", "provider": "cursor"}))
        runrecord.main(["--root", str(self.root), "append", "--stamp", self.stamp, "--status", "ran",
                        "--worker-result", str(run / "worker-result.json")])
        rec = self.record()
        self.assertEqual([h["provider"] for h in rec["review_limits"]], ["codex"], rec)
        return rec["review_limits"][0]

    def test_reviewer_fixture_ignores_the_users_commit_signing(self):
        hostile = self.dir / "hostile.gitconfig"
        hostile.write_text("[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n"
                           "[gpg]\n\tprogram = false\n[gpg \"ssh\"]\n\tprogram = false\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(hostile)}):
            self.assertEqual(self.reviewer_row()["provider"], "codex")

    def batch_skip_row(self):
        self.env()
        run = self.root / "runs" / self.stamp
        run.mkdir(parents=True)
        batch = daily_batch.Batch.__new__(daily_batch.Batch)
        batch.state = {"queue": [{"stamp": self.stamp, "status": "ready", "action": "dispatch_worker"}]}
        with mock.patch.object(daily_batch, "ROOT", self.root), \
                mock.patch.object(daily_batch.shared, "LIB", self.root / "lib"):
            batch.defer_ready("no worker quota available", batch.quota_record())
        runrecord.main(["--root", str(self.root), "append", "--stamp", self.stamp, "--status", "skipped-quota",
                        "--detail", "daily batch deferred", "--quota-gates", str(run / "quota-gates.json")])
        return self.record()["quota"]

    def test_failing_usage_helpers_never_lose_an_observed_limit(self):
        # Each path observes a limit (banner, result-only limit_hit, or a gate) and then calls a
        # usage.py helper that fails. The run record must still carry the provider, cause and reset.
        over_codex = {"weekly": {"used_pct": 85, "reset_epoch": FUTURE}}
        over_cursor = {"included": {"auto_pct": 100, "reset_epoch": LATER}}
        over_claude = {"five_hour": {"used_pct": 90, "reset_epoch": LATER}}
        def at_1150(reset):
            return datetime.fromtimestamp(reset, NY).strftime("%H:%M") == "11:50"

        def lane(**fake):
            def row():
                self.run_lane(**fake)
                return self.record()["quota"]
            return row

        cases = [
            ("planner banner", dict(codex=over_codex), lane(FAKE_CLAUDE="limit"),
             "claude", BANNER, at_1150, {"record-limit", "quota-record"}),
            ("planner skipped over its gate", dict(codex=over_codex, claude=over_claude), lane(),
             "codex", "weekly 85%", lambda r: r == FUTURE, {"quota-record"}),
            ("worker banner", dict(codex=over_codex, cursor=over_cursor),
             lane(FAKE_CLAUDE="plan", FAKE_CLAUDE_WORKER="limit", FAKE_PLAN=self.worker_plan()),
             "claude", BANNER, at_1150, {"record-limit", "quota-record"}),
            ("worker result-only limit_hit", dict(codex=over_codex, cursor=over_cursor),
             lane(FAKE_CLAUDE="plan", FAKE_CLAUDE_WORKER="result-limit", FAKE_PLAN=self.worker_plan(),
                  FAKE_RESULT_LIMIT=BANNER),
             "claude", BANNER, at_1150, {"record-limit", "quota-record"}),
            ("reviewer banner", {}, self.reviewer_row,
             "codex", "You've hit your usage limit · resets in 2h 30m",
             lambda r: time.time() + 2 * 3600 < r <= time.time() + 3 * 3600, {"record-limit"}),
            ("approver banner", dict(codex=over_codex), lambda: self.approver_row()["quota"],
             "claude", BANNER, at_1150, {"record-limit", "quota-record"}),
            # Every worker out with none stopped on: the one that frees up first, the others' reasons too.
            ("daily batch skipped-quota", dict(codex=over_codex, cursor=over_cursor, claude=over_claude),
             self.batch_skip_row, "codex", "cursor: auto pool 100%", lambda r: r == FUTURE, {"quota-record"}),
        ]
        for name, gated, row, provider, cause, reset_ok, failed in cases:
            with self.subTest(path=name):
                self.tearDown()
                self.setUp()
                failures = self.fail_usage_helpers()
                self.usage_state(**gated)
                quota = row()
                self.assertIsNotNone(quota, name)
                self.assertEqual(quota["provider"], provider, quota)
                self.assertIn(cause, quota["cause"], quota)
                self.assertTrue(quota["reset_epoch"] and reset_ok(quota["reset_epoch"]), quota)
                self.assertTrue(quota["reset_at"], quota)
                self.assertTrue(failed <= set(failures.read_text().split()),
                                f"{name}: expected {failed} to fail, log: {failures.read_text()}")

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

    def test_raw_claude_fence_outside_its_json_answer_is_not_a_plan(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CLAUDE="raw")
        self.assert_nothing_dispatched(run)
        rec = self.record()
        self.assertEqual(rec["outcome"], "error", rec)
        self.assertIn("no final answer", rec["detail"])

    # ── a plan is read only from the planner's final answer ──────────────────

    def test_codex_echoing_orchestrator_examples_then_hitting_quota_dispatches_nothing(self):
        # The transcript holds a valid blocked_no_item example from ORCHESTRATOR.md, then the banner.
        self.usage_state(claude={"five_hour": {"used_pct": 90, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CODEX="echo-limit")
        self.assert_nothing_dispatched(run)
        self.assertEqual([(c["bin"], c["role"]) for c in self.calls()], [("codex", "orchestrator")])
        self.assertIn("ECHOED EXAMPLE", (run / "orchestrator-stdout.txt").read_text())
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["quota"]["provider"]), ("quota-paused", "codex"), rec)
        self.assertNotIn("ECHOED EXAMPLE", json.dumps(rec))

    def test_codex_echoing_orchestrator_examples_with_no_final_answer_is_an_error(self):
        self.usage_state(claude={"five_hour": {"used_pct": 90, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CODEX="echo-silent")
        self.assert_nothing_dispatched(run)
        rec = self.record()
        self.assertEqual(rec["outcome"], "error", rec)
        self.assertIn("no final answer", rec["detail"])
        self.assertIsNone(rec["quota"])

    def test_codex_echoing_orchestrator_examples_plans_from_its_final_answer(self):
        self.usage_state(claude={"five_hour": {"used_pct": 90, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CODEX="echo-final")
        self.assertTrue(self.plan_accepted(run))
        self.assertEqual(self.record()["outcome"], "blocked-no-item")
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], NO_WORK["summary"])

    def test_codex_worker_result_comes_only_from_its_final_answer(self):
        # Cursor is over its gate, so Codex works. Its transcript echoes WORKER.md's shipped example.
        over_cursor = {"included": {"auto_pct": 100, "reset_epoch": LATER}}
        for mode, source, summary in (("echo-silent", None, None), ("echo-final", "stdout-fence", "FINAL ANSWER")):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.usage_state(cursor=over_cursor)
                run = self.run_lane(FAKE_CODEX_ORCHESTRATOR="plan", FAKE_CODEX_WORKER=mode,
                                    FAKE_PLAN=self.worker_plan())
                self.assertEqual([c["bin"] for c in self.calls()], ["codex", "codex"])
                rec = self.record()
                self.assertNotEqual(rec["outcome"], "shipped", rec)
                self.assertEqual(rec["worker_result_source"], source, rec)
                if summary:
                    self.assertEqual(json.loads((run / "worker-result.json").read_text())["summary"], summary)
                else:
                    self.assertFalse((run / "worker-result.json").exists())

    # ── a real no-work plan keeps its bookkeeping ────────────────────────────

    def test_no_work_plan_with_a_new_blocker_runs_bookkeeping_and_saves_its_verdict(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        plan = {**NO_WORK, "blockers": [{"repo": "demo", "linear_id": "DEV-7", "detail": "needs a decision"}]}
        self.run_lane(FAKE_CLAUDE="plan", FAKE_PLAN=json.dumps(plan))
        self.assertTrue(self.reached_barrier)
        self.assertEqual([c["bin"] for c in self.calls() if c["role"] == "worker"], ["agent"])
        self.assertEqual(self.record()["outcome"], "blocked-no-item")
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], NO_WORK["summary"])
        self.assertFalse((self.root / "memory").exists(), "the worker owns the ledger row, not run.sh")

    def ledger(self):
        return (self.root.parent / "memory" / "advance-roadmap-runs" / "demo.md")

    def test_noop_no_work_plan_skips_the_worker_writes_the_ledger_row_and_saves_the_verdict(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CLAUDE="plan")
        self.assertFalse(self.reached_barrier, "nothing waits on a worker, so nothing waits at the barrier")
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator"])
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["status"]), ("blocked-no-item", "ran"), rec)
        # Step 8's row, in the worker's own format, so "did the previous run finish?" finds it.
        row = [l for l in self.ledger().read_text().splitlines() if self.stamp in l]
        self.assertEqual(len(row), 1, self.ledger().read_text())
        self.assertRegex(row[0], rf"^\| `{self.stamp}` \| \d{{4}}-\d\d-\d\d \| demo \(no-op[^|]*\) \| blocked-no-item \|$")
        has = subprocess.run([sys.executable, str(self.root / "lib/runmemory.py"), "has-stamp", "--stamp", self.stamp],
                             env=self.env(), capture_output=True, text=True)
        self.assertEqual((has.returncode, has.stdout.strip()), (0, "demo"))
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], NO_WORK["summary"])
        self.assertNotIn("bookkeeping_pending", json.loads(self.verdict.read_text()))
        self.assertTrue((run / "bookkeeping-skipped").exists())
        # The next lane finds nothing moved and skips planning altogether.
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertEqual(self.record()["outcome"], "skipped-unchanged", self.record())
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator"])

    def test_a_canceled_no_work_plan_keeps_its_verdict_and_tomorrow_skips_it(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        plan = {**NO_WORK, "blockers": [{"repo": "demo", "linear_id": "DEV-7", "detail": "needs a decision"}]}
        self.run_lane(cancel=True, FAKE_CLAUDE="plan", FAKE_PLAN=json.dumps(plan))
        self.assertTrue(self.reached_barrier)
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator"])
        self.assertEqual(self.record()["outcome"], "skipped-quota")
        kept = json.loads(self.verdict.read_text())
        self.assertTrue(kept["bookkeeping_pending"])
        self.assertEqual(kept["blockers"][0]["linear_id"], "DEV-7")
        # Its blockers were never posted: the next run re-plans (cheaply) rather than skipping them.
        self.run_lane(FAKE_CLAUDE="plan", FAKE_PLAN=json.dumps(plan))
        self.assertNotEqual(self.record()["outcome"], "skipped-unchanged")
        self.assertIn("never ran", self.log)
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator", "orchestrator", "worker"])
        self.assertNotIn("bookkeeping_pending", json.loads(self.verdict.read_text()))

    def test_a_canceled_dispatch_plan_saves_no_verdict(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.verdict.write_text(json.dumps({"stamp": "20261012-103001", "decided_at": "2026-10-28T10:00:00",
                                            "outcome_token": "blocked-no-item", "fingerprint": {}}))
        before = self.verdict.read_text()
        self.run_lane(cancel=True, FAKE_CLAUDE="plan", FAKE_PLAN=self.worker_plan())
        self.assertEqual(self.record()["outcome"], "skipped-quota")
        self.assertEqual(self.verdict.read_text(), before)

    # ── headless planners ────────────────────────────────────────────────────

    def test_headless_marker_reaches_every_model_call_and_planners_default_to_sonnet_medium(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="plan")
        planner = self.calls()[0]
        self.assertEqual(planner["headless"], "1")
        args = planner["args"]
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(args[args.index("--effort") + 1], "medium")
        self.assertNotIn("advisor", " ".join(args), "--disallowedTools advisor does not stop the advisor server tool")

    def test_planner_model_and_effort_overrides_still_win(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="plan", ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL="claude-opus-5",
                      ADVANCE_ROADMAP_ORCH_CLAUDE_EFFORT="high")
        args = self.calls()[0]["args"]
        self.assertEqual((args[args.index("--model") + 1], args[args.index("--effort") + 1]), ("claude-opus-5", "high"))

    def test_planner_reads_its_repos_snapshot_while_the_fingerprint_keeps_the_whole_queue(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        def issue(id_, labels, state_type="unstarted"):
            return {"id": id_, "title": id_, "description": "", "state": "Todo", "state_type": state_type,
                    "priority": 2, "labels": labels, "blocked_by": [], "human_comments_sha": "-"}
        self.snapshot.write_text(json.dumps({"ok": True, "issues": [
            issue("DEV-1", ["repo/demo"]), issue("DEV-2", ["repo/other"]), issue("DEV-3", []),
            issue("DEV-4", ["repo/demo"], "completed")]}))
        run = self.run_lane(FAKE_CLAUDE="plan")
        planner_snap = json.loads((run / "linear-snapshot-planner.json").read_text())
        self.assertEqual([i["id"] for i in planner_snap["issues"]], ["DEV-1", "DEV-3"])
        self.assertIn(str(run / "linear-snapshot-planner.json"), self.calls()[0]["prompt"])
        self.assertEqual(len(json.loads((run / "linear-snapshot.json").read_text())["issues"]), 4)
        self.assertEqual(sorted(json.loads((run / "fingerprint.json").read_text())["issues"]),
                         ["DEV-1", "DEV-3", "DEV-4"])

    # ── a pending plan only disables its own repo's verdict check ───────────

    def test_pending_plans_of_other_repos_do_not_disable_this_repos_verdict(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="plan")  # carries a no-op verdict for demo
        pending = self.root / "pending"
        pending.mkdir(exist_ok=True)
        for other in ("pending-plan-other.json", "pending-plan-demo2.json", "pending-plan-other.t2.json"):
            (pending / other).write_text("{}")
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertEqual(self.record()["outcome"], "skipped-unchanged", self.record())
        (pending / "pending-plan-demo.t2.json").write_text("{}")  # this repo's second thread: work, not a verdict
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertNotEqual(self.record()["outcome"], "skipped-unchanged")
        (pending / "pending-plan-demo.t2.json").unlink()
        (pending / "pending-plan-demo.json").write_text("{}")
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertNotEqual(self.record()["outcome"], "skipped-unchanged")

    def test_another_repos_commit_leaves_this_repos_carried_verdict_valid(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        for name in ("demo2",):
            subprocess.run(["git", "init", "-q", str(self.code / name)], check=True)
            subprocess.run(["git", "-C", str(self.code / name), "remote", "add", "origin",
                            f"git@github.com:jnelken/{name}.git"], check=True)
        self.run_lane(FAKE_CLAUDE="plan")
        hermetic = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.code / "demo2"),
                        "commit", "-q", "--allow-empty", "-m", "elsewhere"], env=hermetic, check=True)
        self.run_lane(FAKE_CLAUDE="plan")
        self.assertEqual(self.record()["outcome"], "skipped-unchanged", self.record())

    # ── Oct 27: a finished triage must survive the sign-off turn ─────────────

    def write_oct27_transcript(self, sid="oct27-session"):
        projects = self.dir / "claude-projects" / "-tmp-code"
        projects.mkdir(parents=True, exist_ok=True)
        (projects / f"{sid}.jsonl").write_text((FIXTURES.parent / "oct27" / "claude-planner-transcript.jsonl").read_text())
        return sid

    def test_oct27_a_signoff_turn_after_the_fence_keeps_the_planners_triage(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        sid = self.write_oct27_transcript()
        self.run_lane(FAKE_CLAUDE="plan", FAKE_SESSION_ID=sid, FAKE_FINAL="💬 No item for demo",
                      FAKE_PLAN=json.dumps({**NO_WORK, "repo": "demo", "considered": ["WRONG: the live fence"]}))
        self.assertIn("plan recovered from the planner's transcript", self.log)
        rec = self.record()
        self.assertEqual(rec["outcome"], "blocked-no-item", rec)
        saved = json.loads(self.verdict.read_text())
        # The transcript's real fence, not the prompt echo's dispatch example nor the sidechain's.
        self.assertEqual(saved["considered"], ["DEV-7 needs a decision"])
        self.assertEqual(saved["summary"], "No work qualified for demo.")
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator"], "a no-op needs no worker")

    def test_new_repo_issue_during_bookkeeping_still_changes_the_next_verdict_check(self):
        self.usage_state()
        issue = lambda id: {'id': id, 'title': id, 'description': '', 'state': 'Todo',
                            'state_type': 'unstarted', 'priority': 2, 'labels': ['repo/demo'], 'blocked_by': []}
        before = {'ok': True, 'issues': [issue('DEV-7')]}
        after = {'ok': True, 'issues': [issue('DEV-7'), issue('DEV-8')]}
        self.snapshot.write_text(json.dumps(before))
        # The batch preflight is immutable; the post-worker fetch discovers the outside issue.
        (self.root / 'lib/linearsnap.py').write_text(
            "import json, sys\njson.dump(" + repr(after) + ", open(sys.argv[-1], 'w'))\n")
        plan = {**NO_WORK, 'blockers': [{'repo': 'demo', 'linear_id': 'DEV-7', 'detail': 'needs input'}]}
        run = self.run_lane(FAKE_PLAN=json.dumps(plan))
        self.assertTrue(self.reached_barrier, 'bookkeeping must actually run')
        check = subprocess.run([sys.executable, str(HERE / 'lib/verdict.py'), 'check',
                                '--file', str(self.verdict), '--fingerprint', str(run / 'fingerprint-post.json'),
                                '--changes', str(run / 'next-changes.json'), '--root', str(self.root)],
                               env={**os.environ, 'PERSONAL_CODE_DIR': str(self.code)},
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn('issue:DEV-8', check.stdout)

    def test_limit_killed_planner_cannot_recover_a_transcript_fence(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        sid = self.write_oct27_transcript()
        run = self.run_lane(FAKE_CLAUDE="limit", FAKE_SESSION_ID=sid)
        self.assert_nothing_dispatched(run)
        self.assertEqual(self.record()['outcome'], 'quota-paused')
        hit = json.loads((self.root / 'providers-usage.json').read_text())['providers']['claude']['last_limit_hit']
        self.assertIsNotNone(hit)
        self.run_lane(FAKE_CLAUDE="limit", FAKE_SESSION_ID=sid)
        self.assertEqual(self.record()['outcome'], 'skipped-quota')

    def test_limit_killed_planner_with_a_fence_still_fails_over(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        sid = self.write_oct27_transcript()
        run = self.run_lane(FAKE_CLAUDE="limit", FAKE_SESSION_ID=sid, FAKE_UNGATE="codex", FAKE_CODEX="plan")
        self.assertTrue(self.plan_accepted(run))
        self.assertEqual([c['bin'] for c in self.calls() if c['role'] == 'orchestrator'], ['claude', 'codex'])
        self.assertTrue((run / 'orchestrator-limit-claude.json').exists())
        self.assertEqual(self.record()['orchestrator'], 'codex')

    def test_oct27_without_a_transcript_the_sign_off_is_still_an_error(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        self.run_lane(FAKE_CLAUDE="plan", FAKE_SESSION_ID="no-such-session", FAKE_FINAL="💬 No item")
        rec = self.record()
        self.assertEqual(rec["outcome"], "error", rec)
        self.assertFalse(self.verdict.exists())

    def test_offline_deploy_snapshot_is_unknown_and_never_blocks_planning(self):
        self.usage_state(codex={"weekly": {"used_pct": 85, "reset_epoch": FUTURE}})
        run = self.run_lane(FAKE_CLAUDE="plan")
        snap = json.loads((run / "deploy-snapshot.json").read_text())
        self.assertEqual(snap["repos"]["demo"]["status"], "unknown")
        self.assertIn("gh: offline in tests", snap["repos"]["demo"]["error"])
        self.assertNotIn("deploy snapshot failed", self.log)
        self.assertEqual([c["role"] for c in self.calls()], ["orchestrator"])  # planned, with no deploy status
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
