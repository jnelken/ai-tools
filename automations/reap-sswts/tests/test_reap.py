"""reap.py against fake superset/gh CLIs, a real git worktree and a fake HOME.

    python3 -m unittest discover -s automations/reap-sswts/tests
"""

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REAP = os.path.join(HERE, "..", "reap.py")
NOW = 1_800_000_000
WS, TERM, ROOT_WS, ROOT_TERM = "ws-1", "term-1", "ws-root", "term-root"

FAKE_SUPERSET = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
a = [x for x in sys.argv[1:] if x != "--json"]
if a[:1] == ["snapshot"]:
    print(open(os.path.join(d, "snapshot.json")).read())
elif a[:2] == ["workspaces", "list"]:
    print(open(os.path.join(d, "workspaces.json")).read())
elif a[:2] == ["projects", "list"]:
    print(open(os.path.join(d, "projects.json")).read())
elif a[:2] == ["terminals", "close"]:
    open(os.path.join(d, "closed"), "a").write(a[a.index("--terminal") + 1] + "\n")
else:
    sys.exit(f"fake superset: unexpected {a}")
'''

FAKE_GH = r'''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]
if sys.argv[1:3] == ["pr", "list"]:
    print(open(os.path.join(d, "prs.json")).read())
elif sys.argv[1] == "api":
    print(open(os.path.join(d, "commits.txt")).read())
else:
    sys.exit("fake gh: unexpected")
