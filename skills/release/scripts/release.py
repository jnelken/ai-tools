#!/usr/bin/env python3
"""Release this machine's #pr-review queue now instead of at the next window.

    release.py [--list] [--all] [--dry-run] [URL ...]

  (no args)  run the flusher with --force: skips the :00/:30 window, keeps the
             cross-repo sibling hold and deferred (4 PM bundle) holds.
  --all      flush every queued URL with --only: also skips the sibling hold and
             a deferred entry's releaseAt (a deferred entry still needs green checks).
  URL ...    flush just these URLs with --only (same hold skipping as --all).
  --list     print the queue and exit; changes nothing.
  --dry-run  print the queue and what would run; changes nothing.

Prints the queue before, the flush.log lines this run wrote, and what is still
queued after. Exits 1 if any --only URL was not posted, 2 on bad usage.
"""

import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
QUEUE = os.path.join(HOME, ".claude/state/pr-review-queue.jsonl")
RUN = os.path.join(HOME, ".claude/automations/pr-review-batch/run.sh")
LOG = os.path.join(HOME, ".claude/automations/pr-review-batch/logs/flush.log")


def queued():
    """Newest line per URL, in queue order (the flusher's own rule)."""
    try:
        with open(QUEUE) as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    newest = {}
    for i, e in enumerate(lines):
        key = (e.get("queuedAt") or "", i)
        if e.get("url") and (e["url"] not in newest or key >= newest[e["url"]][0]):
            newest[e["url"]] = (key, e)
    return [e for _, e in sorted(newest.values(), key=lambda v: v[0])]


def show(title, entries):
    print(f"{title}: {len(entries)}")
    for e in entries:
        hold = f" deferred until {e.get('releaseAt')}" if e.get("defer") else ""
        print(f"  {e.get('repo')}#{e.get('pr')} queued {e.get('queuedAt')}{hold}  {e['url']}")


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def new_log(since):
    try:
        with open(LOG) as f:
            f.seek(since)
            return f.read().strip()
    except OSError:
        return ""


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    urls = [a for a in argv if not a.startswith("--")]
    unknown = flags - {"--list", "--all", "--dry-run"}
    if unknown or ("--all" in flags and urls):
        print(__doc__, file=sys.stderr)
        return 2

    before = queued()
    show("queued", before)
    if "--list" in flags:
        return 0
    if not before:
        print("nothing to release")
        return 0

    if "--all" in flags:
        urls = [e["url"] for e in before]
    known = {e["url"] for e in before}
    for u in urls:
        if u not in known:
            print(f"not in this machine's queue: {u}")
    wanted = bool(urls)
    urls = [u for u in urls if u in known]
    if wanted and not urls:
        return 1

    runs = [[RUN, "--only", u] for u in urls] if urls else [[RUN, "--force"]]
    if "--dry-run" in flags:
        for r in runs:
            print("would run:", " ".join(r))
        return 0

    start = log_size()
    failed = []
    for r in runs:
        rc = subprocess.run(r, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL).returncode
        if rc != 0 and "--only" in r:
            failed.append(r[-1])
    out = new_log(start)
    print("flush.log:")
    print("\n".join("  " + l for l in out.splitlines()) if out else "  (no lines)")
    show("still queued", queued())
    for u in failed:
        print(f"NOT posted: {u}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
