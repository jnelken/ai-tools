"""followup.py report + tickets + repair + analyst (DEV-242). Temp ADVANCE_ROADMAP_ROOT; one test per behavior."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
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
import followticket  # noqa: E402
import daily_batch  # noqa: E402
import supervisor  # noqa: E402


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.code = t / "root", t / "code"
        (self.root / "lib").mkdir(parents=True)
        self.code.mkdir()
        # SUPERSET_*: a run from a workspace terminal must never aim a stub builder at that workspace.
        self.env = {**{k: v for k, v in os.environ.items()
                       if not k.startswith(("ADVANCE_ROADMAP_", "GIT_", "SUPERSET_"))},
                    "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.code),
                    "ADVANCE_ROADMAP_SUPERSET": "0",
                    # Deterministic suite: analyst off unless a test opts in with stubs.
                    "ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "0",
                    "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "0", "ADVANCE_ROADMAP_FOLLOWUP_POLL_S": "0.2",
                    "ADVANCE_ROADMAP_FOLLOWUP_WORKTREES": str(t / "worktrees"),
                    "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_NONE_S": "0",
                    "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S": "2",
                    "ADVANCE_ROADMAP_FOLLOWUP_CHECKS_TIMEOUT_S": "2",
                    "ADVANCE_ROADMAP_WORKER_POLL_S": "0.1",
                    "ADVANCE_ROADMAP_GH_BIN": self.wrapper(t, "gh"),
                    "ADVANCE_ROADMAP_LINEAR_BIN": self.wrapper(t, "linear"),
                    "ADVANCE_ROADMAP_CODEX_BIN": self.wrapper(t, "codex"),
                    "ADVANCE_ROADMAP_CLAUDE_BIN": self.wrapper(t, "claude"),
                    "ADVANCE_ROADMAP_JLIN": str(self.py_wrapper(t, "jlin")),
                    "AI_TOOLS_HOME": str(t / "no-ai-tools"),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        (self.root / "lib" / "usage.py").write_text(
            f"import runpy, sys\nsys.argv = [{str(STUBS)!r}, 'usage', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUBS)!r}, run_name='__main__')\n")
        for name, tool in (("worker.sh", "worker"), ("review.sh", "review")):
            (self.root / name).write_text(f'#!/bin/sh\nexec {sys.executable} {STUBS} {tool} "$@"\n')
            (self.root / name).chmod(0o755)
        self.make_repo("demo")

    def repair_env(self, **extra):
        """Env that unparks the repair loop (slice 3b)."""
        return {"ADVANCE_ROADMAP_FOLLOWUP_REPAIR": "1", **extra}

    def wrapper(self, t, tool):
        path = t / "bin" / tool
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'#!/bin/sh\nexec {sys.executable} {STUBS} {tool} "$@"\n')
        path.chmod(0o755)
        return str(path)

    def py_wrapper(self, t, tool):
        path = t / "bin" / f"{tool}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"import runpy, sys\nsys.argv = [{str(STUBS)!r}, {tool!r}, *sys.argv[1:]]\n"
                        f"runpy.run_path({str(STUBS)!r}, run_name='__main__')\n")
        return path

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

    def set(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def calls(self, tool=None):
        try:
            rows = [json.loads(l) for l in (self.root / "stub-calls.jsonl").read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if tool is None or r["tool"] == tool]

    def report(self):
        try:
            return [json.loads(l) for l in (self.root / "followups" / "report.jsonl").read_text().splitlines()]
        except OSError:
            return []

    def legacy_ticket(self, ident, title, description="", repo="demo"):
        labels = {"nodes": [{"name": repo, "parent": {"name": "repo"}}]} if repo else {"nodes": []}
        return {"id": ident, "identifier": ident, "title": title, "description": description, "url": "",
                "state": {"name": "Backlog", "type": "backlog"}, "labels": labels}

    def meta_ticket(self, ident, meta, repo="demo"):
        body = "Follow-up.\n\n```advance-roadmap-followup\n" + json.dumps(
            {"workflow": "advance-roadmap-followup", "repo": repo, **meta}) + "\n```\n"
        return self.legacy_ticket(ident, "Follow-up (project-cleanup): something", body, repo)

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
        self.assertEqual(st.get("mode"), "tickets")

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
            fcntl.flock(held, fcntl.LOCK_EX)  # same mechanism as Claim / run.sh zsystem flock
            out = self.run_followup("drain").stdout
        self.assertIn("demo.t2.lock is held", out)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "waiting")
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "deferred")  # open findings park for repair (slice 3b)
        self.assertTrue(st["findings"])
        self.assertEqual((st.get("next") or {}).get("step"), "build")
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
            fcntl.flock(held, fcntl.LOCK_EX)
            out = self.run_followup("drain", "--stage", "tooling").stdout
            self.assertIn("cleanup-s1 has not finished", out)
            self.assertEqual(self.state("tooling-batch-old")["status"], "waiting")
            self.assertFalse(self.state("tooling-batch-old").get("scanned"))
            status = self.run_followup("status", "--origin", "batch-old").stdout
            self.assertIn("waiting", status)
            self.assertIn("age", status)
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-s1")["status"], "deferred")  # markers → park for repair
        # tooling may finish (no findings) or park; either way it is no longer waiting on cleanup
        self.assertNotEqual(self.state("tooling-batch-old")["status"], "waiting")

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
        self.assertEqual(st["status"], "deferred")  # markers remain → park for repair
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
        self.assertEqual(self.state("cleanup-ok-repo")["status"], "deferred")
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

    def test_conductor_reviewed_summary_is_not_merged_unreviewed(self):
        """Review evidence is structured — a summary starting with 'merged' is not a signal."""
        stamp = "20261014-conductor"
        run = Path(self.tmp.name) / "scratch" / stamp
        run.mkdir(parents=True)
        (run / "worker-result.json").write_text(json.dumps({
            "outcome": "shipped", "repo": "demo", "provider": "cursor",
            "summary": "merged PR #42 after a clean review",
            "review": {"reviewer": "codex", "rounds": 1, "unresolved": 0},
        }))
        (run / "reviews.jsonl").write_text(json.dumps({
            "verdict": "clean", "reviewer": "codex", "round": 1}) + "\n")
        row, _ = followscan.external_row(stamp, run)
        self.assertEqual(row["detail"][:6], "merged")
        self.assertTrue(followscan.has_structured_review(row, run))
        self.assertFalse(followscan.is_unreviewed_ship(row, run))
        findings, _ = followscan.tooling_scan(self.root, [stamp], run_dirs={stamp: str(run)})
        self.assertFalse([f for f in findings if "without a completed review" in f.get("title", "")])

    def test_unreviewed_ship_uses_runrecord_marker_not_summary(self):
        stamp = "20261014-noreview"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps({
            "stamp": stamp, "outcome": "shipped",
            "detail": "merged without a logged review", "worker": "cursor"}) + "\n")
        findings, _ = followscan.tooling_scan(self.root, [stamp])
        self.assertTrue([f for f in findings if "without a completed review" in f.get("title", "")])

    def test_drain_origin_skips_unrelated_stages(self):
        """--origin only processes that origin's stages; siblings stay queued."""
        self.shipped_item("20261014-mine")
        self.shipped_item("20261014-theirs")
        self.run_followup("hook", "item-done", "--stamp", "20261014-mine", "--origin", "batch-mine")
        self.run_followup("hook", "item-done", "--stamp", "20261014-theirs", "--origin", "batch-theirs")
        self.run_followup("drain", "--stage", "cleanup", "--origin", "batch-mine")
        self.assertEqual(self.state("cleanup-20261014-mine")["status"], "deferred")
        self.assertEqual(self.state("cleanup-20261014-theirs")["status"], "queued")
        self.assertEqual(self.state("cleanup-20261014-mine").get("drained_by"), "batch-mine")

    def test_origin_scoped_drain_does_not_recover_sibling_interrupted(self):
        """Invariant: origin-scoped drain must not rewrite a sibling origin's running stage."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        for stamp, origin in (("mine", "batch-mine"), ("theirs", "batch-theirs")):
            key = f"cleanup-{stamp}"
            d = self.root / "followups" / key
            d.mkdir(parents=True)
            (d / "state.json").write_text(json.dumps({
                "key": key, "stage": "project-cleanup", "repo": "demo", "origin": origin,
                "status": "running", "blockers": [], "stamp": stamp}))
        fu.recover_interrupted(origin="batch-mine")
        self.assertEqual(fu.load("cleanup-mine")["status"], "queued")
        self.assertEqual(fu.load("cleanup-theirs")["status"], "running")

    def test_timeout_mark_is_claim_reload_recheck_save(self):
        """Timeout recovery owns stage writes via claim → reload → recheck → CAS."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        key = "cleanup-demo"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo",
            "status": "running", "blockers": []}))
        # A concurrent drain finishes the stage between snapshot and save: CAS must no-op.
        held = fu.claim_stage(key)
        self.assertIsNotNone(held)
        try:
            live = fu.load(key)
            live["status"] = "done"
            fu.save(live)
        finally:
            held.close()
        self.assertEqual(fu.mark_running_cleanups_retryable("demo"), 0)
        self.assertEqual(fu.load(key)["status"], "done")
        # Genuine running stage becomes retryable under the claim.
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo",
            "status": "running", "blockers": []}))
        self.assertEqual(fu.mark_running_cleanups_retryable("demo", "timeout", "deadline"), 1)
        st = fu.load(key)
        self.assertEqual((st["status"], st["blockers"][-1]["kind"]), ("retryable", "timeout"))

    # ── slice 3a: tickets (recovery / dedupe / deferral / close / ownership / deploy) ──

    def test_repo_is_only_from_repo_label_never_prose(self):
        """Invariant: repo identity is the repo/* label (or metadata.repo), never title/body prose."""
        title = "reject an empty listing before saving the catalog"
        finding = {"id": "aaaaaaaaaaaa", "title": title, "repo": "demo"}
        unlabeled = self.legacy_ticket(
            "DEV-1", title, f"Repo: mailcruxh\nSeen before: {title}", repo=None)
        self.assertIsNone(followticket.repo_label(unlabeled))
        # Legacy title match reaches the text path but must refuse an unlabeled issue.
        self.assertIsNone(followticket.match([unlabeled], finding))
        wrong_repo = self.legacy_ticket("DEV-2", title, title, repo="mailcruxh")
        self.assertIsNone(followticket.match([wrong_repo], finding))
        labeled = self.legacy_ticket("DEV-3", title, f"Seen before: {title}", repo="demo")
        self.assertEqual(followticket.repo_label(labeled), "demo")
        self.assertEqual(followticket.match([labeled], finding)["identifier"], "DEV-3")
        # Metadata match by finding id must still refuse a cross-repo ticket.
        cross = self.meta_ticket("DEV-4", {"key": "cleanup-x", "finding_ids": [finding["id"]],
                                           "repo": "mailcruxh"}, repo="mailcruxh")
        self.assertIsNone(followticket.match([cross], finding))

    def test_stage_repair_verified_close_gate(self):
        """Invariant owner: deploy ok, final review clean, and that review's SHA matches merge_commit."""
        import followup as fu
        sha = "abc1234deadbeef"
        clean = {"verdict": "clean", "head": sha}
        cases = [
            ("deploy skipped", {"merge_commit": sha, "validation": {
                "checks": "passed", "reviews": [{**clean, "round": 1}], "deploy": "skipped"}}, False),
            ("deploy error", {"merge_commit": sha, "validation": {
                "checks": "passed", "reviews": [clean], "deploy": "error"}}, False),
            ("deploy pending", {"merge_commit": sha, "validation": {
                "checks": "passed", "reviews": [clean], "deploy": "pending"}}, False),
            ("deploy success", {"merge_commit": sha, "validation": {
                "checks": "passed", "reviews": [clean], "deploy": "success"}}, True),
            ("deploy none", {"merge_commit": sha, "validation": {
                "checks": "none", "reviews": [clean], "deploy": "none"}}, True),
            ("merged flag alone", {"merged": True, "validation": {
                "checks": "none", "reviews": [clean], "deploy": "none"}}, False),
            ("clean then findings", {"merge_commit": sha, "validation": {
                "checks": "passed",
                "reviews": [{**clean, "round": 1}, {"verdict": "findings", "round": 2, "head": sha}],
                "deploy": "success"}}, False),
            ("findings then clean", {"merge_commit": sha, "validation": {
                "checks": "passed",
                "reviews": [{"verdict": "findings", "round": 1, "head": "old"},
                            {**clean, "round": 2}],
                "deploy": "success"}}, True),
            ("clean then findings no rounds", {"merge_commit": sha, "validation": {
                "checks": "passed",
                "reviews": [clean, {"verdict": "findings", "head": sha}],
                "deploy": "success"}}, False),
            ("clean on other sha", {"merge_commit": sha, "validation": {
                "checks": "passed",
                "reviews": [{"verdict": "clean", "head": "0000000ffffffff"}],
                "deploy": "success"}}, False),
            ("clean missing sha", {"merge_commit": sha, "validation": {
                "checks": "passed", "reviews": [{"verdict": "clean"}], "deploy": "success"}}, False),
            ("abbrev sha match", {"merge_commit": sha, "validation": {
                "checks": "passed",
                "reviews": [{"verdict": "clean", "head": sha[:7]}],
                "deploy": "success"}}, True),
        ]
        for name, st, want in cases:
            with self.subTest(name):
                self.assertEqual(fu.stage_repair_verified(st), want)

    def test_recovery_updates_existing_stage_next_step_when_pr_arrives(self):
        """Newer Linear metadata with a PR must move an existing stage from build to review."""
        key, fid = "cleanup-20261014-050000", "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "deferred",
            "next": {"step": "build", "round": 1}, "owned_tickets": ["DEV-501"],
            "findings": [{"id": fid, "status": "deferred", "ticket": "DEV-501", "title": "x",
                          "kind": "t", "fix": "f", "evidence": []}],
            "ticket_meta_at": 1, "blockers": [], "scanned": True,
        }))
        ticket = self.meta_ticket("DEV-501", {
            "key": key, "stage": "project-cleanup", "finding_ids": [fid],
            "finding_status": {fid: "open"},
            "branch": "roadmap/x", "pr_url": "https://example/pr/9",
            "updated_at": 99.0,
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state(key)
        self.assertEqual(st.get("pr_url"), "https://example/pr/9")
        self.assertEqual((st.get("next") or {}).get("step"), "review")

    def test_recovery_resumes_open_findings_at_build_not_under_a_sibling_pr(self):
        """A fresh machine reading partial-progress metadata must not review the completed PR."""
        key_a, key_b = "cleanup-20261014-050000", "cleanup-20261014-060000"
        fid_open, fid_fixed = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        ticket = self.meta_ticket("DEV-501", {
            "key": key_a, "stage": "project-cleanup", "repo": "demo",
            "finding_ids": [fid_fixed, fid_open],
            "finding_status": {fid_fixed: "fixed", fid_open: "open"},
            "finding_owners": {
                fid_fixed: {"key": key_b, "branch": "roadmap/done", "pr_url": "https://example/pr/9",
                            "builder_provider": "codex"},
                fid_open: {"key": key_a, "branch": None, "pr_url": None},
            },
            "branch": None, "pr_url": None, "updated_at": time.time(),
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state(key_a)
        self.assertIsNotNone(st)
        self.assertEqual([f["id"] for f in st["findings"]], [fid_open])
        self.assertIsNone(st.get("pr_url"))
        self.assertEqual((st.get("next") or {}).get("step"), "build")
        self.assertEqual(st["status"], "deferred")
        self.assertFalse((self.root / "followups" / key_b / "state.json").exists(),
                         "must not rebuild the completed sibling stage")

    def test_recovery_rebuilds_each_open_finding_under_its_owner(self):
        """Two open findings with different finding_owners must not land on one stage."""
        key_a, key_b = "cleanup-20261014-050000", "cleanup-20261014-060000"
        fid_a, fid_b = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        ticket = self.meta_ticket("DEV-501", {
            "key": key_a, "stage": "project-cleanup", "repo": "demo",
            "finding_ids": [fid_a, fid_b],
            "finding_status": {fid_a: "open", fid_b: "open"},
            "finding_owners": {
                fid_a: {"key": key_a, "branch": None, "pr_url": None, "builder_provider": "cursor"},
                fid_b: {"key": key_b, "branch": "roadmap/b", "pr_url": "https://example/pr/2",
                        "builder_provider": "codex"},
            },
            "branch": None, "pr_url": None, "updated_at": time.time(),
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        st_a, st_b = self.state(key_a), self.state(key_b)
        self.assertEqual([f["id"] for f in st_a["findings"]], [fid_a])
        self.assertEqual([f["id"] for f in st_b["findings"]], [fid_b])
        self.assertIsNone(st_a.get("pr_url"))
        self.assertEqual(st_b.get("pr_url"), "https://example/pr/2")
        self.assertEqual(st_b.get("builder_provider"), "codex")

    def test_recovery_skips_ticket_when_every_finding_is_fixed(self):
        ticket = self.meta_ticket("DEV-501", {
            "key": "cleanup-done", "stage": "project-cleanup",
            "finding_ids": ["aaaaaaaaaaaa"],
            "finding_status": {"aaaaaaaaaaaa": "fixed"},
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        self.assertFalse((self.root / "followups" / "cleanup-done" / "state.json").exists())

    def test_dedupe_does_not_steal_owner_on_description_rewrite(self):
        """A duplicate attach must keep the original stage as finding_owners key."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        fid = "aaaaaaaaaaaa"
        prior = self.meta_ticket("DEV-777", {
            "key": "cleanup-other", "stage": "project-cleanup", "finding_ids": [fid],
            "finding_owners": {fid: {"key": "cleanup-other", "builder_provider": "codex"}},
        })
        self.set("stub-linear.json", {"pages": [[prior]]})
        st = {"key": "cleanup-mine", "stage": "project-cleanup", "repo": "demo",
              "findings": [{"id": fid, "title": "x", "kind": "t", "fix": "f", "status": "duplicate",
                            "duplicate_of": "cleanup-other", "ticket": "DEV-777", "evidence": []}],
              "builder_provider": "cursor"}
        body = fu.rewrite_ticket_description(st, "DEV-777", st["findings"], issues=[prior])
        meta = followticket.parse_meta(body)
        self.assertEqual((meta.get("finding_owners") or {}).get(fid, {}).get("key"), "cleanup-other")

    def test_open_findings_park_for_repair_not_done(self):
        """A scanned stage with open findings stays deferred so slice 3b can resume repair."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "deferred")
        self.assertTrue(st["findings"])
        self.assertEqual((st.get("next") or {}).get("step"), "build")
        # Parked work is not eligible until repair is enabled.
        import followup as fu
        fu.ROOT, fu.STATE = self.root, self.root / "followups"
        self.assertFalse(fu.eligible(st))

    def test_a_finding_already_ticketed_gets_evidence_not_a_duplicate(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.run_followup("drain")   # scan + park
        st = self.state("cleanup-20261014-050000")
        f = st["findings"][0]
        cases = {
            "metadata": self.meta_ticket("DEV-777", {"key": "cleanup-other", "stage": "project-cleanup",
                                                    "finding_ids": [f["id"]]}),
            "legacy text": self.legacy_ticket("DEV-778", "Old bug", f"Seen before: {f['title']}"),
        }
        for label, ticket in cases.items():
            with self.subTest(label):
                (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
                st = self.state("cleanup-20261014-050000")
                # Clear next so eligible() does not treat this as parked repair work.
                st.update(status="queued", evidence_posted=[], scanned=False, last_deferral=None, next=None)
                for g in st["findings"]:
                    g.pop("ticket", None)
                    g["status"] = "open"
                (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
                self.set("stub-linear.json", {"pages": [[ticket]]})
                self.run_followup("drain")
                self.assertEqual(self.calls("jlin"), [])
                comments = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")]
                self.assertTrue(any(c.get("issue") == ticket["identifier"] and f["id"] in (c.get("body") or "")
                                    for c in comments), comments)
                self.assertEqual(self.state(st["key"])["findings"][0]["ticket"], ticket["identifier"])

    def test_defer_creates_one_ticket_and_retries_failed_description_update(self):
        """Deferral create/update is idempotent; a failed description rewrite does not advance last_deferral."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        os.environ["ADVANCE_ROADMAP_JLIN"] = self.env["ADVANCE_ROADMAP_JLIN"]
        key = "cleanup-defer"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        findings = [{"id": "cccccccccccc", "title": "quota stopped the builder", "kind": "unresolved-review",
                     "fix": "resume", "status": "open", "repo": "demo", "stage": "project-cleanup",
                     "evidence": [{"source": "t", "quote": "quota stopped the builder"}]}]
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": findings, "owned_tickets": [], "origin": "batch-x", "stamp": "s",
              "item": "Item", "builder_provider": "cursor"}
        (d / "state.json").write_text(json.dumps(st))
        self.set("stub-linear.json", {"pages": [[]]})
        issues = []
        quota = {"provider": "cursor", "role": "worker", "cause": "over its gate",
                 "reset_at": "2026-10-14T18:00:00", "reset_epoch": time.time() + 600}
        fu.defer(st, findings, quota, "builder round 1", issues=issues)
        fu.save(st)
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(findings[0]["ticket"], "DEV-900")
        self.assertEqual(st["owned_tickets"], ["DEV-900"])
        self.assertIsNotNone(st.get("last_deferral"))
        first_note = st["last_deferral"]
        self.assertEqual(self.calls("jlin")[0]["repo"], "demo")
        # Second deferral (new builder) must rewrite the description; failure must not advance note.
        st["builder_provider"] = "codex"
        # Prior description must carry finding IDs so the rewrite merge has something to preserve.
        prior_body = followticket.description(st, findings, quota, "resume")
        self.set("stub-linear.json", {"pages": [[{
            "identifier": "DEV-900", "title": "t", "description": prior_body,
            "labels": {"nodes": [{"name": "demo", "parent": {"name": "repo"}}]}}]],
            "fail": ["issue update DEV-900"]})
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        quota2 = {**quota, "provider": "codex"}
        fu.defer(st, findings, quota2, "builder round 1", issues=issues)
        self.assertTrue(st.get("pending_deferral_sync"), st)
        self.assertEqual(st.get("last_deferral"), first_note)
        self.set("stub-linear.json", {"pages": [[{
            "identifier": "DEV-900", "title": "t", "description": prior_body,
            "labels": {"nodes": [{"name": "demo", "parent": {"name": "repo"}}]}}]]})
        self.assertTrue(fu.sync_deferral_tickets(
            st, findings, quota2, fu.next_step_text(st, "builder round 1"),
            fu.deferral_note(st, quota2, "builder round 1"), issues=[{
                "identifier": "DEV-900", "description": prior_body}]))
        self.assertIsNone(st.get("pending_deferral_sync"))
        self.assertNotEqual(st.get("last_deferral"), first_note)
        self.assertIn("codex", st["last_deferral"])
        updates = [c for c in self.calls("linear")
                   if c.get("cmd") == "issue update DEV-900" and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        self.assertEqual(followticket.parse_meta(updates[-1]["body"])["builder_provider"], "codex")

    def test_close_and_recovery_reject_cross_repo_stage(self):
        """Close gate and recovery both refuse when the ticket repo does not match the stage."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key, fid = "cleanup-cross-close", "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        sha = "abc1234"
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": sha,
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": sha}],
                           "deploy": "success"},
            "findings": [{"id": fid, "status": "fixed", "ticket": "DEV-501", "title": "x",
                          "kind": "t", "fix": "f", "evidence": []}],
            "pending_close": [{"ticket": "DEV-501", "commented": True}], "blockers": [],
        }))
        foreign = self.meta_ticket("DEV-501", {
            "key": key, "finding_ids": [fid], "repo": "mailcruxh",
        }, repo="mailcruxh")
        self.assertEqual(fu.ticket_close_verdict("DEV-501", current=fu.load(key), issues=[foreign]),
                         "incomplete")
        # Recovery must not mutate the demo stage from a mailcruxh ticket.
        foreign_open = self.meta_ticket("DEV-502", {
            "key": key, "finding_ids": ["bbbbbbbbbbbb"], "finding_status": {"bbbbbbbbbbbb": "open"},
            "repo": "mailcruxh", "updated_at": time.time(),
        }, repo="mailcruxh")
        self.set("stub-linear.json", {"pages": [[foreign_open]]})
        before = fu.load(key)
        fu.recover_tickets()
        after = fu.load(key)
        self.assertEqual(len(after.get("findings") or []), len(before.get("findings") or []))
        self.assertEqual(after.get("repo"), "demo")

    def test_rewrite_refuses_cross_repo_ticket(self):
        """Invariant owner: rewrite_ticket_description must reject a cross-repo open ticket."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        fid = "aaaaaaaaaaaa"
        foreign = self.meta_ticket("DEV-777", {
            "key": "cleanup-other", "finding_ids": [fid], "repo": "mailcruxh",
        }, repo="mailcruxh")
        self.set("stub-linear.json", {"pages": [[foreign]]})
        st = {"key": "cleanup-mine", "stage": "project-cleanup", "repo": "demo",
              "findings": [{"id": fid, "title": "x", "kind": "t", "fix": "f", "status": "open",
                            "evidence": []}]}
        with self.assertRaises(followticket.LinearError) as ctx:
            fu.rewrite_ticket_description(st, "DEV-777", st["findings"], issues=[foreign])
        self.assertIn("repo", str(ctx.exception).lower())
        self.assertFalse(any(c.get("cmd") == "issue update DEV-777" for c in self.calls("linear")))


    def test_defer_discards_cross_repo_index_entry(self):
        """A stale tickets.json entry for another repo must not rewrite that issue on deferral."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        os.environ["ADVANCE_ROADMAP_JLIN"] = self.env["ADVANCE_ROADMAP_JLIN"]
        key = "cleanup-defer-index"
        fid = "cccccccccccc"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        findings = [{"id": fid, "title": "quota stopped the builder", "kind": "unresolved-review",
                     "fix": "resume", "status": "open", "repo": "demo", "stage": "project-cleanup",
                     "evidence": [{"source": "t", "quote": "quota stopped the builder"}]}]
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": findings, "owned_tickets": [], "builder_provider": "cursor"}
        (d / "state.json").write_text(json.dumps(st))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid: {"ticket": "DEV-777", "key": "cleanup-other", "at": time.time()}}))
        foreign = self.meta_ticket("DEV-777", {
            "key": "cleanup-other", "stage": "project-cleanup", "finding_ids": [fid],
            "repo": "mailcruxh",
        }, repo="mailcruxh")
        self.set("stub-linear.json", {"pages": [[foreign]]})
        quota = {"provider": "cursor", "cause": "over its gate", "reset_at": "soon",
                 "reset_epoch": time.time() + 60}
        fu.defer(st, findings, quota, "builder round 1", issues=[])
        self.assertNotEqual(findings[0].get("ticket"), "DEV-777")
        self.assertEqual(findings[0].get("ticket"), "DEV-900")
        self.assertFalse(any(c.get("issue") == "DEV-777" for c in self.calls("linear")))
        index = json.loads((self.root / "followups" / "tickets.json").read_text())
        self.assertEqual((index.get(fid) or {}).get("ticket"), "DEV-900")

    def test_defer_attaches_to_live_ticket_instead_of_creating_duplicate(self):
        """Immediately before create, recheck Linear and attach when another machine already filed."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        os.environ["ADVANCE_ROADMAP_JLIN"] = self.env["ADVANCE_ROADMAP_JLIN"]
        key = "cleanup-defer-race"
        fid = "cccccccccccc"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        findings = [{"id": fid, "title": "quota stopped the builder", "kind": "unresolved-review",
                     "fix": "resume", "status": "open", "repo": "demo", "stage": "project-cleanup",
                     "evidence": [{"source": "t", "quote": "quota stopped the builder"}]}]
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": findings, "owned_tickets": [], "builder_provider": "cursor"}
        (d / "state.json").write_text(json.dumps(st))
        # Drain snapshot is empty (stale); live Linear already has the matching ticket.
        prior = self.meta_ticket("DEV-501", {
            "key": "cleanup-other-machine", "stage": "project-cleanup", "finding_ids": [fid],
        })
        self.set("stub-linear.json", {"pages": [[prior]]})
        quota = {"provider": "cursor", "role": "worker", "cause": "over its gate",
                 "reset_at": "2026-10-14T18:00:00", "reset_epoch": time.time() + 600}
        fu.defer(st, findings, quota, "builder round 1", issues=[])
        self.assertEqual(findings[0]["ticket"], "DEV-501")
        self.assertEqual(self.calls("jlin"), [], "must not create when a live ticket matches")
        updates = [c for c in self.calls("linear")
                   if c.get("cmd") == "issue update DEV-501" and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)

    def test_deferral_description_preserves_sibling_finding_ids(self):
        """Invariant: rewriting a shared ticket's description must keep the sibling's finding IDs."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key_a, key_b = "cleanup-a", "cleanup-b"
        fid_a, fid_b = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        prior = self.meta_ticket("DEV-900", {
            "key": key_a, "stage": "project-cleanup",
            "finding_ids": [fid_a, fid_b],
            "finding_status": {fid_a: "open", fid_b: "open"},
            "finding_owners": {
                fid_a: {"key": key_a}, fid_b: {"key": key_b, "branch": None, "pr_url": None},
            },
        })
        d = self.root / "followups" / key_a
        d.mkdir(parents=True)
        findings = [{"id": fid_a, "title": "stage a finding", "kind": "t", "fix": "f",
                     "status": "open", "repo": "demo", "stage": "project-cleanup", "ticket": "DEV-900",
                     "evidence": []}]
        st = {"key": key_a, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": findings, "owned_tickets": ["DEV-900"], "builder_provider": "cursor",
              "ticket_body": prior["description"]}
        (d / "state.json").write_text(json.dumps(st))
        self.set("stub-linear.json", {"pages": [[prior]]})
        quota = {"provider": "cursor", "cause": "over its gate", "reset_at": "soon",
                 "reset_epoch": time.time() + 60}
        self.assertTrue(fu.sync_deferral_tickets(
            st, findings, quota, "resume", "note-1", issues=[prior]))
        updates = [c for c in self.calls("linear")
                   if c.get("cmd") == "issue update DEV-900" and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        meta = followticket.parse_meta(updates[-1]["body"])
        self.assertEqual(set(meta["finding_ids"]), {fid_a, fid_b})
        self.assertEqual(meta["finding_status"][fid_b], "open")
        self.assertEqual((meta.get("finding_owners") or {}).get(fid_b, {}).get("key"), key_b)

    def test_finish_closes_a_ticket_only_when_every_tracked_finding_is_fixed(self):
        """A stage that verifies one finding must not Done a multi-finding ticket (ownership preserved)."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        os.environ["ADVANCE_ROADMAP_JLIN"] = self.env["ADVANCE_ROADMAP_JLIN"]
        key_a, key_b = "cleanup-20261014-050000", "cleanup-20261014-060000"
        fid1, fid2 = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        for key, fid, status in ((key_a, fid2, "blocked"), (key_b, fid1, "running")):
            d = self.root / "followups" / key
            d.mkdir(parents=True)
            (d / "state.json").write_text(json.dumps({
                "key": key, "stage": "project-cleanup", "repo": "demo", "status": status,
                "owned_tickets": ["DEV-501"], "blockers": [],
                "findings": [{"id": fid, "stage": "project-cleanup", "repo": "demo",
                              "kind": "unresolved-review", "title": f"finding {fid}",
                              "fix": "fix", "status": "open", "ticket": "DEV-501",
                              "evidence": [{"source": "t", "quote": fid}]}],
            }))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid1: {"ticket": "DEV-501", "key": key_a, "at": time.time()},
            fid2: {"ticket": "DEV-501", "key": key_a, "at": time.time()},
        }))
        ticket = self.meta_ticket("DEV-501", {
            "key": key_a, "stage": "project-cleanup", "finding_ids": [fid1, fid2],
            "finding_status": {fid1: "open", fid2: "open"},
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        st_b = fu.load(key_b)
        st_b.update(merge_commit="deadbeef", pr_url="https://example/pr/1",
                    validation={"checks": "passed", "reviews": [{"verdict": "clean", "round": 1, "head": "deadbeef"}],
                                "deploy": "success"},
                    ticket_body=ticket["description"])
        fu.finish(st_b, st_b["findings"])
        fu.save(st_b)
        self.assertEqual(st_b["status"], "done")
        self.assertEqual(st_b["findings"][0]["status"], "fixed")
        done = [c for c in self.calls("linear")
                if c.get("cmd") == "issue update DEV-501" and "Done" in (c.get("args") or [])]
        self.assertEqual(done, [], "must not close while fid2 is still open")
        partial = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                   and c.get("issue") == "DEV-501" and "partial progress" in (c.get("body") or "")]
        self.assertTrue(partial, self.calls("linear"))
        updates = [c for c in self.calls("linear") if c.get("cmd") == "issue update DEV-501"
                   and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        meta = followticket.parse_meta(updates[0]["body"])
        self.assertEqual(meta["finding_status"][fid1], "fixed")
        self.assertEqual(meta["finding_status"][fid2], "open")
        self.assertEqual(meta["key"], key_a)
        self.assertIsNone(meta.get("pr_url"))
        self.assertEqual((meta.get("finding_owners") or {}).get(fid2, {}).get("key"), key_a)
        self.assertEqual((meta.get("finding_owners") or {}).get(fid1, {}).get("key"), key_b)

    def test_a_ticket_close_that_fails_is_retried_by_later_drains(self):
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-20261014-050000"
        fid = "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
              "merge_commit": "abc1234", "pr_url": "https://example/pr/1",
              "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": "abc1234"}], "deploy": "success"},
              "close_note": "Fixed by advance-roadmap follow-up `cleanup-20261014-050000`: deploy success.",
              "findings": [{"id": fid, "status": "fixed", "ticket": "DEV-501", "title": "x",
                            "kind": "t", "fix": "f", "evidence": []}],
              "pending_close": [{"ticket": "DEV-501", "commented": False}], "blockers": []}
        (d / "state.json").write_text(json.dumps(st))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid: {"ticket": "DEV-501", "key": key, "at": time.time()}}))
        ticket = self.meta_ticket("DEV-501", {"key": key, "stage": "project-cleanup", "finding_ids": [fid]})
        self.set("stub-linear.json", {"pages": [[ticket]], "fail": ["issue update DEV-501"]})
        fu.retry_closes()
        st = self.state(key)
        self.assertEqual(st["pending_close"], [{"ticket": "DEV-501", "commented": True}])
        self.assertIn("could not close DEV-501", self.report()[-1]["reason"])
        self.set("stub-linear.json", {"pages": [[ticket]]})
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        fu.retry_closes()
        st = self.state(key)
        self.assertEqual(st["pending_close"], [])
        updates = [c for c in self.calls("linear") if c.get("cmd") == "issue update DEV-501"]
        self.assertTrue(any("Done" in (c.get("args") or []) for c in updates))
        closing = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                   and c.get("issue") == "DEV-501" and "Fixed by" in (c.get("body") or "")]
        self.assertEqual(len(closing), 0)  # commented=True already; retry only moves to Done

    def test_close_tickets_rechecks_before_done_on_retry(self):
        """Invariant: close_tickets refuses Done when a tracked finding is no longer verified."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-recheck"
        fid_ok, fid_open = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": "abc1234",
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": "abc1234"}], "deploy": "success"},
            "findings": [
                {"id": fid_ok, "status": "fixed", "ticket": "DEV-501", "title": "a", "kind": "t",
                 "fix": "f", "evidence": []},
                {"id": fid_open, "status": "open", "ticket": "DEV-501", "title": "b", "kind": "t",
                 "fix": "f", "evidence": []},
            ],
            "pending_close": [{"ticket": "DEV-501", "commented": True}], "blockers": [],
        }))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid_ok: {"ticket": "DEV-501", "key": key, "at": time.time()},
            fid_open: {"ticket": "DEV-501", "key": key, "at": time.time()},
        }))
        self.set("stub-linear.json", {"pages": [[self.meta_ticket(
            "DEV-501", {"key": key, "finding_ids": [fid_ok, fid_open]})]]})
        fu.retry_closes()
        st = self.state(key)
        self.assertEqual(st["pending_close"], [])  # dropped after failed recheck
        self.assertTrue(any(r.get("kind") == "close-recheck" for r in self.report()), self.report())
        done = [c for c in self.calls("linear")
                if c.get("cmd") == "issue update DEV-501" and "Done" in (c.get("args") or [])]
        self.assertEqual(done, [])

    def test_close_retains_pending_when_linear_metadata_unread(self):
        """Invariant: no Done write without a successful Linear metadata read."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-unread"
        fid = "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": "abc1234",
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": "abc1234"}], "deploy": "success"},
            "findings": [{"id": fid, "status": "fixed", "ticket": "DEV-501", "title": "a", "kind": "t",
                          "fix": "f", "evidence": []}],
            "pending_close": [{"ticket": "DEV-501", "commented": True}], "blockers": [],
        }))
        # Empty pages: ticket not in snapshot → unread, retain pending_close.
        self.set("stub-linear.json", {"pages": [[]]})
        fu.retry_closes(issues=[])
        st = self.state(key)
        self.assertEqual(st["pending_close"], [{"ticket": "DEV-501", "commented": True}])
        self.assertTrue(any(r.get("kind") == "close-unread" for r in self.report()), self.report())
        self.assertFalse(any(c.get("cmd") == "issue update DEV-501" for c in self.calls("linear")))

    def test_recovery_adds_open_findings_to_existing_stage(self):
        """An existing stage must restore open finding IDs the ticket gained elsewhere."""
        key = "cleanup-20261014-050000"
        fid_local, fid_remote = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "owned_tickets": ["DEV-501"], "findings": [
                {"id": fid_local, "status": "fixed", "ticket": "DEV-501", "title": "local",
                 "kind": "t", "fix": "f", "evidence": []}],
            "blockers": [], "scanned": True,
        }))
        ticket = self.meta_ticket("DEV-501", {
            "key": key, "stage": "project-cleanup",
            "finding_ids": [fid_local, fid_remote],
            "finding_status": {fid_local: "fixed", fid_remote: "open"},
            "updated_at": time.time(),
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state(key)
        self.assertEqual({f["id"] for f in st["findings"]}, {fid_local, fid_remote})
        self.assertEqual(st["status"], "deferred")
        self.assertEqual((st.get("next") or {}).get("step"), "build")

    def test_recovery_preserves_verified_fixed_when_linear_stale(self):
        """A verified local fix must not be demoted when Linear still says open after a rewrite fail."""
        key = "cleanup-stale-meta"
        fid = "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": "abc1234", "owned_tickets": ["DEV-501"],
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": "abc1234"}], "deploy": "success"},
            "findings": [{"id": fid, "status": "fixed", "ticket": "DEV-501", "title": "x",
                          "kind": "t", "fix": "f", "evidence": []}],
            "blockers": [], "scanned": True,
        }))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid: {"ticket": "DEV-501", "key": key, "at": time.time()}}))
        # Linear metadata never got the fixed rewrite — still open.
        ticket = self.meta_ticket("DEV-501", {
            "key": key, "stage": "project-cleanup", "finding_ids": [fid],
            "finding_status": {fid: "open"}, "updated_at": time.time(),
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state(key)
        self.assertEqual(st["findings"][0]["status"], "fixed")
        self.assertNotEqual(st["status"], "deferred")
        self.assertEqual(st.get("pending_close"), [])
        done = [c for c in self.calls("linear")
                if c.get("cmd") == "issue update DEV-501" and "Done" in (c.get("args") or [])]
        self.assertTrue(done, self.calls("linear"))

    def test_close_removes_ticket_from_shared_open_snapshot(self):
        """Closing must drop the issue from the drain snapshot so later stages cannot rematch it."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-close-snap"
        fid = "aaaaaaaaaaaa"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": "abc1234",
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean", "head": "abc1234"}], "deploy": "success"},
            "findings": [{"id": fid, "status": "fixed", "ticket": "DEV-501", "title": "x",
                          "kind": "t", "fix": "f", "evidence": []}],
            "pending_close": [{"ticket": "DEV-501", "commented": True}], "blockers": [],
        }))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid: {"ticket": "DEV-501", "key": key, "at": time.time()}}))
        ticket = self.meta_ticket("DEV-501", {"key": key, "finding_ids": [fid]})
        issues = [ticket]
        self.set("stub-linear.json", {"pages": [[ticket]]})
        st = fu.load(key)
        fu.close_tickets(st, issues=issues)
        self.assertEqual(issues, [])
        self.assertEqual(st.get("pending_close"), [])

    def test_dedupe_retries_rewrite_without_reposting_evidence_comment(self):
        """If the evidence comment lands but the rewrite fails, retry only the unfinished rewrite."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-evidence-split"
        fid = "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        finding = {"id": fid, "title": title, "kind": "t", "fix": "f", "status": "open",
                   "repo": "demo", "evidence": [{"source": "t", "quote": title}]}
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": [finding], "evidence_posted": [], "evidence_commented": [], "blockers": []}
        (d / "state.json").write_text(json.dumps(st))
        ticket = self.legacy_ticket("DEV-777", title, f"Seen before: {title}", repo="demo")
        self.set("stub-linear.json", {"pages": [[ticket]], "fail": ["issue update DEV-777"]})
        fu.dedupe(st, [ticket])
        comments = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                    and c.get("issue") == "DEV-777"]
        self.assertEqual(len(comments), 1, comments)
        self.assertIn(f"DEV-777:{fid}:{key}", st.get("evidence_commented") or [])
        self.assertNotIn(f"DEV-777:{fid}:{key}", st.get("evidence_posted") or [])
        self.assertTrue(st.get("pending_evidence_sync"))
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        self.set("stub-linear.json", {"pages": [[ticket]]})
        fu.dedupe(st, [ticket])
        comments2 = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                     and c.get("issue") == "DEV-777"]
        self.assertEqual(comments2, [], "retry must not re-comment")
        updates = [c for c in self.calls("linear") if c.get("cmd") == "issue update DEV-777"
                   and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        self.assertIn(f"DEV-777:{fid}:{key}", st.get("evidence_posted") or [])
        self.assertFalse(st.get("pending_evidence_sync"))

    def test_pending_evidence_sync_retries_while_repair_parked(self):
        """Drain-start retry must finish evidence rewrite even when eligible() parks repair."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-parked-evidence"
        fid = "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        tag = f"DEV-777:{fid}:{key}"
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "deferred",
            "next": {"step": "build", "round": 1}, "pending_evidence_sync": True,
            "evidence_commented": [tag], "evidence_posted": [],
            "findings": [{"id": fid, "title": title, "kind": "t", "fix": "f", "status": "deferred",
                          "repo": "demo", "ticket": "DEV-777",
                          "evidence": [{"source": "t", "quote": title}]}],
            "blockers": [], "scanned": True,
        }))
        ticket = self.legacy_ticket("DEV-777", title, f"Seen before: {title}", repo="demo")
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.assertFalse(fu.eligible(fu.load(key)))
        fu.retry_ticket_syncs(issues=[ticket])
        st = self.state(key)
        self.assertIn(tag, st.get("evidence_posted") or [])
        self.assertFalse(st.get("pending_evidence_sync"))

    def test_pending_ticket_sync_stays_until_match_appears(self):
        """Keep pending_ticket_sync until a later-filed ticket matches."""
        import followup as fu
        fu.ROOT, fu.STATE = self.root, self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key, fid = "cleanup-late-ticket", "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        finding = {"id": fid, "title": title, "kind": "t", "fix": "f", "status": "open",
                   "repo": "demo", "evidence": [{"source": "t", "quote": title}]}
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "deferred",
            "pending_ticket_sync": True, "findings": [finding], "blockers": [], "scanned": True}))
        self.set("stub-linear.json", {"pages": [[]]})
        fu.retry_ticket_syncs(issues=[])
        self.assertTrue(self.state(key).get("pending_ticket_sync"))
        ticket = self.legacy_ticket("DEV-777", title, f"Seen before: {title}", repo="demo")
        self.set("stub-linear.json", {"pages": [[ticket]]})
        fu.retry_ticket_syncs(issues=[ticket])
        st = self.state(key)
        self.assertFalse(st.get("pending_ticket_sync"))
        self.assertEqual(st["findings"][0].get("ticket"), "DEV-777")

    def test_linear_unread_keeps_pending_ticket_sync(self):
        """A failed Linear read must not clear ticket sync; a later drain retries dedupe."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-unread-sync"
        fid = "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "ready",
            "findings": [{"id": fid, "title": title, "kind": "t", "fix": "f", "status": "open",
                          "repo": "demo", "evidence": [{"source": "t", "quote": title}]}],
            "blockers": [], "scanned": True, "attempts": 0,
        }))
        fu.process(fu.load(key), issues=None)
        st = self.state(key)
        self.assertTrue(st.get("pending_ticket_sync"))
        self.assertIsNone(st["findings"][0].get("ticket"))
        ticket = self.legacy_ticket("DEV-777", title, f"Seen before: {title}", repo="demo")
        self.set("stub-linear.json", {"pages": [[ticket]]})
        fu.retry_ticket_syncs(issues=[ticket])
        st = self.state(key)
        self.assertFalse(st.get("pending_ticket_sync"))
        self.assertEqual(st["findings"][0].get("ticket"), "DEV-777")


    def test_partial_progress_preserves_live_fixed_and_skips_recomment(self):
        """Retry must not demote Linear-fixed findings or re-post the partial-progress comment."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-partial-retry"
        fid_local, fid_remote = "aaaaaaaaaaaa", "bbbbbbbbbbbb"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        sha = "abc1234"
        (d / "state.json").write_text(json.dumps({
            "key": key, "stage": "project-cleanup", "repo": "demo", "status": "done",
            "merge_commit": sha,
            "validation": {"checks": "passed",
                           "reviews": [{"verdict": "clean", "head": sha, "round": 1}],
                           "deploy": "success"},
            "findings": [{"id": fid_local, "status": "fixed", "ticket": "DEV-501", "title": "a",
                          "kind": "t", "fix": "f", "evidence": []}],
            "pending_partial": [{"ticket": "DEV-501", "status": {fid_local: "fixed", fid_remote: "open"},
                                 "done": False, "commented": False}],
            "blockers": [],
        }))
        (self.root / "followups" / "tickets.json").write_text(json.dumps({
            fid_local: {"ticket": "DEV-501", "key": key, "at": time.time()},
            fid_remote: {"ticket": "DEV-501", "key": "cleanup-other", "at": time.time()},
        }))
        # Live Linear already has fid_remote fixed by another machine; local still says open.
        ticket = self.meta_ticket("DEV-501", {
            "key": key, "finding_ids": [fid_local, fid_remote],
            "finding_status": {fid_local: "open", fid_remote: "fixed"},
        })
        self.set("stub-linear.json", {"pages": [[ticket]], "fail": ["issue update DEV-501"]})
        st = fu.load(key)
        fu.apply_partial_progress(st, issues=[ticket])
        self.assertTrue(st["pending_partial"][0].get("commented"))
        self.assertFalse(st["pending_partial"][0].get("done"))
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        # Refresh live ticket status after failed rewrite attempt.
        ticket = self.meta_ticket("DEV-501", {
            "key": key, "finding_ids": [fid_local, fid_remote],
            "finding_status": {fid_local: "open", fid_remote: "fixed"},
        })
        self.set("stub-linear.json", {"pages": [[ticket]]})
        fu.apply_partial_progress(st, issues=[ticket])
        comments = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                    and c.get("issue") == "DEV-501"]
        self.assertEqual(comments, [], "retry must not re-comment")
        updates = [c for c in self.calls("linear") if c.get("cmd") == "issue update DEV-501"
                   and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        meta = followticket.parse_meta(updates[-1]["body"])
        self.assertEqual(meta["finding_status"][fid_remote], "fixed")
        self.assertEqual(st.get("pending_partial"), [])


    def test_dedupe_rejects_cross_repo_ticket_attachment(self):
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key = "cleanup-cross-repo"
        fid = "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        d = self.root / "followups" / key
        d.mkdir(parents=True)
        foreign = self.meta_ticket("DEV-777", {
            "key": "cleanup-other", "stage": "project-cleanup", "finding_ids": [fid],
            "repo": "mailcruxh",
        }, repo="mailcruxh")
        st = {"key": key, "stage": "project-cleanup", "repo": "demo", "status": "running",
              "findings": [{"id": fid, "title": title, "kind": "t", "fix": "f", "status": "open",
                            "repo": "demo", "ticket": "DEV-777",
                            "evidence": [{"source": "t", "quote": title}]}],
              "evidence_posted": [], "evidence_commented": [], "blockers": []}
        (d / "state.json").write_text(json.dumps(st))
        self.set("stub-linear.json", {"pages": [[foreign]]})
        fu.dedupe(st, [foreign])
        self.assertIsNone(st["findings"][0].get("ticket"))
        self.assertFalse(any(c.get("issue") == "DEV-777" for c in self.calls("linear")))

    def test_duplicate_evidence_comment_failure_is_retried(self):
        """Marking duplicate before a failed comment must still leave pending evidence to retry."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_LINEAR_BIN"] = self.env["ADVANCE_ROADMAP_LINEAR_BIN"]
        key_mine, key_other = "cleanup-dup-mine", "cleanup-dup-other"
        fid = "aaaaaaaaaaaa"
        title = "reject an empty listing before saving the catalog"
        for key, status in ((key_other, "running"), (key_mine, "running")):
            d = self.root / "followups" / key
            d.mkdir(parents=True)
            findings = [{"id": fid, "title": title, "kind": "t", "fix": "f", "status": "open",
                         "repo": "demo", "evidence": [{"source": "t", "quote": title}]}]
            (d / "state.json").write_text(json.dumps({
                "key": key, "stage": "project-cleanup", "repo": "demo", "status": status,
                "findings": findings, "evidence_posted": [], "evidence_commented": [],
                "blockers": [],
            }))
        ticket = self.meta_ticket("DEV-777", {
            "key": key_other, "stage": "project-cleanup", "finding_ids": [fid],
            "finding_owners": {fid: {"key": key_other}},
        })
        self.set("stub-linear.json", {"pages": [[ticket]], "fail": ["issue comment add DEV-777"]})
        st = fu.load(key_mine)
        fu.dedupe(st, [ticket])
        self.assertEqual(st["findings"][0]["status"], "duplicate")
        self.assertTrue(st.get("pending_evidence_sync"), st)
        tag = f"DEV-777:{fid}:{key_mine}"
        self.assertNotIn(tag, st.get("evidence_posted") or [])
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        self.set("stub-linear.json", {"pages": [[ticket]]})
        fu.dedupe(st, [ticket])
        comments = [c for c in self.calls("linear") if str(c.get("cmd", "")).startswith("issue comment")
                    and c.get("issue") == "DEV-777"]
        self.assertTrue(comments)
        self.assertIn(tag, st.get("evidence_posted") or [])
        self.assertFalse(st.get("pending_evidence_sync"))

    # ── repair lifecycle (ADVANCE_ROADMAP_FOLLOWUP_REPAIR=1) ──

    def test_a_clean_review_with_passing_checks_merges_and_records_the_repair(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        self.assertEqual([f["status"] for f in st["findings"]], ["fixed"])
        self.assertEqual(st["findings"][0]["kind"], "unresolved-review")
        self.assertEqual(st["pr_url"], "https://github.com/jnelken/demo/pull/7")
        self.assertEqual(st["validation"]["checks"], "passed")
        self.assertEqual(st["validation"]["deploy"], "success")
        self.assertEqual({(s["role"], s["session_id"]) for s in st["sessions"]},
                         {("builder", "cursor-sess-0"), ("reviewer", "codex-sess-1")})
        [w] = self.calls("worker")
        self.assertEqual(w["followup_env"], "1")
        self.assertIn("Never use `git stash`", w["brief"])
        self.assertIn("do NOT merge", w["brief"])
        self.assertTrue(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.assertTrue(st["usage_snapshots"])

    def test_findings_get_another_round_and_three_unclean_rounds_never_merge(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["findings", "findings", "findings"]})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "blocked")
        self.assertEqual(len(self.calls("worker")), 3)
        self.assertIn("VERDICT: findings", self.calls("worker")[1]["brief"])
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.assertEqual(self.calls("jlin"), [])
        self.assertTrue(any(r["kind"] == "review" for r in self.report()))

    def test_failed_checks_or_a_red_deploy_never_complete_the_ticket(self):
        self.shipped_item("20261014-050000")
        stamp_meta = {"key": "cleanup-20261014-050000", "stage": "project-cleanup", "finding_ids": []}
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        fid = None
        for checks, deploy in (("fail", "success"), ("pass", "failure")):
            self.set("stub-gh.json", {"checks": checks, "deploy": deploy})
            if fid:
                self.set("stub-linear.json", {"pages": [[self.meta_ticket(
                    "DEV-501", {**stamp_meta, "finding_ids": [fid]})]]})
            self.run_followup("drain", env=self.repair_env())
            st = self.state("cleanup-20261014-050000")
            fid = st["findings"][0]["id"]
            # Failed checks block before merge; a red deploy after merge stays pending (retryable).
            self.assertEqual(st["status"], "blocked" if checks == "fail" else "pending")
            # Evidence/description rewrites may update the ticket; never close it as Done.
            self.assertFalse(any(
                c["cmd"].startswith("issue update") and "--state" in (c.get("args") or [])
                and any(a in ("Done", "Canceled") for a in (c.get("args") or []))
                for c in self.calls("linear")))
            st.update(status="pending")
            (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.assertEqual(st["validation"]["deploy"], "failed")
        progress = [c for c in self.calls("linear")
                    if c["cmd"].startswith("issue comment") and c["issue"] == "DEV-501"]
        self.assertTrue(any("deploy is failed" in c["body"] for c in progress), progress)

    def test_an_interrupted_stage_resumes_where_it_stopped(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.set("stub-usage.json", {})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["next"]["step"], "review")
        st["status"] = "running"
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.set("stub-review.json", {"verdicts": ["clean"]})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done")
        self.assertEqual(len(self.calls("worker")), 1)
        self.assertTrue(any(b["kind"] == "interrupted" for b in st["blockers"]))

    def test_an_interrupted_stage_whose_builder_is_still_running_is_left_alone(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        st = self.state("cleanup-20261014-050000")
        st.update(status="running", next={"step": "build", "round": 1})
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        build = self.root / "followups" / st["key"] / "build-r1-a1"
        build.mkdir()
        (build / "worker.pid").write_text(str(os.getpid()))
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state(st["key"])["status"], "running")
        self.assertEqual(self.calls("worker"), [])
        (build / "worker.exit").write_text("0")
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state(st["key"])["status"], "done")

    def test_a_pr_head_that_moved_after_the_review_is_re_reviewed_never_merged(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "head": "0" * 40})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["next"]["step"]), ("pending", "review"))
        self.assertIn("is not the reviewed commit", self.report()[-1]["reason"])
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        [merge] = [c["args"] for c in self.calls("gh") if c["args"][:2] == ["pr", "merge"]]
        head = (self.root / "stub-pr-head.txt").read_text().strip()
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], head)
        self.assertEqual(len(self.calls("review")), 2)

    def test_a_deploy_api_error_is_never_read_as_no_deploy(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "error"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["deploy"]), ("pending", "unverified"))
        self.assertEqual(st["findings"][0]["status"], "open")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "none"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["deploy"]), ("done", "none"))
        self.assertEqual(len([c for c in self.calls("gh") if c["args"][:2] == ["pr", "merge"]]), 1)

    def ai_tools_deploy_clone(self):
        self.make_repo("ai-tools")
        self.shipped_item("20261014-050000", repo="ai-tools")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "merge_commit": "head"})
        home = Path(self.tmp.name) / "ai-home"
        subprocess.run(["git", "clone", "-q",
                        str(Path(self.tmp.name) / "remotes" / "ai-tools.git"), str(home)],
                       check=True, env=self.env)
        return home, {"AI_TOOLS_HOME": str(home), "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "1"}

    def install_script(self, home, body):
        script = home / "install.sh"
        script.write_text(f"#!/bin/sh\necho run >> {Path(self.tmp.name) / 'install-ran'}\n{body}\n")
        script.chmod(0o755)

    def test_an_ai_tools_repair_is_not_done_until_this_machine_installs_it(self):
        home, env = self.ai_tools_deploy_clone()
        self.install_script(home, "exit 1")
        self.run_followup("drain", env=self.repair_env(**env))
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual((st["validation"]["deploy"], st["validation"]["install"]),
                         ("success", "install.sh exit 1"))
        self.assertIn("install failed", self.report()[-1]["reason"])
        branch = "roadmap/followup-cleanup-20261014-050000"
        self.install_script(home, f"git -C {home} fetch -q origin {branch} && "
                                  f"git -C {home} merge -q --ff-only FETCH_HEAD")
        self.run_followup("drain", env=self.repair_env(**env))
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["install"]), ("done", "installed"))
        self.assertEqual(len([c for c in self.calls("gh") if c["args"][:2] == ["pr", "merge"]]), 1)
        self.assertEqual((Path(self.tmp.name) / "install-ran").read_text().count("run"), 2)

    def test_an_install_that_exits_0_without_updating_the_deploy_clone_is_not_installed(self):
        home, env = self.ai_tools_deploy_clone()
        self.install_script(home, "echo 'deploy clone is dirty; skipping update' >&2\nexit 0")
        before = self.git(home, "rev-parse", "HEAD")
        self.run_followup("drain", env=self.repair_env(**env))
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertIn("does not contain", st["validation"]["install"])
        self.assertIn("install failed", self.report()[-1]["reason"])
        self.assertEqual(self.git(home, "rev-parse", "HEAD"), before)
        self.assertEqual(st["findings"][0]["status"], "open")

    def test_repairs_in_one_repo_are_serialized(self):
        for stamp in ("20261014-050000", "20261014-060000"):
            self.shipped_item(stamp)
            self.run_followup("hook", "item-done", "--stamp", stamp)
        self.set("stub-worker.json", {"mode": "ok", "sleep": 1.5})
        threads = [threading.Thread(
            target=self.run_followup, args=("drain", "--wait-s", "60"),
            kwargs={"env": self.repair_env()}) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        spans = sorted((c["start"], c["end"]) for c in self.calls("worker"))
        self.assertEqual(len(spans), 2)
        self.assertLessEqual(spans[0][1], spans[1][0])
        self.assertEqual({self.state(k)["status"] for k in (
            "cleanup-20261014-050000", "cleanup-20261014-060000")}, {"done"})

    def test_quota_during_the_repair_defers_with_a_ticket(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "quota"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "deferred")
        [t] = self.calls("jlin")
        self.assertEqual((t["repo"], t["state"]), ("demo", "backlog"))
        self.assertIn("resets 2026-10-14T12:00:00", t["description"])
        self.assertIn("Next step: `python3 ~/.claude/automations/advance-roadmap/followup.py drain --repo demo`",
                      t["description"])
        self.assertIn('"finding_ids": ["' + st["findings"][0]["id"], t["description"])
        self.assertEqual(st["findings"][0]["ticket"], "DEV-900")
        self.assertEqual(self.calls("review"), [])

    def test_quota_during_the_review_defers_with_the_branch_and_pr(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        out = {"reviewer": {"codex": "over", "cursor": "over", "claude": "over"},
               "reset_at": "2026-10-14T15:00:00", "reset_epoch": time.time() + 7200}
        self.set("stub-worker.json", {"mode": "ok", "after_usage": out})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["next"]["step"]), ("deferred", "review"))
        [t] = self.calls("jlin")
        self.assertIn("pull/7", t["description"])
        self.assertIn(st["branch"], t["description"])
        self.assertIn("resets 2026-10-14T15:00:00", t["description"])
        self.assertIn("it resumes at review round 1", t["description"])
        self.assertIn('"builder_provider": "cursor"', t["description"])
        self.assertEqual(self.calls("review"), [])
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(len(self.calls("jlin")), 1)

    def test_a_builder_with_no_free_reviewer_is_not_started(self):
        """feasibility must match review.sh: cursor is never a reviewer (REVIEWERS owner)."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # Only cursor is free as a "reviewer"; review.sh would never pick it for any builder.
        self.set("stub-usage.json", {"reviewer": {"codex": "over", "claude": "limited", "cursor": "ok"},
                                     "reset_at": "2026-10-14T13:00:00", "reset_epoch": time.time() + 600})
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(self.calls("worker"), [])
        self.assertEqual(st["status"], "deferred")
        self.assertTrue(st.get("quota"))

    def test_codex_builder_not_started_when_only_cursor_reviewer_is_free(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # Workers: only codex free. Reviewers: only cursor free — review.sh reviews codex with claude only.
        self.set("stub-usage.json", {
            "worker": {"codex": "ok", "cursor": "over", "claude": "over"},
            "reviewer": {"cursor": "ok", "codex": "over", "claude": "over"},
            "reset_at": "2026-10-14T13:00:00", "reset_epoch": time.time() + 600})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(self.calls("worker"), [])
        self.assertEqual(st["status"], "deferred")

    def test_child_env_strips_batch_budget_vars_from_launcher(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        env = self.repair_env(
            ADVANCE_ROADMAP_BUDGET_REVIEWERS="codex",
            ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY="cross-provider",
            ADVANCE_ROADMAP_BUDGET_REVIEW_FALLBACK_REASON="x",
            ADVANCE_ROADMAP_INCREMENTAL_PHASE="reviewing",
        )
        self.run_followup("drain", env=env)
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        [launcher] = (self.root / "followups" / st["key"]).glob("build-*/worker-launch.zsh")
        text = launcher.read_text()
        for bad in ("ADVANCE_ROADMAP_BUDGET_REVIEWERS", "ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY",
                    "ADVANCE_ROADMAP_BUDGET_REVIEW_FALLBACK_REASON", "ADVANCE_ROADMAP_INCREMENTAL_PHASE"):
            self.assertNotIn(bad, text)

    def test_unreadable_pr_checks_are_retryable_not_blocked(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "unreadable", "deploy": "success"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending", self.report())
        self.assertEqual(st["validation"]["checks"], "unreadable")
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success"})
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done", self.report())

    def test_an_unavailable_review_is_not_quota_when_only_the_builder_is_out(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-usage.json", {"reviewer": {"cursor": "over"}, "reset_at": "2026-10-14T13:00:00",
                                     "reset_epoch": time.time() + 600})
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["builder_provider"], "cursor")
        self.assertEqual((st["status"], st["next"]["step"]), ("pending", "review"))
        self.assertEqual(self.calls("jlin"), [])
        self.assertIsNone(st.get("quota"))
        self.assertEqual(self.report()[-1]["kind"], "review-unavailable")
        self.set("stub-usage.json", {"reviewer": {"cursor": "over", "claude": "limited"},
                                     "reset_at": "2026-10-14T13:00:00", "reset_epoch": time.time() + 600})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["quota"]["provider"]), ("deferred", "claude"))
        self.assertEqual(len(self.calls("jlin")), 1)
        self.assertNotIn("cursor", st["quota"]["cause"])

    def test_a_review_round_limit_defers_even_when_the_shared_quota_record_fails(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        reset = time.time() + 900
        self.set("stub-usage.json", {"quota_record_fails": True})
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True, "limited": [
            {"provider": "codex", "cause": "You've hit your usage limit", "reset_epoch": reset,
             "reset_at": "2026-10-14T13:15:00"},
            {"provider": "cursor", "cause": "excluded: the builder", "reset_epoch": reset - 600}]})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["builder_provider"], "cursor")
        self.assertEqual((st["status"], st["quota"]["provider"]), ("deferred", "codex"))
        self.assertEqual(st["quota"]["reset_at"], "2026-10-14T13:15:00")
        self.assertNotIn("cursor", st["quota"]["cause"])

    def test_worker_exit_3_with_every_cli_missing_is_a_local_blocker_not_quota(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "missing"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual(self.calls("jlin"), [])
        self.assertIn("missing", self.report()[-1]["reason"])

    def test_quota_status_without_result_is_adopted_after_drain_dies(self):
        """A finished quota builder may leave only worker-status.json; adopt it, don't re-dispatch."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        key = "cleanup-20261014-050000"
        st = self.state(key)
        st.update(status="running", next={"step": "build", "round": 1}, attempts=1)
        (self.root / "followups" / key / "state.json").write_text(json.dumps(st))
        bdir = self.root / "followups" / key / "build-r1-a1"
        bdir.mkdir(parents=True)
        (bdir / "worker.exit").write_text("3\n")
        (bdir / "worker-status.json").write_text(json.dumps({
            "used": None, "limited": [{"provider": "cursor", "state": "limited"}],
            "quota": {"role": "worker", "provider": "cursor", "reset_epoch": time.time() + 3600,
                      "reset_at": "2026-10-14T12:00:00", "cause": "You've hit your usage limit"}}))
        # No worker-result.json — the quota path does not write one.
        self.assertFalse((bdir / "worker-result.json").exists())
        self.run_followup("drain", env=self.repair_env())
        st = self.state(key)
        self.assertEqual(st["status"], "deferred", self.report())
        self.assertEqual(st["quota"]["provider"], "cursor")
        self.assertEqual(self.calls("worker"), [])  # adopted; never re-dispatched
        self.assertTrue((bdir / "consumed").exists())

    def test_a_builder_that_outlives_its_timeout_blocks_every_retry_until_it_exits(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.py_wrapper(Path(self.tmp.name), "workspaces").rename(self.root / "lib" / "workspaces.py")
        self.set("stub-worker.json", {"mode": "ok", "sleep": 4})
        env = self.repair_env(ADVANCE_ROADMAP_SUPERSET="1", ADVANCE_ROADMAP_WORKER_TIMEOUT_MIN="0.02",
                              ADVANCE_ROADMAP_WORKER_POLL_S="0.1")
        self.run_followup("drain", env=env)
        key = "cleanup-20261014-050000"
        self.assertEqual(self.state(key)["status"], "pending")
        self.assertIn("still running", self.report()[-1]["reason"])
        registry = self.root / "inflight" / "demo" / f"followup-{key}.pid"
        self.assertTrue(registry.exists())
        out = self.run_followup("drain", env=env).stdout
        self.assertIn("still running", out)
        self.assertEqual(len(self.calls("workspaces")), 1)  # never a second builder beside the live one
        deadline = time.time() + 30
        while not list((self.root / "followups" / key).glob("build-*/worker.exit")) and time.time() < deadline:
            time.sleep(0.2)
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=env)
        self.assertEqual(self.state(key)["status"], "done", self.report())
        # Finished build artifacts are adopted — worker.sh is not dispatched again.
        self.assertEqual(len(self.calls("workspaces")), 1)
        self.assertFalse(registry.exists())

    def start_plain_build_then_kill_the_drain(self, key, sleep):
        self.set("stub-worker.json", {"mode": "ok", "sleep": sleep})
        drain = subprocess.Popen(
            [sys.executable, str(FOLLOWUP), "drain"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env={**self.env, **self.repair_env()})
        deadline = time.time() + 30
        while not list((self.root / "followups" / key).glob("build-*/worker.pid")) and time.time() < deadline:
            time.sleep(0.1)
        drain.kill()
        drain.wait()
        [bdir] = (self.root / "followups" / key).glob("build-*")
        return bdir

    def test_a_plain_worktree_builder_is_registered_and_never_doubled_after_its_drain_dies(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        key = "cleanup-20261014-050000"
        bdir = self.start_plain_build_then_kill_the_drain(key, 3)
        registry = self.root / "inflight" / "demo" / f"followup-{key}.pid"
        self.assertTrue(registry.exists())
        os.kill(int(registry.read_text().strip()), 0)
        self.assertIn("demo", supervisor.inflight_repos(self.root))
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state(key)["status"], "running")
        self.assertEqual(len(list((self.root / "followups" / key).glob("build-*"))), 1)
        deadline = time.time() + 30
        while not (bdir / "worker.exit").exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(registry.exists())
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state(key)["status"], "done", self.report())

    def test_a_dead_launchers_builder_group_is_reaped_before_recovery(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        key = "cleanup-20261014-050000"
        bdir = self.start_plain_build_then_kill_the_drain(key, 60)
        launcher = int((bdir / "worker.pid").read_text().strip())
        pgid = int((bdir / "worker.pgid").read_text().strip())
        self.assertEqual(pgid, launcher)
        os.kill(launcher, 9)
        deadline = time.time() + 10
        while pid_alive(launcher) and time.time() < deadline:
            time.sleep(0.1)
        os.killpg(pgid, 0)
        self.set("stub-worker.json", {"mode": "ok"})
        out = self.run_followup("drain", env=self.repair_env()).stdout
        self.assertIn("reaped the builder", out)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)
        st = self.state(key)
        self.assertEqual(st["status"], "done", self.report())
        self.assertTrue(any(b["kind"] == "interrupted" for b in st["blockers"]))
        self.assertFalse((self.root / "inflight" / "demo" / f"followup-{key}.pid").exists())
        self.assertEqual(len(self.calls("worker")), 1)

    def test_a_drain_from_a_workspace_terminal_never_builds_in_that_workspace(self):
        self.py_wrapper(Path(self.tmp.name), "workspaces").rename(self.root / "lib" / "workspaces.py")
        decoy = Path(self.tmp.name) / "decoy"
        subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=self.env)
        for superset, stamp in (("1", "20261014-050000"), ("0", "20261014-060000")):
            with self.subTest(superset=superset):
                self.shipped_item(stamp)
                self.run_followup("hook", "item-done", "--stamp", stamp)
                self.run_followup("drain", env=self.repair_env(
                    ADVANCE_ROADMAP_SUPERSET=superset, SUPERSET_WORKSPACE_PATH=str(decoy)))
                key = f"cleanup-{stamp}"
                self.assertEqual(self.state(key)["status"], "done", self.report())
                self.assertEqual(list(decoy.glob("fix-*.txt")), [])
                [launcher] = (self.root / "followups" / key).glob("build-*/worker-launch.zsh")
                self.assertNotIn(str(decoy), launcher.read_text())

    def test_fixture_and_builder_commits_ignore_the_users_commit_signing(self):
        with tempfile.TemporaryDirectory() as d:
            hostile = Path(d) / "gitconfig"
            hostile.write_text("[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n"
                               "[gpg]\n\tformat = ssh\n[user]\n\tsigningkey = /nonexistent/key.pub\n")
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(hostile)}):
                self.tearDown()
                self.setUp()
                self.shipped_item("20261014-050000")
                self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
                self.run_followup("drain", env=self.repair_env())
        self.assertEqual(len(self.calls("worker")), 1)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done")

    def test_a_recovered_pr_with_no_recorded_builder_is_never_reviewed(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # Scan once without repair so findings exist, then resume at review with no builder.
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertTrue(st["findings"])
        st.update(status="pending", next={"step": "review", "round": 1},
                  branch="roadmap/followup-cleanup-20261014-050000",
                  pr_url="https://github.com/jnelken/demo/pull/7",
                  findings=[{**st["findings"][0], "status": "open"}],
                  validation={"reviews": []}, sessions=[], scanned=True)
        st.pop("builder_provider", None)
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.run_followup("drain", env=self.repair_env())
        st = self.state(st["key"])
        self.assertEqual(st["status"], "blocked")
        self.assertEqual(self.calls("review"), [])
        self.assertEqual(self.report()[-1]["kind"], "unknown-builder")

    def test_builder_that_ships_is_self_merged_hard_stop(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "ok", "outcome": "shipped"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "blocked")
        self.assertEqual(self.report()[-1]["kind"], "builder-self-merged")
        self.assertEqual(self.calls("review"), [])
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))

    def test_failed_builder_validation_redispatches_on_retry(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "ok", "validation": "failed"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual(len(self.calls("worker")), 1)
        self.assertTrue(list((self.root / "followups" / st["key"]).glob("build-*/consumed")))
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(len(self.calls("worker")), 2)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done", self.report())

    def test_skipped_deploy_check_is_not_success(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {
            "checks": "pass", "deploy": "success",
            "check_runs": [{"name": "Vercel", "status": "completed", "conclusion": "skipped"}],
        })
        self.run_followup("drain", env=self.repair_env(ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S="0.5"))
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st.get("validation", {}).get("deploy"), "success")
        self.assertNotEqual(st["status"], "done")
        self.assertTrue(st["findings"][0]["status"] in ("open", "deferred"))

    def test_skipped_deploy_check_blocks_status_success(self):
        """A success commit status must not win over a skipped deploy-named check run."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {
            "checks": "pass", "deploy": "success",
            "statuses": [{"context": "Vercel", "state": "success"}],
            "check_runs": [{"name": "Vercel", "status": "completed", "conclusion": "skipped"}],
        })
        self.run_followup("drain", env=self.repair_env(ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S="0.5"))
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st.get("validation", {}).get("deploy"), "success")
        self.assertNotEqual(st["status"], "done")

    def test_latest_deploy_status_per_context_wins(self):
        """An older failed Vercel status must not block a later success for the same context."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {
            "checks": "pass", "deploy": "success",
            "statuses": [
                {"context": "Vercel", "state": "failure", "updated_at": "2026-10-14T10:00:00Z"},
                {"context": "Vercel", "state": "success", "updated_at": "2026-10-14T10:05:00Z"},
            ],
            "check_runs": [],
        })
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        self.assertEqual(st["validation"]["deploy"], "success")

    def test_deploy_prefers_updated_at_over_created_at(self):
        """updated_at wins even when an older failure was created later than a success."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {
            "checks": "pass", "deploy": "success",
            "statuses": [
                # Success was updated most recently; its created_at is older.
                {"context": "Vercel", "state": "success",
                 "created_at": "2026-10-14T09:00:00Z", "updated_at": "2026-10-14T11:00:00Z"},
                # Failure created later but updated earlier — must not win.
                {"context": "Vercel", "state": "failure",
                 "created_at": "2026-10-14T10:30:00Z", "updated_at": "2026-10-14T10:30:00Z"},
            ],
            "check_runs": [],
        })
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        self.assertEqual(st["validation"]["deploy"], "success")

    def test_in_progress_check_run_beats_older_completed_success(self):
        """A newer started check run must win over an older completed success."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {
            "checks": "pass", "deploy": "success",
            "statuses": [],
            "check_runs": [
                {"name": "Vercel", "status": "completed", "conclusion": "success",
                 "started_at": "2026-10-14T10:00:00Z", "completed_at": "2026-10-14T10:05:00Z"},
                {"name": "Vercel", "status": "in_progress",
                 "started_at": "2026-10-14T10:10:00Z"},
            ],
        })
        self.run_followup("drain", env=self.repair_env(ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S="0.5"))
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st.get("validation", {}).get("deploy"), "success")
        self.assertNotEqual(st["status"], "done")

    def test_deploy_config_read_from_merge_commit_not_dirty_tree(self):
        """A vercel.json added by the PR must prevent deploy:none even if root checkout lacks it."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # Builder commits vercel.json on the follow-up branch; root checkout stays without it.
        self.set("stub-worker.json", {"mode": "ok", "extra_files": {"vercel.json": "{}\n"}})
        self.set("stub-gh.json", {"checks": "pass", "deploy": "none", "merge_commit": "head"})
        self.run_followup("drain", env=self.repair_env(ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_NONE_S="0",
                                                       ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S="0.5"))
        st = self.state("cleanup-20261014-050000")
        # Has config at merge commit → empty APIs are timeout/unverified, never "none".
        self.assertNotEqual(st.get("validation", {}).get("deploy"), "none")
        self.assertNotEqual(st["status"], "done")

    def test_nonzero_builder_exit_is_rejected_despite_pr_result(self):
        """Exit nonzero with a blocked-branch-left result must not proceed to review/merge."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "ok", "exit": 1})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending", self.report())
        self.assertEqual(self.calls("review"), [])
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.assertIn("exited 1", self.report()[-1]["reason"])
        # Retry with a clean exit succeeds.
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done", self.report())
        self.assertEqual(len(self.calls("worker")), 2)

    def test_builder_reported_wrong_branch_is_rejected(self):
        """Stage key owns the branch; a builder cannot rename it into land()."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "ok", "branch": "roadmap/attacker"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending", self.report())
        self.assertEqual(self.calls("review"), [])
        self.assertIn("roadmap/attacker", self.report()[-1]["reason"])
        self.assertIn("expected", self.report()[-1]["reason"])

    def test_clean_review_log_with_nonzero_exit_cannot_merge(self):
        """A clean reviews.jsonl row is not mergeable when review.sh exits nonzero."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "exit": 1})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st["status"], "done", self.report())
        self.assertIsNone(st.get("reviewed_sha"))
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.assertIn("review", self.report()[-1]["kind"])

    def test_findings_review_log_with_nonzero_exit_is_error_not_rebuild(self):
        """A findings log row with review.sh exit 1 must not burn another builder round."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["findings"], "exit": 1})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(len(self.calls("worker")), 1)  # no second builder
        self.assertEqual(self.calls("review")[-1].get("round"), 1)
        self.assertIn("review", self.report()[-1]["kind"])
        self.assertNotEqual(st.get("next", {}).get("step"), "build")

    def test_successful_build_persists_review_step_immediately(self):
        """After build ok, next=review is on disk before review runs (crash recovery)."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["next"]["step"], "review")
        self.assertEqual(len(self.calls("worker")), 1)
        # A second drain must not re-dispatch the builder — review step was persisted.
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(len(self.calls("worker")), 1)
        self.assertEqual(self.state("cleanup-20261014-050000")["next"]["step"], "review")

    def test_unreadable_pr_head_does_not_clear_reviewed_sha(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "head_unreadable": True})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertIsNotNone(st.get("reviewed_sha"))
        self.assertEqual(st["next"]["step"], "land")
        self.assertEqual(len(self.calls("review")), 1)
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success"})
        self.run_followup("drain", env=self.repair_env())
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done", self.report())
        self.assertEqual(len(self.calls("review")), 1)  # no extra review round

    def test_already_merged_pr_is_adopted_after_drain_dies(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # First pass: merge succeeds, then deploy fails so we stop with merged persisted.
        self.set("stub-gh.json", {"checks": "pass", "deploy": "failure", "merge_commit": "head"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertTrue(st.get("merged"))
        self.assertTrue(st.get("merge_commit"))
        # Simulate a kill that lost in-memory state but left disk: clear merged, keep sha gone,
        # force merge to fail while PR is already MERGED at the reviewed head.
        st.pop("merged", None)
        st.pop("merge_commit", None)
        st["status"] = "pending"
        st["next"] = {"step": "land", "round": 1}
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "merge_fails": True,
                                  "pr_state": "MERGED", "merge_commit": "head"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        self.assertTrue(st.get("merged"))

    def test_already_merged_pr_with_wrong_head_is_not_adopted(self):
        """An external MERGED tip that is not reviewed_sha must not finish the stage."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # Reach land with a clean review, then simulate an external merge at a different tip.
        self.set("stub-gh.json", {"checks": "pass", "deploy": "failure", "merge_commit": "head"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertTrue(st.get("merged"))
        reviewed = st["reviewed_sha"]
        st.pop("merged", None)
        st.pop("merge_commit", None)
        st["status"] = "pending"
        st["next"] = {"step": "land", "round": 1}
        st["reviewed_sha"] = reviewed
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "merge_fails": True,
                                  "pr_state": "MERGED", "merged_head": "a" * 40, "merge_commit": "head"})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st["status"], "done", self.report())
        self.assertFalse(st.get("merged"))
        self.assertIn("not the reviewed commit", self.report()[-1]["reason"])
        self.assertEqual(st["next"]["step"], "review")

    def test_take_repo_lock_preclaims_high_thread_slots(self):
        """Repair holds t1…ceiling so a later run.sh THREAD=8 cannot appear beside it."""
        import followup as fu
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        fu.ROOT = self.root
        lock = fu.take_repo_lock("demo")
        try:
            self.assertTrue(lock and lock.ok and lock.claims)
            ceiling = fu.REPO_THREAD_LOCK_CEILING
            held = {Path(c.fh.name).name for c in lock.claims}
            self.assertIn("demo.lock", held)
            for i in range(1, ceiling + 1):
                self.assertIn(f"demo.t{i}.lock", held)
        finally:
            lock.close()

    def test_claim_flock_contends_with_zsystem_flock(self):
        """Claim and run.sh must share flock(2) — a zsh holder blocks take_repo_lock."""
        import followup as fu
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        fu.ROOT = self.root
        lock_path = self.root / "repo-locks" / "demo.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.touch()
        # Hold with zsystem flock like run.sh, then Claim must fail.
        holder = subprocess.Popen(
            ["zsh", "-c",
             f'zmodload zsh/system; exec {{FD}}<>{lock_path}; '
             f'zsystem flock -e -f FD {lock_path}; sleep 30'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                # Wait until the holder has the lock.
                probe = fu.Claim(lock_path)
                if not probe.ok:
                    break
                probe.close()
                time.sleep(0.05)
            else:
                self.fail("zsh flock holder never acquired the lock")
            lock = fu.take_repo_lock("demo")
            self.assertTrue(lock is None or not lock.ok or not lock.claims)
        finally:
            holder.kill()
            holder.wait(timeout=5)

    def test_eligible_reviewers_excludes_prior_builders(self):
        """Owner-policy table: eligible_reviewers never returns a prior builder."""
        import followrepair
        cases = [
            # (builder, excluded, expected)
            ("cursor", (), ("codex", "claude")),
            ("cursor", {"codex"}, ("claude",)),
            ("cursor", {"codex", "claude"}, ()),
            ("cursor", {"cursor"}, ("codex", "claude")),  # current worker isn't in REVIEWERS[self]
            ("codex", {"claude"}, ()),
            ("claude", {"codex"}, ()),
            ("claude", (), ("codex",)),
            ("unknown", {"codex"}, ()),
        ]
        for builder, excluded, want in cases:
            with self.subTest(builder=builder, excluded=excluded):
                self.assertEqual(followrepair.eligible_reviewers(builder, excluded), want)

    def test_verify_pr_target_policy(self):
        """Owner-policy table: verify_pr_target accepts only the stage's repo/head/base."""
        import followrepair as fr
        repo = self.code / "demo"
        st = {"key": "cleanup-demo", "repo": "demo", "branch": "roadmap/followup-cleanup-demo",
              "pr_url": "https://github.com/jnelken/demo/pull/7"}
        self.set("stub-gh.json", {"head_ref": "roadmap/followup-cleanup-demo", "base_ref": "main"})
        # Point followrepair.gh at the stub for in-process calls.
        stub = self.env["ADVANCE_ROADMAP_GH_BIN"]
        env = {"ADVANCE_ROADMAP_ROOT": str(self.root)}
        with patch.dict(os.environ, env), patch.object(fr, "GH", stub):
            ok, _ = fr.verify_pr_target(st, repo)
            self.assertTrue(ok)
            st_bad = {**st, "pr_url": "https://github.com/jnelken/other/pull/1"}
            ok, reason = fr.verify_pr_target(st_bad, repo)
            self.assertFalse(ok)
            self.assertIn("not in jnelken/demo", reason)
            self.set("stub-gh.json", {"head_ref": "wrong-branch", "base_ref": "main"})
            ok, reason = fr.verify_pr_target(st, repo)
            self.assertFalse(ok)
            self.assertIn("wrong-branch", reason)
            self.set("stub-gh.json", {"head_ref": "roadmap/followup-cleanup-demo", "base_ref": "develop"})
            ok, reason = fr.verify_pr_target(st, repo)
            self.assertFalse(ok)
            self.assertIn("develop", reason)

    def test_land_refuses_pr_for_wrong_repo(self):
        """Entry-point delegation: land() must not merge a PR outside the stage repo."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["next"]["step"], "review")
        st["pr_url"] = "https://github.com/jnelken/other/pull/99"
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.set("stub-review.json", {"verdicts": ["clean"]})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertNotEqual(st["status"], "done", self.report())
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.assertIn("not in jnelken/demo", self.report()[-1]["reason"])

    def test_run_review_passes_prior_builders_as_exclude(self):
        """Entry-point delegation: run_review must pass --exclude-reviewers to review.sh."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["findings", "clean"]})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done", self.report())
        reviews = self.calls("review")
        self.assertEqual(len(reviews), 2)
        # Round 1: the cursor builder is already recorded before review runs.
        self.assertIn("cursor", reviews[0].get("exclude") or "")
        # Round 2: both builders (same provider in the stub) stay excluded.
        self.assertIn("cursor", reviews[1].get("exclude") or "")
        self.assertEqual(reviews[0].get("strategy"), "cross-provider")

    def test_run_review_fails_closed_when_ff_merge_fails(self):
        """A divergent review checkout must not invoke review.sh on a stale tip."""
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["next"]["step"], "review")
        # Divergent tips: origin and the review checkout each have a unique commit.
        wt = Path(self.env["ADVANCE_ROADMAP_FOLLOWUP_WORKTREES"]) / st["key"]
        self.assertTrue(wt.is_dir(), f"expected follow-up worktree at {wt}")
        base = self.git(wt, "rev-parse", "HEAD")
        (wt / "origin-only.txt").write_text("origin\n")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-qm", "origin-only")
        self.git(wt, "push", "-q", "origin", st["branch"])
        self.git(wt, "reset", "--hard", "-q", base)
        (wt / "stale-local.txt").write_text("local\n")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-qm", "local-only")
        self.set("stub-review.json", {"verdicts": ["clean"]})
        before = len(self.calls("review"))
        self.run_followup("drain", env=self.repair_env())
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending", self.report())
        self.assertEqual(len(self.calls("review")), before)  # never reached review.sh again
        self.assertIn("ff-merge", self.report()[-1]["reason"])

    # ── tooling analysis (slice 3c) ──

    def test_tooling_dry_run_on_the_oct13_evidence_keeps_only_quoted_analyst_findings(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "batch-verdicts").mkdir()
        (self.root / "batch-verdicts" / "apt-sqft.json").write_text(
            (FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        good = {"title": "Bookkeeping summary blames the orchestrator limit", "kind": "misleading-surface",
                "fix": "Name the quota in the alert",
                "evidence": [{"source": str(self.root / "runs.jsonl"),
                              "quote": "the orchestrator hit its session limit before triage"}]}
        bad = {"title": "Invented friction",
               "evidence": [{"source": str(run / "orchestrator-result.json"),
                             "quote": "this text is nowhere in the corpus"}]}
        (self.root / "stub-analyst.txt").write_text(
            "thinking...\n```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [good, bad], "non_findings": [
                {"title": "planner retried", "why": "by design"}]}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        kinds = {f["kind"] for f in doc["findings"]}
        self.assertIn("misfiring-automation", kinds)   # worker after a failed plan
        self.assertIn("misleading-surface", kinds)     # quota recorded as error; the analyst's finding
        self.assertIn("wrong-knowledge", kinds)        # contaminated no-work verdict
        titles = [f["title"] for f in doc["findings"]]
        self.assertIn(good["title"], titles)
        self.assertNotIn(bad["title"], titles)
        self.assertTrue(any(n["title"] == bad["title"] and "rejected" in n["why"] for n in doc["non_findings"]))
        self.assertTrue(all(f["repo"] == "ai-tools" and f["evidence"] for f in doc["findings"]))
        self.assertEqual(doc["analyst"], "codex: answered")
        self.assertEqual(self.calls("worker"), [])
        self.assertFalse((self.root / "followups" / "tooling-batch-2026-10-13").exists())

    ANALYST_FINDING = {"title": "Bookkeeping summary blames the orchestrator limit", "kind": "misleading-surface",
                       "quote": "the orchestrator hit its session limit before triage"}

    def analyst_scan(self, answer_file, answer):
        """A tooling dry run whose analyst answers with a valid, quoted findings fence when `answer`."""
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / answer_file).write_text(self.analyst_fence() if answer else "I found nothing worth reporting.\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        return doc, [f["title"] for f in doc["findings"]]

    def analyst_fence(self):
        f = self.ANALYST_FINDING
        good = {"title": f["title"], "kind": f["kind"],
                "evidence": [{"source": str(self.root / "runs.jsonl"), "quote": f["quote"]}]}
        return "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [good]}) + "\n```\n"

    def test_analyst_findings_come_only_from_its_final_answer(self):
        # Codex's stdout carries a valid, quoted findings fence; its final answer (-o) has none.
        (self.root / "stub-codex-transcript.txt").write_text(self.analyst_fence())
        doc, titles = self.analyst_scan("stub-analyst.txt", answer=False)
        self.assertEqual(doc["analyst"], "codex: no findings block")
        self.assertNotIn(self.ANALYST_FINDING["title"], titles)

    def test_a_limited_analyst_saves_its_limit_first_then_fails_over(self):
        # Dry-run scan: parse-limit + stage-local hit only (no shared usage record-limit).
        (self.root / "stub-codex-limit").write_text("")
        self.set("stub-usage.json", {"record_limit_fails": True})
        doc, titles = self.analyst_scan("stub-claude-analyst.txt", answer=True)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], titles)
        cmds = [c.get("cmd") for c in self.calls("usage")]
        self.assertIn("parse-limit", cmds)
        self.assertEqual(self.calls("usage-record-limit"), [])

    def test_drain_analyst_limit_records_shared_usage(self):
        # Non-dry-run path still gates the provider for everyone else.
        import followanalyst
        import followrepair
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        followrepair.ROOT = self.root
        followrepair.STATE = self.root / "followups"
        (self.root / "lib").mkdir(exist_ok=True)
        st = {"key": "cleanup-limit", "dry_run": False}
        (self.root / "followups" / st["key"]).mkdir(parents=True)
        followanalyst.record_analyst_limit(st, "codex", "You've hit your usage limit.")
        self.assertEqual([(c["provider"], c["scope"]) for c in self.calls("usage-record-limit")],
                         [("codex", "analyst")])

    def test_dry_run_gate_does_not_mutate_shared_usage_file(self):
        import followanalyst
        import followrepair
        import usage as usagelib
        # Point followrepair.usage at the real usage.py (not the stub) so --readonly is exercised.
        real_usage = ADV / "lib" / "usage.py"
        (self.root / "lib" / "usage.py").write_text(real_usage.read_text())
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        followrepair.ROOT = self.root
        followrepair.STATE = self.root / "followups"
        usagelib.save(self.root, usagelib.empty_state())
        path = usagelib.usage_path(self.root)
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        with mock.patch.object(followanalyst, "run_analyst",
                               return_value=("ok", self.analyst_fence(), 0, None)):
            st = {"key": "dry-gate", "dry_run": True, "stage": "tooling-hardening",
                  "repo": "ai-tools", "children": ["20261013-103004"], "usage_snapshots": []}
            (self.root / "followups" / st["key"]).mkdir(parents=True)
            (self.root / "runs.jsonl").write_text(
                json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
            followanalyst.analyze(st, [], [self.root / "runs.jsonl"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_readonly_gate_fails_closed_without_saved_usage(self):
        import followrepair
        import usage as usagelib
        real_usage = ADV / "lib" / "usage.py"
        (self.root / "lib" / "usage.py").write_text(real_usage.read_text())
        followrepair.ROOT = self.root
        path = usagelib.usage_path(self.root)
        path.unlink(missing_ok=True)
        gate = followrepair.gate_one("analyst", "codex", readonly=True)
        self.assertIsNotNone(gate)
        self.assertFalse(gate["available"])
        # Malformed file also fails closed.
        path.write_text("{not-json")
        gate = followrepair.gate_one("analyst", "claude", readonly=True)
        self.assertIsNotNone(gate)
        self.assertFalse(gate["available"])

    def test_analyst_ignores_an_echoed_limit_banner_when_its_final_answer_succeeded(self):
        # Codex stdout quotes a prior corpus log's banner; -o still has a real findings fence.
        (self.root / "stub-codex-transcript.txt").write_text(
            "reading runs.jsonl\nYou've hit your usage limit. Upgrade to Pro or try again at 7:00 PM.\n")
        doc, titles = self.analyst_scan("stub-analyst.txt", answer=True)
        self.assertEqual(doc["analyst"], "codex: answered")
        self.assertIn(self.ANALYST_FINDING["title"], titles)
        self.assertEqual(self.calls("usage-record-limit"), [])

    def test_analyst_disabled_preserves_deterministic_findings_only(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-analyst.txt").write_text(self.analyst_fence())
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp)
        doc = json.loads(p.stdout)
        self.assertIsNone(doc.get("analyst"))
        self.assertNotIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])
        self.assertEqual(self.calls("codex"), [])

    def test_analyst_skips_when_both_providers_are_gated(self):
        self.set("stub-usage.json", {"analyst": {"codex": "over", "claude": "over"}})
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-analyst.txt").write_text(self.analyst_fence())
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertIn("skipped:", doc["analyst"])
        self.assertNotIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])
        self.assertEqual(self.calls("codex"), [])

    def test_analyst_skips_when_usage_gate_is_unreadable(self):
        # Invariant owner: analyze() treats gate_one None as unavailable (fail closed).
        self.set("stub-usage.json", {"fail": True})
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-analyst.txt").write_text(self.analyst_fence())
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertIn("skipped:", doc["analyst"])
        self.assertEqual(self.calls("codex"), [])

    def test_analyst_rejects_finding_with_any_malformed_evidence_entry(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        mixed = {"title": self.ANALYST_FINDING["title"], "kind": self.ANALYST_FINDING["kind"],
                 "evidence": [{"source": str(self.root / "runs.jsonl"),
                               "quote": self.ANALYST_FINDING["quote"]},
                              "not-a-dict"]}
        (self.root / "stub-analyst.txt").write_text(
            "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [mixed]}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertNotIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])
        self.assertTrue(any(n["title"] == self.ANALYST_FINDING["title"] and "rejected" in n["why"]
                            for n in doc["non_findings"]))

    def test_scan_dry_runs_use_distinct_stage_dirs(self):
        import followup as fu
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        os.environ["ADVANCE_ROADMAP_FOLLOWUP_ANALYST"] = "0"
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        keys = []
        real_stage_dir = fu.stage_dir

        def spy(key):
            keys.append(key)
            return real_stage_dir(key)

        args = argparse.Namespace(stage="tooling", stamp=[stamp], origin="batch-x")
        with mock.patch.object(fu, "stage_dir", side_effect=spy):
            with mock.patch.object(fu.time, "time_ns", side_effect=[111, 222]):
                with mock.patch.object(fu.os, "getpid", return_value=7):
                    fu.cmd_scan(args)
                    fu.cmd_scan(args)
        self.assertEqual(sorted(set(keys)), ["scan-batch-x-7-111", "scan-batch-x-7-222"])

    def test_analyst_ignores_final_answer_when_provider_exits_nonzero(self):
        (self.root / "stub-codex-fail").write_text("")
        (self.root / "stub-claude-analyst.txt").write_text("I found nothing worth reporting.\n")
        doc, titles = self.analyst_scan("stub-analyst.txt", answer=True)
        self.assertTrue(doc["analyst"].startswith("failed:") or "no findings" in doc["analyst"]
                        or doc["analyst"].startswith("claude:"), doc["analyst"])
        self.assertNotIn(self.ANALYST_FINDING["title"], titles)

    def test_analyst_runs_inside_corpus_sandbox(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-analyst.txt").write_text(self.analyst_fence())
        self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                          env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        calls = self.calls("codex")
        self.assertTrue(calls)
        cwd = calls[0].get("cwd") or ""
        self.assertIn("corpus-sandbox", cwd)

    def test_analyst_malformed_findings_collection_preserves_deterministic(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-analyst.txt").write_text(
            "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": {"title": "bad"}}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertEqual(doc["analyst"], "codex: malformed findings block")
        self.assertTrue(doc["findings"])  # deterministic findings remain
        self.assertNotIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])

    def test_analyst_falls_over_when_codex_fails_to_launch(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-claude-analyst.txt").write_text(self.analyst_fence())
        # Point Codex at a missing binary so run_analyst raises OSError; Claude should still answer.
        p = self.run_followup(
            "scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
            env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1",
                 "ADVANCE_ROADMAP_CODEX_BIN": str(self.root / "missing-codex-bin")})
        doc = json.loads(p.stdout)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])

    def test_analyst_rejects_non_string_evidence_source_without_raising(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        bad = {"title": "typed wrong", "kind": "analyst",
               "evidence": [{"source": 12345, "quote": "the orchestrator hit its session limit before triage"}]}
        (self.root / "stub-analyst.txt").write_text(
            "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [bad]}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertEqual(doc["analyst"], "codex: answered")
        self.assertNotIn("typed wrong", [f["title"] for f in doc["findings"]])
        self.assertTrue(any(n["title"] == "typed wrong" and "rejected" in n["why"] for n in doc["non_findings"]))

    def test_analyst_ignores_non_string_title(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        bad = {"title": 99, "kind": "analyst",
               "evidence": [{"source": str(self.root / "runs.jsonl"),
                             "quote": "the orchestrator hit its session limit before triage"}]}
        (self.root / "stub-analyst.txt").write_text(
            "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [bad]}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertTrue(all(isinstance(f["title"], str) for f in doc["findings"]))
        self.assertTrue(all(isinstance(n["title"], str) for n in doc["non_findings"]))

    def test_analyst_claude_skips_garbage_json_and_honors_is_error(self):
        (self.root / "stub-codex-limit").write_text("")
        (self.root / "stub-claude-prefix-garbage").write_text("")
        (self.root / "stub-claude-is-error").write_text("")
        doc, titles = self.analyst_scan("stub-claude-analyst.txt", answer=True)
        self.assertTrue(doc["analyst"].startswith("failed:"), doc["analyst"])
        self.assertNotIn(self.ANALYST_FINDING["title"], titles)

    def test_analyst_inline_runs_jsonl_keeps_only_stage_stamps(self):
        import followanalyst
        stamp = "20261013-103004"
        other = "20261012-999999"
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        (self.root / "runs.jsonl").write_text(
            json.dumps({**row, "stamp": other, "detail": "unrelated historical quote for another stage"}) + "\n"
            + json.dumps(row) + "\n")
        text, _ = followanalyst.inline_corpus([self.root / "runs.jsonl"], stamps=[stamp])
        self.assertIn(stamp, text)
        self.assertNotIn("unrelated historical quote for another stage", text)
        self.assertNotIn(other, text)

    def test_readonly_gate_rejects_wrong_shaped_usage_json(self):
        import followrepair
        import usage as usagelib
        real_usage = ADV / "lib" / "usage.py"
        (self.root / "lib" / "usage.py").write_text(real_usage.read_text())
        followrepair.ROOT = self.root
        usagelib.usage_path(self.root).write_text("[]\n")
        gate = followrepair.gate_one("analyst", "codex", readonly=True)
        self.assertIsNotNone(gate)
        self.assertFalse(gate["available"])
        usagelib.usage_path(self.root).write_text(json.dumps({"providers": {}}) + "\n")
        gate = followrepair.gate_one("analyst", "codex", readonly=True)
        self.assertFalse(gate["available"])

    def test_inline_runs_jsonl_skips_non_object_lines(self):
        import followanalyst
        stamp = "20261013-103004"
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        (self.root / "runs.jsonl").write_text(
            "[1,2,3]\n" + json.dumps(row) + "\n")
        text, _ = followanalyst.inline_corpus([self.root / "runs.jsonl"], stamps=[stamp])
        self.assertIn(stamp, text)

    def test_analyst_cwd_sandbox_has_no_corpus_files(self):
        import followanalyst
        import followrepair
        os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
        followrepair.ROOT = self.root
        followrepair.STATE = self.root / "followups"
        st = {"key": "empty-cwd", "children": ["20261013-103004"]}
        (self.root / "followups" / st["key"]).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text("{}\n")
        sandbox = followanalyst.empty_analyst_cwd(st)
        self.assertTrue(sandbox.is_dir())
        self.assertEqual(list(sandbox.iterdir()), [])

    def test_inline_corpus_truncates_by_utf8_bytes(self):
        import followanalyst
        path = self.root / "wide.txt"
        # 3-byte UTF-8 chars: 100 chars = 300 bytes.
        path.write_text("€" * 100)
        out, pasted = followanalyst.inline_corpus([path], max_bytes=120)
        self.assertLessEqual(len(out.encode("utf-8")), 120)
        self.assertIn("€", out)
        self.assertTrue(len(path.read_text().encode("utf-8")) > 120)
        self.assertTrue(pasted)

    def test_analyst_sandbox_prepare_failure_preserves_deterministic(self):
        import followanalyst
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        with mock.patch.object(followanalyst, "empty_analyst_cwd",
                               side_effect=OSError("disk full")):
            # In-process: patch is visible to followup.scan → analyze.
            import followup as fu
            os.environ["ADVANCE_ROADMAP_ROOT"] = str(self.root)
            os.environ["ADVANCE_ROADMAP_FOLLOWUP_ANALYST"] = "1"
            fu.ROOT = self.root
            fu.STATE = self.root / "followups"
            st = {"key": "scan-prep-fail", "stage": "tooling-hardening", "children": [stamp],
                  "origin": "batch-x", "repo": "ai-tools", "usage_snapshots": []}
            (self.root / "followups" / st["key"]).mkdir(parents=True)
            fu.scan(st)
        self.assertTrue(str(st.get("analyst") or "").startswith("failed: sandbox prepare"))
        self.assertTrue(st.get("findings"))

    def test_analyst_rejects_when_evidence_builder_raises(self):
        # evidence_holds normalizes whitespace; evidence() needs an exact file substring.
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        # Quote matches after whitespace fold but not as an exact contiguous span in the file.
        folded = "the   orchestrator    hit its session limit before triage"
        bad = {"title": "whitespace fold only", "kind": "analyst",
               "evidence": [{"source": str(self.root / "runs.jsonl"), "quote": folded}]}
        (self.root / "stub-analyst.txt").write_text(
            "```FOLLOWUP_FINDINGS_JSON\n" + json.dumps({"findings": [bad]}) + "\n```\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1"})
        doc = json.loads(p.stdout)
        self.assertNotIn("whitespace fold only", [f["title"] for f in doc["findings"]])
        self.assertTrue(any(n["title"] == "whitespace fold only" and "rejected" in n["why"]
                            for n in doc["non_findings"]))


if __name__ == "__main__":
    unittest.main()
