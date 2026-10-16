#!/usr/bin/env python3
"""advance-roadmap follow-up runner — deterministic report-only (DEV-232 Phase B slice 2).

After work ships, scan logs for proven problems and record findings. No model, repair, PRs,
merge, install, or Linear (slice 3). Stages under $ROOT/followups/<key>/:

  project-cleanup    one per shipped item once the repo is quiescent
  tooling-hardening  one per batch/run/conductor session once children + cleanups finish

  followup.py hook item-done --stamp S [--run-dir D] [--origin O]
  followup.py hook run-end --origin O [--stamp S ...] [--run-dir D ...]
  followup.py drain [--repo R] [--origin O] [--wait-s N]
  followup.py status [--origin O] [--json]

ADVANCE_ROADMAP_FOLLOWUP=1 suppresses nested follow-ups.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import followscan  # noqa: E402
import supervisor as shared  # noqa: E402

ROOT = shared.ROOT
STATE = ROOT / "followups"
FOLLOWUP_ENV = "ADVANCE_ROADMAP_FOLLOWUP"
SHIPPED = ("shipped", "shipped-deploy-failed")
STAGES = {"cleanup": "project-cleanup", "tooling": "tooling-hardening"}
# Stage state machine: queued → waiting(reason) → ready → running → done | blocked | retryable.
QUEUED, WAITING, READY, RUNNING, DONE, BLOCKED, RETRYABLE = (
    "queued", "waiting", "ready", "running", "done", "blocked", "retryable")
ELIGIBLE = {QUEUED, WAITING, READY, RETRYABLE}
MAX_ATTEMPTS = int(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_MAX_ATTEMPTS", "3"))
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_POLL_S", "15"))
# Externally supplied path segments (stamp, origin, repo, key): no '/', '..', NUL, or empties.
# Production stamps look like YYYYMMDD-HHMMSS; fixtures may use any SAFE_ID.
SAFE_ID = re.compile(r"[A-Za-z0-9._-]+")


# ── shared helpers (slice 3 will share these with the repair module) ─────────

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def log(msg):
    print(f"{datetime.now():%H:%M:%S} followup: {msg}", flush=True)


def require_id(value, name="id"):
    """Reject path-traversal identifiers. Every externally supplied path segment goes through here."""
    if not isinstance(value, str) or not value or not SAFE_ID.fullmatch(value) or value in (".", "..") or ".." in value:
        raise ValueError(f"invalid {name}: {value!r} (letters, digits, . _ - only; no path separators)")
    return value


def under(root, *parts, name="path"):
    """Join validated parts under root; refuse escape."""
    root = Path(root).resolve()
    for p in parts:
        require_id(p, name)
    path = root.joinpath(*parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as e:
        raise ValueError(f"{name} escapes {root}: {parts!r}") from e
    return path


def read_json(path):
    return followscan.read_json(path)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=1) + "\n")
    os.replace(tmp, path)


def stage_dir(key):
    """Durable stage dir under $ROOT/followups only — never deletes or writes outside it."""
    return under(STATE, require_id(key, "stage key"), name="stage key")


def repo_path(repo):
    code = shared.code_dir()
    if not code or not repo:
        return None
    try:
        return under(code, require_id(repo, "repo"), name="repo")
    except ValueError:
        return None


def run_dir_for(stamp, external=None):
    """Resolved run dir for a stamp: external map entry, or $ROOT/runs/<stamp>."""
    external = external or {}
    if stamp in external:
        return Path(external[stamp])
    return under(ROOT / "runs", require_id(stamp, "stamp"), name="stamp")


# ── durable stage state ──────────────────────────────────────────────────────

def load(key):
    st = read_json(stage_dir(key) / "state.json")
    return st if isinstance(st, dict) else None


def save(st):
    st["updated_at"] = now_iso()
    write_json(stage_dir(st["key"]) / "state.json", st)


def cas_status(st, expected, new_status=None, **fields):
    """CAS under the caller's claim. False when status is no longer expected."""
    allowed = expected if isinstance(expected, (set, frozenset)) else {expected}
    if st.get("status") not in allowed:
        return False
    if new_status is not None:
        st["status"] = new_status
    st.update(fields)
    save(st)
    return True


def all_stages():
    out = []
    if not STATE.is_dir():
        return out
    root = STATE.resolve()
    for f in sorted(STATE.glob("*/state.json")):
        try:
            f.resolve().relative_to(root)
        except ValueError:
            continue
        st = read_json(f)
        if isinstance(st, dict) and st.get("key"):
            try:
                require_id(st["key"], "stage key")
            except ValueError:
                continue
            out.append(st)
    return sorted(out, key=lambda s: s.get("created_at") or "")


