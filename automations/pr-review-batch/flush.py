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
  2b. Pair cross-repo siblings: for an entry in api, woodrow or folio-platform
     whose text carries (CON-N), find Jake's open PRs into the default branch
     of the other two repos with the exact same (CON-N) in the title. A
     sibling's state is read from its body on GitHub, so it is the same from
     either machine: posted (badge), queued (QUEUED_MARKER, which /post-pr
     adds at enqueue) or in progress (neither). An entry with an in-progress
     sibling is held in the queue until the sibling is queued or posted, or
     siblingHoldMin minutes (default 60) after its queuedAt; then it posts
     alone. Every paired message gets a second line naming its siblings.
  2c. Deferred entries ("defer": true, set by /post-pr for a PR that fixes no
     bug and ships no feature) are held, with no gh call, until "releaseAt"
     (the next weekday deferReleaseAt in deferTz, default 16:00
     America/New_York). Sub-PRs merge into a deferred bundle while it waits,
     so its head moving is expected and never drops it; instead it releases
     only when `gh pr checks` is all green on the current head (pending: held
     to the next tick; failing: held and logged loudly). Its message gets an
     "Also includes:" line listing the sub-PRs merged into it.
  3. Post each survivor with chat.postMessage, in queued order, except that
     same-ticket entries post together, api before woodrow.
  4. On ok, append to pr-review-posted.jsonl immediately.
  5. Badge (and remove QUEUED_MARKER and DEFER_MARKER), 6. undraft, 7. auto-merge. A failure here is logged and never
     undoes the post.
  8. Failed posts go back into the live queue.

    flush.py [--dry-run] [--only URL] [--channel ID] [--force] [--siblings URL]
             [--release-at [ISO]] [--defer-plan URL]

--only and --force skip the window check; --only flushes just that URL,
skips the sibling and release holds (never the checks gate), and exits
nonzero unless it was posted. --force keeps both holds. --dry-run claims nothing and writes nothing, and always logs a
preflight line. --siblings URL prints that PR's siblings and the line its
message would carry, and changes nothing (/post-pr uses it to report).
--release-at prints the next deferred release (after ISO, default now) as UTC.
--defer-plan URL prints, as JSON, the release time and today's bundle for that
PR's repo (Jake's open, unposted, unapproved PR into the default branch with
DEFER_MARKER in its body). Both are read-only.

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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zoneinfo

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
QUEUED_MARKER = "<!-- pr-review-queued -->"
DEFER_MARKER = "<!-- pr-review-deferred -->"
DEFAULT_DEFER_RELEASE_AT = "16:00"
DEFAULT_DEFER_TZ = "America/New_York"
DEFAULT_WINDOW_MIN = 30
DEFAULT_SIBLING_HOLD_MIN = 60
OWNER = "Concentro-Inc"
SIBLING_REPOS = ("api", "woodrow", "folio-platform")  # also the posting order within a ticket
TICKET_RE = re.compile(r"\((CON-\d+)\)")
SUBPR_RE = re.compile(r"^- #(\d+)\b", re.MULTILINE)
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


def sibling_hold_min(cfg):
    try:
        h = float(cfg.get("siblingHoldMin", DEFAULT_SIBLING_HOLD_MIN))
    except (TypeError, ValueError):
        h = DEFAULT_SIBLING_HOLD_MIN
    return max(h, 0)


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


# --- Deferral ----------------------------------------------------------------

