#!/usr/bin/env python3
"""Release queued PRs to #pr-review on window boundaries.

Launched every 5 minutes by launchd (com.jake.pr-review-batch). /post-pr appends
one line per finished PR to ~/.claude/state/pr-review-queue.jsonl instead of
posting it. On a tick that crosses a window boundary (windowMin in
~/.claude/state/pr-review-batch.json, default 30, so :00 and :30), this posts
every still-valid entry to #pr-review as its own message, then badges,
undrafts and enables auto-merge on each posted PR. windowMin 0 flushes on
every tick. A tick that crosses no boundary exits silently without calling gh.

Order, per run (all under one flock):
  1. Claim: merge back any leftover .flushing.* file, rename the queue to
     .flushing.<ts> so concurrent `>>` appends land in a fresh file, and keep
     the newest entry (queuedAt, then line order) per URL.
  2. Revalidate with gh. Drop (and log) entries whose PR is not OPEN, whose
     base is not the default branch, whose URL is already in
     pr-review-posted.jsonl, or whose head moved past headSha. Anything that
     merely failed (gh error, timeout) is requeued.
  3. Post each survivor with chat.postMessage, in queued order.
  4. On ok, append to pr-review-posted.jsonl immediately.
  5. Badge, 6. undraft, 7. auto-merge. A failure here is logged and never
     undoes the post.
  8. Failed posts go back into the live queue.

    flush.py [--dry-run] [--only URL] [--channel ID] [--force]

--only and --force skip the window check; --only flushes just that URL and
exits nonzero unless it was posted. --dry-run claims nothing and writes
nothing, and always logs a preflight line.

Env: PR_REVIEW_BATCH_CHANNEL (channel override), PR_REVIEW_BATCH_NOW (epoch
seconds, tests), PR_REVIEW_BATCH_SLACK_API (Slack API base URL, tests).
"""

import argparse
import datetime as dt
import fcntl
import getpass
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "state")
QUEUE = os.path.join(STATE, "pr-review-queue.jsonl")
POSTED = os.path.join(STATE, "pr-review-posted.jsonl")
CONFIG = os.path.join(STATE, "pr-review-batch.json")
LOCK = os.path.join(STATE, "pr-review-queue.lock")
CHANNEL = "C0APAAD3PP0"  # #pr-review
SLACK_API = os.environ.get("PR_REVIEW_BATCH_SLACK_API", "https://slack.com/api").rstrip("/")
TOKEN_SERVICE = "slack-pr-review-user-token"
BADGE = "📣 **Posted to Slack**"
DEFAULT_WINDOW_MIN = 30
GH_TIMEOUT = 60
POST_SPACING_S = 1.0


def log(msg):
    print(f"{dt.datetime.now():%F %T} {msg}", flush=True)


def now_epoch():
    return float(os.environ.get("PR_REVIEW_BATCH_NOW") or time.time())


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def run(*cmd, input=None):
    """CompletedProcess, or None on timeout / missing binary."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=GH_TIMEOUT, input=input)
    except (subprocess.TimeoutExpired, OSError):
        return None


def err(res):
    if res is None:
        return "timed out or not found"
    return (res.stderr or res.stdout).strip().replace("\n", " ")[:200]


# --- Window ------------------------------------------------------------------

def read_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(cfg):
    fd, tmp = tempfile.mkstemp(dir=STATE, prefix=".pr-review-batch.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG)


def window_min(cfg):
    try:
        w = float(cfg.get("windowMin", DEFAULT_WINDOW_MIN))
    except (TypeError, ValueError):
        w = DEFAULT_WINDOW_MIN
    return max(w, 0)


def boundary_crossed(now, last, window_minutes):
    """True when this tick should flush: windowMin 0 always does; otherwise a
    window boundary (multiple of windowMin minutes since the epoch, i.e. :00
    and :30 for 30) lies between the last flush and now. With no last flush,
    wait for the next boundary rather than flushing mid-window."""
    if window_minutes <= 0:
        return True
    if last is None:
        return False
    w = window_minutes * 60
    return int(now // w) > int(last // w)


# --- Queue -------------------------------------------------------------------

def read_lines(path, offset=0):
    """(entries, end_offset). Bad lines are logged and skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], offset
    # Only consume complete lines; a half-written trailing line waits.
    cut = data.rfind(b"\n") + 1
    entries = []
    for raw in data[:cut].decode("utf-8", "replace").splitlines():
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            log(f"skip unparseable queue line: {raw[:120]!r}")
            continue
        if isinstance(e, dict) and e.get("url"):
            entries.append(e)
    return entries, offset + cut


