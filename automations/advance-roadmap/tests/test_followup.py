"""followup.py end to end, offline: a temp ADVANCE_ROADMAP_ROOT, a local bare repo standing in for
GitHub, and tests/followup_stubs.py for usage.py, worker.sh, review.sh, gh, linear, jlin.py and the
analyst. Nothing here reaches Linear or GitHub."""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

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
                    "ADVANCE_ROADMAP_SUPERSET": "0", "ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "0",
                    "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "0", "ADVANCE_ROADMAP_FOLLOWUP_POLL_S": "0.2",
                    "ADVANCE_ROADMAP_FOLLOWUP_WORKTREES": str(t / "worktrees"),
                    "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_NONE_S": "0", "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S": "2",
                    "ADVANCE_ROADMAP_FOLLOWUP_CHECKS_TIMEOUT_S": "2", "ADVANCE_ROADMAP_WORKER_POLL_S": "0.1",
                    "ADVANCE_ROADMAP_GH_BIN": self.wrapper(t, "gh"),
                    "ADVANCE_ROADMAP_LINEAR_BIN": self.wrapper(t, "linear"),
                    "ADVANCE_ROADMAP_CODEX_BIN": self.wrapper(t, "codex"),
                    "ADVANCE_ROADMAP_JLIN": str(self.py_wrapper(t, "jlin")),
                    "AI_TOOLS_HOME": str(t / "no-ai-tools"),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        (self.root / "lib" / "usage.py").write_text(
            f"import runpy, sys\nsys.argv = [{str(STUBS)!r}, 'usage', *sys.argv[1:]]\nrunpy.run_path({str(STUBS)!r}, run_name='__main__')\n")
        for name, tool in (("worker.sh", "worker"), ("review.sh", "review")):
            (self.root / name).write_text(f'#!/bin/sh\nexec {sys.executable} {STUBS} {tool} "$@"\n')
            (self.root / name).chmod(0o755)
        self.make_repo("demo")

    def tearDown(self):
        self.tmp.cleanup()

    # ── fixtures ──

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

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                              env=self.env).stdout.strip()

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
        """A run dir for an item whose merge added a review marker; returns its merge commit."""
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
            "deploy": {"status": "success"}}))
        return sha

    def set(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def run_followup(self, *args, env=None, check=True):
        p = subprocess.run([sys.executable, str(FOLLOWUP), *args], capture_output=True, text=True, timeout=180,
                           env={**self.env, **(env or {})})
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def state(self, key):
        return json.loads((self.root / "followups" / key / "state.json").read_text())

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
        return {"id": ident, "identifier": ident, "title": title, "description": description, "url": "",
                "state": {"name": "Backlog", "type": "backlog"},
                "labels": {"nodes": [{"name": repo, "parent": {"name": "repo"}}]}}

    def meta_ticket(self, ident, meta, repo="demo"):
        body = "Follow-up.\n\n```advance-roadmap-followup\n" + json.dumps(
            {"workflow": "advance-roadmap-followup", "repo": repo, **meta}) + "\n```\n"
        return self.legacy_ticket(ident, "Follow-up (project-cleanup): something", body, repo)

    # ── triggers ──

    def test_only_a_shipped_item_queues_cleanup_and_a_second_hook_is_a_no_op(self):
        for stamp, outcome in (("20261014-010000", "blocked-branch-left"), ("20261014-020000", "quota-paused"),
                               ("20261014-030000", "failed"), ("20261014-040000", "incomplete")):
            run = self.root / "runs" / stamp
            run.mkdir(parents=True)
            (run / "worker-result.json").write_text(json.dumps({"outcome": outcome, "repo": "demo"}))
            out = self.run_followup("hook", "item-done", "--stamp", stamp).stdout
            self.assertIn("recoverable work", out)
        self.assertFalse(list((self.root / "followups").glob("*/state.json")) if (self.root / "followups").exists() else [])
        self.shipped_item("20261014-050000", outcome="shipped-deploy-failed")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000", "--origin", "batch-x")
        again = self.run_followup("hook", "item-done", "--stamp", "20261014-050000", "--origin", "batch-x").stdout
        self.assertIn("already queued", again)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "pending")
        self.assertEqual(len(list((self.root / "followups").glob("*/state.json"))), 1)

    def test_recursion_is_suppressed_by_env_and_by_marker(self):
        self.shipped_item("20261014-050000")
        out = self.run_followup("hook", "item-done", "--stamp", "20261014-050000",
                                env={"ADVANCE_ROADMAP_FOLLOWUP": "1"}).stdout
        self.assertIn("recursion suppressed", out)
        self.run_followup("hook", "run-end", "--origin", "batch-x", "--stamp", "a", env={"ADVANCE_ROADMAP_FOLLOWUP": "1"})
        self.run_followup("drain", env={"ADVANCE_ROADMAP_FOLLOWUP": "1"})
        (self.root / "runs" / "20261014-050000" / "followup-child").write_text("cleanup-x\n")
        self.assertIn("recursion suppressed", self.run_followup("hook", "item-done", "--stamp", "20261014-050000").stdout)
        self.assertFalse((self.root / "followups").exists() and list((self.root / "followups").glob("*/state.json")))

    def test_cleanup_waits_for_the_repo_to_be_quiescent(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        lock = self.root / "repo-locks" / "demo.t2.lock"
        lock.parent.mkdir(parents=True)
        with open(lock, "a") as held:
            fcntl.lockf(held, fcntl.LOCK_EX)
            out = self.run_followup("drain").stdout
        self.assertIn("demo.t2.lock is held", out)
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "pending")
        pid = self.root / "inflight" / "demo" / "roadmap-x.pid"
        pid.parent.mkdir(parents=True)
        pid.write_text(str(os.getpid()))
        self.assertIn("timed out on", self.run_followup("drain").stdout)
        self.assertEqual(self.calls("worker"), [])
        pid.unlink()
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done")

    def test_tooling_runs_only_after_every_child_run_and_cleanup_finishes(self):
        self.run_followup("hook", "run-end", "--origin", "batch-2026-10-14", "--stamp", "s1", "--stamp", "s2")
        row = {"stamp": "s1", "outcome": "skipped-unchanged", "detail": "unchanged"}
        (self.root / "runs.jsonl").write_text(json.dumps(row) + "\n")
        self.assertIn("1 run(s) of batch-2026-10-14 have not recorded", self.run_followup("drain").stdout)
        self.assertEqual(self.state("tooling-batch-2026-10-14")["status"], "pending")
        self.shipped_item("s2")
        self.run_followup("hook", "item-done", "--stamp", "s2")
        with open(self.root / "runs.jsonl", "a") as f:
            f.write(json.dumps({**row, "stamp": "s2", "outcome": "shipped", "detail": "PR #4"}) + "\n")
        self.set("stub-gh.json", {"checks": "fail", "deploy": "success"})
        self.run_followup("drain", "--stage", "tooling")
        self.assertEqual(self.state("tooling-batch-2026-10-14")["status"], "pending")   # cleanup-s2 pending
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-s2")["status"], "blocked")
        tooling = self.state("tooling-batch-2026-10-14")
        self.assertTrue(tooling["scanned"])
        self.assertTrue(any(f["title"].startswith("a follow-up stage was blocked: PR checks failed")
                            for f in tooling["findings"]), tooling["findings"])
        self.assertTrue(any(n["title"] == "s1 skipped unchanged" for n in tooling["non_findings"]))
        # Its finding is ai-tools', and this machine has no ai-tools checkout to repair it in.
        self.assertEqual(tooling["status"], "blocked")
        self.assertEqual(self.report()[-1]["kind"], "unsafe-repo")

    def test_a_conductor_run_dir_counts_once_its_result_exists_and_feeds_the_corpus(self):
        run = Path(self.tmp.name) / "scratch" / "20261014-090000"
        run.mkdir(parents=True)
        self.run_followup("hook", "run-end", "--origin", "conductor-x", "--run-dir", str(run))
        self.assertIn("1 run(s) of conductor-x have not recorded", self.run_followup("drain", "--stage", "tooling").stdout)
        result = run.resolve() / "worker-result.json"
        result.write_text(json.dumps({"outcome": "failed", "repo": "demo", "summary": "builder crashed"}))
        self.run_followup("drain", "--stage", "tooling")
        st = self.state("tooling-conductor-x")
        self.assertTrue(st["scanned"])
        self.assertIn(str(result), st["corpus"])
        [f] = [f for f in st["findings"] if f["kind"] == "fragile-tooling"]
        self.assertEqual(f["evidence"][0]["source"], str(result))

    # ── repair lifecycle ──

    def test_a_clean_review_with_passing_checks_merges_and_records_the_repair(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.run_followup("drain")
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
        self.run_followup("drain")
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
                self.set("stub-linear.json", {"pages": [[self.meta_ticket("DEV-501", {**stamp_meta, "finding_ids": [fid]})]]})
            self.run_followup("drain")
            st = self.state("cleanup-20261014-050000")
            fid = st["findings"][0]["id"]
            self.assertEqual(st["status"], "blocked")   # left for a human, never merged past
            self.assertFalse(any(c["cmd"].startswith("issue update") for c in self.calls("linear")))
            st.update(status="pending")
            (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.assertEqual(st["validation"]["deploy"], "failed")
        progress = [c for c in self.calls("linear") if c["cmd"].startswith("issue comment") and c["issue"] == "DEV-501"]
        self.assertTrue(any("deploy is failed" in c["body"] for c in progress), progress)

    def test_an_interrupted_stage_resumes_where_it_stopped(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.set("stub-usage.json", {})
        self.run_followup("drain")   # builds, then review unavailable with nobody out → local report
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["next"]["step"], "review")
        st["status"] = "running"     # as if the drain had been killed mid-review
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.set("stub-review.json", {"verdicts": ["clean"]})
        self.run_followup("drain")
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
        self.run_followup("drain")
        self.assertEqual(self.state(st["key"])["status"], "running")
        self.assertEqual(self.calls("worker"), [])
        (build / "worker.exit").write_text("0")
        self.run_followup("drain")
        self.assertEqual(self.state(st["key"])["status"], "done")

    def test_a_builder_that_outlives_its_timeout_blocks_every_retry_until_it_exits(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.py_wrapper(Path(self.tmp.name), "workspaces").rename(self.root / "lib" / "workspaces.py")
        self.set("stub-worker.json", {"mode": "ok", "sleep": 4})
        env = {"ADVANCE_ROADMAP_SUPERSET": "1", "ADVANCE_ROADMAP_WORKER_TIMEOUT_MIN": "0.02",
               "ADVANCE_ROADMAP_WORKER_POLL_S": "0.1"}
        self.run_followup("drain", env=env)
        key = "cleanup-20261014-050000"
        self.assertEqual(self.state(key)["status"], "pending")
        self.assertIn("still running", self.report()[-1]["reason"])
        registry = self.root / "inflight" / "demo" / f"followup-{key}.pid"
        self.assertTrue(registry.exists())   # the shared registry every lane and drain reads
        out = self.run_followup("drain", env=env).stdout
        self.assertIn("still running", out)
        self.assertEqual(len(self.calls("workspaces")), 1)   # no second builder beside the live one
        deadline = time.time() + 30
        while not list((self.root / "followups" / key).glob("build-*/worker.exit")) and time.time() < deadline:
            time.sleep(0.2)
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain", env=env)
        self.assertEqual(self.state(key)["status"], "done", self.report())
        self.assertEqual(len(self.calls("workspaces")), 2)
        self.assertFalse(registry.exists())

    def test_a_builder_whose_drain_dies_before_seeing_its_pid_is_still_registered(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.py_wrapper(Path(self.tmp.name), "workspaces").rename(self.root / "lib" / "workspaces.py")
        self.set("stub-worker.json", {"mode": "ok", "sleep": 3})
        key = "cleanup-20261014-050000"
        # The drain sleeps a full minute before its first look at worker.pid, so it dies first.
        drain = subprocess.Popen([sys.executable, str(FOLLOWUP), "drain"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env={**self.env, "ADVANCE_ROADMAP_SUPERSET": "1",
                                                                 "ADVANCE_ROADMAP_WORKER_POLL_S": "60"})
        deadline = time.time() + 30
        while not list((self.root / "followups" / key).glob("build-*/worker.pid")) and time.time() < deadline:
            time.sleep(0.1)
        drain.kill()
        drain.wait()
        registry = self.root / "inflight" / "demo" / f"followup-{key}.pid"
        self.assertTrue(registry.exists())
        pid = int(registry.read_text().strip())
        os.kill(pid, 0)   # a live builder the drain never recorded
        self.assertIn("demo", supervisor.inflight_repos(self.root))
        while not list((self.root / "followups" / key).glob("build-*/worker.exit")) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(registry.exists())   # the launcher unregisters before recording its exit

    def start_plain_build_then_kill_the_drain(self, key, sleep):
        """A plain-worktree drain whose builder is mid-run when the drain is killed: the build dir."""
        self.set("stub-worker.json", {"mode": "ok", "sleep": sleep})
        drain = subprocess.Popen([sys.executable, str(FOLLOWUP), "drain"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=self.env)
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
        os.kill(int(registry.read_text().strip()), 0)   # the builder outlived its drain
        self.assertIn("demo", supervisor.inflight_repos(self.root))
        self.run_followup("drain")
        self.assertEqual(self.state(key)["status"], "running")   # left alone, never a second builder
        self.assertEqual(len(list((self.root / "followups" / key).glob("build-*"))), 1)
        deadline = time.time() + 30
        while not (bdir / "worker.exit").exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(registry.exists())
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain")
        self.assertEqual(self.state(key)["status"], "done", self.report())

    def test_a_dead_launchers_builder_group_is_reaped_before_recovery(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        key = "cleanup-20261014-050000"
        bdir = self.start_plain_build_then_kill_the_drain(key, 60)
        launcher = int((bdir / "worker.pid").read_text().strip())
        pgid = int((bdir / "worker.pgid").read_text().strip())
        self.assertEqual(pgid, launcher)
        os.kill(launcher, 9)   # the launcher dies; the builder it started keeps running
        deadline = time.time() + 10
        while pid_alive(launcher) and time.time() < deadline:
            time.sleep(0.1)
        os.killpg(pgid, 0)
        self.set("stub-worker.json", {"mode": "ok"})
        out = self.run_followup("drain").stdout
        self.assertIn("reaped the builder", out)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)
        st = self.state(key)
        self.assertEqual(st["status"], "done", self.report())
        self.assertTrue(any(b["kind"] == "interrupted" for b in st["blockers"]))
        self.assertFalse((self.root / "inflight" / "demo" / f"followup-{key}.pid").exists())
        self.assertEqual(len(self.calls("worker")), 1)   # only the recovery's builder finished

    def test_a_drain_from_a_workspace_terminal_never_builds_in_that_workspace(self):
        self.py_wrapper(Path(self.tmp.name), "workspaces").rename(self.root / "lib" / "workspaces.py")
        decoy = Path(self.tmp.name) / "decoy"
        subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=self.env)
        for superset, stamp in (("1", "20261014-050000"), ("0", "20261014-060000")):
            with self.subTest(superset=superset):
                self.shipped_item(stamp)
                self.run_followup("hook", "item-done", "--stamp", stamp)
                self.run_followup("drain", env={"ADVANCE_ROADMAP_SUPERSET": superset,
                                                "SUPERSET_WORKSPACE_PATH": str(decoy)})
                key = f"cleanup-{stamp}"
                self.assertEqual(self.state(key)["status"], "done", self.report())
                self.assertEqual(list(decoy.glob("fix-*.txt")), [])
                [launcher] = (self.root / "followups" / key).glob("build-*/worker-launch.zsh")
                self.assertNotIn(str(decoy), launcher.read_text())

    def test_a_pr_head_that_moved_after_the_review_is_re_reviewed_never_merged(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "head": "0" * 40})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["next"]["step"]), ("pending", "review"))
        self.assertIn("is not the reviewed commit", self.report()[-1]["reason"])
        self.assertFalse(any(c["args"][:2] == ["pr", "merge"] for c in self.calls("gh")))
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success"})
        self.run_followup("drain")
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
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["deploy"]), ("pending", "unverified"))
        self.assertEqual(st["findings"][0]["status"], "open")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "none"})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["deploy"]), ("done", "none"))
        self.assertEqual(len([c for c in self.calls("gh") if c["args"][:2] == ["pr", "merge"]]), 1)

    def ai_tools_deploy_clone(self):
        """An ai-tools item ready to drain, a deploy clone of its remote, and the env pointing at it.
        The stub reports the PR head as the merge commit, so the clone can really fetch it."""
        self.make_repo("ai-tools")
        self.shipped_item("20261014-050000", repo="ai-tools")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-gh.json", {"checks": "pass", "deploy": "success", "merge_commit": "head"})
        home = Path(self.tmp.name) / "ai-home"
        subprocess.run(["git", "clone", "-q", str(Path(self.tmp.name) / "remotes" / "ai-tools.git"), str(home)],
                       check=True, env=self.env)
        return home, {"AI_TOOLS_HOME": str(home), "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "1"}

    def install_script(self, home, body):
        script = home / "install.sh"
        script.write_text(f"#!/bin/sh\necho run >> {Path(self.tmp.name) / 'install-ran'}\n{body}\n")
        script.chmod(0o755)

    def test_an_ai_tools_repair_is_not_done_until_this_machine_installs_it(self):
        home, env = self.ai_tools_deploy_clone()
        self.install_script(home, "exit 1")
        self.run_followup("drain", env=env)
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual((st["validation"]["deploy"], st["validation"]["install"]), ("success", "install.sh exit 1"))
        self.assertIn("install failed", self.report()[-1]["reason"])
        branch = "roadmap/followup-cleanup-20261014-050000"
        self.install_script(home, f"git -C {home} fetch -q origin {branch} && "
                                  f"git -C {home} merge -q --ff-only FETCH_HEAD")
        self.run_followup("drain", env=env)
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["validation"]["install"]), ("done", "installed"))
        self.assertEqual(len([c for c in self.calls("gh") if c["args"][:2] == ["pr", "merge"]]), 1)
        self.assertEqual((Path(self.tmp.name) / "install-ran").read_text().count("run"), 2)

    def test_an_install_that_exits_0_without_updating_the_deploy_clone_is_not_installed(self):
        home, env = self.ai_tools_deploy_clone()
        self.install_script(home, "echo 'deploy clone is dirty; skipping update' >&2\nexit 0")
        before = self.git(home, "rev-parse", "HEAD")
        self.run_followup("drain", env=env)
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertIn("does not contain", st["validation"]["install"])
        self.assertIn("install failed", self.report()[-1]["reason"])
        self.assertEqual(self.git(home, "rev-parse", "HEAD"), before)   # the check itself is read-only
        self.assertEqual(st["findings"][0]["status"], "open")

    def test_repairs_in_one_repo_are_serialized(self):
        for stamp in ("20261014-050000", "20261014-060000"):
            self.shipped_item(stamp)
            self.run_followup("hook", "item-done", "--stamp", stamp)
        self.set("stub-worker.json", {"mode": "ok", "sleep": 1.5})
        threads = [threading.Thread(target=self.run_followup, args=("drain", "--wait-s", "60")) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        spans = sorted((c["start"], c["end"]) for c in self.calls("worker"))
        self.assertEqual(len(spans), 2)
        self.assertLessEqual(spans[0][1], spans[1][0])
        self.assertEqual({self.state(k)["status"] for k in ("cleanup-20261014-050000", "cleanup-20261014-060000")},
                         {"done"})

    # ── quota and blockers ──

    def test_quota_during_the_repair_defers_with_a_ticket(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "quota"})
        self.run_followup("drain")
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
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["next"]["step"]), ("deferred", "review"))
        [t] = self.calls("jlin")
        self.assertIn("pull/7", t["description"])
        self.assertIn(st["branch"], t["description"])
        self.assertIn("resets 2026-10-14T15:00:00", t["description"])
        self.assertIn("it resumes at review round 1", t["description"])
        self.assertIn('"builder_provider": "cursor"', t["description"])
        self.assertEqual(self.calls("review"), [])
        # Same reset on the next drain: still deferred, no second ticket.
        self.run_followup("drain")
        self.assertEqual(len(self.calls("jlin")), 1)

    def test_a_builder_with_no_free_reviewer_is_not_started(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-usage.json", {"reviewer": {"codex": "over", "claude": "limited", "cursor": "ok"},
                                     "reset_at": "2026-10-14T13:00:00", "reset_epoch": time.time() + 600})
        self.set("stub-worker.json", {"mode": "ok"})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        # cursor would build, but neither codex nor claude could review it; codex builds for cursor's review.
        [w] = self.calls("worker")
        self.assertEqual(w["providers"], "codex claude")
        self.assertEqual(st["status"], "done")

    def test_an_unavailable_review_is_not_quota_when_only_the_builder_is_out(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        # cursor builds; codex and claude pass their gates but are missing or at capacity in review.sh.
        self.set("stub-usage.json", {"reviewer": {"cursor": "over"}, "reset_at": "2026-10-14T13:00:00",
                                     "reset_epoch": time.time() + 600})
        self.set("stub-review.json", {"verdicts": ["clean"], "unavailable": True})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["builder_provider"], "cursor")
        self.assertEqual((st["status"], st["next"]["step"]), ("pending", "review"))
        self.assertEqual(self.calls("jlin"), [])
        self.assertIsNone(st.get("quota"))
        self.assertEqual(self.report()[-1]["kind"], "review-unavailable")
        # An eligible reviewer that is really out is still quota, and names that reviewer.
        self.set("stub-usage.json", {"reviewer": {"cursor": "over", "claude": "limited"},
                                     "reset_at": "2026-10-14T13:00:00", "reset_epoch": time.time() + 600})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual((st["status"], st["quota"]["provider"]), ("deferred", "claude"))
        self.assertEqual(len(self.calls("jlin")), 1)
        self.assertNotIn("cursor", st["quota"]["cause"])

    def test_worker_exit_3_with_every_cli_missing_is_a_local_blocker_not_quota(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-worker.json", {"mode": "missing"})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual(self.calls("jlin"), [])
        self.assertEqual(self.report()[-1]["kind"], "builder")
        self.assertIn("missing: cursor codex claude", self.report()[-1]["reason"])

    def test_one_finding_in_two_stages_gets_one_ticket_per_drain(self):
        for stamp in ("20261014-050000", "20261014-060000"):
            self.shipped_item(stamp)
            self.run_followup("hook", "item-done", "--stamp", stamp)
        self.set("stub-usage.json", {"fail": True})
        self.run_followup("drain")   # scans both, then stops on unknown usage
        first, second = self.state("cleanup-20261014-050000"), self.state("cleanup-20261014-060000")
        second["findings"] = [dict(first["findings"][0])]
        (self.root / "followups" / second["key"] / "state.json").write_text(json.dumps(second))
        self.set("stub-usage.json", {"worker": {"cursor": "over", "codex": "over", "claude": "over"},
                                     "reset_at": "2026-10-14T18:00:00", "reset_epoch": time.time() + 600})
        self.run_followup("drain")
        self.assertEqual(len(self.calls("jlin")), 1)
        self.assertEqual({self.state(k)["findings"][0]["ticket"] for k in (first["key"], second["key"])}, {"DEV-900"})
        comments = [c for c in self.calls("linear") if c["cmd"].startswith("issue comment")]
        self.assertTrue(any(c["issue"] == "DEV-900" and second["key"] in c["body"] for c in comments), comments)
        index = json.loads((self.root / "followups" / "tickets.json").read_text())
        self.assertEqual(index[first["findings"][0]["id"]]["ticket"], "DEV-900")

    def test_recovery_after_deferrals_by_different_builders_reviews_against_the_latest_builder(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        key = "cleanup-20261014-050000"
        past = time.time() - 1
        reviewers_out = {"reviewer": {"codex": "over", "cursor": "over", "claude": "over"},
                         "reset_at": "2026-10-14T15:00:00", "reset_epoch": past}
        # Deferral 1: cursor builds, then every reviewer is out.
        self.set("stub-worker.json", {"mode": "ok", "after_usage": reviewers_out})
        self.run_followup("drain")
        st = self.state(key)
        self.assertEqual((st["status"], st["builder_provider"], st["owned_tickets"]), ("deferred", "cursor", ["DEV-900"]))
        [created] = self.calls("jlin")
        self.assertEqual(followticket.parse_meta(created["description"])["builder_provider"], "cursor")
        stale = json.dumps(st)   # what a machine that only saw deferral 1 has on disk
        # Deferral 2: the round-1 review finds issues, cursor is out as a worker so codex rebuilds,
        # then every reviewer is out again.
        self.set("stub-usage.json", {"worker": {"cursor": "over"}, "reset_at": "2026-10-14T15:00:00",
                                     "reset_epoch": past})
        self.set("stub-review.json", {"verdicts": ["findings", "clean"]})
        self.set("stub-worker.json", {"mode": "ok", "after_usage": reviewers_out})
        self.run_followup("drain")
        st = self.state(key)
        self.assertEqual((st["status"], st["builder_provider"], st["next"]["step"]), ("deferred", "codex", "review"))
        updates = [c for c in self.calls("linear") if c["cmd"] == "issue update DEV-900" and "--description-file" in c["args"]]
        self.assertEqual(len(updates), 1)
        meta = followticket.parse_meta(updates[-1]["body"])
        self.assertEqual((meta["key"], meta["builder_provider"]), (key, "codex"))
        ticket = {**self.legacy_ticket("DEV-900", "Follow-up (project-cleanup): x"), "description": updates[-1]["body"]}
        self.set("stub-usage.json", {})
        self.set("stub-review.json", {"verdicts": ["clean"]})
        self.set("stub-linear.json", {"pages": [[ticket]]})
        followups = self.root / "followups"
        for machine in ("no local state", "stale local state"):
            with self.subTest(machine):
                (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
                shutil.rmtree(followups)
                if machine == "stale local state":
                    (followups / key).mkdir(parents=True)
                    (followups / key / "state.json").write_text(stale)
                self.run_followup("drain")
                [review] = self.calls("review")
                self.assertEqual(review["worker"], "codex")   # so review.sh never picks codex to review itself
                self.assertEqual(self.state(key)["status"], "done", self.report())

    def test_a_recovered_pr_with_no_recorded_builder_is_never_reviewed(self):
        meta = {"key": "cleanup-20261001-000000", "stage": "project-cleanup", "finding_ids": ["aaaaaaaaaaaa"],
                "branch": "roadmap/followup-cleanup-20261001-000000", "pr_url": "https://github.com/jnelken/demo/pull/3"}
        self.set("stub-linear.json", {"pages": [[self.meta_ticket("DEV-321", meta)]]})
        self.run_followup("drain")
        st = self.state("cleanup-20261001-000000")
        self.assertEqual(st["status"], "blocked")
        self.assertEqual(self.report()[-1]["kind"], "unknown-builder")
        self.assertEqual(self.calls("review"), [])

    def test_unknown_usage_goes_to_the_local_report_not_a_ticket(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-usage.json", {"fail": True})
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "pending")
        self.assertEqual(self.calls("jlin"), [])
        self.assertEqual(self.calls("worker"), [])
        self.assertEqual(self.report()[-1]["kind"], "unknown-usage")
        self.set("stub-usage.json", {})
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-20261014-050000")["status"], "done")

    # ── prior tickets ──

    def test_a_prior_ticket_on_a_later_page_is_resumed_first_and_closed_after_delivery(self):
        bare = Path(self.tmp.name) / "remotes" / "demo.git"
        repo = self.code / "demo"
        self.git(repo, "checkout", "-q", "-b", "roadmap/followup-cleanup-20261001-000000")
        (repo / "half.txt").write_text("half done\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "half")
        self.git(repo, "push", "-q", "origin", "roadmap/followup-cleanup-20261001-000000")
        (self.root / "stub-pr-head.txt").write_text(self.git(repo, "rev-parse", "HEAD"))
        self.git(repo, "checkout", "-q", "main")
        self.git(repo, "branch", "-q", "-D", "roadmap/followup-cleanup-20261001-000000")
        self.assertTrue(bare.exists())
        meta = {"key": "cleanup-20261001-000000", "stage": "project-cleanup", "finding_ids": ["aaaaaaaaaaaa"],
                "branch": "roadmap/followup-cleanup-20261001-000000", "pr_url": "https://github.com/jnelken/demo/pull/3",
                "stamp": "20261001-000000", "item": "Old item", "builder_provider": "codex"}
        filler = [self.legacy_ticket(f"DEV-{n}", f"Unrelated {n}") for n in range(3)]
        self.set("stub-linear.json", {"pages": [filler, filler, [self.meta_ticket("DEV-321", meta)]]})
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.run_followup("drain")
        self.assertEqual([c["page"] for c in self.calls("linear") if c["cmd"] == "api"], [0, 1, 2])
        old = self.state("cleanup-20261001-000000")
        self.assertEqual((old["status"], old["recovered_from"]), ("done", "DEV-321"))
        reviews = self.calls("review")
        self.assertIn("followup-cleanup-20261001-000000", reviews[0]["repo"] + json.dumps(old))
        self.assertEqual(reviews[0]["worker"], "codex")   # the ticket's builder, so codex never reviews itself
        first_worker = self.calls("worker")[0]
        self.assertNotEqual(first_worker["branch"], "roadmap/followup-cleanup-20261001-000000")  # resumed at review
        lin = [c for c in self.calls("linear") if c["cmd"] != "api"]
        self.assertTrue(any(c["cmd"] == "issue update DEV-321" and "Done" in c["args"] for c in lin), lin)
        closing = next(c for c in lin if c["cmd"].startswith("issue comment") and c["issue"] == "DEV-321")
        self.assertIn("deploy success", closing["body"])
        order = [c["tool"] for c in self.calls() if c["tool"] in ("review", "worker")]
        self.assertEqual(order[0], "review")   # the prior PR is reviewed before any new build

    def test_a_ticket_close_that_fails_is_retried_by_later_drains(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-usage.json", {"fail": True})
        self.run_followup("drain")   # scans, then stops on unknown usage
        key = "cleanup-20261014-050000"
        fid = self.state(key)["findings"][0]["id"]
        ticket = self.meta_ticket("DEV-501", {"key": key, "stage": "project-cleanup", "finding_ids": [fid]})
        self.set("stub-usage.json", {})
        self.set("stub-linear.json", {"pages": [[ticket]], "fail": ["issue update DEV-501"]})
        self.run_followup("drain")
        st = self.state(key)
        self.assertEqual(st["status"], "done", self.report())
        self.assertEqual(st["pending_close"], [{"ticket": "DEV-501", "commented": True}])
        self.assertIn("could not close DEV-501", self.report()[-1]["reason"])
        self.set("stub-linear.json", {"pages": [[ticket]]})
        self.run_followup("drain")
        st = self.state(key)
        self.assertEqual(st["pending_close"], [])
        updates = [c for c in self.calls("linear") if c["cmd"] == "issue update DEV-501"]
        self.assertEqual([c["failed"] for c in updates], [True, False])
        self.assertIn("Done", updates[-1]["args"])
        closing = [c for c in self.calls("linear") if c["cmd"].startswith("issue comment")
                   and c["issue"] == "DEV-501" and "Fixed by" in (c["body"] or "")]
        self.assertEqual(len(closing), 1)   # the retry only moves it to Done
        self.assertEqual(len(self.calls("worker")), 1)   # the finished repair never ran again

    def test_a_finding_already_ticketed_gets_evidence_not_a_duplicate(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        self.set("stub-usage.json", {"fail": True})
        self.run_followup("drain")   # scans, then stops on unknown usage
        st = self.state("cleanup-20261014-050000")
        f = st["findings"][0]
        self.set("stub-usage.json", {"worker": {"cursor": "over", "codex": "over", "claude": "over"},
                                     "reset_at": "2026-10-14T18:00:00", "reset_epoch": time.time() + 600})
        cases = {
            "metadata": self.meta_ticket("DEV-777", {"key": "cleanup-other", "stage": "project-cleanup",
                                                    "finding_ids": [f["id"]]}),
            "legacy text": self.legacy_ticket("DEV-778", "Old bug", f"Seen before: {f['title']}"),
        }
        for label, ticket in cases.items():
            with self.subTest(label):
                (self.root / "stub-calls.jsonl").unlink(missing_ok=True)
                st = self.state("cleanup-20261014-050000")
                st.update(status="pending", evidence_posted=[], last_deferral=None)
                for g in st["findings"]:
                    g.pop("ticket", None)
                    g["status"] = "open"
                (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
                self.set("stub-linear.json", {"pages": [[ticket]]})
                self.run_followup("drain")
                self.assertEqual(self.calls("jlin"), [])
                comments = [c for c in self.calls("linear") if c["cmd"].startswith("issue comment")]
                self.assertTrue(any(c["issue"] == ticket["identifier"] and f["id"] in c["body"] for c in comments),
                                comments)
                self.assertEqual(self.state(st["key"])["findings"][0]["ticket"], ticket["identifier"])

    # ── tooling analysis ──

    def test_tooling_dry_run_on_the_oct13_evidence_keeps_only_quoted_analyst_findings(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "batch-verdicts").mkdir()
        (self.root / "batch-verdicts" / "apt-sqft.json").write_text((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        good = {"title": "Bookkeeping summary blames the orchestrator limit", "kind": "misleading-surface",
                "fix": "Name the quota in the alert", "evidence": [{"source": str(self.root / "runs.jsonl"),
                                                                    "quote": "the orchestrator hit its session limit before triage"}]}
        bad = {"title": "Invented friction", "evidence": [{"source": str(run / "orchestrator-result.json"),
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

    def test_only_an_executed_git_stash_is_a_finding_never_an_echoed_rule(self):
        run = self.root / "runs" / "s1"
        run.mkdir(parents=True)
        log = run / "worker-output.log"
        echoed = ("- Never use `git stash`. Never run git after a `cd` that may have failed.\n"
                  "exec\n/bin/zsh -lc \"rg -n 'git stash' SAFETY.md && git stash list\" in /r\n"
                  " succeeded in 3ms:\nSAFETY.md:12:- Never use `git stash`.\n"
                  "exec\n/bin/zsh -lc 'echo \"git stash would lose WIP\"' in /r\n")
        log.write_text(echoed)

        def stash_findings():
            findings, _ = followscan.tooling_scan(self.root, ["s1"])
            return [f for f in findings if f["id"] == followscan.finding_id(
                "tooling-hardening", "ai-tools", "misfiring-automation", "git-stash")]
        self.assertEqual(stash_findings(), [])
        ran = '/bin/zsh -lc "cd /r && git -C /r stash push -m wip" in /r'
        log.write_text(echoed + f"exec\n{ran}\n succeeded in 9ms:\nSaved working directory\n")
        [f] = stash_findings()
        self.assertEqual(f["evidence"][0]["quote"], ran)
        self.assertTrue(followscan.evidence_holds(f["evidence"][0], [log]))

    def test_status_summarizes_fixed_deferred_and_blocked(self):
        for stamp in ("20261014-050000", "20261014-060000", "20261014-070000"):
            self.shipped_item(stamp)
            self.run_followup("hook", "item-done", "--stamp", stamp, "--origin", "batch-2026-10-14")
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state("cleanup-20261014-060000")
        st.update(status="deferred", quota={"provider": "codex", "reset_at": "2026-10-14T12:00:00"})
        st["findings"][0].update(status="deferred", ticket="DEV-900")
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        st = self.state("cleanup-20261014-070000")
        st.update(status="blocked", blockers=[{"kind": "review", "reason": "still findings"}])
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        line = self.run_followup("status", "--origin", "batch-2026-10-14").stdout.strip()
        self.assertEqual(line, "Follow-ups: 1 follow-up(s) fixed and merged; 1 deferred for quota "
                               "(codex resets 2026-10-14T12:00:00), tickets DEV-900; 1 blocked (see followups/report.jsonl).")
        rows = json.loads(self.run_followup("status", "--origin", "batch-2026-10-14", "--json").stdout)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