def defer_settings(cfg):
    """(hour, minute, tzinfo) from deferReleaseAt / deferTz, falling back to
    16:00 America/New_York for a missing or bad value."""
    try:
        h, m = (int(x) for x in str(cfg.get("deferReleaseAt", DEFAULT_DEFER_RELEASE_AT)).split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
    except (TypeError, ValueError):
        h, m = (int(x) for x in DEFAULT_DEFER_RELEASE_AT.split(":"))
    try:
        tz = zoneinfo.ZoneInfo(str(cfg.get("deferTz", DEFAULT_DEFER_TZ)))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        tz = zoneinfo.ZoneInfo(DEFAULT_DEFER_TZ)
    return h, m, tz


def next_release(after, cfg):
    """Epoch of the first weekday release strictly after `after`. Built as a
    local wall-clock time and converted, so it stays 16:00 across DST."""
    h, m, tz = defer_settings(cfg)
    day = dt.datetime.fromtimestamp(after, tz).date()
    while True:
        at = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
        if at.weekday() < 5 and at.timestamp() > after:
            return at.timestamp()
        day += dt.timedelta(days=1)


def release_epoch(e, cfg):
    """When a deferred entry may go out: its releaseAt, or (missing or bad)
    the release after its queuedAt, or now when that is bad too."""
    at = parse_iso(e.get("releaseAt")) if e.get("releaseAt") else None
    if at is not None:
        return at
    queued = parse_iso(e.get("queuedAt")) if e.get("queuedAt") else None
    return next_release(queued, cfg) if queued is not None else now_epoch()


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
        missing = [k for k in ("url", "repo", "text") if not (isinstance(e, dict) and isinstance(e.get(k), str) and e.get(k))]
        if missing:
            log(f"dropped malformed queue line (missing {', '.join(missing)}): {raw[:120]!r}")
            continue
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
    res = run("gh", "pr", "view", e["url"], "--json", "state,baseRefName,headRefOid,headRefName,isDraft")
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
    # A deferred bundle's head moves every time a sub-PR merges in; the checks
    # gate on the current head replaces this drop for it.
    if e.get("headSha") and not e.get("defer") and pr.get("headRefOid") != e["headSha"]:
        return "drop", f"head moved {str(e['headSha'])[:9]} -> {str(pr.get('headRefOid'))[:9]}"
    return "post", pr


def checks_gate(e):
    """For a deferred entry at release: ('post', None) when every check on the
    current head passed or was skipped (or there are none), ('hold', reason)
    while any is pending or failing, ('requeue', reason) when gh fails."""
    res = run("gh", "pr", "checks", e["url"], "--json", "bucket,name")
    if res is None:
        return "requeue", f"gh pr checks: {err(res)}"
    # gh exits 8 for pending and 1 for failing, still printing the JSON.
    try:
        checks = json.loads(res.stdout) if res.stdout.strip() else None
    except json.JSONDecodeError:
        checks = None
    if checks is None:
        if "no checks reported" in (res.stderr or ""):
            return "post", None
        return "requeue", f"gh pr checks: {err(res)}"
    failing = sorted(c.get("name", "?") for c in checks if c.get("bucket") in ("fail", "cancel"))
    pending = sorted(c.get("name", "?") for c in checks if c.get("bucket") == "pending")
    if failing:
        return "hold", f"CHECKS FAILING ({', '.join(failing)})"
    if pending:
        return "hold", f"checks pending ({', '.join(pending)})"
    return "post", None


def also_includes(e, pr):
    """The "Also includes:" line for a deferred bundle: the sub-PRs merged into
    its head branch, oldest first. None when there are none; raises
    RuntimeError when gh fails."""
    head = pr.get("headRefName")
    if not head:
        return None
    res = run("gh", "pr", "list", "--repo", e["repo"], "--base", head, "--state", "merged",
              "--json", "number,url", "--limit", "100")
    if res is None or res.returncode != 0:
        raise RuntimeError(f"gh pr list --base {head}: {err(res)}")
    try:
        subs = sorted(json.loads(res.stdout), key=lambda p: p["number"])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise RuntimeError(f"gh pr list --base {head}: bad JSON")
    if not subs:
        return None
    return "Also includes: " + ", ".join(f"<{p['url']}|#{p['number']}>" for p in subs)


def badge(url):
    res = run("gh", "pr", "view", url, "--json", "body", "-q", ".body")
    if res is None or res.returncode != 0:
        return f"failed ({err(res)})"
    body = res.stdout.rstrip()
    # The queued marker goes in the same edit: once posted, the badge is the state.
    stripped = "\n".join(l for l in body.split("\n") if l.strip() not in (QUEUED_MARKER, DEFER_MARKER)).rstrip()
    if stripped.endswith(BADGE):
        if stripped == body:
            return "already"
        new, done = f"{stripped}\n", "already, marker removed"
    else:
        new, done = f"{stripped}\n\n---\n{BADGE}\n", "appended"
    fd, tmp = tempfile.mkstemp(prefix="pr-body.", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new)
        res = run("gh", "pr", "edit", url, "--body-file", tmp)
    finally:
        os.unlink(tmp)
    return done if res is not None and res.returncode == 0 else f"failed ({err(res)})"


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


# --- Siblings ----------------------------------------------------------------

_sibling_prs = {}  # (repo, ticket) -> open same-ticket PRs into its default branch


def sibling_scope(e):
    """(short repo name, ticket) when the entry can have siblings, else None."""
    owner, _, name = e["repo"].partition("/")
    m = TICKET_RE.search(e.get("text") or "")
    if owner != OWNER or name not in SIBLING_REPOS or not m:
        return None
    return name, m.group(1)


def find_siblings(e, posted, local_queued):
    """Open same-ticket PRs in the other Concentro repos, each with its state
    as GitHub (or this machine) shows it. Raises RuntimeError when gh fails."""
    scope = sibling_scope(e)
    if not scope:
        return []
    name, ticket = scope
    sibs = []
    for other in SIBLING_REPOS:
        if other == name:
            continue
        repo = f"{OWNER}/{other}"
        if (repo, ticket) not in _sibling_prs:
            res = run("gh", "pr", "list", "--repo", repo, "--base", default_branch(repo), "--state", "open",
                      "--search", f"{ticket} in:title", "--author", "@me",
                      "--json", "number,title,url,body", "--limit", "20")
            if res is None or res.returncode != 0:
                raise RuntimeError(f"gh pr list {repo}: {err(res)}")
            try:
                prs = json.loads(res.stdout)
            except json.JSONDecodeError:
                raise RuntimeError(f"gh pr list {repo}: bad JSON")
            # The search is fuzzy (CON-44 finds CON-441); the title must carry exactly (CON-N).
            _sibling_prs[(repo, ticket)] = sorted(
                (p for p in prs if ticket in TICKET_RE.findall(p.get("title") or "")),
                key=lambda p: p["number"])
        for p in _sibling_prs[(repo, ticket)]:
            body = p.get("body") or ""
            if BADGE in body or p["url"] in posted:
                state = "posted"  # wins over a marker an older flusher left behind
            elif QUEUED_MARKER in body or p["url"] in local_queued:
                state = "queued"
            else:
                state = "in progress"
            sibs.append({"name": other, "number": p["number"], "url": p["url"], "state": state})
    return sibs


def sib_label(s):
    return f"{s['name']}#{s['number']}"


def sibling_line(name, sibs):
    """The message's second line, or None. Merge order is only claimed for a
    lone api/woodrow pair: api first, the UI follow-up after."""
    if not sibs:
        return None
    links = [f"<{s['url']}|{s['name']} #{s['number']}>" for s in sibs]
    if len(sibs) == 1 and {name, sibs[0]["name"]} == {"api", "woodrow"}:
        return f"UI follow-up: {links[0]}, merge after this one" if name == "api" else f"Merges after {links[0]}"
    return "Sibling: " + ", ".join(links)


class Pairing:
    """Per-run inputs for the sibling decision."""

    def __init__(self, posted, local_queued, hold, now, hold_min, cfg=None):
        self.posted, self.local_queued = posted, local_queued
        self.hold, self.now, self.hold_min = hold, now, hold_min
        self.cfg = cfg or {}

    def hold_start(self, e):
        """queuedAt, or for a deferred entry its release, so the sibling hold
        is not already spent by the time the entry is allowed out."""
        queued = parse_iso(e.get("queuedAt")) if e.get("queuedAt") else None
        if e.get("defer") and queued is not None:
            return max(queued, release_epoch(e, self.cfg))
        return queued

    def decide(self, e):
        """('post', text) | ('hold', reason) | ('requeue', reason), logging
        one line for any entry that has siblings."""
        text = e.get("text") or e["url"]
        scope = sibling_scope(e)
        if not scope:
            return "post", text
        queued = self.hold_start(e)
        expired = queued is None or self.now >= queued + self.hold_min * 60
        holding = self.hold and not expired
        try:
            sibs = find_siblings(e, self.posted, self.local_queued)
        except RuntimeError as x:
            if holding:
                return "requeue", f"sibling lookup failed ({x})"
            log(f"posting {label(e)} without a sibling line: lookup failed ({x})")
            return "post", text
        if not sibs:
            return "post", text
        waiting = [s for s in sibs if s["state"] == "in progress"]
        states = ", ".join(f"{sib_label(s)} {s['state']}" for s in sibs)
        if waiting and holding:
            until = dt.datetime.fromtimestamp(queued + self.hold_min * 60).strftime("%H:%M")
            return "hold", f"held for sibling {', '.join(sib_label(s) for s in waiting)} (until {until})"
        why = "" if not waiting else " (--only, no hold)" if not self.hold else " (hold expired)"
        log(f"paired {label(e)} with {states}{why}")
        return "post", f"{text}\n{sibling_line(scope[0], sibs)}"


def post_order(ready):
    """Queued order, except that entries for one ticket post together, at the
    first one's place, api before woodrow before folio-platform."""
    first, keyed = {}, []
    for i, item in enumerate(ready):
        scope = sibling_scope(item[0])
        if scope:
            g = first.setdefault(scope[1], i)
            keyed.append(((g, SIBLING_REPOS.index(scope[0]), i), item))
        else:
            keyed.append(((i, 0, i), item))
    return [item for _, item in sorted(keyed, key=lambda kv: kv[0])]


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


def pairing(entries, posted, hold):
    cfg = read_config()
    return Pairing(posted, {e["url"] for e in entries}, hold, now_epoch(), sibling_hold_min(cfg), cfg)


def release_label(at, cfg):
    return dt.datetime.fromtimestamp(at, defer_settings(cfg)[2]).strftime("%a %Y-%m-%d %H:%M %Z")


def decide(e, posted, pair):
    """(verdict, pr, text_or_reason): verdict is post, drop, requeue or hold.
    A deferred entry is held until its release (unless --only), then must
    pass revalidation, green checks on its current head and the sibling
    hold, and gets its "Also includes:" line. Everything else is unchanged."""
    deferred = bool(e.get("defer"))
    if deferred and pair.hold:
        at = release_epoch(e, pair.cfg)
        if pair.now < at:
            return "hold", None, f"deferred until {release_label(at, pair.cfg)}"
    verdict, why = revalidate(e, posted)
    if verdict != "post":
        return verdict, None, why
    pr = why
    if deferred:
        verdict, why = checks_gate(e)
        if verdict == "hold":
            sha = str(pr.get("headRefOid"))[:9]
            loud = why.startswith("CHECKS FAILING")
            return "hold", None, f"deferred bundle held at release: {why} on {sha}"
        if verdict != "post":
            return verdict, None, why
    verdict, text = pair.decide(e)
    if verdict != "post":
        return verdict, None, text
    if deferred:
        try:
            line = also_includes(e, pr)
        except RuntimeError as x:
            return "requeue", None, f"sub-PR lookup failed ({x})"
        if line:
            text = f"{text}\n{line}"
    return "post", pr, text


def dry_run(only, channel):
    preflight()
    entries = []
    for p in sorted(glob.glob(QUEUE + ".flushing.*")) + [QUEUE]:
        entries.extend(read_lines(p)[0])
    entries = newest_per_url(entries)
    posted = posted_urls()
    pair = pairing(entries, posted, hold=not only)
    if only:
        entries = [e for e in entries if e["url"] == only]
    ready = []
    for e in entries:
        verdict, _, why = decide(e, posted, pair)
        if verdict == "post":
            ready.append((e, why))
        else:
            log(f"DRY would {verdict} {label(e)}: {why}")
    for e, text in post_order(ready):
        log(f"DRY would post {label(e)} to {channel}: {text!r} automerge={e.get('autoMerge') or 'off'}")
    return 0


def show_siblings(url):
    """--siblings: what pairing would do for this PR, from GitHub and this
    machine's queue. Writes nothing."""
    res = run("gh", "pr", "view", url, "--json", "url,number,title")
    if res is None or res.returncode != 0:
        print(f"gh pr view: {err(res)}", file=sys.stderr)
        return 1
    pr = json.loads(res.stdout)
    repo = "/".join(pr["url"].split("/")[3:5])
    name = repo.split("/")[1]
    tag = {"woodrow": "ui", "folio-platform": "platform"}.get(name, name)
    e = {"url": pr["url"], "repo": repo, "pr": pr["number"], "text": f"<{pr['url']}|[{tag}] {pr['title']}>"}
    queued = [x["url"] for p in sorted(glob.glob(QUEUE + ".flushing.*")) + [QUEUE] for x in read_lines(p)[0]]
    try:
        sibs = find_siblings(e, posted_urls(), set(queued))
    except RuntimeError as x:
        print(f"sibling lookup failed: {x}", file=sys.stderr)
        return 1
    for s in sibs:
        print(f"sibling {sib_label(s)} {s['state']} {s['url']}")
    line = sibling_line(name, sibs)
    print("message:")
    print(e["text"] + (f"\n{line}" if line else ""))
    return 0


def show_release_at(after):
    """--release-at: the next deferred release after `after` (ISO, default
    now), as UTC ISO."""
    when = parse_iso(after) if after else now_epoch()
    if when is None:
        print(f"bad time {after!r}", file=sys.stderr)
        return 1
    print(iso(next_release(when, read_config())))
    return 0


def queued_release(url):
    """releaseAt of this machine's newest queue line for url, or None."""
    entries = []
    for p in sorted(glob.glob(QUEUE + ".flushing.*")) + [QUEUE]:
        entries.extend(read_lines(p)[0])
    for e in newest_per_url(entries):
        if e["url"] == url and e.get("defer") and parse_iso(e.get("releaseAt") or "") is not None:
            return e["releaseAt"]
    return None


def has_line(body, marker):
    return any(l.strip() == marker for l in (body or "").split("\n"))


def show_defer_plan(url):
    """--defer-plan: JSON {releaseAt, releaseLocal, self, bundle} for a
    deferrable PR. self is true when the PR already carries DEFER_MARKER (it is
    a bundle; a re-run keeps its queued releaseAt). Otherwise bundle is the
    oldest of Jake's open PRs into the same repo's default branch that carry
    DEFER_MARKER, have no badge and aren't approved, or null. Read from GitHub,
    not the local queue, so jake1 and jake2 see the same bundle. Writes nothing."""
    res = run("gh", "pr", "view", url, "--json", "url,number,body")
    if res is None or res.returncode != 0:
        print(f"gh pr view: {err(res)}", file=sys.stderr)
        return 1
    me = json.loads(res.stdout)
    repo = "/".join(me["url"].split("/")[3:5])
    cfg = read_config()
    out = {"self": has_line(me.get("body"), DEFER_MARKER), "bundle": None}
    release = queued_release(me["url"]) if out["self"] else None
    if not out["self"]:
        try:
            base = default_branch(repo)
        except RuntimeError as x:
            print(str(x), file=sys.stderr)
            return 1
        res = run("gh", "pr", "list", "--repo", repo, "--base", base, "--state", "open", "--author", "@me",
                  "--json", "number,url,title,headRefName,body,reviewDecision", "--limit", "200")
        if res is None or res.returncode != 0:
            print(f"gh pr list {repo}: {err(res)}", file=sys.stderr)
            return 1
        bundles = sorted(
            (p for p in json.loads(res.stdout)
             if p["number"] != me["number"] and has_line(p.get("body"), DEFER_MARKER)
             and BADGE not in (p.get("body") or "") and p.get("reviewDecision") != "APPROVED"),
            key=lambda p: p["number"])
        if bundles:
            b = bundles[0]
            out["bundle"] = {"number": b["number"], "url": b["url"], "title": b["title"], "head": b["headRefName"]}
    at = parse_iso(release) if release else next_release(now_epoch(), cfg)
    out["releaseAt"] = iso(at)
    out["releaseLocal"] = release_label(at, cfg)
    print(json.dumps(out))
    return 0


def flush(only, channel):
    claim = Claim()
    entries = claim.take()
    keep = []  # entries that go back into the live queue
    pending = []  # claimed entries not yet posted, dropped or requeued
    try:
        if only:
            keep = [e for e in entries if e["url"] != only]
            entries = [e for e in entries if e["url"] == only]
            append_lines(QUEUE, keep)  # put the rest back before any network call
            local = entries + keep
            keep = []
        else:
            local = entries
        if not entries:
            if only:
                log(f"--only {only}: not in the queue")
            return False

        pending = list(entries)
        if channel != CHANNEL:
            log(f"channel override: posting to {channel}, not #pr-review")
        posted = posted_urls()
        pair = pairing(local, posted, hold=not only)

        def settle(e, back):
            if back:
                keep.append(e)
            pending.remove(e)

        # Decide every entry before posting any, so siblings post together.
        ready = []
        for e in entries:
            try:
                verdict, pr, why = decide(e, posted, pair)
                if verdict == "drop":
                    log(f"dropped {label(e)}: {why}")
                    settle(e, back=False)
                elif verdict == "requeue":
                    log(f"requeued {label(e)}: {why}")
                    settle(e, back=True)
                elif verdict == "hold":
                    # A red bundle never releases on its own: make it stand out in the log.
                    log(f"{'!!! ' if 'CHECKS FAILING' in why else ''}{label(e)} {why}")
                    settle(e, back=True)
                else:
                    ready.append((e, pr, why))
            except Exception as x:  # one bad entry must not cost the rest of the batch
                log(f"requeued {label(e)}: unexpected error {type(x).__name__}: {x}")
                if e in pending:
                    settle(e, back=True)

        token, token_err = None, None
        ok_urls = set()
        first = True
        for e, pr, text in post_order(ready):
            try:
                if token is None and token_err is None:
                    token, token_err = slack_token()
                if token is None:
                    log(f"requeued {label(e)}: no Slack token ({token_err})")
                    keep.append(e)
                    continue
                if not first:
                    time.sleep(POST_SPACING_S)
                first = False
                perr = post(token, channel, text)
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
            except Exception as x:  # one bad entry must not cost the rest of the batch
                if e["url"] in ok_urls:
                    log(f"posted {label(e)}, then unexpected error {type(x).__name__}: {x}")
                else:
                    log(f"requeued {label(e)}: unexpected error {type(x).__name__}: {x}")
                    keep.append(e)
            finally:
                pending.remove(e)
        return only in ok_urls if only else True
    finally:
        append_lines(QUEUE, keep + pending)
        claim.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", metavar="URL")
    ap.add_argument("--channel", default=os.environ.get("PR_REVIEW_BATCH_CHANNEL") or CHANNEL)
    ap.add_argument("--force", action="store_true", help="flush now, ignoring the window")
    ap.add_argument("--siblings", metavar="URL", help="print a PR's siblings and its message; writes nothing")
    ap.add_argument("--release-at", nargs="?", const="", metavar="ISO",
                    help="print the next deferred release after ISO (default now), as UTC; writes nothing")
    ap.add_argument("--defer-plan", metavar="URL",
                    help="print the release time and today's deferred bundle for URL's repo, as JSON; writes nothing")
    args = ap.parse_args()
    os.makedirs(STATE, exist_ok=True)

    if args.siblings:
        return show_siblings(args.siblings)
    if args.release_at is not None:
        return show_release_at(args.release_at)
    if args.defer_plan:
        return show_defer_plan(args.defer_plan)

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