def append_lines(path, entries):
    if not entries:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def newest_per_url(entries):
    """Keep the entry with the latest queuedAt per URL (later line wins ties),
    ordered by queuedAt. A requeued line can land after a newer enqueue for the
    same URL, so line order alone would resurrect a stale headSha."""
    best = {}
    for i, e in enumerate(entries):
        key = (parse_iso(e.get("queuedAt")) or 0, i)
        if e["url"] not in best or key >= best[e["url"]][0]:
            best[e["url"]] = (key, e)
    return [e for _, e in sorted(best.values(), key=lambda kv: kv[0])]


class Claim:
    """The queue files this run owns, and how far it has read each one."""

    def __init__(self):
        self.files = {}  # path -> bytes consumed

    def take(self):
        os.makedirs(STATE, exist_ok=True)
        paths = sorted(glob.glob(QUEUE + ".flushing.*"))
        if os.path.exists(QUEUE):
            dst = f"{QUEUE}.flushing.{time.time_ns()}"
            os.rename(QUEUE, dst)
            paths.append(dst)
        entries = []
        for p in paths:
            got, end = read_lines(p)
            self.files[p] = end
            entries.extend(got)
        return newest_per_url(entries)

    def release(self):
        """Move anything appended to a claimed file after we read it (a `>>`
        whose fd predates the rename) to the live queue, then drop the files."""
        for p, offset in self.files.items():
            late, _ = read_lines(p, offset)
            if late:
                log(f"moved {len(late)} late append(s) back to the queue")
                append_lines(QUEUE, late)
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def posted_urls():
    urls = set()
    try:
        with open(POSTED, encoding="utf-8") as f:
            for line in f:
                try:
                    urls.add(json.loads(line).get("url"))
                except (json.JSONDecodeError, AttributeError):
                    pass
    except FileNotFoundError:
        pass
    return urls


# --- GitHub ------------------------------------------------------------------

_default_branch = {}


def default_branch(repo):
    if repo not in _default_branch:
        res = run("gh", "api", f"repos/{repo}", "--jq", ".default_branch")
        if res is None or res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(f"default branch of {repo}: {err(res)}")
        _default_branch[repo] = res.stdout.strip()
    return _default_branch[repo]


def revalidate(e, posted):
    """('post', pr_info) | ('drop', reason) | ('requeue', reason)."""
    if e["url"] in posted:
        return "drop", "already in pr-review-posted.jsonl"
    res = run("gh", "pr", "view", e["url"], "--json", "state,baseRefName,headRefOid,isDraft")
    if res is None or res.returncode != 0:
        return "requeue", f"gh pr view: {err(res)}"
    try:
        pr = json.loads(res.stdout)
    except json.JSONDecodeError:
        return "requeue", "gh pr view: bad JSON"
    if pr.get("state") != "OPEN":
        return "drop", f"PR is {pr.get('state')}"
    try:
        base = default_branch(e["repo"])
    except RuntimeError as x:
        return "requeue", str(x)
    if pr.get("baseRefName") != base:
        return "drop", f"base {pr.get('baseRefName')} is not the default branch {base}"
    if e.get("headSha") and pr.get("headRefOid") != e["headSha"]:
        return "drop", f"head moved {e['headSha'][:9]} -> {str(pr.get('headRefOid'))[:9]}"
    return "post", pr


