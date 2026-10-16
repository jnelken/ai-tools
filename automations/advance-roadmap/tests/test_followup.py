"""followup.py deterministic report-only (DEV-232 Phase B slice 2), offline.

Temp ADVANCE_ROADMAP_ROOT + tests/followup_stubs.py for usage.py.
No model analyst, Linear, GitHub, worker, or review — those arrive in slice 3.
"""
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
sys.path.insert(0, str(ADV))
sys.path.insert(0, str(ADV / "lib"))
import followscan  # noqa: E402


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
            "deploy": {"status": "success"}}, indent=1) + "\n")
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
        self.run_followup("drain", "--stage", "tooling")
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
        """Historical/slice-3 analyst blockers stay tooling findings, not expected safety stops."""
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

    def test_tooling_dry_run_on_the_oct13_evidence_is_deterministic(self):
        stamp = "20261013-103004"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        (run / "orchestrator-result.json").write_text((FIXTURES / "orchestrator-result.json").read_text())
        (self.root / "runs.jsonl").write_text(json.dumps(json.loads((FIXTURES / "runs-row.json").read_text())) + "\n")
        (self.root / "batch-verdicts").mkdir()
        (self.root / "batch-verdicts" / "apt-sqft.json").write_text((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "batch-2026-10-13", "--stamp", stamp)
        doc = json.loads(p.stdout)
        kinds = {f["kind"] for f in doc["findings"]}
        self.assertIn("misfiring-automation", kinds)
        self.assertIn("misleading-surface", kinds)
        self.assertIn("wrong-knowledge", kinds)
        self.assertEqual(doc.get("mode"), "report-only")
        self.assertTrue(all(f["repo"] == "ai-tools" and f["evidence"] for f in doc["findings"]))
        corpus = followscan.corpus_files(self.root, [stamp])
        for f in doc["findings"]:
            for ev in f["evidence"]:
                self.assertTrue(followscan.evidence_holds(ev, corpus, [stamp]), (f["title"], ev))
        self.assertEqual(self.calls("worker"), [])
        self.assertFalse((self.root / "followups" / "tooling-batch-2026-10-13").exists())

    def test_pretty_printed_worker_result_and_reviews_evidence_holds(self):
        """Deploy and unresolved-review quotes come from the file as stored, never json.dumps."""
        run = self.root / "runs" / "pretty"
        run.mkdir(parents=True)
        result = {
            "outcome": "shipped", "repo": "demo", "item": "x", "merge_commit": "abc123",
            "review": {"reviewer": "codex", "rounds": 2, "unresolved": 1},
            "deploy": {"status": "failed", "url": "https://example.test/d", "attempts": 2},
        }
        result_path = run / "worker-result.json"
        result_path.write_text(json.dumps(result, indent=1) + "\n")
        reviews_path = run / "reviews.jsonl"
        # Pretty-printed JSONL object (multi-line) still must cite a verbatim source span.
        reviews_path.write_text(json.dumps({"round": 1, "verdict": "findings", "reviewer": "codex"}, indent=1) + "\n"
                                + json.dumps({"round": 2, "verdict": "clean", "reviewer": "codex"}) + "\n")
        findings, quiet = followscan.cleanup_scan(run, None)
        kinds = {f["kind"] for f in findings}
        self.assertIn("red-deploy", kinds)
        self.assertIn("unresolved-review", kinds)
        for f in findings:
            for ev in f["evidence"]:
                self.assertTrue(followscan.evidence_holds(ev, [Path(ev["source"])]), (f["title"], ev))
                raw = Path(ev["source"]).read_text()
                # Quote (whitespace-collapsed) must be a substring of the collapsed source.
                self.assertIn(ev["quote"], " ".join(raw.split()))
                # Must not be a compact json.dumps reconstruction (space after { differs).
                if f["kind"] == "red-deploy":
                    self.assertNotEqual(ev["quote"], '"deploy": ' + json.dumps(result["deploy"]))
        # Clean last review round with unresolved=0 uses the last JSONL line verbatim.
        result["review"]["unresolved"] = 0
        result["deploy"]["status"] = "success"
        result_path.write_text(json.dumps(result, indent=1) + "\n")
        findings, quiet = followscan.cleanup_scan(run, None)
        [n] = [x for x in quiet if x["title"] == "review closed clean"]
        last_line = reviews_path.read_text().splitlines()[-1]
        self.assertEqual(n["evidence"][0]["quote"], " ".join(last_line.split())[:400])
        self.assertTrue(followscan.evidence_holds(n["evidence"][0], [reviews_path]))
        [d] = [x for x in quiet if x["title"] == "deploy success"]
        self.assertTrue(followscan.evidence_holds(d["evidence"][0], [result_path]))

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

    def test_concurrent_drains_preserve_findings_and_scan_once(self):
        """Two drains on one stage: claim serializes; findings from the first scan survive."""
        self.shipped_item("20261014-race")
        self.run_followup("hook", "item-done", "--stamp", "20261014-race", "--origin", "batch-race")
        key = "cleanup-20261014-race"
        barrier = threading.Barrier(2)
        results = []

        def drain_once():
            barrier.wait(timeout=10)
            p = subprocess.run([sys.executable, str(FOLLOWUP), "drain", "--stage", "cleanup",
                                "--origin", "batch-race"],
                               capture_output=True, text=True, timeout=180, env=self.env)
            results.append(p)

        threads = [threading.Thread(target=drain_once) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual({p.returncode for p in results}, {0}, [p.stdout + p.stderr for p in results])
        st = self.state(key)
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["scanned"])
        self.assertTrue(st["findings"])
        # Exactly one successful analyze: attempts may be 1 (loser skipped after claim) or 2 if
        # the second saw eligible before the first saved done — but findings must match a fresh scan.
        fresh, _ = followscan.cleanup_scan(self.root / "runs" / "20261014-race", self.code / "demo")
        self.assertEqual([f["id"] for f in st["findings"]], [f["id"] for f in fresh])
        self.assertEqual(st.get("attempts", 0), 1, st)

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

    def test_scan_rejects_path_traversal_and_never_deletes_outside_scratch(self):
        """Hostile --origin/--stamp must not rmtree $ROOT/runs (or anything under followups)."""
        import followup as fu
        runs = self.root / "runs"
        runs.mkdir(parents=True)
        sentinel = runs / "keep-me"
        sentinel.write_text("durable\n")
        # Slash / .. in origin: argparse path would have been scan-x/../../runs → rmtree(runs).
        p = self.run_followup("scan", "--stage", "tooling", "--origin", "x/../../runs",
                             "--stamp", "s", check=False)
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(sentinel.exists(), "scan must not delete $ROOT/runs")
        self.assertFalse(any((self.root / "followups").glob("scan-*")), "dry-run must not leave stage dirs")
        p2 = self.run_followup("scan", "--stage", "tooling", "--origin", "..",
                               "--stamp", "20261015-000001", check=False)
        self.assertNotEqual(p2.returncode, 0)
        self.assertTrue(sentinel.exists())
        with self.assertRaises(ValueError):
            fu.require_id("x/../../runs", "origin")
        with self.assertRaises(ValueError):
            fu.require_id("..", "origin")
        with self.assertRaises(ValueError):
            fu.under(self.root / "followups", "scan-x", "..", "runs")

    def test_recover_interrupted_cas_skips_when_status_no_longer_running(self):
        """A drain that finishes between snapshot and claim must not be reset to queued."""
        import followup as fu
        # Point the in-process module at this test's temp root (subprocess tests use self.env).
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        self.shipped_item("20261015-cas001")
        self.run_followup("hook", "item-done", "--stamp", "20261015-cas001", "--origin", "batch-cas")
        key = "cleanup-20261015-cas001"
        st = self.state(key)
        st.update(status="done", scanned=True, findings=[{"id": "keep", "status": "open"}],
                  attempts=1, blockers=[])
        (self.root / "followups" / key / "state.json").write_text(json.dumps(st))
        # CAS: reloaded status is done, so a recover expecting running must not write.
        self.assertFalse(fu.cas_status(dict(st), "running", "queued"))
        self.assertEqual(self.state(key)["status"], "done")
        self.assertEqual(self.state(key)["findings"], [{"id": "keep", "status": "open"}])
        # A truly interrupted stage still recovers; the done sibling stays done.
        other = self.root / "followups" / "cleanup-ghost"
        other.mkdir(parents=True)
        (other / "state.json").write_text(json.dumps({
            "key": "cleanup-ghost", "stage": "project-cleanup", "repo": "demo", "origin": "x",
            "status": "running", "created_at": "2026-10-15T00:00:00", "attempts": 1,
            "findings": [], "non_findings": [], "blockers": [], "stamp": "ghost", "mode": "report-only"}))
        fu.recover_interrupted()
        ghost = self.state("cleanup-ghost")
        self.assertEqual(ghost["status"], "queued")
        self.assertTrue(any(b["kind"] == "interrupted" for b in ghost["blockers"]))
        self.assertEqual(self.state(key)["status"], "done")
        self.assertFalse(any(b.get("kind") == "interrupted" for b in self.state(key).get("blockers") or []))

    def test_added_markers_rejects_option_like_merge_commit(self):
        """An unchecked merge_commit such as --output=/path must never become a git option."""
        repo = self.code / "demo"
        out_dir = Path(self.tmp.name) / "evil-out"
        out_dir.mkdir()
        target = out_dir / "pwned"
        hostile = f"--output={target}"
        with self.assertRaises(ValueError):
            followscan.require_sha(hostile, "merge_commit")
        self.assertEqual(followscan.added_markers(repo, hostile), [])
        self.assertFalse(target.exists())
        # Short / uppercase / empty / path-shaped values are also rejected.
        for bad in ("abc123", "ABCDEFabcdef", "", "deadbeef/../x", "-c", "HEAD"):
            self.assertEqual(followscan.added_markers(repo, bad), [])
            with self.assertRaises(ValueError):
                followscan.require_sha(bad, "merge_commit")
        # A real merge SHA still finds markers.
        sha = self.shipped_item("20261015-shaok")
        markers = followscan.added_markers(repo, sha)
        self.assertTrue(markers)
        self.assertTrue(any(MARKER_LINE in line for _, line in markers))

    def test_new_stage_recovers_dir_without_state_under_claim(self):
        """A crash after mkdir and before state.json must not leave an undiscoverable orphan."""
        import followup as fu
        fu.ROOT = self.root
        fu.STATE = self.root / "followups"
        stamp = "20261015-orphan"
        self.shipped_item(stamp)
        key = f"cleanup-{stamp}"
        orphan = self.root / "followups" / key
        orphan.mkdir(parents=True)
        (orphan / "claim.lock").write_text("")  # dir exists, no state.json
        # Hook recovers: re-inits from input under the claim and writes state.json.
        out = self.run_followup("hook", "item-done", "--stamp", stamp, "--origin", "batch-orphan").stdout
        self.assertIn("queued", out)
        st = self.state(key)
        self.assertEqual(st["status"], "queued")
        self.assertEqual(st["repo"], "demo")
        self.assertTrue(any(b["kind"] == "recovered" for b in st["blockers"]))
        # Second hook is still a no-op once state is healthy.
        again = self.run_followup("hook", "item-done", "--stamp", stamp, "--origin", "batch-orphan").stdout
        self.assertIn("already queued", again)
        # Drain-side: a dir with no state (no second hook) becomes a blocked, discoverable stage.
        ghost_key = "cleanup-20261015-ghost"
        ghost = self.root / "followups" / ghost_key
        ghost.mkdir(parents=True)
        fu.recover_orphan_dirs()
        ghost_st = self.state(ghost_key)
        self.assertEqual(ghost_st["status"], "blocked")
        self.assertTrue(any(b["kind"] == "orphan-stage" for b in ghost_st["blockers"]))
        # A later hook with full input replaces the orphan placeholder.
        self.shipped_item("20261015-ghost")
        out = self.run_followup("hook", "item-done", "--stamp", "20261015-ghost",
                                "--origin", "batch-orphan").stdout
        self.assertIn("queued", out)
        recovered = self.state(ghost_key)
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["repo"], "demo")
        self.assertTrue(any(b["kind"] == "recovered" for b in recovered["blockers"]))

    def test_planner_error_evidence_uses_source_span_not_reserialized_json(self):
        """Escaped strings and pretty-printed planner_error dicts must quote the file as stored."""
        stamp = "20261015-planerr"
        run = self.root / "runs" / stamp
        run.mkdir(parents=True)
        # Pretty-printed object with an escaped quote inside a string value.
        orch_text = (
            '{\n'
            ' "action": null,\n'
            ' "planner_error": {\n'
            '  "kind": "invalid_output",\n'
            '  "cause": "missing \\"ORCHESTRATOR_RESULT_JSON\\" fence"\n'
            ' },\n'
            ' "parse_error": "missing \\"ORCHESTRATOR_RESULT_JSON\\""\n'
            '}\n'
        )
        orch_path = run / "orchestrator-result.json"
        orch_path.write_text(orch_text)
        row = {"stamp": stamp, "outcome": "error", "worker": "cursor",
               "detail": "orchestrator: missing fence", "action": "dispatch_worker"}
        (self.root / "runs.jsonl").write_text(json.dumps(row) + "\n")
        findings, _ = followscan.tooling_scan(self.root, [stamp])
        misfires = [f for f in findings if f["id"] == followscan.finding_id(
            "tooling-hardening", "ai-tools", "misfiring-automation", "worker-after-failed-plan")]
        self.assertTrue(misfires, findings)
        orch_evs = [ev for f in misfires for ev in f["evidence"] if ev["source"].endswith("orchestrator-result.json")]
        self.assertTrue(orch_evs)
        raw = orch_path.read_text()
        for ev in orch_evs:
            self.assertTrue(followscan.evidence_holds(ev, [orch_path]), ev)
            self.assertIn(ev["quote"], " ".join(raw.split()))
            # Must not be a compact json.dumps of the decoded dict (spacing / escaping differ).
            decoded = json.loads(raw)["parse_error"]
            self.assertNotEqual(ev["quote"], decoded)
            self.assertNotEqual(ev["quote"], json.dumps(json.loads(raw)["planner_error"]))
        # orchestration_failure_evidence alone (fragile-tooling path) also uses source spans.
        orch = json.loads(raw)
        ev = followscan.orchestration_failure_evidence(row, orch, run, self.root / "runs.jsonl")
        self.assertTrue(followscan.evidence_holds(ev, [orch_path]), ev)
        self.assertIn('"parse_error"', ev["quote"])
        self.assertIn(ev["quote"], " ".join(raw.split()))


if __name__ == "__main__":
    unittest.main()
