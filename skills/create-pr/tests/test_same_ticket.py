#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "same_ticket.py"

# Every run is offline: HOME has no denylist, no Linear key is set, and the only
# `linear` on PATH is this fake. It prints $FAKE_LINEAR_OUT when set and fails
# (exit 4, "not authenticated") otherwise.
SANDBOX = tempfile.TemporaryDirectory()
FAKE_BIN = Path(SANDBOX.name) / "bin"
FAKE_BIN.mkdir()
(FAKE_BIN / "linear").write_text(
    '#!/bin/sh\n'
    'echo "$@" >> "${FAKE_LINEAR_LOG:-/dev/null}"\n'
    '[ -n "$FAKE_LINEAR_OUT" ] && exec cat "$FAKE_LINEAR_OUT"\n'
    'echo "not authenticated" >&2; exit 4\n'
)
(FAKE_BIN / "linear").chmod(0o755)


def sandbox_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LINEAR_", "FAKE_LINEAR_"))}
    env["HOME"] = SANDBOX.name
    env["PATH"] = f"{FAKE_BIN}:/usr/bin:/bin"
    env.update(extra)
    return env


def run(*args, cwd=None, env=None):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args], text=True, capture_output=True, cwd=cwd,
        env=env or sandbox_env(),
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def pr(number, title, head, decision="REVIEW_REQUIRED"):
    return {"number": number, "title": title, "headRefName": head,
            "reviewDecision": decision, "isDraft": False}


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def decide(self, prs, *extra, repo="Concentro-Inc/woodrow", ticket="CON-4451", branch="jake/new"):
        path = self.dir / "prs.json"
        path.write_text(json.dumps(prs))
        args = ["decide", "--repo", repo, "--default", "main", "--ticket", ticket,
                "--branch", branch, "--prs-json", str(path), *extra]
        if "--no-fetch" not in extra:
            args.append("--skip-ancestry")
        return json.loads(run(*args, cwd=self.dir))

    def test_stacks_onto_unapproved_same_ticket_pr(self):
        out = self.decide([pr(1972, "Recordings: speakers (CON-4451)", "jake/recordings-speakers")])
        self.assertEqual(out["decision"], "stack")
        self.assertEqual(out["base"], "jake/recordings-speakers")
        self.assertEqual(out["base_line"], "Base: stacked onto #1972 (same CON-4451, not yet approved)")

    def test_approved_match_stays_on_main(self):
        out = self.decide([pr(1969, "Update kb (CON-4461)", "con-4461-kb", "APPROVED")], ticket="CON-4461")
        self.assertEqual(out["decision"], "main")
        self.assertEqual(out["base"], "main")
        self.assertEqual(out["base_line"], "Base: main (#1969 already approved)")

    def test_prefix_ticket_does_not_match(self):
        out = self.decide([pr(10, "Something (CON-441)", "b")], ticket="CON-44")
        self.assertEqual(out["decision"], "none")

    def test_own_branch_is_ignored(self):
        out = self.decide([pr(10, "Mine (CON-4451)", "jake/new")])
        self.assertEqual(out["decision"], "none")

    def test_prefers_lowest_unapproved(self):
        out = self.decide([
            pr(30, "Later (CON-4451)", "c"),
            pr(10, "Approved (CON-4451)", "a", "APPROVED"),
            pr(20, "Open (CON-4451)", "b"),
        ])
        self.assertEqual((out["decision"], out["pr"]), ("stack", 20))

    def test_out_of_scope_repos(self):
        for repo in ("jnelken/ai-tools", "Concentro-Inc/internal-tools"):
            out = self.decide([pr(1, "X (CON-4451)", "a")], repo=repo)
            self.assertEqual(out["decision"], "none", repo)

    def test_ancestry_gate(self):
        repo = self.dir / "repo"
        origin = self.dir / "origin.git"
        git(self.dir, "init", "-q", "--bare", str(origin))
        git(self.dir, "init", "-q", "-b", "main", str(repo))
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        git(repo, "remote", "add", "origin", str(origin))

        def commit(name):
            (repo / name).write_text(name)
            git(repo, "add", name)
            git(repo, "commit", "-q", "-m", name)

        commit("base")
        git(repo, "checkout", "-q", "-b", "parent")
        commit("parent-work")
        git(repo, "push", "-q", "origin", "parent")
        git(repo, "checkout", "-q", "main")
        commit("unrelated-main")
        git(repo, "push", "-q", "origin", "main")

        prs = [pr(5, "Parent (CON-4451)", "parent")]

        # Follow-up cut from the parent branch: stacks.
        git(repo, "checkout", "-q", "-b", "from-parent", "origin/parent")
        commit("follow-up-a")
        path = self.dir / "prs.json"
        path.write_text(json.dumps(prs))
        base = ["decide", "--repo", "Concentro-Inc/woodrow", "--default", "main",
                "--ticket", "CON-4451", "--prs-json", str(path)]
        out = json.loads(run(*base, "--branch", "from-parent", cwd=repo))
        self.assertEqual(out["decision"], "stack")

        # Follow-up cut from a main the parent lacks: falls back.
        git(repo, "checkout", "-q", "-b", "from-main", "origin/main")
        commit("follow-up-b")
        out = json.loads(run(*base, "--branch", "from-main", cwd=repo))
        self.assertEqual(out["decision"], "main")
        self.assertEqual(
            out["base_line"],
            "Base: main (could not stack onto #5: branch is cut from a newer main than #5)",
        )


