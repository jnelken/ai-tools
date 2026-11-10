#!/usr/bin/env python3
"""runrecord.py: quota pauses keep their provider/reset/cause and never feed the error alert."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import runrecord  # noqa: E402
import usage  # noqa: E402


def quota(role, attempts, primary=None):
    """What worker.sh / run.sh save from `usage.py quota-record`; no live readings beyond `attempts`."""
    return usage.quota_record(usage.empty_state(), role, attempts, primary)

QUOTA_PLAN = {"action": None, "summary": "Planner claude hit its usage limit",
              "planner_error": {"kind": "quota", "provider": "claude", "tried": ["claude"],
                                "limit_text": "You've hit your session limit · resets 11:50am (America/New_York)",
                                "reset_epoch": 1791906600, "reset_at": "2026-10-13T11:50:00-04:00"}}
INVALID_PLAN = {"action": None, "parse_error": "missing ORCHESTRATOR_RESULT_JSON",
                "planner_error": {"kind": "invalid_output", "provider": "claude", "cause": "missing ORCHESTRATOR_RESULT_JSON"}}


class RunRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, orch=None, wstatus=None, wresult=None, assigned_repo=None):
        self.n += 1
        paths = {}
        for name, value in (("orch", orch), ("status", wstatus), ("result", wresult)):
            p = self.root / f"{name}-{self.n}.json"
            if value is not None:
                p.write_text(json.dumps(value))
            paths[name] = str(p)
        runrecord.main(["--root", str(self.root), "append", "--stamp", f"20261013-1030{self.n:02d}",
                        "--status", "ran", *(["--repo", assigned_repo] if assigned_repo else []), "--orch-provider", "claude", "--orch-result", paths["orch"],
                        "--worker-status", paths["status"], "--worker-result", paths["result"]])
        return runrecord.read_records(str(self.root))[-1]

    def test_assigned_repo_is_preserved_when_result_reports_a_fallback_repo(self):
        rec = self.append({'action': 'blocked_no_item', 'repo': 'fallback'},
                          {'used': 'none', 'exit': 0}, {'outcome': 'bookkeeping', 'repo': 'fallback'},
                          assigned_repo='actual')
        self.assertEqual(rec['repo'], 'actual')
        self.assertEqual(rec['assigned_repo'], 'actual')
        self.assertEqual(rec['reported_repo'], 'fallback')

    def test_planner_quota_is_a_quota_pause_with_its_details(self):
        rec = self.append(QUOTA_PLAN)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertIn("claude", rec["detail"])
        self.assertEqual(rec["quota"]["provider"], "claude")
        self.assertEqual(rec["quota"]["reset_epoch"], 1791906600)
        self.assertIn("11:50am", rec["quota"]["cause"])
        self.assertEqual(rec["quota"]["role"], "orchestrator")

    def test_planner_quota_lists_every_planner_that_ran_out(self):
        both = json.loads(json.dumps(QUOTA_PLAN))
        both["planner_error"]["tried"] = ["codex", "claude"]
        both["planner_error"]["limits"] = [
            {"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000},
            {"provider": "claude", "state": "limited", "cause": "session limit", "reset_epoch": 1791906600}]
        rec = self.append(both)
        self.assertEqual(rec["quota"]["provider"], "claude")
        self.assertEqual([p["provider"] for p in rec["quota"]["providers"]], ["codex", "claude"])
        self.assertNotIn("providers", self.append(QUOTA_PLAN)["quota"])

    def test_malformed_plan_is_still_an_error(self):
        rec = self.append(INVALID_PLAN)
        self.assertEqual(rec["outcome"], "error")
        self.assertIsNone(rec["quota"])

    def test_every_worker_over_its_gate_is_a_quota_pause(self):
        limited = [
            {"provider": "cursor", "state": "over", "reason": "auto pool 96% >= 95%", "reset_epoch": 1792000000},
            {"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000}]
        status = {"used": None, "exit": 3, "fatal": None, "missing": [], "limited": limited,
                  "quota": quota("worker", limited)}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertEqual(rec["quota"], status["quota"])
        self.assertEqual((rec["quota"]["provider"], rec["quota"]["reset_epoch"]), ("codex", 1791910000))

    def test_leased_worker_limit_without_result_is_a_quota_pause(self):
        limited = [{"provider": "cursor", "state": "limited",
                    "cause": "ActionRequiredError: You've hit your usage limit"}]
        status = {"used": "cursor", "exit": 1, "fatal": None, "missing": [], "limited": limited,
                  "quota": quota("worker", limited, "cursor")}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertIn("no unleased fallback", rec["detail"])
        self.assertEqual(rec["quota"], status["quota"])

    def test_failure_after_a_different_provider_limit_remains_an_error(self):
        status = {"used": "codex", "exit": 1, "fatal": None, "missing": [],
                  "limited": [{"provider": "cursor", "state": "limited", "cause": "usage limit"}]}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["detail"], "worker exited 1 without a result")

    def test_worker_quota_pause_with_no_known_reset_still_names_a_provider(self):
        limited = [{"provider": "cursor", "state": "over", "reason": "auto pool 96% >= 95%"},
                   {"provider": "codex", "state": "limited", "cause": "usage limit"}]
        status = {"used": None, "exit": 3, "fatal": None, "missing": [], "limited": limited,
                  "quota": quota("worker", limited)}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertEqual((rec["quota"]["provider"], rec["quota"]["reset_epoch"]), ("cursor", None))
        self.assertNotIn("?", rec["detail"])

    def test_every_worker_gated_without_a_quota_record_still_has_one(self):
        # usage.py quota-record failed, so worker.sh's status has no "quota"; its own log still counts.
        limited = [
            {"provider": "cursor", "state": "over", "reason": "auto pool 96% >= 95%", "reset_epoch": 1792000000,
             "reset_at": "2026-10-14T13:46:40-04:00"},
            {"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000,
             "reset_at": "2026-10-13T12:46:40-04:00"}]
        status = {"used": None, "exit": 3, "fatal": None, "missing": [], "limited": limited, "quota": None}
        rec = self.append({"action": "dispatch_worker", "repo": "demo"}, status)
        self.assertEqual(rec["outcome"], "quota-paused")
        q = rec["quota"]
        self.assertEqual((q["role"], q["provider"], q["reset_epoch"], q["reset_at"]),
                         ("worker", "codex", 1791910000, "2026-10-13T12:46:40-04:00"))
        self.assertIn("cursor: auto pool 96%", q["cause"])
        self.assertIn("codex: usage limit", q["cause"])
        self.assertEqual([(p["provider"], p["state"]) for p in q["providers"]],
                         [("cursor", "over"), ("codex", "limited")])

    def test_worker_limit_hit_without_a_quota_record_keeps_the_logged_reset(self):
        limited = [{"provider": "cursor", "state": "over", "reason": "auto pool 96% >= 95%", "reset_epoch": 1791900000},
                   {"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000}]
        rec = self.append({"action": "dispatch_worker"}, {"used": "codex", "exit": 0, "limited": limited},
                          {"outcome": "limit_hit", "provider": "codex", "limit_text": "usage limit"})
        self.assertEqual((rec["quota"]["provider"], rec["quota"]["reset_epoch"], rec["quota"]["cause"]),
                         ("codex", 1791910000, "usage limit"))
        self.assertEqual(len(rec["quota"]["providers"]), 2)

    def test_a_missing_worker_binary_is_still_an_error(self):
        status = {"used": None, "exit": 3, "fatal": None, "missing": ["claude"],
                  "limited": [{"provider": "cursor", "state": "over"}]}
        self.assertEqual(self.append({"action": "dispatch_worker"}, status)["outcome"], "error")

    def test_worker_limit_hit_is_a_quota_pause(self):
        rec = self.append({"action": "dispatch_worker"}, {"used": "codex", "exit": 0},
                          {"outcome": "limit_hit", "provider": "codex", "limit_text": "usage limit"})
        self.assertEqual(rec["outcome"], "quota-paused")
        self.assertEqual(rec["quota"]["cause"], "usage limit")

    def test_worker_limit_hit_carries_the_reset_worker_sh_recorded(self):
        # worker.sh records a result-only limit_hit itself; its status holds the record and reset.
        limited = [{"provider": "codex", "state": "limited", "cause": "usage limit", "reset_epoch": 1791910000,
                    "reset_at": "2026-10-13T12:46:40-04:00"}]
        status = {"used": "codex", "exit": 0, "limited": limited, "quota": quota("worker", limited, "codex")}
        for result in ({"outcome": "limit_hit", "provider": "codex", "limit_text": "usage limit"},
                       {"outcome": "limit_hit", "limit_text": "usage limit"}):
            with self.subTest(result=result):
                rec = self.append({"action": "dispatch_worker"}, status, result)
                self.assertEqual((rec["quota"]["provider"], rec["quota"]["reset_epoch"]), ("codex", 1791910000))
                self.assertEqual(rec["quota"]["cause"], "usage limit")

    def test_worker_limit_hit_without_a_quota_record_still_names_who_stopped(self):
        rec = self.append({"action": "dispatch_worker"}, {"used": "codex", "exit": 0, "limited": []},
                          {"outcome": "limit_hit", "limit_text": "usage limit"})
        self.assertEqual((rec["quota"]["role"], rec["quota"]["provider"]), ("worker", "codex"))

    def test_quota_pauses_never_alert_and_never_count_toward_the_error_streak(self):
        for _ in range(4):
            self.append(QUOTA_PLAN)
        self.assertEqual(runrecord.alert_text(runrecord.read_records(str(self.root))), "")
        self.append(INVALID_PLAN)
        self.assertEqual(runrecord.error_streak(runrecord.read_records(str(self.root))), 1)
        self.append(QUOTA_PLAN)
        records = runrecord.read_records(str(self.root))
        self.assertEqual(runrecord.error_streak(records), 1)
        self.assertEqual(runrecord.alert_text(records), "")
        self.append(INVALID_PLAN)
        self.assertIn("failed 2 runs in a row", runrecord.alert_text(runrecord.read_records(str(self.root))))

    def skip(self, gates):
        self.n += 1
        args = ["--root", str(self.root), "append", "--stamp", f"20261013-1031{self.n:02d}",
                "--status", "skipped-quota", "--detail", "no orchestrator available"]
        if gates is not None:
            path = self.root / f"gates-{self.n}.json"
            path.write_text(json.dumps(gates))
            args += ["--quota-gates", str(path)]
        runrecord.main(args)
        return runrecord.read_records(str(self.root))[-1]

    ORCH_GATED = quota("orchestrator", [
        {"provider": "codex", "state": "over", "reason": "weekly 90% >= 90%",
         "reset_epoch": 1792000000, "reset_at": "2026-10-15T09:00:00-04:00"},
        {"provider": "claude", "state": "limited", "reason": "session limit",
         "reset_epoch": 1791906600, "reset_at": "2026-10-13T11:50:00-04:00"}])

    def test_quota_skip_keeps_who_is_out_and_the_earliest_reset(self):
        rec = self.skip(self.ORCH_GATED)
        self.assertEqual(rec["outcome"], "skipped-quota")
        self.assertEqual((rec["quota"]["role"], rec["quota"]["provider"], rec["quota"]["reset_epoch"]),
                         ("orchestrator", "claude", 1791906600))
        self.assertEqual(rec["quota"]["reset_at"], "2026-10-13T11:50:00-04:00")
        self.assertIn("codex: weekly 90%", rec["quota"]["cause"])
        self.assertEqual(len(rec["quota"]["providers"]), 2)

    def test_quota_skip_without_a_gated_reading_has_no_quota(self):
        self.assertIsNone(self.skip(None)["quota"])
        self.assertIsNone(self.skip(quota("worker", []))["quota"])

    def test_quota_skips_never_alert_and_never_count_toward_the_error_streak(self):
        self.append(INVALID_PLAN)
        for _ in range(3):
            self.skip(self.ORCH_GATED)
        records = runrecord.read_records(str(self.root))
        self.assertEqual(runrecord.error_streak(records), 1)
        self.assertEqual(runrecord.alert_text(records), "")




class RecoveryAlertTest(unittest.TestCase):
    def row(self, stamp, outcome, repo=None, item=None, **extra):
        return dict(stamp=stamp, outcome=outcome, repo=repo, linear_id=item, **extra)

    def failures(self):
        return [self.row('a', 'error', 'ai-tools', 'DEV-324'),
                self.row('b', 'error', 'typey.site', 'DEV-211')]

    def shipped(self, stamp='c', repo='ai-tools', item='DEV-324', **extra):
        return self.row(stamp, 'shipped', repo, item, worker='codex', worker_exit=0, **extra)

    def test_unrelated_no_work_never_claims_recovery(self):
        records = self.failures()
        self.assertIn('failed 2 runs in a row', runrecord.alert_text(records))
        records.append(self.row('c', 'blocked-no-item'))
        self.assertEqual(runrecord.alert_text(records), '')
        records.append(self.row('d', 'blocked-no-item', 'ai-tools', 'DEV-324'))
        self.assertEqual(runrecord.alert_text(records), '')

    def test_different_repository_or_ticket_success_does_not_recover_failed_work(self):
        for success in [self.shipped(repo='other'), self.shipped(item='DEV-999')]:
            self.assertEqual(runrecord.alert_text(self.failures() + [success]), '')

    def test_one_worker_recovery_is_scoped_and_keeps_other_failures_visible(self):
        text = runrecord.alert_text(self.failures() + [self.shipped()])
        self.assertIn('recovered ai-tools · DEV-324', text)
        self.assertIn('other failed work remains unresolved', text)

    def test_recovery_survives_interleaved_noops_and_quota_pauses(self):
        records = self.failures() + [self.row('c', 'blocked-no-item'), self.row('d', 'quota-paused'),
                                    self.row('e', 'skipped-quota')]
        records.append(self.shipped(stamp='f'))
        self.assertIn('recovered ai-tools', runrecord.alert_text(records))
        records.append(self.shipped(stamp='g', repo='typey.site', item='DEV-211'))
        text = runrecord.alert_text(records)
        self.assertIn('recovered typey.site', text)
        self.assertNotIn('remain', text)
        records.append(self.shipped(stamp='h', repo='typey.site', item='DEV-211'))
        self.assertEqual(runrecord.alert_text(records), '')

    def test_missing_worker_or_nonzero_exit_cannot_confirm_recovery(self):
        for patch in [{'worker': None}, {'worker_exit': None}, {'worker_exit': 1}, {'outcome': 'shipped-deploy-failed'}]:
            success = self.shipped(); success.update(patch)
            self.assertNotIn('recovered', runrecord.alert_text(self.failures() + [success]))

    def test_missing_work_identity_cannot_establish_same_item_recovery(self):
        records = [self.row('a', 'error', 'demo'), self.row('b', 'error', 'demo')]
        self.assertEqual(runrecord.alert_text(records + [self.shipped(repo='demo', item=None)]), '')

    def test_branch_or_item_can_identify_ticketless_work(self):
        for field in ['branch', 'item']:
            records = [dict(self.row('a', 'error', 'demo'), **{field: 'same-work'}),
                       dict(self.row('b', 'error', 'demo'), **{field: 'same-work'}),
                       dict(self.shipped(repo='demo', item=None), **{field: 'same-work'})]
            self.assertIn('recovered demo · same-work', runrecord.alert_text(records))

    def test_unidentified_planner_failure_is_not_cleared_by_any_worker(self):
        records = [self.row('a', 'error'), self.row('b', 'error')]
        self.assertEqual(runrecord.alert_text(records + [self.shipped()]), '')


if __name__ == "__main__":
    unittest.main()
