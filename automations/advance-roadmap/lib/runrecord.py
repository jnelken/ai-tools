#!/usr/bin/env python3
"""The one structured record per advance-roadmap run, and the alert built on it.

run.sh calls `append` exactly once per run — every skip path, the normal end, and
an EXIT trap for aborts — so runs.jsonl has a row even when an agent crashed or
was killed before reporting. Outcomes come from exit codes run.sh observed and
from result files the agents wrote; nothing here reads the run log.

  runrecord.py --root R append --stamp S --status ran|skipped-backoff|skipped-quota|skipped-lock|skipped-unchanged|skipped-superset|aborted
               [--detail TEXT] [--orch-provider P] [--orch-exit N] [--orch-result FILE]
               [--worker-status FILE] [--worker-result FILE] [--usage-log FILE] [--quota-gates FILE] [--exit N]
  runrecord.py --root R alert        # prints a Slack line when the error streak warrants one
"""
import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tokens  # noqa: E402

ERROR_OUTCOMES = ("error", "failed", "incomplete", "shipped-deploy-failed")
SKIP_OUTCOMES = ("skipped-backoff", "skipped-quota", "skipped-lock", "skipped-unchanged", "skipped-superset")
# A provider's usage ran out mid-run (planner banner, every worker over its gate or limited).
# Not a tooling failure: it neither counts toward nor breaks an error streak, and never alerts.
QUOTA_OUTCOMES = ("quota-paused",)
# Alert on the 2nd consecutive error, then again every 4 more (~8h at the 2h cadence).
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
    perr = orch.get("planner_error") or {}
    if perr.get("kind") == "quota":
        reset = f", resets {perr['reset_at']}" if perr.get("reset_at") else ""
        return "quota-paused", f"planner {perr.get('provider') or '?'} hit its usage limit{reset}; nothing dispatched"
    if orch.get("parse_error"):
        return "error", f"orchestrator: {orch['parse_error']}"

    w_exit = (wstatus or {}).get("exit")
    if wstatus is None:
        return "error", "worker.sh left no status"
    if wstatus.get("fatal"):
        return "error", f"worker.sh: {wstatus['fatal']}"
    if not wstatus.get("used"):
        if wstatus.get("limited") and not wstatus.get("missing"):
            names = ", ".join(l.get("provider") or "?" for l in wstatus["limited"])
            return "quota-paused", f"every worker provider was over its usage gate or limited ({names})"
        return "error", "every worker provider was limited or missing"

    if wresult:
        o = wresult.get("outcome") or ""
        if o == "bookkeeping":
            return (orch.get("outcome_token") or "blocked-no-item"), ""
        if o == "blocked-branch-left":
            return "blocked", wresult.get("summary", "")[:200]
        if o == "limit_hit":
            return "quota-paused", f"worker hit a limit: {wresult.get('limit_text') or '?'}"
        if o in ("shipped", "failed", "archive-only"):
            return o, ""
        if o == "shipped-deploy-failed":
            d = wresult.get("deploy") or {}
            return o, f"deploy {d.get('status') or 'failed'}: {d.get('url') or '?'}"
        return "error", f"unknown worker outcome {o!r}"

    if w_exit not in (0, None):
        return "error", f"worker exited {w_exit} without a result"
    if action in ("dispatch_worker", "resume_worker"):
        return "incomplete", "worker exited 0 without writing a result"
    # Bookkeeping runs may legitimately skip the result file.
    return (orch.get("outcome_token") or "blocked-no-item"), "no worker result (bookkeeping)"


def quota_info(orch, wstatus, wresult):
    """Who ran out, when it resets and the CLI's own words — for a quota-paused run."""
    perr = (orch or {}).get("planner_error") or {}
    if perr.get("kind") == "quota":
        info = {"role": "orchestrator", "provider": perr.get("provider"), "reset_epoch": perr.get("reset_epoch"),
                "reset_at": perr.get("reset_at"), "cause": perr.get("limit_text"), "tried": perr.get("tried")}
        limits = [l for l in perr.get("limits") or [] if isinstance(l, dict)]
        if limits:
            info["providers"] = limits  # every planner that ran out, when one failed over to another
        return info
    # worker.sh wrote `usage.py quota-record --role worker` into its status: every worker provider.
    w = wstatus or {}
    quota = recorded_quota(w.get("quota"))
    if quota:
        return quota
    # No record (usage.py failed): rebuild it from the gates and limits worker.sh logged itself,
    # and still say who stopped and why.
    limit_hit = (wresult or {}).get("outcome") == "limit_hit"
    primary = (wresult.get("provider") or w.get("used")) if limit_hit else None
    quota = attempts_quota("worker", w.get("limited"), primary)
    if limit_hit:
        if quota is None or (primary and quota["provider"] != primary):
            quota = {"role": "worker", "provider": primary, "reset_epoch": None, "reset_at": None,
                     "cause": None, "providers": (quota or {}).get("providers", [])}
        quota["cause"] = quota["cause"] or wresult.get("limit_text")
    return quota


def recorded_quota(record):
    """A `usage.py quota-record` result as the row's quota, or None when nobody in it is out."""
    return record if isinstance(record, dict) and record.get("provider") else None