TIPS = {"id": "proj-tips", "name": "In-app Folio tips"}
UMBRELLA = {"id": "proj-kf", "name": "Knowledge Foundation"}


class ProjectTests(unittest.TestCase):
    """Fall back to the Linear project when no other PR carries the same ticket."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.log = self.dir / "linear.log"

    def tearDown(self):
        self.temp.cleanup()

    def decide(self, prs, projects=None, denylist=None, ticket="CON-4373"):
        """projects: ticket -> project dict (None for no project); omitted, Linear fails."""
        (self.dir / "prs.json").write_text(json.dumps(prs))
        env = {"FAKE_LINEAR_LOG": str(self.log)}
        if projects is not None:
            nodes = [{"identifier": t, "project": p} for t, p in projects.items()]
            (self.dir / "linear.json").write_text(json.dumps({"data": {"issues": {"nodes": nodes}}}))
            env["FAKE_LINEAR_OUT"] = str(self.dir / "linear.json")
        args = ["decide", "--repo", "Concentro-Inc/woodrow", "--default", "main",
                "--ticket", ticket, "--branch", "jake/new", "--prs-json", str(self.dir / "prs.json"),
                "--skip-ancestry"]
        if denylist is not None:
            (self.dir / "stack.json").write_text(json.dumps({"projectDenylist": denylist}))
            args += ["--config", str(self.dir / "stack.json")]
        return json.loads(run(*args, cwd=self.dir, env=sandbox_env(**env)))

    def linear_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_stacks_onto_same_project_pr(self):
        out = self.decide(
            [pr(1964, "Tip registry (CON-4372)", "jake/tips-registry"),
             pr(1950, "Unrelated (CON-4000)", "jake/other")],
            {"CON-4373": TIPS, "CON-4372": TIPS, "CON-4000": None},
        )
        self.assertEqual((out["decision"], out["pr"], out["base"]), ("stack", 1964, "jake/tips-registry"))
        self.assertEqual(out["match"], "project")
        self.assertEqual(out["base_line"],
                         'Base: stacked onto #1964 (same project "In-app Folio tips", not yet approved)')
        self.assertIsNone(out["note"])
        calls = self.linear_calls()
        self.assertEqual(len(calls), 1, "one batched lookup")
        self.assertIn("--workspace concentro", calls[0])
        self.assertIn("[4000, 4372, 4373]", calls[0])

    def test_same_ticket_wins_over_same_project(self):
        out = self.decide(
            [pr(1960, "Same project (CON-4372)", "jake/a"),
             pr(1967, "Same ticket (CON-4373)", "jake/b")],
            {"CON-4373": TIPS, "CON-4372": TIPS},
        )
        self.assertEqual((out["decision"], out["pr"], out["match"]), ("stack", 1967, "ticket"))
        self.assertEqual(self.linear_calls(), [], "no Linear call when the ticket matches")

    def test_approved_same_project_parent_is_skipped(self):
        out = self.decide(
            [pr(1964, "Approved (CON-4372)", "jake/a", "APPROVED"),
             pr(1966, "Open (CON-4374)", "jake/b")],
            {"CON-4373": TIPS, "CON-4372": TIPS, "CON-4374": TIPS},
        )
        self.assertEqual((out["decision"], out["pr"]), ("stack", 1966))

    def test_all_same_project_approved_stays_on_main(self):
        out = self.decide([pr(1964, "Approved (CON-4372)", "jake/a", "APPROVED")],
                          {"CON-4373": TIPS, "CON-4372": TIPS})
        self.assertEqual(out["decision"], "main")
        self.assertEqual(out["base_line"],
                         'Base: main (#1964 same project "In-app Folio tips" already approved)')

    def test_ticket_without_project_never_project_stacks(self):
        out = self.decide([pr(1964, "Other (CON-4372)", "jake/a")],
                          {"CON-4373": None, "CON-4372": None})
        self.assertEqual(out["decision"], "none")
        self.assertIn("has no project", out["reason"])
        self.assertIsNone(out["base_line"])

    def test_denylisted_project_by_name_or_id(self):
        prs = [pr(1636, "Task views (CON-4223)", "jake/tasks")]
        projects = {"CON-4373": UMBRELLA, "CON-4223": UMBRELLA}
        for entry in ("knowledge foundation", "proj-kf"):
            out = self.decide(prs, projects, denylist=[entry])
            self.assertEqual(out["decision"], "none", entry)
            self.assertIn("denylisted", out["reason"])
        self.assertEqual(self.decide(prs, projects, denylist=["Other"])["decision"], "stack")

    def test_linear_failure_falls_back_with_a_note(self):
        out = self.decide([pr(1964, "Tips (CON-4372)", "jake/a")])
        self.assertEqual(out["decision"], "none")
        self.assertIsNone(out["base_line"])
        self.assertTrue(out["note"].startswith("Project stacking skipped: linear api exited 4"))

    def test_ticket_missing_from_linear_is_a_failure_not_no_project(self):
        out = self.decide([pr(1964, "Tips (CON-4372)", "jake/a")], {"CON-4372": TIPS})
        self.assertEqual(out["decision"], "none")
        self.assertIn("did not return CON-4373", out["note"])

    def test_no_ticketed_prs_skips_linear(self):
        out = self.decide([pr(1283, "[HOLD] Family tree", "jake/tree")])
        self.assertEqual(out["decision"], "none")
        self.assertIsNone(out["note"])
        self.assertEqual(self.linear_calls(), [])


BODY = """## Summary
Did a thing.

