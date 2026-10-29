#!/usr/bin/env python3
"""verdict.py: planner failures are never saved as no-work verdicts, and old ones are discarded."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "lib" / "verdict.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oct13"
sys.path.insert(0, str(HERE / "lib"))
import verdict  # noqa: E402
STAMP = "20261013-103004"


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "root"
        (self.root / "runs").mkdir(parents=True)
        (self.dir / "code").mkdir()
        self.verdict = self.root / "batch-verdicts" / "apt-sqft.json"
        self.verdict.parent.mkdir()
        self.fp = self.dir / "fingerprint.json"
        self.fp.write_text(json.dumps({"repos": {}, "issues": {}, "linear_ok": True}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_verdict(self, *args):
        env = dict(os.environ, PERSONAL_CODE_DIR=str(self.dir / "code"), ADVANCE_ROADMAP_ROOT=str(self.root))
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def check(self):
        return self.run_verdict("check", "--file", str(self.verdict), "--fingerprint", str(self.fp),
                                "--changes", str(self.dir / "changes.json"), "--root", str(self.root))

    def write_verdict(self, **over):
        v = {"stamp": "20261014-103004", "decided_at": datetime.now().isoformat(timespec="seconds"),
             "outcome_token": "blocked-no-item", "blockers": [], "considered": [],
             "summary": "No work qualified for apt-sqft.", "fingerprint": json.loads(self.fp.read_text())}
        v.update(over)
        self.verdict.write_text(json.dumps(v))

    def save(self, orch, worker):
        o, w = self.dir / "orch.json", self.dir / "worker.json"
        o.write_text(json.dumps(orch))
        w.write_text(json.dumps(worker))
        return self.run_verdict("save", "--file", str(self.verdict), "--fingerprint", str(self.fp),
                                "--orch-result", str(o), "--worker-result", str(w), "--stamp", "20261014-103004")

    # ── save ──────────────────────────────────────────────────────────────────

    def test_valid_no_work_verdict_is_saved(self):
        p = self.save({"action": "blocked_no_item", "outcome_token": "blocked-no-item", "summary": "nothing"},
                      {"outcome": "bookkeeping"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(self.verdict.read_text())["summary"], "nothing")

    def test_oct13_synthesized_plan_is_not_saved(self):
        orch = json.loads((FIXTURES / "orchestrator-result.json").read_text())
        self.write_verdict()
        p = self.save(orch, {"outcome": "bookkeeping"})
        self.assertIn("not carried forward", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_planner_errors_are_never_saved(self):
        cases = [
            ({"action": "blocked_no_item", "summary": "You've hit your session limit · resets 11:50am"}, {"outcome": "bookkeeping"}),
            ({"action": None, "planner_error": {"kind": "quota", "provider": "claude"}}, {"outcome": "bookkeeping"}),
            ({"action": "blocked_no_item", "parse_error": "missing ORCHESTRATOR_RESULT_JSON"}, {"outcome": "bookkeeping"}),
            ({"action": "blocked_no_item", "summary": "nothing"},
             {"outcome": "bookkeeping", "limit_text": "Orchestrator: You've hit your session limit"}),
        ]
        for orch, worker in cases:
            with self.subTest(orch=orch, worker=worker):
                self.write_verdict()
                self.save(orch, worker)
                self.assertFalse(self.verdict.exists())

    # ── check ─────────────────────────────────────────────────────────────────

    def test_unchanged_valid_verdict_still_skips(self):
        self.write_verdict()
        p = self.check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.verdict.exists())

    def test_oct13_contaminated_verdict_is_discarded_and_stays_gone(self):
        v = json.loads((FIXTURES / "batch-verdict-apt-sqft.json").read_text())
        v.update(decided_at=datetime.now().isoformat(timespec="seconds"), fingerprint=json.loads(self.fp.read_text()))
        self.verdict.write_text(json.dumps(v))
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("discarded", p.stdout)
        self.assertFalse(self.verdict.exists())
        self.assertFalse((self.dir / "changes.json").exists(), "the planner must not be handed it to carry forward")
        p = self.check()  # idempotent: nothing left to discard
        self.assertEqual(p.returncode, 1)
        self.assertIn("no carried verdict", p.stdout)

    def test_clean_summary_is_discarded_on_its_runs_orchestrator_evidence(self):
        self.write_verdict(stamp=STAMP)
        (self.root / "runs" / STAMP).mkdir()
        shutil.copy(FIXTURES / "orchestrator-result.json", self.root / "runs" / STAMP / "orchestrator-result.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("planner output unusable", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_clean_summary_is_discarded_on_its_run_record(self):
        self.write_verdict(stamp=STAMP)
        row = json.loads((FIXTURES / "runs-row.json").read_text())
        with open(self.root / "runs.jsonl", "w") as f:
            f.write(json.dumps({"stamp": "20261013-103003", "outcome": "shipped"}) + "\n")
            f.write(json.dumps(row) + "\n")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("ended error", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_a_good_run_record_keeps_the_verdict(self):
        self.write_verdict(stamp=STAMP)
        (self.root / "runs.jsonl").write_text(json.dumps({"stamp": STAMP, "outcome": "blocked-no-item"}) + "\n")
        self.assertEqual(self.check().returncode, 0)
        self.assertTrue(self.verdict.exists())


def issue(id_, labels=(), state="Todo", state_type="unstarted", blocked_by=(), description="", title=None):
    return {"id": id_, "title": title or id_, "description": description, "state": state, "state_type": state_type,
            "priority": 2, "labels": list(labels), "blocked_by": list(blocked_by), "human_comments_sha": "-"}


class ScopedVerdictTest(unittest.TestCase):
    """A daily batch's verdict depends on its own repo and the issues that concern it, nothing more."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "root"
        (self.root / "runs").mkdir(parents=True)
        self.code = self.dir / "code"
        for name in ("a", "b"):
            self.git_repo(name)
        self.issues = [issue("DEV-1", ["repo/a"]), issue("DEV-2", ["repo/b"]), issue("DEV-3")]
        self.deploys = {"a": "success", "b": "success"}
        self.verdict = self.root / "last-verdict-a.json"

    def tearDown(self):
        self.tmp.cleanup()

    def git_repo(self, name):
        repo = self.code / name
        hermetic = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], env=hermetic, check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", f"git@github.com:jnelken/{name}.git"],
                       env=hermetic, check=True)
        self.commit(name, "init")

    def commit(self, name, message):
        hermetic = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.code / name), "commit",
                        "-q", "--allow-empty", "-m", message], env=hermetic, check=True)

    def run_verdict(self, *args):
        env = dict(os.environ, PERSONAL_CODE_DIR=str(self.code), ADVANCE_ROADMAP_ROOT=str(self.root))
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def fingerprint(self, out="fp.json", repo="a"):
        snap, deploys = self.dir / "snap.json", self.dir / "deploys.json"
        snap.write_text(json.dumps({"ok": True, "issues": self.issues}))
        deploys.write_text(json.dumps({"repos": {k: {"status": v} for k, v in self.deploys.items()}}))
        args = ["fingerprint", "--snapshot", str(snap), "--deploys", str(deploys), "--out", str(self.dir / out)]
        p = self.run_verdict(*args, *(["--repo", repo] if repo else []))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads((self.dir / out).read_text())

    def save(self, orch, worker=None, pending=False, fp="fp.json", post=None, consumed=False):
        o, w = self.dir / "orch.json", self.dir / "worker.json"
        o.write_text(json.dumps(orch))
        w.write_text(json.dumps({"outcome": "bookkeeping"} if worker is None else worker))
        return self.run_verdict("save", "--file", str(self.verdict), "--fingerprint", str(self.dir / fp),
                                "--orch-result", str(o), "--worker-result", str(w), "--stamp", "20261014-103004",
                                *(["--bookkeeping-pending"] if pending else []),
                                *(["--post-fingerprint", str(self.dir / post), "--repo", "a"] if post else []),
                                *( ["--partial-triage-consumed"] if consumed else []))

    def check(self, fp="fp2.json", *extra):
        return self.run_verdict("check", "--file", str(self.verdict), "--fingerprint", str(self.dir / fp),
                                "--changes", str(self.dir / "changes.json"), "--root", str(self.root), *extra)

    NO_WORK = {"action": "blocked_no_item", "outcome_token": "blocked-no-item", "summary": "nothing"}

    def carried(self):
        self.fingerprint("fp.json")
        self.assertEqual(self.save(self.NO_WORK).returncode, 0)
        self.assertTrue(self.verdict.exists())

    def test_post_worker_updates_only_bookkeeping_issue_ids(self):
        # Even changed metadata and newly added issues stay visible on the next check.
        self.issues += [issue('DEV-4', ['repo/a']), issue('DEV-5', ['repo/a'])]
        self.fingerprint()  # these existed at planning time too
        orch = {**self.NO_WORK, 'blockers': [{'linear_id': 'DEV-1'}],
                'closeouts': [{'linear_id': 'DEV-3'}], 'directive_ticket': 'DEV-4'}
        self.issues = [{**i, 'title': 'updated'} for i in self.issues]
        self.issues.append(issue('DEV-6', ['repo/a']))
        self.fingerprint('fp2.json')
        self.save(orch, post='fp2.json')
        p = self.check()
        self.assertEqual(p.returncode, 1, p.stdout)
        changes = json.loads((self.dir / 'changes.json').read_text())['changed']
        self.assertEqual(changes, ['issue:DEV-5', 'issue:DEV-6'])

    def test_post_worker_repo_changes_are_adopted_only_for_committed_target_archives(self):
        for archives, merge, adopted in (([], 'abc', False), (['a/docs/plan.md'], None, False),
                                          (['b/docs/plan.md'], 'abc', False),
                                          (['a/docs/plan.md'], 'abc', True)):
            with self.subTest(archives=archives, merge=merge):
                self.fingerprint()
                self.commit('a', 'outside or archive commit')
                self.fingerprint('fp2.json')
                self.save({**self.NO_WORK, 'repo': 'a', 'archives': archives},
                          worker={'outcome': 'bookkeeping', 'merge_commit': merge}, post='fp2.json')
                self.assertEqual(self.check().returncode, 0 if adopted else 1)

    def test_failed_post_snapshot_does_not_erase_the_bookkeeping_issues(self):
        self.fingerprint()
        post = self.dir / 'post.json'
        fp = json.loads((self.dir / 'fp.json').read_text())
        post.write_text(json.dumps({**fp, 'linear_ok': False, 'issues': {}}))
        self.save({**self.NO_WORK, 'blockers': [{'linear_id': 'DEV-1'}]}, post='post.json')
        self.assertEqual(json.loads(self.verdict.read_text())['fingerprint'], fp)

    def test_a_commit_in_repo_b_leaves_repo_a_valid(self):
        self.carried()
        self.commit("b", "work in b")
        self.fingerprint("fp2.json")
        p = self.check()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.commit("a", "work in a")
        self.fingerprint("fp2.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("repo:a", p.stdout)

    def test_ticket_edits_in_other_repos_leave_it_valid_but_this_repos_and_unlabeled_ones_do_not(self):
        self.carried()
        self.issues[1] = issue("DEV-2", ["repo/b"], title="renamed")
        self.fingerprint("fp2.json")
        self.assertEqual(self.check().returncode, 0)
        for edited in (issue("DEV-1", ["repo/a"], title="renamed"), issue("DEV-3", title="renamed")):
            with self.subTest(issue=edited["id"]):
                self.issues = [issue("DEV-1", ["repo/a"]), issue("DEV-2", ["repo/b"]), issue("DEV-3")]
                self.issues = [edited if i["id"] == edited["id"] else i for i in self.issues]
                self.fingerprint("fp2.json")
                p = self.check()
                self.assertEqual(p.returncode, 1)
                self.assertIn(f"issue:{edited['id']}", p.stdout)

    def test_a_blocker_finishing_in_another_repo_reopens_triage(self):
        self.issues[0] = issue("DEV-1", ["repo/a"], blocked_by=["DEV-2"])
        self.carried()
        self.issues[1] = issue("DEV-2", ["repo/b"], state="Done", state_type="completed")
        self.fingerprint("fp2.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("issue:DEV-2", p.stdout)

    def test_deploys_are_in_both_fingerprints_so_the_saved_verdict_can_pass_its_check(self):
        # The fingerprint saved after the worker once omitted --deploys: `deploy:<repo>` then read as
        # changed against every check-time fingerprint, so a repo with a deploy never skipped.
        self.carried()
        self.assertEqual(json.loads(self.verdict.read_text())["fingerprint"]["deploys"], {"a": "success"})
        self.fingerprint("fp2.json")
        self.assertEqual(self.check().returncode, 0)
        self.deploys["b"] = "failure"  # another repo's deploy is not this verdict's business
        self.fingerprint("fp2.json")
        self.assertEqual(self.check().returncode, 0)
        self.deploys["a"] = "failure"
        self.fingerprint("fp2.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("deploy:a", p.stdout)

    def test_unscoped_fingerprint_still_covers_everything(self):
        self.carried()
        fp = self.fingerprint("fp-all.json", repo=None)
        self.assertEqual(sorted(fp["repos"]), ["a", "b"])
        self.assertEqual(sorted(fp["issues"]), ["DEV-1", "DEV-2", "DEV-3"])

    def test_partial_triage_presence_changes_the_repo_fingerprint(self):
        before = self.fingerprint("before.json")
        triage = self.code / ".advance-roadmap" / "partial-triage-a.md"
        triage.parent.mkdir(parents=True)
        triage.write_text("unfinished planner findings\n")
        after = self.fingerprint("after.json")
        self.assertEqual(before["partial_triage"], {})
        self.assertIn("a", after["partial_triage"])
        self.assertIn("partial_triage:a", verdict.diff(before, after))

    def test_consumed_partial_triage_is_settled_absent_in_saved_fingerprint(self):
        triage = self.code / ".advance-roadmap" / "partial-triage-a.md"
        triage.parent.mkdir(parents=True)
        triage.write_text("findings consumed by planner\n")
        self.fingerprint("fp.json")
        triage.unlink()
        self.fingerprint("post.json")
        p = self.save(self.NO_WORK, fp="fp.json", post="post.json", consumed=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        saved = json.loads(self.verdict.read_text())
        self.assertNotIn("a", saved["fingerprint"]["partial_triage"])
        self.fingerprint("fp2.json")
        self.assertEqual(self.check().returncode, 0, "the digest deletion is already settled")

    def test_batch_runs_carry_a_verdict_for_72_hours(self):
        self.carried()
        v = json.loads(self.verdict.read_text())
        v["decided_at"] = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
        self.verdict.write_text(json.dumps(v))
        self.fingerprint("fp2.json")
        self.assertEqual(self.check().returncode, 1)  # a lone tick re-derives it after 24h
        self.assertEqual(self.check("fp2.json", "--max-age-hours", "72").returncode, 0)
        v["decided_at"] = (datetime.now() - timedelta(hours=80)).isoformat(timespec="seconds")
        self.verdict.write_text(json.dumps(v))
        self.assertEqual(self.check("fp2.json", "--max-age-hours", "72").returncode, 1)

    # ── a bookkeeping worker is skipped only when it has nothing to do ────────

    def noop(self, orch):
        o = self.dir / "noop-orch.json"
        o.write_text(json.dumps({**self.NO_WORK, **orch}))
        return self.run_verdict("noop-check", "--file", str(self.verdict), "--orch-result", str(o)).returncode

    def test_noop_needs_a_clean_no_work_result(self):
        self.assertEqual(self.noop({}), 0, "no blockers, no previous verdict: nothing to post")
        for over in ({"archives": ["a/docs/plans/x.md"]}, {"closeouts": [{"repo": "a", "linear_id": "DEV-1", "evidence": "e"}]},
                     {"directive_ticket": "DEV-1"}, {"worker_brief": "close out the recovered deploy ticket"},
                     {"action": "dispatch_worker"}, {"planner_error": {"kind": "quota"}}):
            with self.subTest(over=over):
                self.assertEqual(self.noop(over), 1)

    def test_noop_only_when_every_blocker_is_already_in_the_previous_verdict(self):
        self.carried()
        v = json.loads(self.verdict.read_text())
        v["blockers"] = [{"repo": "a", "linear_id": "DEV-1", "detail": "needs a decision"}]
        self.verdict.write_text(json.dumps(v))
        self.assertEqual(self.noop({"blockers": [{"repo": "a", "linear_id": "DEV-1", "detail": "reworded"}]}), 0)
        self.assertEqual(self.noop({"blockers": [{"repo": "a", "linear_id": "DEV-1", "detail": "x"},
                                                 {"repo": "a", "linear_id": "DEV-9", "detail": "new"}]}), 1)
        self.assertEqual(self.noop({"blockers": [{"repo": "a", "detail": "no ticket, no previous"}]}), 1)

    def test_a_deferred_verdict_is_marked_and_its_blockers_are_not_taken_as_posted(self):
        blocker = {"repo": "a", "linear_id": "DEV-1", "detail": "needs a decision"}
        self.fingerprint("fp.json")
        # The batch deferred the bookkeeping worker: no worker result exists at all.
        p = self.save({**self.NO_WORK, "blockers": [blocker]}, worker={}, pending=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(self.verdict.read_text())["bookkeeping_pending"])
        self.assertEqual(self.noop({"blockers": [blocker]}), 1)
        # Without the flag an unsettled worker still means "not carried forward".
        p = self.save({**self.NO_WORK, "blockers": [blocker]}, worker={})
        self.assertIn("not carried forward", p.stdout)
        self.assertFalse(self.verdict.exists())

    def test_a_deferred_verdict_is_never_skipped_unchanged(self):
        self.fingerprint("fp.json")
        self.save({**self.NO_WORK, "blockers": [{"repo": "a", "linear_id": "DEV-1", "detail": "d"}]},
                  worker={}, pending=True)
        self.fingerprint("fp2.json")
        p = self.check()
        self.assertEqual(p.returncode, 1)
        self.assertIn("bookkeeping", p.stdout)
        self.assertTrue((self.dir / "changes.json").exists(), "the planner still gets the previous verdict to carry forward")

    def test_pending_verdict_cannot_be_saved_for_dispatched_work(self):
        self.carried()
        p = self.save({"action": "dispatch_worker", "outcome_token": None, "summary": "x"}, worker={}, pending=True)
        self.assertIn("not carried forward", p.stdout)
        self.assertFalse(self.verdict.exists())


if __name__ == "__main__":
    unittest.main()