'''

FAKE_TEARDOWN = 'echo "$@" >> "$FAKE_DIR/teardown"\n'


def slurp(path):
    with open(path) as f:
        return f.read()


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ReapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.fake = os.path.join(t, "fake")
        self.bin = os.path.join(t, "bin")
        for p in (self.home, self.fake, self.bin, os.path.join(self.home, ".claude", "state")):
            os.makedirs(p)
        for name, body in (("superset", FAKE_SUPERSET), ("gh", FAKE_GH)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.teardown = os.path.join(t, "teardown.sh")
        with open(self.teardown, "w") as f:
            f.write(FAKE_TEARDOWN)

        # A root clone with a merged feature branch checked out in a worktree.
        self.root = os.path.join(t, "repo")
        self.wt = os.path.join(t, "wt")
        sh("git", "init", "-q", "-b", "main", self.root)
        sh("git", "-C", self.root, "remote", "add", "origin", "git@github.com:acme/app.git")
        sh("git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        sh("git", "-C", self.root, "worktree", "add", "-q", "-b", "feat", self.wt)
        sh("git", "-C", self.wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "work")
        self.head = sh("git", "-C", self.wt, "rev-parse", "HEAD")

        self.transcripts = {}
        self.terminals = [{"id": TERM, "title": "⛏️ done", "agent": {"id": "claude", "state": "idle"}}]
        self.root_terminals = []
        self.prs = [{"number": 7, "state": "MERGED", "headRefOid": self.head}]
        self.pr_commits = self.head
        self.signoff(TERM, "⛏️ Reapable", age_min=120)

    def tearDown(self):
        self.tmp.cleanup()

    def signoff(self, term, last_line, age_min, then_user=False):
        when = dt.datetime.fromtimestamp(NOW - age_min * 60, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        entries = [
            {"type": "user", "message": {"content": "do it"}, "timestamp": when},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": f"All done.\n\n{last_line}"}]}, "timestamp": when},
        ]
        if then_user:
            entries.append({"type": "user", "message": {"content": "wait, one more thing"}, "timestamp": when})
        path = os.path.join(self.tmp.name, f"{term}.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(e) for e in entries) + "\n")
        self.transcripts[term] = path

    def write(self, name, obj):
        with open(os.path.join(self.fake, name), "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def reap(self, *args):
        snapshot = {"workspaces": [
            {"id": WS, "name": "#7 feat", "project": "app", "branch": "feat", "terminals": self.terminals},
            {"id": ROOT_WS, "name": "local", "project": "app", "branch": "main", "terminals": self.root_terminals},
        ]}
        self.write("snapshot.json", snapshot)
        self.write("workspaces.json", [
            {"id": WS, "name": "#7 feat", "type": "worktree", "worktreePath": self.wt, "branch": "feat", "projectId": "p"},
            {"id": ROOT_WS, "name": "local", "type": "local", "worktreePath": self.root, "branch": "main", "projectId": "p"},
        ])
        self.write("projects.json", [{"id": "p", "path": self.root}])
        self.write("prs.json", self.prs)
        self.write("commits.txt", self.pr_commits)
        db_dir = os.path.join(self.home, ".superset", "host", "org")
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, "host.db"))
        conn.execute("create table if not exists terminal_agent_bindings (terminal_id text primary key, transcript_path text, agent_session_id text)")
        conn.executemany("insert or replace into terminal_agent_bindings values (?, ?, null)", self.transcripts.items())
        conn.commit()
        conn.close()
        env = {
            "HOME": self.home, "FAKE_DIR": self.fake, "PATH": f"{self.bin}:/usr/bin:/bin",
            "REAP_SSWTS_NOW": str(NOW), "REAP_SSWTS_TEARDOWN": self.teardown,
        }
        res = subprocess.run([sys.executable, "-I", REAP, *args], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def torn_down(self):
        path = os.path.join(self.fake, "teardown")
        return slurp(path).split() if os.path.exists(path) else None

    def closed(self):
        path = os.path.join(self.fake, "closed")
        return slurp(path).split() if os.path.exists(path) else []

    def test_reaps_merged_clean_worktree_after_grace(self):
        out = self.reap()
        self.assertIn("reap app/#7 feat", out)
        self.assertEqual(self.torn_down(), [WS, TERM, self.root, self.wt, "feat", self.head])

    def test_respects_grace(self):
        self.signoff(TERM, "⛏️ Reapable", age_min=20)
        self.reap()
        self.assertIsNone(self.torn_down())

    def test_other_glyph_is_left_alone(self):
        self.signoff(TERM, "✅ Done", age_min=120)
        self.reap()
        self.assertIsNone(self.torn_down())

    def test_user_reply_after_signoff_cancels(self):
        self.signoff(TERM, "⛏️ Reapable", age_min=120, then_user=True)
        self.reap()
        self.assertIsNone(self.torn_down())

    def test_working_session_is_left_alone(self):
        self.terminals[0]["agent"]["state"] = "working"
        self.reap()
        self.assertIsNone(self.torn_down())

    def test_dirty_tree_blocks(self):
        open(os.path.join(self.wt, "scratch.txt"), "w").close()
        self.assertIn("uncommitted changes", self.reap())
        self.assertIsNone(self.torn_down())

    def test_open_pr_blocks(self):
        self.prs.append({"number": 9, "state": "OPEN", "headRefOid": self.head})
        self.assertIn("OPEN PR", self.reap())
        self.assertIsNone(self.torn_down())

    def test_head_found_in_pr_commits(self):
        self.prs[0]["headRefOid"] = "0" * 40
        self.reap()
        self.assertIsNotNone(self.torn_down())

    def test_unmerged_head_blocks(self):
        self.prs[0]["headRefOid"] = "0" * 40
        self.pr_commits = ""
        self.assertIn("HEAD has commits no merged PR carried", self.reap())
        self.assertIsNone(self.torn_down())

    def test_busy_sibling_tab_blocks(self):
        self.terminals.append({"id": "term-2", "title": "pnpm dev", "agent": None})
        self.assertIn("other tab not reapable", self.reap())
        self.assertIsNone(self.torn_down())

    def test_idle_shell_sibling_is_fine(self):
        self.terminals.append({"id": "term-2", "title": "jake@mac:~/wt", "agent": None})
        self.reap()
        self.assertIsNotNone(self.torn_down())

    def test_root_checkout_only_closes_the_tab(self):
        self.terminals = []
        self.root_terminals = [{"id": ROOT_TERM, "title": "⛏️ x", "agent": {"id": "claude", "state": "idle"}}]
        self.signoff(ROOT_TERM, "⛏️ Reapable", age_min=120)
        self.reap()
        self.assertEqual(self.closed(), [ROOT_TERM])
        self.assertIsNone(self.torn_down())

    def test_dry_run_changes_nothing(self):
        ledger = os.path.join(self.home, ".claude", "state", "pr-review-posted.jsonl")
        with open(ledger, "w") as f:
            f.write(json.dumps({"repo": "acme/app", "pr": 7}) + "\n")
        self.root_terminals = [{"id": ROOT_TERM, "title": "⛏️ x", "agent": {"id": "claude", "state": "idle"}}]
        self.signoff(ROOT_TERM, "⛏️ Reapable", age_min=120)
        self.reap("--dry-run")
        self.assertEqual(self.closed(), [])
        self.assertIn('"pr": 7', slurp(ledger))

    def test_drops_pr_ledger_entry(self):
        ledger = os.path.join(self.home, ".claude", "state", "pr-review-posted.jsonl")
        with open(ledger, "w") as f:
            f.write(json.dumps({"repo": "acme/app", "pr": 7}) + "\n" + json.dumps({"repo": "acme/app", "pr": 8}) + "\n")
        self.reap()
        self.assertEqual([json.loads(x)["pr"] for x in slurp(ledger).splitlines()], [8])

    def test_dry_run_through_real_teardown(self):
        self.teardown = os.path.join(HERE, "..", "..", "..", "skills", "self-destruct", "scripts", "teardown.sh")
        out = self.reap("--dry-run")
        self.assertIn("DRY: superset workspaces archive ws-1 --local", out)
        self.assertIn("DRY: git -C %s branch -D feat" % self.root, out)
        self.assertTrue(os.path.isdir(self.wt))


if __name__ == "__main__":
    unittest.main()
