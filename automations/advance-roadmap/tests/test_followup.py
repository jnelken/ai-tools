"""followup.py report + tickets (DEV-242 slice 3a). Temp ADVANCE_ROADMAP_ROOT; one test per behavior."""
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
import followticket  # noqa: E402
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
                    "ADVANCE_ROADMAP_LINEAR_BIN": self.wrapper(t, "linear"),
                    "ADVANCE_ROADMAP_JLIN": str(self.py_wrapper(t, "jlin")),
                    "AI_TOOLS_HOME": str(t / "no-ai-tools"),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        (self.root / "lib" / "usage.py").write_text(
            f"import runpy, sys\nsys.argv = [{str(STUBS)!r}, 'usage', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUBS)!r}, run_name='__main__')\n")
        self.make_repo("demo")

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
            fcntl.lockf(held, fcntl.LOCK_EX)
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
            fcntl.lockf(held, fcntl.LOCK_EX)
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

    def test_skipped_deploy_is_never_success(self):
        """Invariant: validation.deploy must be success|none; skipped/error/pending never close."""
        import followup as fu
        for bad in ("skipped", "error", "pending", "failure", "unverified", "timeout"):
            st = {"merge_commit": "abc", "validation": {
                "checks": "passed", "reviews": [{"verdict": "clean"}], "deploy": bad}}
            self.assertFalse(fu.stage_repair_verified(st), bad)
        self.assertTrue(fu.stage_repair_verified({
            "merge_commit": "abc", "validation": {
                "checks": "passed", "reviews": [{"verdict": "clean"}], "deploy": "success"}}))
        self.assertTrue(fu.stage_repair_verified({
            "merged": True, "validation": {
                "checks": "none", "reviews": [{"verdict": "clean"}], "deploy": "none"}}))

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
        self.set("stub-linear.json", {"pages": [[{
            "identifier": "DEV-900", "title": "t", "description": "",
            "labels": {"nodes": [{"name": "demo", "parent": {"name": "repo"}}]}}]],
            "fail": ["issue update DEV-900"]})
        (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
        quota2 = {**quota, "provider": "codex"}
        fu.defer(st, findings, quota2, "builder round 1", issues=issues)
        self.assertTrue(st.get("pending_deferral_sync"), st)
        self.assertEqual(st.get("last_deferral"), first_note)
        self.set("stub-linear.json", {"pages": [[{
            "identifier": "DEV-900", "title": "t", "description": "",
            "labels": {"nodes": [{"name": "demo", "parent": {"name": "repo"}}]}}]]})
        self.assertTrue(fu.sync_deferral_tickets(
            st, findings, quota2, fu.next_step_text(st, "builder round 1"),
            fu.deferral_note(st, quota2, "builder round 1")))
        self.assertIsNone(st.get("pending_deferral_sync"))
        self.assertNotEqual(st.get("last_deferral"), first_note)
        self.assertIn("codex", st["last_deferral"])
        updates = [c for c in self.calls("linear")
                   if c.get("cmd") == "issue update DEV-900" and "--description-file" in (c.get("args") or [])]
        self.assertTrue(updates)
        self.assertEqual(followticket.parse_meta(updates[-1]["body"])["builder_provider"], "codex")

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
                    validation={"checks": "passed", "reviews": [{"verdict": "clean", "round": 1}],
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
              "merge_commit": "abc", "pr_url": "https://example/pr/1",
              "validation": {"checks": "passed", "reviews": [{"verdict": "clean"}], "deploy": "success"},
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
            "merge_commit": "abc",
            "validation": {"checks": "passed", "reviews": [{"verdict": "clean"}], "deploy": "success"},
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


if __name__ == "__main__":
    unittest.main()