## Changes
- One
- Two

## Validation
- [ ] Check it



---
📣 **Posted to Slack**
"""


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / "body.md"

    def tearDown(self):
        self.temp.cleanup()

    def note(self, body, number, title):
        self.file.write_text(body)
        run("note", "--body-file", str(self.file), "--pr", str(number), "--title", title)
        return self.file.read_text()

    def test_inserts_after_changes_and_keeps_rest(self):
        out = self.note(BODY, 1980, "Follow-up (CON-1)")
        self.assertIn(
            "- Two\n\n## Stacked follow-ups\nThis PR grew after it was first opened:\n"
            "- #1980: Follow-up (CON-1), merges into this PR after its own review\n\n## Validation",
            out,
        )
        self.assertTrue(out.endswith("- [ ] Check it\n\n\n\n---\n📣 **Posted to Slack**\n"))

    def test_idempotent_and_appends_second(self):
        once = self.note(BODY, 1980, "Follow-up (CON-1)")
        self.assertEqual(self.note(once, 1980, "Follow-up (CON-1)"), once)
        twice = self.note(once, 1981, "Another (CON-1)")
        self.assertIn("after its own review\n- #1981: Another (CON-1), merges", twice)
        self.assertEqual(twice.count("## Stacked follow-ups"), 1)

    def test_no_changes_section_goes_before_badge(self):
        body = "## Summary\nX\n\n---\n📣 **Posted to Slack**\n"
        out = self.note(body, 7, "T")
        self.assertTrue(out.startswith("## Summary\nX\n\n## Stacked follow-ups\n"))
        self.assertTrue(out.endswith("own review\n\n---\n📣 **Posted to Slack**\n"))

    def test_plain_body_appends(self):
        out = self.note("Just text", 7, "T")
        self.assertEqual(
            out,
            "Just text\n\n## Stacked follow-ups\nThis PR grew after it was first opened:\n"
            "- #7: T, merges into this PR after its own review\n",
        )


if __name__ == "__main__":
    unittest.main()