def _usable_stage(st):
    return isinstance(st, dict) and st.get("key") and st.get("stage") and st.get("status")


def new_stage(key, stage, repo, origin, **extra):
    """Create or recover a stage under its claim; healthy existing stage is a no-op."""
    require_id(key, "stage key")
    if origin is not None:
        require_id(origin, "origin")
    if repo is not None:
        require_id(repo, "repo")
    d = stage_dir(key)
    preexisted = d.is_dir()
    c = claim_stage(key)
    if not c:
        return None
    try:
        existing = load(key)
        if _usable_stage(existing):
            return None
        # Findings carry stable finding_id via followscan.finding; ticket fields arrive in slice 3.
        st = {"key": key, "stage": stage, "repo": repo, "origin": origin, "status": QUEUED,
              "created_at": now_iso(), "attempts": 0, "findings": [], "non_findings": [], "blockers": [],
              "usage_snapshots": [], "sessions": [], "mode": "report-only", **extra}
        if preexisted or (existing is not None and not _usable_stage(existing)):
            st.setdefault("blockers", []).append({
                "at": now_iso(), "kind": "recovered",
                "reason": "stage dir existed without usable state.json; re-initialized from hook input"})
        save(st)  # write_json: temp + rename under the claim
        return st
    finally:
        c.close()