def attempts_quota(role, attempts, primary=None):
    """`usage.py quota-record`'s shape from only the attempts a lane saw ({provider, state,
    cause/reason, reset_epoch, reset_at}), or None when none names a provider. `provider` is
    `primary` when it is among them, else the one that frees up first."""
    seen = {}
    for a in attempts or ():
        if isinstance(a, dict) and a.get("provider"):
            seen[a["provider"]] = {
                "provider": a["provider"],
                "state": a.get("state") if a.get("state") in ("over", "limited") else "limited",
                "cause": a.get("cause") or a.get("reason"),
                "reset_epoch": a.get("reset_epoch"), "reset_at": a.get("reset_at")}
    out = list(seen.values())
    if not out:
        return None
    first = seen.get(primary)
    if first is None:
        resets = [p for p in out if isinstance(p["reset_epoch"], (int, float))]
        first = min(resets, key=lambda p: p["reset_epoch"]) if resets else out[0]
    if first["provider"] == primary:
        cause = first["cause"]
    else:
        cause = "; ".join(f"{p['provider']}: {p['cause'] or p['state']}" for p in out)
    return {"role": role, "provider": first["provider"], "reset_epoch": first["reset_epoch"],
            "reset_at": first["reset_at"], "cause": cause, "providers": out}


def read_reviews(worker_result_path):
    """Step 5b rounds, from the log review.sh writes beside the worker result."""
    if not worker_result_path:
        return []
    path = os.path.join(os.path.dirname(worker_result_path), "reviews.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def review_limits(reviews):
    """Every reviewer a limit banner stopped in Step 5b (provider, cause, reset), the latest per
    provider — straight from the review log, whatever happened to the shared usage file."""
    latest = {}
    for r in reviews:
        hits = r.get("limited") if isinstance(r, dict) else None
        for hit in hits if isinstance(hits, list) else ():
            if isinstance(hit, dict) and hit.get("provider"):
                latest[hit["provider"]] = hit
    return list(latest.values())


def cmd_append(a):
    orch = load_json(a.orch_result)
    wstatus = load_json(a.worker_status)
    wresult = load_json(a.worker_result)
    outcome, detail = derive(a, orch, wstatus, wresult)
    reviews = read_reviews(a.worker_result)
    if outcome in ("shipped", "shipped-deploy-failed") and a.expect_pr and not (wresult or {}).get("pr_url"):
        # Only a merged PR from the workspace counts on the leaderboard; flag a ship without one.
        detail = "shipped without a PR URL — not credited"
    if outcome in ("shipped", "shipped-deploy-failed") and not detail:
        if not reviews:
            detail = "merged without a logged review"
        elif reviews[-1].get("verdict") == "unavailable" or not reviews[-1].get("reviewer"):
            detail = "merged unreviewed: every reviewer was limited"
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
        "lane": a.lane,  # set when supervisor.py ran this as one of several parallel lanes
        "outcome": outcome,
        "detail": detail,
        "exit": a.exit,
        "orchestrator": a.orch_provider or None,
        "orchestrator_exit": a.orch_exit,
        "action": o.get("action"),
        "worker": (wstatus or {}).get("used"),
        "worker_exit": (wstatus or {}).get("exit"),
        "worker_session": (wstatus or {}).get("session_id"),
        "worker_result_source": w.get("_source") or ("agent" if wresult else None),
        "repo": w.get("repo") or o.get("repo") or a.repo or None,
        "item": w.get("item") or o.get("item"),
        "work_kind": o.get("work_kind") or "feature",
        "branch": w.get("branch") or o.get("branch"),
        "merge_commit": w.get("merge_commit"),
        # The merged PR (Superset credits it to the workspace the worker ran in).
        "pr_url": w.get("pr_url"),
        "linear_id": w.get("linear_id") or o.get("linear_id"),
        "review_rounds": len(reviews),
        "reviewer": reviews[-1].get("reviewer") if reviews else None,
        "review_strategy": reviews[-1].get("strategy") if reviews else None,
        "review_verdict": reviews[-1].get("verdict") if reviews else None,
        "review_sessions": [r["session_id"] for r in reviews if r.get("session_id")],
        "review_unresolved": (w.get("review") or {}).get("unresolved"),
        "review_limits": review_limits(reviews),
        "summary": (w.get("summary") or o.get("summary") or "")[:1000],
        "slack_summary": (w.get("slack_summary") or "")[:400] or None,
        "quota": (quota_info(orch, wstatus, wresult) if outcome in QUOTA_OUTCOMES
                  else recorded_quota(load_json(a.quota_gates)) if outcome == "skipped-quota" else None),
        # Tokens this run spent, per (role, review round, model): see lib/tokens.py.
        "tokens": tokens.summarize(a.usage_log),
    }
    path = os.path.join(a.root, "runs.jsonl")
    # Parallel lanes append concurrently; one writer at a time keeps every record whole.
    with open(path + ".lock", "w") as lk:
        fcntl.lockf(lk, fcntl.LOCK_EX)
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
    """Consecutive error runs, newest first; skipped ticks and quota pauses are not evidence."""
    n = 0
    for r in reversed(records):
        if r.get("outcome") in SKIP_OUTCOMES + QUOTA_OUTCOMES:
            continue
        if r.get("outcome") in ERROR_OUTCOMES:
            n += 1
            continue
        break
    return n


def alert_text(records):
    ran = [r for r in records if r.get("outcome") not in SKIP_OUTCOMES + QUOTA_OUTCOMES]
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
    ap.add_argument("--usage-log", default="")
    ap.add_argument("--quota-gates", default="",
                    help="usage.py quota-record output saved when the run skipped for quota")
    ap.add_argument("--repo", default="", help="target repo, including skips before planning")
    ap.add_argument("--lane", type=int, default=None)
    ap.add_argument("--expect-pr", action="store_true",
                    help="the worker ran in a Superset workspace, so a ship must name its PR")
    sub.add_parser("alert")
    a = p.parse_args(argv)
    {"append": cmd_append, "alert": cmd_alert}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
