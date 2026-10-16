"""followup.py report-only (DEV-232 Phase B slice 2), offline.

Temp ADVANCE_ROADMAP_ROOT + tests/followup_stubs.py for usage.py and the analyst.
No Linear, GitHub, worker, or review — those arrive in slice 3.
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
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
                    "ADVANCE_ROADMAP_CLAUDE_BIN": self.wrapper(t, "claude"),
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
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "queued")
        self.assertEqual(st.get("mode"), "report-only")
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
        waiting = self.state("cleanup-20261014-050000")
        self.assertEqual(waiting["status"], "waiting")
        self.assertIn("demo.t2.lock is held", waiting.get("waiting_reason", ""))
        pid = self.root / "inflight" / "demo" / "roadmap-x.pid"
        pid.parent.mkdir(parents=True)
        pid.write_text(str(os.getpid()))
        self.assertIn("timed out on", self.run_followup("drain").stdout)
        self.assertEqual(self.calls("worker"), [])
        pid.unlink()
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["scanned"])
        self.assertTrue(st["findings"])
        self.assertEqual({f["status"] for f in st["findings"]}, {"open"})
        self.assertEqual(self.calls("worker"), [])
        self.assertEqual(self.calls("linear"), [])

    def test_tooling_runs_only_after_every_child_run_and_cleanup_finishes(self):
        self.run_followup("hook", "run-end", "--origin", "batch-2026-10-14", "--stamp", "s1", "--stamp", "s2")
        row = {"stamp": "s1", "outcome": "skipped-unchanged", "detail": "unchanged"}
        (self.root / "runs.jsonl").write_text(json.dumps(row) + "\n")
        self.assertIn("have not finished", self.run_followup("drain").stdout)
        self.assertEqual(self.state("tooling-batch-2026-10-14")["status"], "waiting")
        self.shipped_item("s2")
        self.run_followup("hook", "item-done", "--stamp", "s2")
        with open(self.root / "runs.jsonl", "a") as f:
            f.write(json.dumps({**row, "stamp": "s2", "outcome": "shipped", "detail": "PR #4"}) + "\n")
        self.run_followup("drain", "--stage", "tooling")
        self.assertEqual(self.state("tooling-batch-2026-10-14")["status"], "waiting")
        self.run_followup("drain")
        self.assertEqual(self.state("cleanup-s2")["status"], "done")
        tooling = self.state("tooling-batch-2026-10-14")
        self.assertTrue(tooling["scanned"])
        self.assertEqual(tooling["status"], "done")
        self.assertTrue(any(n["title"] == "s1 skipped unchanged" for n in tooling["non_findings"]))
        self.assertEqual(tooling["repo"], "ai-tools")
        self.assertEqual(self.calls("worker"), [])

    def test_tooling_age_bypass_never_skips_an_active_cleanup(self):
        self.run_followup("hook", "run-end", "--origin", "batch-old", "--stamp", "s1")
        self.shipped_item("s1")
        self.run_followup("hook", "item-done", "--stamp", "s1")
        (self.root / "runs.jsonl").write_text(json.dumps(
            {"stamp": "s1", "outcome": "shipped", "detail": "PR #1"}) + "\n")
        tooling = self.state("tooling-batch-old")
        tooling["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 13 * 3600))
        (self.root / "followups" / tooling["key"] / "state.json").write_text(json.dumps(tooling))
        pid = self.root / "inflight" / "demo" / "followup-cleanup-s1.pid"
        pid.parent.mkdir(parents=True)
        pid.write_text(str(os.getpid()))
        out = self.run_followup("drain", "--stage", "tooling").stdout
        self.assertIn("cleanup-s1 has not finished", out)
        self.assertEqual(self.state("tooling-batch-old")["status"], "waiting")
        self.assertFalse(self.state("tooling-batch-old").get("scanned"))
        pid.unlink()
        self.run_followup("drain", "--stage", "tooling", env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "0"})
        self.assertTrue(self.state("tooling-batch-old")["scanned"])

    def test_a_conductor_run_dir_counts_once_its_result_exists_and_feeds_the_corpus(self):
        run = Path(self.tmp.name) / "scratch" / "20261014-090000"
        run.mkdir(parents=True)
        self.run_followup("hook", "run-end", "--origin", "conductor-x", "--run-dir", str(run))
        self.assertIn("have not finished", self.run_followup("drain", "--stage", "tooling").stdout)
        result = run.resolve() / "worker-result.json"
        result.write_text(json.dumps({"outcome": "failed", "repo": "demo", "summary": "tests failed"}))
        self.run_followup("drain", "--stage", "tooling")
        st = self.state("tooling-conductor-x")
        self.assertTrue(st["scanned"])
        self.assertIn(str(result), st["corpus"])
        self.assertFalse([f for f in st["findings"] if f["kind"] == "fragile-tooling"], st["findings"])
        self.assertTrue(any(n["title"] == "20261014-090000 ended failed" for n in st["non_findings"]),
                        st["non_findings"])

    def test_tooling_scan_needs_orchestration_evidence_for_error_outcomes(self):
        run = self.root / "runs" / "s-fail"
        run.mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps({
            "stamp": "s-fail", "outcome": "failed", "detail": "npm test failed", "worker": "cursor"}) + "\n")
        findings, quiet = followscan.tooling_scan(self.root, ["s-fail"])
        self.assertFalse([f for f in findings if f["kind"] == "fragile-tooling"])
        self.assertTrue(any(n["title"] == "s-fail ended failed" for n in quiet), quiet)
        (self.root / "runs.jsonl").write_text(json.dumps({
            "stamp": "s-err", "outcome": "error",
            "detail": "orchestrator: missing ORCHESTRATOR_RESULT_JSON", "worker": "none"}) + "\n")
        run2 = self.root / "runs" / "s-err"
        run2.mkdir(parents=True)
        (run2 / "orchestrator-result.json").write_text(json.dumps({"parse_error": "missing ORCHESTRATOR_RESULT_JSON"}))
        findings, quiet = followscan.tooling_scan(self.root, ["s-err"])
        [f] = [f for f in findings if f["kind"] == "fragile-tooling"]
        self.assertIn("missing ORCHESTRATOR_RESULT_JSON", f["evidence"][0]["quote"])

    def test_tooling_scan_skips_expected_safety_blockers(self):
        stages = [
            {"key": "cleanup-noroadmap", "blockers": [
                {"kind": "unsafe-repo", "reason": "demo has opted out (.noroadmap)"}]},
            {"key": "cleanup-dirty", "blockers": [
                {"kind": "unsafe-repo", "reason": "dirty tree (3 files)"}]},
            {"key": "cleanup-land", "blockers": [
                {"kind": "land", "reason": "PR checks failed on https://example/pull/1"}]},
        ]
        findings, quiet = followscan.tooling_scan(self.root, [], stages=stages)
        titles = [f["title"] for f in findings if f["kind"] == "fragile-tooling"]
        self.assertEqual(titles, ["a follow-up stage was blocked: PR checks failed on https://example/pull/1"])
        self.assertTrue(any("noroadmap" in n["title"] or "unsafe-repo" in n["title"] for n in quiet), quiet)
        self.assertTrue(any("cleanup-dirty" in n["title"] for n in quiet), quiet)

    def test_tooling_scan_classifies_hard_analyst_failures_as_tooling_defects(self):
        """Missing binaries / crashes are tooling findings, not expected safety non-findings."""
        stages = [
            {"key": "tooling-batch-fail-analyst", "blockers": [
                {"kind": "analyst",
                 "reason": "failed: codex: [Errno 2] No such file; claude: exit 1"}]},
        ]
        findings, quiet = followscan.tooling_scan(self.root, [], stages=stages)
        [f] = [x for x in findings if x["kind"] == "fragile-tooling"]
        self.assertIn("failed: codex", f["title"])
        self.assertTrue(f["evidence"])
        self.assertFalse([n for n in quiet if "analyst" in n["title"]], quiet)

    def test_an_interrupted_stage_resumes_where_it_stopped(self):
        self.shipped_item("20261014-050000")
        self.run_followup("hook", "item-done", "--stamp", "20261014-050000")
        st = self.state("cleanup-20261014-050000")
        st.update(status="running", next={"step": "analyze"}, attempts=1)
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        self.run_followup("drain")
        st = self.state("cleanup-20261014-050000")
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["scanned"])
        self.assertTrue(any(b["kind"] == "interrupted" for b in st["blockers"]))
        self.assertEqual(self.calls("worker"), [])

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
        self.assertIn("misfiring-automation", kinds)
        self.assertIn("misleading-surface", kinds)
        self.assertIn("wrong-knowledge", kinds)
        titles = [f["title"] for f in doc["findings"]]
        self.assertIn(good["title"], titles)
        self.assertNotIn(bad["title"], titles)
        self.assertTrue(any(n["title"] == bad["title"] and "rejected" in n["why"] for n in doc["non_findings"]))
        self.assertTrue(all(f["repo"] == "ai-tools" and f["evidence"] for f in doc["findings"]))
        # Deterministic findings preserve verbatim source lines (Oct 13 fixture reproduces this).
        corpus = followscan.corpus_files(self.root, [stamp])
        for f in doc["findings"]:
            if f["title"] == good["title"]:
                continue
            for ev in f["evidence"]:
                self.assertTrue(followscan.evidence_holds(ev, corpus, [stamp]), (f["title"], ev))
        self.assertEqual(doc["analyst"], "codex: answered")
        self.assertEqual(self.calls("worker"), [])
        self.assertFalse((self.root / "followups" / "tooling-batch-2026-10-13").exists())

    def test_corpus_and_evidence_are_scoped_to_stage_child_stamps(self):
        stamp = "20261013-103004"
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        other = {**row, "stamp": "20261012-999999",
                 "summary": "unrelated historical quote for another stage",
                 "detail": "unrelated historical quote for another stage"}
        (self.root / "runs.jsonl").write_text(json.dumps(other) + "\n" + json.dumps(row) + "\n")
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "batch-verdicts").mkdir()
        (self.root / "batch-verdicts" / "apt-sqft.json").write_text((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        (self.root / "batch-verdicts" / "other.json").write_text(json.dumps({
            "stamp": "20261012-999999", "summary": "You've hit your session limit · resets never\n"}))
        corpus = followscan.corpus_files(self.root, [stamp], excerpt_dir=self.root / "corpus-x")
        self.assertEqual([p for p in corpus if p.name == "runs.jsonl"], [self.root / "corpus-x" / "runs.jsonl"])
        self.assertNotIn(self.root / "batch-verdicts" / "other.json", corpus)
        self.assertIn(self.root / "batch-verdicts" / "apt-sqft.json", corpus)
        borrowed = {"source": str(self.root / "runs.jsonl"),
                    "quote": "unrelated historical quote for another stage"}
        self.assertFalse(followscan.evidence_holds(borrowed, [self.root / "runs.jsonl"], [stamp]))
        own = {"source": str(self.root / "runs.jsonl"),
               "quote": "the orchestrator hit its session limit before triage"}
        self.assertTrue(followscan.evidence_holds(own, [self.root / "runs.jsonl"], [stamp]))

    def test_runs_jsonl_stamp_selection_streams_past_read_limit(self):
        """Past the 2 MB capped read, stamp rows must still be found; never cite another stamp."""
        stamp = "20261015-tail"
        other = {"stamp": "20261001-head", "outcome": "shipped",
                 "summary": "unrelated historical quote for another stage",
                 "detail": "unrelated historical quote for another stage"}
        target = {"stamp": stamp, "outcome": "incomplete",
                  "summary": "recent row after the size cap",
                  "detail": "recent row after the size cap"}
        pad = json.dumps({"stamp": "pad", "detail": "x" * 2000}) + "\n"
        with open(self.root / "runs.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps(other) + "\n")
            while f.tell() < 2_000_000 + 50_000:
                f.write(pad)
            f.write(json.dumps(target) + "\n")
        line = followscan.jsonl_stamp_line(self.root / "runs.jsonl", stamp)
        self.assertIsNotNone(line)
        self.assertIn(stamp, line)
        self.assertIn("recent row after the size cap", line)
        ev = followscan.row_evidence(self.root / "runs.jsonl", stamp)
        self.assertIn(stamp, ev["quote"])
        self.assertNotIn("unrelated historical quote", ev["quote"])
        missing = followscan.row_evidence(self.root / "runs.jsonl", "no-such-stamp")
        self.assertEqual(missing["quote"], "no-such-stamp")
        corpus = followscan.corpus_files(self.root, [stamp], excerpt_dir=self.root / "corpus-big")
        excerpt = next(p for p in corpus if p.name == "runs.jsonl")
        self.assertIn(stamp, excerpt.read_text())
        self.assertNotIn("unrelated historical quote", excerpt.read_text())
        own = {"source": str(self.root / "runs.jsonl"), "quote": "recent row after the size cap"}
        self.assertTrue(followscan.evidence_holds(own, [self.root / "runs.jsonl"], [stamp]))

    def test_analyst_missing_binary_fails_over_then_hard_fail_stays_retryable(self):
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "stub-claude-analyst.txt").write_text(self.analyst_fence())
        missing = str(self.root / "no-such-codex")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1",
                                   "ADVANCE_ROADMAP_CODEX_BIN": missing})
        doc = json.loads(p.stdout)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])
        # Both providers hard-fail → durable drain leaves the stage unscanned/retryable.
        (self.root / "stub-claude-crash").write_text("")
        self.run_followup("hook", "run-end", "--origin", "batch-fail-analyst", "--stamp", stamp)
        self.run_followup("drain", "--stage", "tooling", "--origin", "batch-fail-analyst",
                          env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1",
                               "ADVANCE_ROADMAP_CODEX_BIN": missing})
        st = self.state("tooling-batch-fail-analyst")
        self.assertFalse(st.get("scanned"))
        self.assertEqual(st["status"], "retryable")
        self.assertTrue(any(b["kind"] == "analyst" for b in st["blockers"]), st["blockers"])
        self.assertTrue(any(r["kind"] == "analyst" for r in self.report()), self.report())

    def test_analyst_timeout_fails_over_to_claude(self):
        (self.root / "stub-codex-hang").write_text("")
        (self.root / "stub-claude-analyst.txt").write_text(self.analyst_fence())
        stamp = "20261013-103004"
        (self.root / "runs" / stamp).mkdir(parents=True)
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp,
                              env={"ADVANCE_ROADMAP_FOLLOWUP_ANALYST": "1",
                                   "ADVANCE_ROADMAP_FOLLOWUP_ANALYST_TIMEOUT_S": "0.3"},
                              check=True)
        doc = json.loads(p.stdout)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], [f["title"] for f in doc["findings"]])
        orphan = self.root / "stub-codex-orphan.pid"
        self.assertTrue(orphan.exists(), "hang stub must spawn a SIGTERM-ignoring child")
        pid = int(orphan.read_text())
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"orphan pid {pid} still alive after analyst timeout reap")

    def test_analyst_non_zero_without_limit_fails_over_to_claude(self):
        (self.root / "stub-codex-crash").write_text("")
        doc, titles = self.analyst_scan("stub-claude-analyst.txt", answer=True)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], titles)
        self.assertTrue(any(c["tool"] == "codex" for c in self.calls()))
        self.assertTrue(any(c["tool"] == "claude" for c in self.calls()))

    ANALYST_FINDING = {"title": "Bookkeeping summary blames the orchestrator limit", "kind": "misleading-surface",
                       "quote": "the orchestrator hit its session limit before triage"}

    def analyst_scan(self, answer_file, answer):
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
        (self.root / "stub-codex-transcript.txt").write_text(self.analyst_fence())
        doc, titles = self.analyst_scan("stub-analyst.txt", answer=False)
        self.assertEqual(doc["analyst"], "codex: no findings block")
        self.assertNotIn(self.ANALYST_FINDING["title"], titles)

    def test_a_limited_analyst_saves_its_limit_first_then_fails_over(self):
        (self.root / "stub-codex-limit").write_text("")
        self.set("stub-usage.json", {"record_limit_fails": True})
        doc, titles = self.analyst_scan("stub-claude-analyst.txt", answer=True)
        self.assertEqual(doc["analyst"], "claude: answered")
        self.assertIn(self.ANALYST_FINDING["title"], titles)
        cmds = [c.get("cmd") or c["tool"] for c in self.calls() if c["tool"] in ("usage", "usage-record-limit")]
        self.assertLess(cmds.index("parse-limit"), cmds.index("usage-record-limit"))
        self.assertEqual([(c["provider"], c["scope"]) for c in self.calls("usage-record-limit")], [("codex", "analyst")])

    def test_analyst_ignores_an_echoed_limit_banner_when_its_final_answer_succeeded(self):
        (self.root / "stub-codex-transcript.txt").write_text(
            "reading runs.jsonl\nYou've hit your usage limit. Upgrade to Pro or try again at 7:00 PM.\n")
        doc, titles = self.analyst_scan("stub-analyst.txt", answer=True)
        self.assertEqual(doc["analyst"], "codex: answered")
        self.assertIn(self.ANALYST_FINDING["title"], titles)
        self.assertEqual(self.calls("usage-record-limit"), [])

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

    def test_status_summarizes_reported_and_blocked(self):
        for stamp in ("20261014-050000", "20261014-060000", "20261014-070000"):
            self.shipped_item(stamp)
            self.run_followup("hook", "item-done", "--stamp", stamp, "--origin", "batch-2026-10-14")
        self.run_followup("drain", "--stage", "cleanup")
        st = self.state("cleanup-20261014-060000")
        st.update(status="retryable", blockers=[{"kind": "interrupted", "reason": "waiting on a sibling"}])
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        st = self.state("cleanup-20261014-070000")
        st.update(status="blocked", blockers=[{"kind": "crash", "reason": "scan blew up"}])
        (self.root / "followups" / st["key"] / "state.json").write_text(json.dumps(st))
        line = self.run_followup("status", "--origin", "batch-2026-10-14").stdout.strip()
        self.assertIn("1 follow-up(s) reported", line)
        self.assertIn("2 blocked", line)
        rows = json.loads(self.run_followup("status", "--origin", "batch-2026-10-14", "--json").stdout)
        self.assertEqual(len(rows), 3)

    def test_consolidation_state_machine_covers_review_findings(self):
        """Table-driven coverage of the four independent-review findings (resume #3)."""
        cases = [
            ("missing-repo", "shipped result without repo → blocked; sibling still drains"),
            ("status-complete", "terminal worker-status finishes a conductor child into corpus"),
            ("waiting-status", "waiting stages appear in status, never 'Follow-ups: none.'"),
            ("ready-guards", "quiescent/ready never TypeError on a bad repo value"),
        ]
        import followup as fu
        self.assertEqual(fu.quiescent(None), (False, "no repo"), cases[3])
        self.assertEqual(fu.quiescent(["x"]), (False, "no repo"), cases[3])
        # 1) missing repo at queue + sibling isolation
        run = self.root / "runs" / "no-repo"
        run.mkdir(parents=True)
        (run / "worker-result.json").write_text(json.dumps({
            "outcome": "shipped", "repo": None, "item": "x", "merge_commit": "abc",
            "summary": "shipped without repo"}))
        self.run_followup("hook", "item-done", "--stamp", "no-repo", "--origin", "batch-c")
        st = self.state("cleanup-no-repo")
        self.assertEqual(st["status"], "blocked", cases[0])
        self.assertTrue(any(b["kind"] == "missing-repo" for b in st["blockers"]), cases[0])
        self.shipped_item("ok-repo")
        self.run_followup("hook", "item-done", "--stamp", "ok-repo", "--origin", "batch-c")
        self.run_followup("drain", "--stage", "cleanup", "--origin", "batch-c")
        self.assertEqual(self.state("cleanup-ok-repo")["status"], "done", cases[0])
        self.assertEqual(self.state("cleanup-no-repo")["status"], "blocked", cases[0])
        # 2–3) worker-status completion + waiting visible in status
        ext = Path(self.tmp.name) / "scratch" / "cond-status"
        ext.mkdir(parents=True)
        self.run_followup("hook", "run-end", "--origin", "conductor-status", "--run-dir", str(ext))
        out = self.run_followup("drain", "--stage", "tooling", "--origin", "conductor-status").stdout
        self.assertIn("have not finished", out)
        line = self.run_followup("status", "--origin", "conductor-status").stdout.strip()
        self.assertNotEqual(line, "Follow-ups: none.", cases[2])
        self.assertIn("waiting", line, cases[2])
        status_path = ext.resolve() / "worker-status.json"
        status_path.write_text(json.dumps({
            "used": "cursor", "exit": 1, "fatal": "quota exhausted", "limited": [],
            "missing": [], "quota": {"provider": "cursor"}}))
        self.run_followup("drain", "--stage", "tooling", "--origin", "conductor-status")
        tooling = self.state("tooling-conductor-status")
        self.assertEqual(tooling["status"], "done", cases[1])
        self.assertTrue(any(p.endswith("worker-status.json") for p in tooling["corpus"]), cases[1])


if __name__ == "__main__":
    unittest.main()