class Claim:
    """An exclusive, non-blocking kernel lock on one file; released when closed or the process dies."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "a")
        try:
            fcntl.lockf(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.ok = True
        except OSError:
            self.ok = False
            self.fh.close()

    def close(self):
        if self.ok:
            self.fh.close()
            self.ok = False


def claim_stage(key):
    c = Claim(stage_dir(key) / "claim.lock")
    return c if c.ok else None


def report(st, kind, reason, next_step=""):
    """The durable local follow-up report (blockers, crashes)."""
    entry = {"at": now_iso(), "key": st["key"] if st else None, "stage": st and st.get("stage"),
             "repo": st and st.get("repo"), "kind": kind, "reason": reason, "next_step": next_step}
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "report.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    if st is not None:
        st.setdefault("blockers", []).append({"at": entry["at"], "kind": kind, "reason": reason})
    log(f"{st['key'] if st else '-'}: {kind}: {reason}")


def recursion_suppressed():
    if os.environ.get(FOLLOWUP_ENV) == "1":
        print("follow-up child: no follow-ups from a follow-up (recursion suppressed)")
        return True
    return False


# ── lifecycle hooks ──────────────────────────────────────────────────────────

def hook_item_done(stamp, run_dir=None, origin=None):
    if recursion_suppressed():
        return 0
    try:
        require_id(stamp, "stamp")
        if origin is not None:
            require_id(origin, "origin")
    except ValueError as e:
        print(f"item-done: {e}", file=sys.stderr)
        return 2
    run_dir = Path(run_dir).expanduser().resolve() if run_dir else run_dir_for(stamp)
    if (run_dir / "followup-child").exists():
        print(f"{stamp} is a follow-up's own run: no follow-ups (recursion suppressed)")
        return 0
    result = read_json(run_dir / "worker-result.json") or {}
    if result.get("outcome") not in SHIPPED:
        print(f"{stamp}: outcome {result.get('outcome')!r} did not ship — recoverable work, not a cleanup")
        return 0
    repo = result.get("repo") or (read_json(run_dir / "orchestrator-result.json") or {}).get("repo")
    # Schema allows repo=null; never queue a cleanup that would TypeError in readiness.
    if not repo or not isinstance(repo, str):
        st = new_stage(f"cleanup-{stamp}", STAGES["cleanup"], None, origin, stamp=stamp,
                       item=result.get("item"), run_dir=str(run_dir),
                       merge_commit_item=result.get("merge_commit"), status=BLOCKED)
        if st:
            report(st, "missing-repo", "shipped result has no repo", next_step="")
            save(st)
            print(f"blocked cleanup-{stamp} (missing repo)")
        else:
            print(f"cleanup-{stamp} already queued")
        return 0
    try:
        require_id(repo, "repo")
    except ValueError as e:
        print(f"item-done: {e}", file=sys.stderr)
        return 2
    st = new_stage(f"cleanup-{stamp}", STAGES["cleanup"], repo, origin, stamp=stamp,
                   item=result.get("item"), run_dir=str(run_dir), merge_commit_item=result.get("merge_commit"))
    print(f"queued cleanup-{stamp} ({repo})" if st else f"cleanup-{stamp} already queued")
    return 0


def hook_run_end(origin, stamps, run_dirs=()):
    """Queue the tooling pass. `run_dirs` are runs outside $ROOT/runs (a conductor session's), each
    named for its stamp; those complete once worker-result.json exists. Internal stamps still need
    a runs.jsonl row (see child_complete)."""
    if recursion_suppressed():
        return 0
    try:
        require_id(origin, "origin")
        for s in stamps:
            require_id(s, "stamp")
    except ValueError as e:
        print(f"run-end: {e}", file=sys.stderr)
        return 2
    external = {}
    for d in run_dirs:
        path = Path(d).expanduser().resolve()
        try:
            require_id(path.name, "stamp")
        except ValueError:
            print(f"run-end: {d}: a run dir must be named for its stamp", file=sys.stderr)
            return 2
        external[path.name] = str(path)
    children = list(dict.fromkeys([*stamps, *external]))
    st = new_stage(f"tooling-{origin}", STAGES["tooling"], followscan.TOOLING_REPO, origin,
                   children=children, run_dirs=external)
    print(f"queued tooling-{origin} ({len(children)} run(s))" if st else f"tooling-{origin} already queued")
    return 0


# ── readiness / locks ────────────────────────────────────────────────────────

def repo_lock_files(repo):
    try:
        require_id(repo, "repo")
    except ValueError:
        return []
    base = ROOT / "repo-locks"
    return [base / f"{repo}.lock", *sorted(base.glob(f"{glob_escape(repo)}.t*.lock"))]


def glob_escape(s):
    return re.sub(r"([*?\[])", r"[\1]", s)


def quiescent(repo):
    """(True, "") when no lane/thread/follow-up holds the repo. Do not call while holding it."""
    if not repo or not isinstance(repo, str):
        return False, "no repo"
    try:
        require_id(repo, "repo")
    except ValueError:
        return False, "no repo"
    if repo in shared.inflight_repos(ROOT):
        return False, "a worker an earlier run timed out on is still running in it"
    for path in repo_lock_files(repo):
        if not path.exists():
            continue
        c = Claim(path)
        if not c.ok:
            return False, f"{path.name} is held (a lane, thread or follow-up is working in {repo})"
        c.close()
    return True, ""


def child_complete(stamp, external=None):
    """(done, how): internal via runs.jsonl; external via worker-result.json only."""
    external = external or {}
    try:
        require_id(stamp, "stamp")
    except ValueError:
        return False, ""
    if stamp in shared.run_records({stamp}):
        return True, "runs.jsonl"
    if stamp not in external:
        return False, ""
    try:
        run = run_dir_for(stamp, external)
    except ValueError:
        return False, ""
    if (run / "worker-result.json").exists():
        return True, "worker-result"
    return False, ""


def ready(st):
    """No age bypass — wait until every child run and cleanup is terminal."""
    if st["stage"] == STAGES["cleanup"]:
        return quiescent(st.get("repo"))
    children = st.get("children") or []
    external = st.get("run_dirs") or {}
    unfinished = [s for s in children if not child_complete(s, external)[0]]
    if unfinished:
        names = ", ".join(unfinished[:5]) + ("…" if len(unfinished) > 5 else "")
        return False, f"{len(unfinished)} run(s) of {st['origin']} have not finished ({names})"
    active = {QUEUED, WAITING, READY, RUNNING, RETRYABLE}
    for other in all_stages():
        if other["stage"] != STAGES["cleanup"] or other.get("stamp") not in children:
            continue
        if other.get("status") in active:
            return False, f"{other['key']} has not finished"
    return True, ""


def age_s(st):
    try:
        return time.time() - datetime.fromisoformat(st["created_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def eligible(st, now=None):
    now = time.time() if now is None else now
    status = st.get("status")
    if status in ELIGIBLE or status == "pending":  # pending = legacy alias for queued/retryable
        return True
    if status == "deferred":
        until = st.get("deferred_until")
        return not until or until <= now
    return False


def set_waiting(st, reason):
    """Mark waiting. Caller must hold the stage claim — no save outside a claim."""
    if st.get("status") not in ELIGIBLE | {"pending"}:
        return False
    st["waiting_reason"] = reason
    blockers = st.setdefault("blockers", [])
    if not blockers or blockers[-1].get("kind") != "waiting" or blockers[-1].get("reason") != reason:
        blockers.append({"at": now_iso(), "kind": "waiting", "reason": reason})
    return cas_status(st, ELIGIBLE | {"pending", WAITING}, WAITING)


def set_ready(st):
    """Mark ready. Caller must hold the stage claim — no save outside a claim."""
    if st.get("status") == READY:
        return True
    st.pop("waiting_reason", None)
    return cas_status(st, ELIGIBLE | {"pending"}, READY)


def recover_interrupted():
    """A stage left `running` whose claim nobody holds was killed mid-step: it resumes next."""
    for st in all_stages():
        if st.get("status") != RUNNING:
            continue
        c = claim_stage(st["key"])
        if not c:
            continue
        try:
            st = load(st["key"]) or st
            # CAS: a concurrent drain may have finished between the snapshot and this claim.
            if st.get("status") != RUNNING:
                continue
            st.setdefault("blockers", []).append({"at": now_iso(), "kind": "interrupted",
                                                  "reason": "the previous drain stopped mid-stage; resuming"})
            if cas_status(st, RUNNING, QUEUED):
                log(f"{st['key']}: recovered an interrupted stage")
        finally:
            c.close()


# ── ANALYZE (deterministic recognition tests only — no model) ────────────────

def scan(st):
    """Recognize findings (pure reads; no excerpts)."""
    if st["stage"] == STAGES["cleanup"]:
        if st.get("run_dir"):
            run_dir = Path(st["run_dir"])
        else:
            run_dir = run_dir_for(st["stamp"])
        repo_dir = repo_path(st["repo"])
        findings, quiet = followscan.cleanup_scan(run_dir, repo_dir if repo_dir and repo_dir.is_dir() else None)
        files = [p for p in (run_dir / n for n in (
            "worker-result.json", "reviews.jsonl", "worker-output.log")) if p.exists()]
    else:
        children = st.get("children") or []
        for child in children:
            require_id(child, "stamp")
        cleanups = [s for s in all_stages() if s.get("stamp") in children]
        findings, quiet = followscan.tooling_scan(ROOT, children, cleanups, st.get("run_dirs"))
        files = followscan.corpus_files(ROOT, children, st.get("run_dirs"), stages=cleanups)
    st["findings"] = findings
    st["non_findings"] = quiet
    st["corpus"] = [str(p) for p in files]
    st["scanned"] = True
    return st


# ── process / drain (ANALYZE + REPORT only) ──────────────────────────────────

def process(st):
    """ANALYZE then REPORT under the caller's stage claim."""
    st.pop("waiting_reason", None)
    if not cas_status(st, READY, RUNNING, attempts=st.get("attempts", 0) + 1,
                      next={"step": "analyze"}):
        return
    if not st.get("scanned"):
        scan(st)
        if st.get("status") != RUNNING:
            return
        save(st)
    if st.get("status") != RUNNING:
        return
    fs = st.get("findings") or []
    quiet = st.get("non_findings") or []
    if cas_status(st, RUNNING, DONE, next=None, reported_at=now_iso(),
                  open_findings=sum(1 for f in fs if f.get("status") == "open")):
        log(f"{st['key']}: reported {len(fs)} finding(s), {len(quiet)} non-finding(s) (report-only)")


def order(st):
    # Prefer cleanups (project) before tooling; oldest first. Slice 3 will prefer stages with
    # unfinished repair work (branch/PR/ticket) ahead of fresh ones.
    return (0 if st["stage"] == STAGES["cleanup"] else 1, st.get("created_at") or "")


def drain(repo=None, origin=None, stage=None, wait_s=0.0):
    """Drain ready stages. Every status transition is claim → reload → eligibility → act → save."""
    STATE.mkdir(parents=True, exist_ok=True)
    recover_interrupted()
    deadline = time.time() + wait_s
    while True:
        waiting, progressed = [], False
        for st in sorted(all_stages(), key=order):
            if repo and st.get("repo") != repo or stage and st["stage"] != STAGES[stage]:
                continue
            if not eligible(st):
                continue
            c = claim_stage(st["key"])
            if not c:
                continue   # another drain holds this stage
            try:
                st = load(st["key"]) or st
                if not eligible(st):
                    continue
                try:
                    ok, why = ready(st)
                except Exception as e:  # malformed stage must not abort the drain
                    report(st, "readiness", f"{type(e).__name__}: {e}")
                    cas_status(st, ELIGIBLE | {"pending"}, BLOCKED)
                    progressed = True
                    continue
                if not ok:
                    set_waiting(st, why)
                    waiting.append(f"{st['key']}: {why}")
                    continue
                if st.get("status") != READY and not set_ready(st):
                    continue
                if origin:
                    st["drained_by"] = origin
                try:
                    process(st)
                except Exception as e:  # one broken stage must never stop the rest
                    report(st, "crash", f"{type(e).__name__}: {e}")
                    # CAS: only demote if we still own the running transition.
                    dest = RETRYABLE if st.get("attempts", 0) < MAX_ATTEMPTS else BLOCKED
                    cas_status(st, RUNNING, dest)
                progressed = True
            finally:
                c.close()
        if not waiting or time.time() >= deadline:
            for w in waiting:
                log(f"waiting: {w}")
            return 0
        if not progressed:
            time.sleep(min(POLL_S, max(0.0, deadline - time.time())))


def summary(origin=None):
    rows = []
    for st in all_stages():
        if origin and origin not in (st.get("origin"), st.get("drained_by")):
            continue
        fs = st.get("findings") or []
        status = st.get("status")
        reason = st.get("waiting_reason")
        if not reason and st.get("blockers"):
            reason = st["blockers"][-1].get("reason")
        age = age_s(st)
        rows.append({"key": st["key"], "stage": st["stage"], "repo": st.get("repo"), "status": status,
                     "item": st.get("item"), "findings": len(fs),
                     "open_findings": sum(f.get("status") == "open" for f in fs),
                     "non_findings": len(st.get("non_findings") or []),
                     "mode": st.get("mode") or "report-only",
                     "age_s": int(age),
                     "waiting_reason": st.get("waiting_reason"),
                     "blocker": reason if status in (BLOCKED, WAITING, RETRYABLE, "pending") else None})
    return rows


def _age_label(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{((seconds % 3600) // 60):02d}m"


def summary_line(rows):
    """Batch/run summary sentence; waiting stages include age and reason."""
    done = [r for r in rows if r["status"] == DONE]
    blocked = [r for r in rows if r["status"] == BLOCKED]
    waiting = [r for r in rows if r["status"] == WAITING]
    retryable = [r for r in rows if r["status"] in (RETRYABLE, "pending") and r.get("blocker")]
    parts = []
    if done:
        n_find = sum(r["findings"] for r in done)
        n_open = sum(r.get("open_findings") or 0 for r in done)
        parts.append(f"{len(done)} follow-up(s) reported ({n_find} finding(s), {n_open} open)")
    if waiting:
        bits = []
        for r in waiting[:3]:
            why = r.get("waiting_reason") or r.get("blocker") or "not ready"
            bits.append(f"{r['key']} age {_age_label(r.get('age_s'))}: {why}")
        extra = f" (+{len(waiting) - 3} more)" if len(waiting) > 3 else ""
        parts.append(f"{len(waiting)} waiting ({'; '.join(bits)}{extra})")
    if blocked or retryable:
        parts.append(f"{len(blocked) + len(retryable)} blocked (see followups/report.jsonl)")
    return ("Follow-ups: " + "; ".join(parts) + ".") if parts else ""


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook")
    h.add_argument("event", choices=["item-done", "run-end"])
    h.add_argument("--stamp", action="append", default=[])
    h.add_argument("--run-dir", action="append", default=[], dest="run_dir_list",
                   help="item-done: the run's dir (once); run-end: a finished run outside $ROOT/runs, named for its stamp")
    h.add_argument("--origin")
    d = sub.add_parser("drain")
    d.add_argument("--repo")
    d.add_argument("--origin")
    d.add_argument("--stage", choices=sorted(STAGES))
    d.add_argument("--wait-s", type=float, default=0.0, help="wait this long for repos/children to finish")
    t = sub.add_parser("status")
    t.add_argument("--origin")
    t.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "hook":
        if a.event == "item-done":
            if len(a.stamp) != 1 or len(a.run_dir_list) > 1:
                p.error("item-done takes exactly one --stamp and at most one --run-dir")
            return hook_item_done(a.stamp[0], (a.run_dir_list or [None])[0], a.origin)
        if not a.origin:
            p.error("run-end needs --origin (letters, digits, . _ -)")
        try:
            require_id(a.origin, "origin")
        except ValueError as e:
            p.error(str(e))
        return hook_run_end(a.origin, a.stamp, a.run_dir_list)
    if a.cmd == "drain":
        if recursion_suppressed():
            return 0
        try:
            if a.repo:
                require_id(a.repo, "repo")
            if a.origin:
                require_id(a.origin, "origin")
        except ValueError as e:
            p.error(str(e))
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        return drain(a.repo, a.origin, a.stage, a.wait_s)
    try:
        if a.origin:
            require_id(a.origin, "origin")
    except ValueError as e:
        p.error(str(e))
    rows = summary(a.origin)
    print(json.dumps(rows, indent=1) if a.json else (summary_line(rows) or "Follow-ups: none."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
