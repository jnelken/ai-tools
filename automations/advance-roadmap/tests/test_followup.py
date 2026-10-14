"""followup.py end to end, offline: a temp ADVANCE_ROADMAP_ROOT, a local bare repo standing in for
GitHub, and tests/followup_stubs.py for usage.py, worker.sh, review.sh, gh, linear, jlin.py and the
analyst. Nothing here reaches Linear or GitHub."""
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

HERE = Path(__file__).resolve().parent
ADV = HERE.parent
FOLLOWUP = ADV / "followup.py"
STUBS = HERE / "followup_stubs.py"
FIXTURES = HERE / "fixtures" / "oct13"
MARKER_LINE = "# TODO(advance-roadmap review): reject an empty listing before saving"


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.code = t / "root", t / "code"
        (self.root / "lib").mkdir(parents=True)
        self.code.mkdir()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("ADVANCE_ROADMAP_", "GIT_"))},
                    "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.code),
                    "ADVANCE_ROADMAP_SUPERSET": "0", "ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "0",
                    "ADVANCE_ROADMAP_FOLLOWUP_INSTALL": "0", "ADVANCE_ROADMAP_FOLLOWUP_POLL_S": "0.2",
                    "ADVANCE_ROADMAP_FOLLOWUP_WORKTREES": str(t / "worktrees"),
                    "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_NONE_S": "0", "ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S": "2",
                    "ADVANCE_ROADMAP_FOLLOWUP_CHECKS_TIMEOUT_S": "2",
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
            (self.root / name).write_text(f'exec {sys.executable} {STUBS} {tool} "$@"\n')
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
        self.git(repo, "checkout", "-q", "main")
        self.git(repo, "branch", "-q", "-D", "roadmap/followup-cleanup-20261001-000000")
        self.assertTrue(bare.exists())
        meta = {"key": "cleanup-20261001-000000", "stage": "project-cleanup", "finding_ids": ["aaaaaaaaaaaa"],
                "branch": "roadmap/followup-cleanup-20261001-000000", "pr_url": "https://github.com/jnelken/demo/pull/3",
                "stamp": "20261001-000000", "item": "Old item"}
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
        first_worker = self.calls("worker")[0]
        self.assertNotEqual(first_worker["branch"], "roadmap/followup-cleanup-20261001-000000")  # resumed at review
        lin = [c for c in self.calls("linear") if c["cmd"] != "api"]
        self.assertTrue(any(c["cmd"] == "issue update DEV-321" and "Done" in c["args"] for c in lin), lin)
        closing = next(c for c in lin if c["cmd"].startswith("issue comment") and c["issue"] == "DEV-321")
        self.assertIn("deploy success", closing["body"])
        order = [c["tool"] for c in self.calls() if c["tool"] in ("review", "worker")]
        self.assertEqual(order[0], "review")   # the prior PR is reviewed before any new build

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
