"""followup.py report-only (DEV-232 B2). Temp ADVANCE_ROADMAP_ROOT; one test per behavior."""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADV = HERE.parent
FOLLOWUP = ADV / "followup.py"
STUBS = HERE / "followup_stubs.py"
FIXTURES = HERE / "fixtures" / "oct13"
MARKER_LINE = "# TODO(advance-roadmap review): reject an empty listing before saving"
sys.path.insert(0, str(ADV))
sys.path.insert(0, str(ADV / "lib"))
import followscan  # noqa: E402
import daily_batch  # noqa: E402


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.code = t / "root", t / "code"
        (self.root / "lib").mkdir(parents=True)
        self.code.mkdir()
        self.env = {**{k: v for k, v in os.environ.items()
                       if not k.startswith(("ADVANCE_ROADMAP_", "GIT_", "SUPERSET_"))},
                    "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.code),
                    "ADVANCE_ROADMAP_SUPERSET": "0",
                    "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "0", "ADVANCE_ROADMAP_FOLLOWUP_POLL_S": "0.2",
                    "ADVANCE_ROADMAP_FOLLOWUP_WORKTREES": str(t / "worktrees"),
                    "AI_TOOLS_HOME": str(t / "no-ai-tools"),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        (self.root / "lib" / "usage.py").write_text(
            f"import runpy, sys\nsys.argv = [{str(STUBS)!r}, 'usage', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUBS)!r}, run_name='__main__')\n")
        self.make_repo("demo")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              check=True, env=self.env).stdout.strip()

    def make_repo(self, name):
        bare = Path(self.tmp.name) / "remotes" / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=self.env)
        repo = self.code / name
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=self.env)
        url = f"git@github.com:jnelken/{name}.git"
        self.git(repo, "remote", "add", "origin", url)
        self.git(repo, "config", f"url.{bare}.insteadOf", url)
        (repo / "app.py").write_text("print('hi')\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "init")
        self.git(repo, "push", "-q", "-u", "origin", "main")
        return repo

    def shipped_item(self, stamp, outcome="shipped", marker=True, repo="demo"):
        r = self.code / repo
        self.git(r, "checkout", "-q", "-b", f"roadmap/{stamp}")
        (r / f"feature_{stamp}.py").write_text((MARKER_LINE + "\n" if marker else "") + "def save(): pass\n")
        self.git(r, "add", "-A")
        self.git(r, "commit", "-qm", "feature")
        self.git(r, "checkout", "-q", "main")
        self.git(r, "merge", "-q", "--no-ff", "-m", "merge", f"roadmap/{stamp}")
        self.git(r, "push", "-q", "origin", "main")
        sha = self.git(r, "rev-parse", "HEAD")
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "worker-result.json").write_text(json.dumps({
            "outcome": outcome, "repo": repo, "item": f"Feature {stamp}", "merge_commit": sha,
            "review": {"reviewer": "codex", "rounds": 2, "unresolved": 1 if marker else 0},
            "deploy": {"status": "success"}}, indent=1) + "\n")
        return sha

    def run_followup(self, *args, env=None, check=True):
        p = subprocess.run([sys.executable, str(FOLLOWUP), *args], capture_output=True, text=True,
                           timeout=180, env={**self.env, **(env or {})})
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def state(self, key):
        return json.loads((self.root / "followups" / key / "state.json").read_text())

    def test_shipped_item_queues_cleanup_idempotently(self):
        run = self.root / "runs" / "20261014-010000"
        run.mkdir(parents=True)
        (run / "worker-result.json").write_text(json.dumps({"outcome": "failed", "repo": "demo"}))
        self.assertIn("recoverable work",
                      self.run_followup("hook", "item-done", "--stamp", "20261014-010000").stdout)
        self.shipped_item("20261014-050000", outcome="shipped-deploy-failed")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000", "--origin", "batch-x")
        again = self.run_followup("hook", "item-done", "--stamp", "20261014-050000", "--origin", "batch-x")
        self.assertIn("already queued", again.stdout)
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "queued")
        self.assertEqual(st.get("mode"), "report-only")

    def test_recursion_suppressed_by_env_and_marker(self):
        self.shipped_item("20261014-050000")
        out = self.run_followup("hook", "item-done", "--stamp", "20261014-050000",
                                env={"ADVANCE_ROADMAP_FOLLOWUP": "1"}).stdout
        self.assertIn("recursion suppressed", out)
        (self.root / "runs" / "20261014-050000" / "followup-child").write_text("x\n")
        self.assertIn("recursion suppressed",
                      self.run_followup("hook", "item-done", "--stamp", "20261014-050000").stdout)
        self.assertFalse((self.root / "followups").exists() and
                         list((self.root / "followups").glob("*/state.json")))

    def test_cleanup_waits_for_repo_then_reports_markers(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        lock = self.root / "repo-locks" / "demo.t2.lock"
        lock.parent.mkdir(parents=True)
        with open(lock, "a") as held:
            fcntl.lockf(held, fcntl.LOCK_EX)
            out = self.run_followup("drain").stdout
        self.assertIn("demo.t2.lock is held", out)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "waiting")
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["findings"])
        self.assertFalse(list((self.root / "runs" / "20261014-050000").glob("*.excerpt")))

    def test_tooling_waits_indefinitely_no_age_bypass(self):
        self.run_followup("hook", "run-end", "--origin", "batch-old", "--stamp", "s1")
        self.shipped_item("s1")
        self.run_followup("hook", "item-done", "--stamp", "s1")
        (self.root / "runs.jsonl").write_text(json.dumps(
            {"stamp": "s1", "outcome": "shipped", "detail": "PR #1"}) + "\n")
        tooling = self.state("tooling-batch-old")
        tooling["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 13 * 3600))
        (self.root / "followups" / tooling["key"] / "state.json").write_text(json.dumps(tooling))
        lock = self.root / "repo-locks" / "demo.lock"
        lock.parent.mkdir(parents=True)
        with open(lock, "a") as held:
            fcntl.lockf(held, fcntl.LOCK_EX)
            out = self.run_followup("drain", "--stage", "tooling").stdout
            self.assertIn("cleanup-s1 has not finished", out)
            self.assertEqual(self.state("tooling-batch-old")["status"], "waiting")
            self.assertFalse(self.state("tooling-batch-old").get("scanned"))
            status = self.run_followup("status", "--origin", "batch-old").stdout
            self.assertIn("waiting", status)
            self.assertIn("age", status)
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-s1")["status"], "done")
        self.assertEqual(self.state("tooling-batch-old")["status"], "done")

    def test_conductor_completes_from_worker_result_only(self):
        run = Path(self.tmp.name) / "scratch" / "20261014-090000"
        run.mkdir(parents=True)
        self.run_followup("hook", "run-end", "--origin", "conductor-x", "--run-dir", str(run))
        self.assertIn("have not finished", self.run_followup("drain", "--stage", "tooling").stdout)
        (run / "worker-status.json").write_text(json.dumps({"exit": 1, "fatal": "x"}))
        self.assertIn("have not finished", self.run_followup("drain", "--stage", "tooling").stdout)
        result = run.resolve() / "worker-result.json"
        result.write_text(json.dumps({"outcome": "failed", "repo": "demo", "summary": "tests failed"}))
        self.run_followup("drain", "--stage", "tooling")
        st = self.state("tooling-conductor-x")
        self.assertEqual(st["status"], "done")
        self.assertIn(str(result), st["corpus"])

    def test_interrupted_stage_resumes(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        st = self.state("cleanup-20261014-050000")
        st.update(status="running", next={"step": "analyze"}, attempts=1)
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done")
        self.assertTrue(any(b["kind"] == "interrupted" for b in st["blockers"]))

    def test_oct13_replay_is_deterministic(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "batch-verdicts").mkdir()
        (self.root / "batch-verdicts" / "apt-sqft.json").write_text(
            (FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        findings, _ = followscan.tooling_scan(self.root, [stamp])
        kinds = {f["kind"] for f in findings}
        self.assertTrue({"misfiring-automation", "misleading-surface", "wrong-knowledge"} <= kinds)
        corpus = followscan.corpus_files(self.root, [stamp])
        self.assertIn((self.root / "runs.jsonl").resolve(), corpus)
        for f in findings:
            for ev in f["evidence"]:
                self.assertTrue(followscan.evidence_holds(ev, corpus, [stamp]), (f["title"], ev))

    def test_scans_are_pure_reads_cite_original_files(self):
        stamp = "20261013-103004"
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        other = {**row, "stamp": "20261012-999999",
                 "detail": "unrelated historical quote for another stage"}
        (self.root / "runs.jsonl").write_text(json.dumps(other) + "\n" + json.dumps(row) + "\n")
        (self.root / "runs" / stamp).mkdir(parents=True)
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
        corpus = followscan.corpus_files(self.root, [stamp])
        findings, _ = followscan.tooling_scan(self.root, [stamp])
        after = {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "scan must not create or modify files under the root")
        self.assertEqual([p for p in corpus if p.name == "runs.jsonl"],
                         [(self.root / "runs.jsonl").resolve()])
        borrowed = {"source": str(self.root / "runs.jsonl"),
                    "quote": "unrelated historical quote for another stage"}
        self.assertFalse(followscan.evidence_holds(borrowed, [self.root / "runs.jsonl"], [stamp]))
        sha = self.shipped_item("mark")
        findings, _ = followscan.cleanup_scan(self.root / "runs" / "mark", self.code / "demo")
        self.assertTrue(findings)
        self.assertFalse(list((self.root / "runs" / "mark").glob("*.excerpt")))
        for f in findings:
            for ev in f["evidence"]:
                src = Path(ev["source"])
                self.assertTrue(src.is_file())
                self.assertNotEqual(src.name, "review-markers.excerpt")
        self.assertTrue(followscan.added_markers(self.code / "demo", sha))

    def test_path_ids_reject_traversal_and_hook_recovers_empty_dir(self):
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        with self.assertRaises(ValueError):
            fu.require_id("x/../../runs", "origin")
        with self.assertRaises(ValueError):
            fu.under(self.root / "followups", "scan-x", "..", "runs")
        stamp = "20261015-orphan"
        self.shipped_item(stamp)
        key = f"cleanup-{stamp}"
        (self.root / "followups" / key).mkdir(parents=True)
        (self.root / "followups" / key / "claim.lock").write_text("")
        self.assertIn("queued", self.run_followup(
            "hook", "item-done", "--stamp", stamp, "--origin", "batch-orphan").stdout)
        st = self.state(key)
        self.assertEqual(st["status"], "queued")
        self.assertTrue(any(b["kind"] == "recovered" for b in st["blockers"]))

    def test_recognition_evidence_holds_on_pretty_json(self):
        run = self.root / "runs" / "pretty"
        run.mkdir(parents=True)
        result = {
            "outcome": "shipped", "repo": "demo", "item": "x", "merge_commit": "abc1234",
            "review": {"reviewer": "codex", "rounds": 2, "unresolved": 1},
            "deploy": {"status": "failed", "url": "https://example.test/d", "attempts": 2},
        }
        result_path = run / "worker-result.json"
        result_path.write_text(json.dumps(result, indent=1) + "\n")
        findings, _ = followscan.cleanup_scan(run, None)
        self.assertIn("red-deploy", {f["kind"] for f in findings})
        for f in findings:
            for ev in f["evidence"]:
                self.assertTrue(followscan.evidence_holds(ev, [Path(ev["source"])]))
        (self.root / "runs.jsonl").write_text(json.dumps({
            "stamp": "s-fail", "outcome": "failed", "detail": "npm test failed", "worker": "cursor"}) + "\n")
        (self.root / "runs" / "s-fail").mkdir(parents=True)
        findings, quiet = followscan.tooling_scan(self.root, ["s-fail"])
        self.assertFalse([f for f in findings if f["kind"] == "fragile-tooling"])
        self.assertTrue(any(n["title"] == "s-fail ended failed" for n in quiet))

    def test_missing_repo_blocks_without_stopping_siblings(self):
        run = self.root / "runs" / "no-repo"
        run.mkdir(parents=True)
        (run / "worker-result.json").write_text(json.dumps({
            "outcome": "shipped", "repo": None, "item": "x", "merge_commit": "abc1234"}))
        self.run_followup("hook", "item-done", "--stamp", "no-repo", "--origin", "batch-c")
        self.assertEqual(self.state("cleanup-no-repo")["status"], "blocked")
        self.shipped_item("ok-repo")
        self.run_followup("hook", "item-done", "--stamp", "ok-repo", "--origin", "batch-c")
        self.run_followup("drain", "--stage", "cleanup", "--origin", "batch-c")
        self.assertEqual(self.state("cleanup-ok-repo")["status"], "done")
        self.assertEqual(self.state("cleanup-no-repo")["status"], "blocked")

    def test_followup_failure_never_changes_batch_outcome(self):
        """Raise/hang/kill of follow-up cannot stall the batch or rewrite outcomes."""
        with patch.object(daily_batch, "ROOT", self.root), \
                patch.object(daily_batch.shared, "log", lambda *_: None), \
                patch.object(daily_batch.procrun, "run", side_effect=RuntimeError("boom")):
            self.assertIsNone(daily_batch.followup("hook", "item-done", "--stamp", "x"))
        child_pid = self.root / "child.pid"
        child_script = self.root / "child.py"
        child_script.write_text(
            "import os, signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(child_pid)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(120)\n")
        (self.root / "followup.py").write_text(
            "import os, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, {str(child_script)!r}])\n"
            "time.sleep(120)\n")
        with patch.object(daily_batch, "ROOT", self.root), \
                patch.object(daily_batch.shared, "log", lambda *_: None):
            t0 = time.time()
            result = daily_batch.followup("drain", "--origin", "batch-x", timeout=0.4)
            self.assertIsNone(result)
            self.assertLess(time.time() - t0, 8)
        deadline = time.time() + 3
        while time.time() < deadline and not child_pid.exists():
            time.sleep(0.05)
        self.assertTrue(child_pid.exists())
        pid = int(child_pid.read_text())
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"child pid {pid} still alive after group reap")


if __name__ == "__main__":
    unittest.main()
