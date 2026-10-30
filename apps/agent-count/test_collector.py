import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import collector as c


def snapshot(provider="claude", state="working"):
    return {"workspaces": [{"id": "w", "terminals": [
        {"id": "t", "agent": {"id": provider, "state": state}}]}]}


class CollectorTests(unittest.TestCase):
    def test_dead_and_reused_claude_pids_are_not_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            registry = home / ".claude/sessions"
            registry.mkdir(parents=True)
            for pid, state in [(1, "busy"), (2, "waiting"), (3, "idle"), (4, "busy")]:
                (registry / (str(pid) + ".json")).write_text(json.dumps({"pid": pid, "status": state}))
            (registry / "broken.json").write_text("{")
            result = c.claude_group({"1": "claude", "2": "claude", "4": "zsh"}, home)
            self.assertEqual(result, {"provider": "claude", "working": 1, "waiting": 1, "total": 2})

    def test_open_rollout_overrides_stale_hook_and_ignores_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "rollout-session.jsonl"
            def write_events(events):
                path.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {"type": e}}) for e in events) + "\n")
            processes = {"1": "codex", "2": "codex-code-mode-host"}
            files = "p1\nn%s\np2\nn%s\n" % (path, path)
            write_events(["task_started"])
            self.assertEqual(c.codex_group(processes, home, files)[0]["working"], 1)
            write_events(["task_started", "task_complete"])
            self.assertEqual(c.codex_group(processes, home, files)[0]["working"], 0)
            write_events(["task_started", "turn_aborted"])
            self.assertEqual(c.codex_group(processes, home, files)[0]["working"], 0)
            self.assertEqual(c.codex_group({}, home, files)[0]["total"], 0)

    def test_long_turn_and_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n" +
                            json.dumps({"type": "response_item", "payload": {"text": "x" * 300000}}) + "\n{partial")
            self.assertEqual(c.rollout_status(path), "active")

    def test_local_sources_are_not_added_twice(self):
        local = [c.group("claude", ["busy", "idle"]), c.group("codex", ["active"])]
        merged = c.merge_local(local, snapshot())
        self.assertEqual(sum(g["working"] for g in merged), 2)
        self.assertEqual(sum(g["total"] for g in merged), 3)
        self.assertEqual(c.merge_local([], snapshot("cursor"))[0]["working"], 1)

    def test_snapshot_deduplicates_terminals_and_excludes_non_agents(self):
        data = snapshot()
        data["workspaces"][0]["terminals"] *= 2
        data["workspaces"][0]["terminals"].append({"id": "shell", "agent": None})
        self.assertEqual(c.snapshot_groups(data)[0]["total"], 1)
        self.assertEqual(c.snapshot_groups(snapshot(state="blocked"))[0]["waiting"], 1)

    def test_unreachable_host_replaces_old_count_and_marks_unknown(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, "CACHE", Path(directory)):
            cache = c.CACHE / "hosts.json"
            cache.write_text(json.dumps({"timestamp": time.time() - 31, "hosts": [{"groups": [c.group("claude", ["busy"])]}]}))
            with patch.object(c, "run", return_value=json.dumps([
                {"id": "self", "name": "self", "online": "local"},
                {"id": "remote", "name": "remote", "online": "yes"},
                {"id": "offline", "name": "offline", "online": "no"}])):
                with patch.object(c, "cli_snapshot", side_effect=subprocess.TimeoutExpired("superset", 7)):
                    result = c.remote_hosts("superset")
            self.assertEqual(len(result["hosts"]), 2)
            self.assertTrue(all(h["error"] and not h["groups"] for h in result["hosts"]))
            with patch.object(c, "run", side_effect=AssertionError("Must use fresh cache")):
                self.assertEqual(c.remote_hosts("superset"), result)

    def test_superset_discovery_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, "CACHE", Path(directory)):
            with patch.object(c, "run", side_effect=OSError("offline")):
                self.assertTrue(c.remote_hosts("superset")["errors"])

    def test_markup_in_host_names_cannot_add_actions(self):
        self.assertNotIn("|", c.safe("host\n| bash=/bin/sh"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            c.menu({"working": 2, "waiting": 1, "total": 5, "hosts": [], "errors": ["unreachable"]})
        self.assertTrue(output.getvalue().startswith("2 ! |"))


if __name__ == "__main__":
    unittest.main()
