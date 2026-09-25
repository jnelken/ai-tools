#!/usr/bin/env python3
"""The one structured record per advance-roadmap run, and the alert built on it.

run.sh calls `append` exactly once per run — every skip path, the normal end, and
an EXIT trap for aborts — so runs.jsonl has a row even when an agent crashed or
was killed before reporting. Outcomes come from exit codes run.sh observed and
from result files the agents wrote; nothing here reads the run log.

  runrecord.py --root R append --stamp S --status ran|skipped-backoff|skipped-quota|skipped-lock|aborted
               [--detail TEXT] [--orch-provider P] [--orch-exit N] [--orch-result FILE]
               [--worker-status FILE] [--worker-result FILE] [--exit N]
  runrecord.py --root R alert        # prints a Slack line when the error streak warrants one
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ERROR_OUTCOMES = ("error", "failed", "incomplete")
SKIP_OUTCOMES = ("skipped-backoff", "skipped-quota", "skipped-lock")
# Alert on the 2nd consecutive error, then again every 4 more (~daily at 6h cadence).
ALERT_FIRST, ALERT_EVERY = 2, 4


def load_json(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def stamp_epoch(stamp):
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


def derive(a, orch, wstatus, wresult):
    """(outcome, detail) from what run.sh observed plus what the agents wrote."""
    if a.status in SKIP_OUTCOMES:
        return a.status, a.detail or ""
    if a.status == "aborted":
        return "incomplete", a.detail or f"run.sh exited {a.exit} before finishing"

    action = (orch or {}).get("action") or ""
    if orch is None:
        return "error", "orchestrator result unreadable"
    if orch.get("parse_error"):
        return "error", f"orchestrator: {orch['parse_error']}"

    w_exit = (wstatus or {}).get("exit")
    if wstatus is None:
        return "error", "worker.sh left no status"
    if wstatus.get("fatal"):
        return "error", f"worker.sh: {wstatus['fatal']}"
    if not wstatus.get("used"):
        return "error", "every worker provider was limited or missing"

    if wresult:
        o = wresult.get("outcome") or ""
        if o == "bookkeeping":
            return (orch.get("outcome_token") or "blocked-no-item"), ""
        if o == "blocked-branch-left":
            return "blocked", wresult.get("summary", "")[:200]
        if o == "limit_hit":
            return "error", f"worker hit a limit: {wresult.get('limit_text') or '?'}"
        if o in ("shipped", "failed", "archive-only"):
            return o, ""
        return "error", f"unknown worker outcome {o!r}"

    if w_exit not in (0, None):
        return "error", f"worker exited {w_exit} without a result"
    if action in ("dispatch_worker", "resume_worker"):
        return "incomplete", "worker exited 0 without writing a result"
    # Bookkeeping runs may legitimately skip the result file.
    return (orch.get("outcome_token") or "blocked-no-item"), "no worker result (bookkeeping)"


def cmd_append(a):
    orch = load_json(a.orch_result)
    wstatus = load_json(a.worker_status)
    wresult = load_json(a.worker_result)
    outcome, detail = derive(a, orch, wstatus, wresult)
    started = stamp_epoch(a.stamp)
    now = time.time()
    w = wresult or {}
    o = orch or {}
    rec = {
        "stamp": a.stamp,
        "started_at": datetime.fromtimestamp(started).isoformat(timespec="seconds") if started else None,
        "finished_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "duration_s": int(now - started) if started else None,
        "status": a.status,
        "outcome": outcome,
        "detail": detail,
        "exit": a.exit,
        "orchestrator": a.orch_provider or None,
        "orchestrator_exit": a.orch_exit,
        "action": o.get("action"),
        "worker": (wstatus or {}).get("used"),
        "worker_exit": (wstatus or {}).get("exit"),
        "worker_result_source": w.get("_source") or ("agent" if wresult else None),
        "repo": w.get("repo") or o.get("repo"),
        "item": w.get("item") or o.get("item"),
        "branch": w.get("branch") or o.get("branch"),
        "merge_commit": w.get("merge_commit"),
        "linear_id": w.get("linear_id") or o.get("linear_id"),
        "summary": (w.get("summary") or o.get("summary") or "")[:1000],
    }
    path = os.path.join(a.root, "runs.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"recorded {a.stamp}: {outcome}" + (f" ({detail})" if detail else ""))


def read_records(root):
    out = []
    try:
        with open(os.path.join(root, "runs.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def error_streak(records):
    """Consecutive error runs, newest first; skipped ticks are not evidence."""
    n = 0
    for r in reversed(records):
        if r.get("outcome") in SKIP_OUTCOMES:
            continue
        if r.get("outcome") in ERROR_OUTCOMES:
            n += 1
            continue
        break
    return n


def alert_text(records):
    ran = [r for r in records if r.get("outcome") not in SKIP_OUTCOMES]
    if not ran:
        return ""
    streak = error_streak(records)
    last = ran[-1]
    if streak >= ALERT_FIRST and (streak - ALERT_FIRST) % ALERT_EVERY == 0:
        return (f"<!here> :rotating_light: advance-roadmap has failed {streak} runs in a row — "
                f"latest `{last['stamp']}`: {last.get('detail') or last.get('outcome')}")
    if streak == 0 and len(ran) >= 2:
        prior = error_streak(records[:records.index(last)])
        if prior >= ALERT_FIRST:
            return (f":white_check_mark: advance-roadmap recovered after {prior} failed runs — "
                    f"`{last['stamp']}` {last.get('outcome')}")
    return ""


def cmd_alert(a):
    t = alert_text(read_records(a.root))
    if t:
        print(t)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("append")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--status", required=True,
                    choices=("ran", "aborted") + SKIP_OUTCOMES)
    ap.add_argument("--detail", default="")
    ap.add_argument("--exit", type=int, default=None)
    ap.add_argument("--orch-provider", default="")
    ap.add_argument("--orch-exit", type=int, default=None)
    ap.add_argument("--orch-result", default="")
    ap.add_argument("--worker-status", default="")
    ap.add_argument("--worker-result", default="")
    sub.add_parser("alert")
    a = p.parse_args(argv)
    {"append": cmd_append, "alert": cmd_alert}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