def badge(url):
    res = run("gh", "pr", "view", url, "--json", "body", "-q", ".body")
    if res is None or res.returncode != 0:
        return f"failed ({err(res)})"
    body = res.stdout.rstrip()
    if body.endswith(BADGE):
        return "already"
    fd, tmp = tempfile.mkstemp(prefix="pr-body.", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{body}\n\n---\n{BADGE}\n")
        res = run("gh", "pr", "edit", url, "--body-file", tmp)
    finally:
        os.unlink(tmp)
    return "appended" if res is not None and res.returncode == 0 else f"failed ({err(res)})"


def undraft(url, is_draft):
    if not is_draft:
        return "not draft"
    res = run("gh", "pr", "ready", url)
    return "cleared" if res is not None and res.returncode == 0 else f"failed ({err(res)})"


def auto_merge(url, method):
    if not method:
        return "off"
    if method not in ("squash", "merge", "rebase"):
        return f"failed (unknown method {method!r})"
    res = run("gh", "pr", "merge", url, "--auto", f"--{method}")
    return method if res is not None and res.returncode == 0 else f"failed ({err(res)})"


# --- Slack -------------------------------------------------------------------

def slack_token():
    user = os.environ.get("USER") or getpass.getuser()
    res = run("security", "find-generic-password", "-s", TOKEN_SERVICE, "-a", user, "-w")
    if res is None or res.returncode != 0 or not res.stdout.strip():
        return None, err(res)
    return res.stdout.strip(), None


def post(token, channel, text):
    """None on success, else the error."""
    payload = json.dumps({
        "channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{SLACK_API}/chat.postMessage", data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as x:
        return f"http: {x}"
    return None if body.get("ok") else f"slack: {body.get('error', 'not ok')}"


# --- Flush -------------------------------------------------------------------

def label(e):
    return f"{e.get('repo', '?')}#{e.get('pr', '?')}"


def preflight():
    gh = shutil.which("gh")
    sec = shutil.which("security")
    _, token_err = slack_token()
    auth = run("gh", "auth", "status")
    log(f"preflight: gh={gh} security={sec} token={'ok' if not token_err else 'MISSING (' + token_err + ')'} "
        f"gh-auth={'ok' if auth is not None and auth.returncode == 0 else 'FAILED'}")


def dry_run(only, channel):
    preflight()
    entries = []
    for p in sorted(glob.glob(QUEUE + ".flushing.*")) + [QUEUE]:
        entries.extend(read_lines(p)[0])
    entries = newest_per_url(entries)
    if only:
        entries = [e for e in entries if e["url"] == only]
    posted = posted_urls()
    for e in entries:
        verdict, why = revalidate(e, posted)
        if verdict == "post":
            log(f"DRY would post {label(e)} to {channel}: {e.get('text')!r} automerge={e.get('autoMerge') or 'off'}")
        else:
            log(f"DRY would {verdict} {label(e)}: {why}")
    return 0


def flush(only, channel):
    claim = Claim()
    entries = claim.take()
    keep = []  # entries that go back into the live queue
    try:
        if only:
            keep = [e for e in entries if e["url"] != only]
            entries = [e for e in entries if e["url"] == only]
            append_lines(QUEUE, keep)  # put the rest back before any network call
            keep = []
        if not entries:
            if only:
                log(f"--only {only}: not in the queue")
            return False

        if channel != CHANNEL:
            log(f"channel override: posting to {channel}, not #pr-review")
        posted = posted_urls()
        token, token_err = None, None
        ok_urls = set()
        first = True
        for e in entries:
            verdict, why = revalidate(e, posted)
            if verdict == "drop":
                log(f"dropped {label(e)}: {why}")
                continue
            if verdict == "requeue":
                log(f"requeued {label(e)}: {why}")
                keep.append(e)
                continue
            pr = why
            if token is None and token_err is None:
                token, token_err = slack_token()
            if token is None:
                log(f"requeued {label(e)}: no Slack token ({token_err})")
                keep.append(e)
                continue
            if not first:
                time.sleep(POST_SPACING_S)
            first = False
            perr = post(token, channel, e.get("text") or e["url"])
            if perr:
                log(f"requeued {label(e)}: post failed ({perr})")
                keep.append(e)
                continue
            append_lines(POSTED, [{"url": e["url"], "repo": e.get("repo"), "pr": e.get("pr"),
                                   "postedAt": iso(time.time())}])
            posted.add(e["url"])
            ok_urls.add(e["url"])
            b = badge(e["url"])
            d = undraft(e["url"], pr.get("isDraft"))
            m = auto_merge(e["url"], e.get("autoMerge"))
            log(f"posted {label(e)}: badge={b} draft={d} automerge={m}")
        return only in ok_urls if only else True
    finally:
        append_lines(QUEUE, keep)
        claim.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", metavar="URL")
    ap.add_argument("--channel", default=os.environ.get("PR_REVIEW_BATCH_CHANNEL") or CHANNEL)
    ap.add_argument("--force", action="store_true", help="flush now, ignoring the window")
    args = ap.parse_args()
    os.makedirs(STATE, exist_ok=True)

    if args.dry_run:
        return dry_run(args.only, args.channel)

    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # blocking: --only must not silently no-op
        if not (args.only or args.force):
            cfg = read_config()
            now = now_epoch()
            last = parse_iso(cfg.get("lastFlushAt")) if cfg.get("lastFlushAt") else None
            crossed = boundary_crossed(now, last, window_min(cfg))
            if not crossed and last is not None:
                return 0
            cfg["lastFlushAt"] = iso(now)
            write_config(cfg)
            if not crossed:
                return 0  # first tick ever: start counting windows from here
            if not os.path.exists(QUEUE) and not glob.glob(QUEUE + ".flushing.*"):
                return 0
        ok = flush(args.only, args.channel)
    return 0 if ok or not args.only else 1


if __name__ == "__main__":
    sys.exit(main())
