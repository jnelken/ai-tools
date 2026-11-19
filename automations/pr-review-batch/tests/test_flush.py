"""flush.py against a fake gh, a fake security, a local Slack stub and a fake HOME.

    python3 -m pytest automations/pr-review-batch/tests
    python3 -m unittest discover -s automations/pr-review-batch/tests
"""

import datetime as dt
import glob
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FLUSH = os.path.join(HERE, "..", "flush.py")
REPO = "acme/app"
A = f"https://github.com/{REPO}/pull/1"
B = f"https://github.com/{REPO}/pull/2"
C = f"https://github.com/{REPO}/pull/3"
# 2026-01-01T10:00:00Z, a :00 boundary.
T0 = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.timezone.utc).timestamp()

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
home = os.environ["HOME"]
state = os.path.join(home, ".claude", "state")
a = sys.argv[1:]
prs_path = os.path.join(d, "prs.json")
prs = json.load(open(prs_path))

def event(s):
    open(os.path.join(d, "events"), "a").write(s + "\n")

def posted_has(url):
    p = os.path.join(state, "pr-review-posted.jsonl")
    return os.path.exists(p) and url in open(p).read()

if a[:2] == ["auth", "status"]:
    sys.exit(0)
if a[:1] == ["api"]:
    event("api")
    print("main")
    sys.exit(0)
url = a[2] if len(a) > 2 else None
pr = prs.get(url)
if a[:2] == ["pr", "view"] and "body" in a:
    print(pr["body"])
elif a[:2] == ["pr", "view"]:
    event("view " + url)
    once = os.path.join(d, "on_first_view")
    if os.path.exists(once):
        hook = json.load(open(once))
        os.unlink(once)
        if hook.get("live"):
            open(os.path.join(state, "pr-review-queue.jsonl"), "a").write(hook["live"])
        if hook.get("claimed"):
            import glob
            f = sorted(glob.glob(os.path.join(state, "pr-review-queue.jsonl.flushing.*")))[-1]
            open(f, "a").write(hook["claimed"])
    if pr is None or pr.get("fail"):
        sys.exit("fake gh: boom")
    print(json.dumps({k: pr[k] for k in ("state", "baseRefName", "headRefOid", "isDraft")}))
elif a[:2] == ["pr", "edit"]:
    body = open(a[a.index("--body-file") + 1]).read()
    pr["body"] = body
    event(f"badge {url} posted={posted_has(url)}")
elif a[:2] == ["pr", "ready"]:
    pr["isDraft"] = False
    event(f"ready {url}")
elif a[:2] == ["pr", "merge"]:
    event(f"merge {url} {' '.join(a[3:])}")
else:
    sys.exit(f"fake gh: unexpected {a}")
json.dump(prs, open(prs_path, "w"))
'''

FAKE_SECURITY = "#!/bin/sh\necho xoxp-test\n"


class Slack(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.requests.append({"auth": self.headers["Authorization"], **body})
        with open(os.path.join(srv.fake, "events"), "a") as f:
            f.write(f"post {body['text']}\n")
        ok = body["text"] not in srv.fail_texts
        out = json.dumps({"ok": ok, **({} if ok else {"error": "rate_limited"})}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class FlushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.fake = os.path.join(t, "fake")
        self.bin = os.path.join(t, "bin")
        self.state = os.path.join(self.home, ".claude", "state")
        for p in (self.fake, self.bin, self.state):
            os.makedirs(p)
        for name, body in (("gh", FAKE_GH), ("security", FAKE_SECURITY)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.queue = os.path.join(self.state, "pr-review-queue.jsonl")
        self.posted = os.path.join(self.state, "pr-review-posted.jsonl")
        self.config = os.path.join(self.state, "pr-review-batch.json")
        self.prs = {}
        for url, n in ((A, 1), (B, 2), (C, 3)):
            self.prs[url] = {"state": "OPEN", "baseRefName": "main", "headRefOid": f"sha{n}",
                             "isDraft": True, "body": f"## Summary\nPR {n}"}

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Slack)
        self.srv.requests, self.srv.fake, self.srv.fail_texts = [], self.fake, set()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    # -- helpers --

    def entry(self, url, queued_min=0, **kw):
        n = int(url.rsplit("/", 1)[1])
        e = {"url": url, "repo": REPO, "pr": n, "text": f"<{url}|[api] PR {n}>",
             "headSha": f"sha{n}", "autoMerge": None, "queuedAt": iso(T0 - 600 + queued_min * 60)}
        e.update(kw)
        return e

    def enqueue(self, *entries, path=None):
        with open(path or self.queue, "a") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def flush(self, *args, now=T0 + 60, expect=0):
        with open(os.path.join(self.fake, "prs.json"), "w") as f:
            json.dump(self.prs, f)
        env = {
            "HOME": self.home, "FAKE_DIR": self.fake, "PATH": f"{self.bin}:/usr/bin:/bin",
            "USER": "tester", "PR_REVIEW_BATCH_NOW": str(now),
            "PR_REVIEW_BATCH_SLACK_API": f"http://127.0.0.1:{self.srv.server_port}",
        }
        res = subprocess.run([sys.executable, "-I", FLUSH, *args], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, expect, res.stdout + res.stderr)
        with open(os.path.join(self.fake, "prs.json")) as f:
            self.prs = json.load(f)
        self.out = res.stdout
        return res.stdout

    def last_flush(self, when):
        with open(self.config, "w") as f:
            json.dump({"lastFlushAt": iso(when)}, f)

    def lines(self, path):
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f if x.strip()]

    def events(self):
        p = os.path.join(self.fake, "events")
        return open(p).read().splitlines() if os.path.exists(p) else []

    def posted_texts(self):
        return [r["text"] for r in self.srv.requests]

    # -- happy path / order --

    def test_posts_badges_undrafts_automerges_in_order(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A, autoMerge="squash"))
        self.flush()
        self.assertEqual(self.posted_texts(), [f"<{A}|[api] PR 1>"])
        req = self.srv.requests[0]
        self.assertEqual(req["channel"], "C0APAAD3PP0")
        self.assertFalse(req["unfurl_links"])
        self.assertFalse(req["unfurl_media"])
        self.assertEqual(req["auth"], "Bearer xoxp-test")
        ev = [e for e in self.events() if not e.startswith(("view", "api"))]
        self.assertEqual(ev, [f"post <{A}|[api] PR 1>", f"badge {A} posted=True", f"ready {A}", f"merge {A} --auto --squash"])
        self.assertTrue(self.prs[A]["body"].endswith("\n\n---\n📣 **Posted to Slack**\n"))
        posted = self.lines(self.posted)
        self.assertEqual([(p["url"], p["repo"], p["pr"]) for p in posted], [(A, REPO, 1)])
        self.assertTrue(posted[0]["postedAt"].endswith("Z"))
        self.assertEqual(self.lines(self.queue), [])
        self.assertEqual(glob.glob(self.queue + ".flushing.*"), [])
        self.assertIn(f"posted {REPO}#1: badge=appended draft=cleared automerge=squash", self.out)

    def test_posts_in_queued_order_one_message_each(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(B, queued_min=2), self.entry(A, queued_min=1), self.entry(C, queued_min=3))
        self.flush()
        self.assertEqual(self.posted_texts(), [f"<{u}|[api] PR {n}>" for u, n in ((A, 1), (B, 2), (C, 3))])

    def test_badge_is_idempotent_and_no_merge_when_null(self):
        self.last_flush(T0 - 60)
        self.prs[A]["body"] = "## Summary\nx\n\n---\n📣 **Posted to Slack**\n"
        self.prs[A]["isDraft"] = False
        self.enqueue(self.entry(A))
        self.flush()
        self.assertEqual(self.prs[A]["body"], "## Summary\nx\n\n---\n📣 **Posted to Slack**\n")
        ev = self.events()
        self.assertFalse(any(e.startswith(("badge", "ready", "merge")) for e in ev), ev)
        self.assertIn("badge=already draft=not draft automerge=off", self.out)

    def test_last_entry_per_url_wins(self):
        self.last_flush(T0 - 60)
        self.prs[A]["headRefOid"] = "sha1-new"
        self.enqueue(self.entry(A, queued_min=1), self.entry(A, queued_min=2, headSha="sha1-new", text="new"))
        self.flush()
        self.assertEqual(self.posted_texts(), ["new"])

    def test_newer_queued_at_beats_later_requeued_line(self):
        self.last_flush(T0 - 60)
        self.prs[A]["headRefOid"] = "sha1-new"
        self.enqueue(self.entry(A, queued_min=5, headSha="sha1-new", text="new"),
                     self.entry(A, queued_min=1, text="stale requeue"))
        self.flush()
        self.assertEqual(self.posted_texts(), ["new"])

    # -- drops --

    def assert_dropped(self, reason):
        self.assertEqual(self.posted_texts(), [])
        self.assertEqual(self.lines(self.queue), [])
        self.assertIn(f"dropped {REPO}#1: {reason}", self.out)

    def test_drop_not_open(self):
        self.last_flush(T0 - 60)
        self.prs[A]["state"] = "MERGED"
        self.enqueue(self.entry(A))
        self.flush()
        self.assert_dropped("PR is MERGED")

    def test_drop_base_not_default(self):
        self.last_flush(T0 - 60)
        self.prs[A]["baseRefName"] = "feature/x"
        self.enqueue(self.entry(A))
        self.flush()
        self.assert_dropped("base feature/x is not the default branch main")

    def test_drop_already_posted(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A))
        self.enqueue({"url": A, "repo": REPO, "pr": 1, "postedAt": "2026-01-01T00:00:00Z"}, path=self.posted)
        self.flush()
        self.assert_dropped("already in pr-review-posted.jsonl")

    def test_drop_head_moved(self):
        self.last_flush(T0 - 60)
        self.prs[A]["headRefOid"] = "other"
        self.enqueue(self.entry(A))
        self.flush()
        self.assert_dropped("head moved sha1 -> other")

    # -- requeues --

    def test_failed_post_is_requeued_others_proceed(self):
        self.last_flush(T0 - 60)
        self.srv.fail_texts.add(f"<{A}|[api] PR 1>")
        self.enqueue(self.entry(A), self.entry(B, queued_min=1))
        self.flush()
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [A])
        self.assertEqual([p["url"] for p in self.lines(self.posted)], [B])
        self.assertTrue(self.prs[A]["isDraft"])
        self.assertNotIn("Posted to Slack", self.prs[A]["body"])
        self.assertIn("requeued acme/app#1: post failed (slack: rate_limited)", self.out)

    def test_gh_error_requeues_not_drops(self):
        self.last_flush(T0 - 60)
        self.prs[A]["fail"] = True
        self.enqueue(self.entry(A))
        self.flush()
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [A])

    # -- claim / crash safety --

    def test_concurrent_append_after_claim_stays_queued(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A))
        hook = {"live": json.dumps(self.entry(B, queued_min=9)) + "\n"}
        with open(os.path.join(self.fake, "on_first_view"), "w") as f:
            json.dump(hook, f)
        self.flush()
        self.assertEqual(self.posted_texts(), [f"<{A}|[api] PR 1>"])
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [B])

    def test_late_write_into_claimed_file_is_moved_back(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A))
        hook = {"claimed": json.dumps(self.entry(B, queued_min=9)) + "\n"}
        with open(os.path.join(self.fake, "on_first_view"), "w") as f:
            json.dump(hook, f)
        self.flush()
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [B])
        self.assertEqual(glob.glob(self.queue + ".flushing.*"), [])

    def test_leftover_flushing_file_is_recovered(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A), path=self.queue + ".flushing.123")
        self.enqueue(self.entry(B, queued_min=1))
        self.flush()
        self.assertEqual(self.posted_texts(), [f"<{A}|[api] PR 1>", f"<{B}|[api] PR 2>"])
        self.assertEqual(glob.glob(self.queue + ".flushing.*"), [])

    def test_leftover_alone_is_flushed(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A), path=self.queue + ".flushing.123")
        self.flush()
        self.assertEqual(self.posted_texts(), [f"<{A}|[api] PR 1>"])

    # -- flags --

    def test_only_posts_one_and_keeps_rest(self):
        self.last_flush(T0 + 30)  # same window: a plain tick would not flush
        self.enqueue(self.entry(A), self.entry(B, queued_min=1))
        self.flush("--only", B)
        self.assertEqual(self.posted_texts(), [f"<{B}|[api] PR 2>"])
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [A])
        self.assertEqual(json.load(open(self.config))["lastFlushAt"], iso(T0 + 30))

    def test_only_exits_nonzero_when_not_posted(self):
        self.enqueue(self.entry(A))
        self.flush("--only", B, expect=1)
        self.assertIn("not in the queue", self.out)
        self.srv.fail_texts.add(f"<{A}|[api] PR 1>")
        self.flush("--only", A, expect=1)
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [A])

    def test_channel_flag_and_env(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A))
        self.flush("--channel", "D123")
        self.assertEqual(self.srv.requests[0]["channel"], "D123")
        self.assertIn("channel override", self.out)

    def test_dry_run_writes_nothing(self):
        self.last_flush(T0 - 60)
        self.enqueue(self.entry(A))
        before = open(self.config).read()
        out = self.flush("--dry-run")
        self.assertIn("preflight: gh=", out)
        self.assertIn("token=ok", out)
        self.assertIn(f"DRY would post {REPO}#1", out)
        self.assertEqual(self.posted_texts(), [])
        self.assertEqual([e["url"] for e in self.lines(self.queue)], [A])
        self.assertEqual(open(self.config).read(), before)
        self.assertEqual(glob.glob(self.queue + ".flushing.*"), [])

    def test_dry_run_empty_queue_still_preflights(self):
        self.assertIn("preflight:", self.flush("--dry-run"))

    # -- window --

    def test_empty_queue_is_silent(self):
        self.last_flush(T0 - 60)
        self.assertEqual(self.flush(), "")
        self.assertEqual(self.events(), [])

    def test_no_boundary_is_silent_and_calls_no_gh(self):
        self.last_flush(T0 + 60)
        self.enqueue(self.entry(A))
        self.assertEqual(self.flush(now=T0 + 25 * 60), "")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.posted_texts(), [])
        self.assertEqual(json.load(open(self.config))["lastFlushAt"], iso(T0 + 60))

    def test_boundary_flushes_and_records(self):
        self.last_flush(T0 + 25 * 60)  # 10:25
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 31 * 60)   # 10:31 crosses 10:30
        self.assertEqual(len(self.posted_texts()), 1)
        self.assertEqual(json.load(open(self.config))["lastFlushAt"], iso(T0 + 31 * 60))

    def test_empty_boundary_tick_still_records(self):
        # An empty :00 tick must advance lastFlushAt, or an entry queued at :10
        # would flush on the :15 tick instead of waiting for :30.
        self.last_flush(T0 - 60)
        self.flush(now=T0 + 60)
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 15 * 60)
        self.assertEqual(self.posted_texts(), [])
        self.flush(now=T0 + 30 * 60 + 5)
        self.assertEqual(len(self.posted_texts()), 1)

    def test_first_tick_without_state_waits_for_boundary(self):
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 7 * 60)
        self.assertEqual(self.posted_texts(), [])
        self.assertEqual(json.load(open(self.config))["lastFlushAt"], iso(T0 + 7 * 60))
        self.flush(now=T0 + 31 * 60)
        self.assertEqual(len(self.posted_texts()), 1)

    def test_custom_window(self):
        with open(self.config, "w") as f:
            json.dump({"windowMin": 60, "lastFlushAt": iso(T0 + 60)}, f)
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 31 * 60)
        self.assertEqual(self.posted_texts(), [])
        self.flush(now=T0 + 61 * 60)
        self.assertEqual(len(self.posted_texts()), 1)
        self.assertEqual(json.load(open(self.config))["windowMin"], 60)

    def test_window_zero_flushes_every_tick(self):
        with open(self.config, "w") as f:
            json.dump({"windowMin": 0, "lastFlushAt": iso(T0 + 60)}, f)
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 2 * 60)
        self.assertEqual(len(self.posted_texts()), 1)
        self.enqueue(self.entry(B))
        self.flush(now=T0 + 3 * 60)
        self.assertEqual(len(self.posted_texts()), 2)

    def test_window_zero_without_state(self):
        with open(self.config, "w") as f:
            json.dump({"windowMin": 0}, f)
        self.enqueue(self.entry(A))
        self.flush(now=T0 + 7 * 60)
        self.assertEqual(len(self.posted_texts()), 1)

    def test_force_ignores_window(self):
        self.last_flush(T0 + 60)
        self.enqueue(self.entry(A))
        self.flush("--force", now=T0 + 2 * 60)
        self.assertEqual(len(self.posted_texts()), 1)


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    unittest.main()
