#!/usr/bin/env python3
"""advance-roadmap follow-up runner — report + tickets + repair (DEV-242 slice 3b).

After work ships, scan logs for proven problems and record findings. Slice 3a added Linear ticket
recovery, dedupe, deferral and close (lib/followticket.py). Slice 3b connects repair: a delegated
builder, independent cross-provider review, PR checks, merge, deploy verification and ticket
finish/deferral (lib/followrepair.py). No model analyst yet (slice 3c). Stages under
$ROOT/followups/<key>/:

  project-cleanup    one per shipped item once the repo is quiescent
  tooling-hardening  one per batch/run/conductor session once children + cleanups finish

  followup.py hook item-done --stamp S [--run-dir D] [--origin O]
  followup.py hook run-end --origin O [--stamp S ...] [--run-dir D ...]
  followup.py drain [--repo R] [--origin O] [--wait-s N]
  followup.py status [--origin O] [--json]

ADVANCE_ROADMAP_FOLLOWUP=1 suppresses nested follow-ups.
ADVANCE_ROADMAP_FOLLOWUP_LINEAR=0 disables Linear reads/writes.
ADVANCE_ROADMAP_FOLLOWUP_REPAIR=1 enables the repair loop (builder → review → land).
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
import followrepair  # noqa: E402
import followscan  # noqa: E402
import followticket  # noqa: E402
import supervisor as shared  # noqa: E402
from followrepair import (  # noqa: E402
    MAX_ROUNDS, builder_alive, feasibility, land, run_builder, run_review, safe_repo,
)

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


# ── shared helpers ───────────────────────────────────────────────────────────

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
        # Findings carry stable finding_id via followscan.finding; tickets via followticket (slice 3a).
        st = {"key": key, "stage": stage, "repo": repo, "origin": origin, "status": QUEUED,
              "created_at": now_iso(), "attempts": 0, "findings": [], "non_findings": [], "blockers": [],
              "usage_snapshots": [], "sessions": [], "validation": {"reviews": []},
              "branch": None, "pr_url": None, "merge_commit": None, "mode": "tickets", **extra}
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


class RepoLock:
    """Holds every lock file for a repo (primary + thread locks) or none."""

    def __init__(self, claims):
        self.claims = claims
        self.ok = bool(claims)

    def close(self):
        for c in self.claims:
            c.close()
        self.claims = []
        self.ok = False


# How many run.sh thread slots a repair preclaims. Shared with run.sh's THREAD cap.
REPO_THREAD_LOCK_CEILING = 64


def take_repo_lock(repo):
    """Exclusive non-blocking lock on primary + every batch thread slot.

    Owner of repair↔batch exclusion: hold `repo.lock` and `repo.t1`…`tN` (N =
    REPO_THREAD_LOCK_CEILING) so run.sh cannot open a new thread beside a live repair.
    Also claims any higher `.t*.lock` files that already exist.
    """
    if not repo:
        return None
    try:
        require_id(repo, "repo")
    except ValueError:
        return None
    n = REPO_THREAD_LOCK_CEILING
    base = ROOT / "repo-locks"
    paths = [base / f"{repo}.lock"]
    paths.extend(base / f"{repo}.t{i}.lock" for i in range(1, n + 1))
    for extra in sorted(base.glob(f"{glob_escape(repo)}.t*.lock")):
        if extra not in paths:
            paths.append(extra)
    claims = []
    for path in paths:
        c = Claim(path)
        if not c.ok:
            for held in claims:
                held.close()
            return RepoLock([])
        claims.append(c)
    return RepoLock(claims)


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
    """No age bypass — wait until every child run and cleanup is terminal.

    When repair is enabled, also require the repo quiescent so a tooling stage
    does not race a concurrent batch thread before take_repo_lock.
    """
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
    if repair_enabled() and st.get("repo"):
        return quiescent(st.get("repo"))
    return True, ""


def age_s(st):
    try:
        return time.time() - datetime.fromisoformat(st["created_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def repair_enabled():
    """Slice 3b flips this on. Until then, recovered ticket work that needs build/review stays parked."""
    return os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_REPAIR", "0") == "1"


def awaits_repair(st):
    """True when the stage holds unfinished ticket work that only the repair loop can advance."""
    step = (st.get("next") or {}).get("step")
    if step in ("build", "review", "land"):
        return True
    if st.get("recovered_from") and st.get("status") == "deferred":
        return True
    if st.get("branch") or st.get("pr_url"):
        todo = [f for f in st.get("findings") or [] if f.get("status") in ("open", "deferred")]
        return bool(todo)
    return False


def eligible(st, now=None):
    now = time.time() if now is None else now
    status = st.get("status")
    # Park recovered/deferred repair work until slice 3b — otherwise drain would spin forever.
    if not repair_enabled() and awaits_repair(st) and status in (QUEUED, WAITING, READY, RETRYABLE,
                                                                  "pending", "deferred"):
        return False
    if status in ELIGIBLE or status == "pending":  # pending = legacy alias for queued/retryable
        return True
    if status == "deferred":
        until = st.get("deferred_until")
        return not until or until <= now
    return False


def set_waiting(st, reason):
    """Mark waiting. Caller must hold the stage claim — no save outside a claim."""
    if st.get("status") not in ELIGIBLE | {"pending", "deferred"}:
        return False
    st["waiting_reason"] = reason
    blockers = st.setdefault("blockers", [])
    if not blockers or blockers[-1].get("kind") != "waiting" or blockers[-1].get("reason") != reason:
        blockers.append({"at": now_iso(), "kind": "waiting", "reason": reason})
    return cas_status(st, ELIGIBLE | {"pending", "deferred", WAITING}, WAITING)


def set_ready(st):
    """Mark ready. Caller must hold the stage claim — no save outside a claim.

    Deferred stages (quota pause, parked repair) become ready once eligible() says so.
    """
    if st.get("status") == READY:
        return True
    st.pop("waiting_reason", None)
    return cas_status(st, ELIGIBLE | {"pending", "deferred"}, READY)


def recover_interrupted(repo=None, origin=None):
    """A stage left `running` whose claim nobody holds was killed mid-step: it resumes next.

    When repo/origin are set (origin-scoped drain), only matching stages are rewritten —
    a sibling batch's running stage must not be touched. A builder that outlived its drain
    must not be recovered beside a second builder.
    """
    for st in all_stages():
        if st.get("status") != RUNNING:
            continue
        if repo and st.get("repo") != repo:
            continue
        if origin and st.get("origin") != origin:
            continue
        if builder_alive(st["key"]):
            continue  # its builder outlived the drain; never start a second beside it
        c = claim_stage(st["key"])
        if not c:
            continue
        try:
            st = load(st["key"]) or st
            # CAS: a concurrent drain may have finished between the snapshot and this claim.
            if st.get("status") != RUNNING:
                continue
            if repo and st.get("repo") != repo:
                continue
            if origin and st.get("origin") != origin:
                continue
            if builder_alive(st["key"]):
                continue
            st.setdefault("blockers", []).append({"at": now_iso(), "kind": "interrupted",
                                                  "reason": "the previous drain stopped mid-stage; resuming"})
            if cas_status(st, RUNNING, QUEUED):
                log(f"{st['key']}: recovered an interrupted stage")
        finally:
            c.close()


def mark_running_cleanups_retryable(repo, kind="timeout", reason="cleanup drain exceeded its deadline"):
    """Timeout recovery owner: every matching stage write is claim → reload → recheck → CAS save.

    Callers (daily_batch cleanup reap) must not write followups/*/state.json themselves.
    """
    require_id(repo, "repo")
    marked = 0
    for snapshot in all_stages():
        if not (snapshot.get("repo") == repo
                and snapshot.get("stage") == STAGES["cleanup"]
                and snapshot.get("status") == RUNNING):
            continue
        key = snapshot["key"]
        c = claim_stage(key)
        if not c:
            continue
        try:
            live = load(key)
            if not (isinstance(live, dict)
                    and live.get("repo") == repo
                    and live.get("stage") == STAGES["cleanup"]
                    and live.get("status") == RUNNING):
                continue
            live.setdefault("blockers", []).append({"at": now_iso(), "kind": kind, "reason": reason})
            if cas_status(live, RUNNING, RETRYABLE):
                marked += 1
                log(f"{key}: marked retryable ({kind})")
        finally:
            c.close()
    return marked


# ── the local finding → ticket index ─────────────────────────────────────────

# Reentrant tickets lock: nested TicketIndex must not close the fd (that releases the POSIX lock).
_TICKET_LOCK = {"depth": 0, "fh": None, "entries": None}


class TicketIndex:
    """followups/tickets.json under a blocking lock: which open ticket tracks each finding id.

    The drain's Linear snapshot is read once, so a ticket filed mid-drain — by this drain or a
    concurrent one — is only visible here. Hold it across the check-then-create. Nested enters
    share one fd so rewrite_ticket_description inside sync_deferral_tickets cannot drop the lock.
    """

    def __init__(self):
        self._outer = False

    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        if _TICKET_LOCK["depth"] == 0:
            fh = open(STATE / "tickets.lock", "a")
            fcntl.lockf(fh, fcntl.LOCK_EX)
            doc = read_json(STATE / "tickets.json")
            _TICKET_LOCK["fh"] = fh
            _TICKET_LOCK["entries"] = doc if isinstance(doc, dict) else {}
            self._outer = True
        _TICKET_LOCK["depth"] += 1
        self.entries = _TICKET_LOCK["entries"]
        return self

    def __exit__(self, *exc):
        _TICKET_LOCK["depth"] -= 1
        if _TICKET_LOCK["depth"] == 0:
            write_json(STATE / "tickets.json", _TICKET_LOCK["entries"] or {})
            _TICKET_LOCK["fh"].close()
            _TICKET_LOCK["fh"] = None
            _TICKET_LOCK["entries"] = None

    def ticket(self, fid):
        return (self.entries.get(fid) or {}).get("ticket")

    def add(self, fid, ident, key):
        self.entries[fid] = {"ticket": ident, "key": key, "at": time.time()}

    def drop_finding(self, fid):
        self.entries.pop(fid, None)
        _TICKET_LOCK["entries"] = self.entries

    def drop_tickets(self, idents):
        self.entries = {k: e for k, e in self.entries.items() if e.get("ticket") not in idents}
        _TICKET_LOCK["entries"] = self.entries


# ── prior tickets (recovery / dedupe / deferral / close) ──────────────────────

def recover_tickets():
    """Open follow-up tickets from earlier runs (any machine), reconciled into local state.

    Returns every open issue (for deduping fresh findings), or None when Linear could not be read.
    Every stage write is claim → reload → recheck → save.
    """
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        return []
    fetched_at = time.time()
    try:
        issues = followticket.fetch_open()
    except followticket.LinearError as e:
        report(None, "linear", f"could not read open follow-up tickets: {e}")
        return None
    open_ids = {i.get("identifier") for i in issues}
    with TicketIndex() as index:
        # Mutate the shared dict in place — replacing index.entries would orphan the lock's copy.
        keep = {k: e for k, e in list(index.entries.items())
                if e.get("ticket") in open_ids or (e.get("at") or 0) >= fetched_at}
        index.entries.clear()
        index.entries.update(keep)
    for issue, meta in followticket.followup_tickets(issues):
        ident = issue.get("identifier")
        status_map = meta.get("finding_status") or {}
        owners = meta.get("finding_owners") or {}
        all_ids = list(meta.get("finding_ids") or status_map)
        open_fids = [fid for fid in all_ids if status_map.get(fid, "open") != "fixed"]
        if not open_fids:
            # Ticket still open but metadata says all fixed — close only when a local stage
            # already has verified repair for every finding (never invent validation).
            key = meta.get("key")
            try:
                if key:
                    require_id(key, "stage key")
            except ValueError:
                continue
            st = load(key) if key else None
            if st and ticket_close_verdict(ident, current=st, issues=issues) == "ready":
                c = claim_stage(key)
                if c:
                    try:
                        live = load(key) or st
                        live.setdefault("pending_close", [])
                        if not any(e.get("ticket") == ident for e in live["pending_close"]):
                            live["pending_close"].append({"ticket": ident, "commented": False})
                        live.setdefault("close_note", (
                            f"Fixed findings on `{ident}` verified locally; closing stranded open ticket."))
                        close_tickets(live, issues=issues)
                        save(live)
                    finally:
                        c.close()
            continue
        partial = bool(owners) or (any(status_map.get(fid) == "fixed" for fid in all_ids)
                                   and bool(status_map))
        # Repo identity: metadata block or the single repo/* label — never title/description prose.
        repo = meta.get("repo") or followticket.repo_label(issue)
        if not repo:
            continue
        # Group open findings by recorded owner so each stage only gets its own work.
        groups = {}  # key -> {"fids": [...], "owner": {...}}
        if partial:
            for fid in open_fids:
                o = owners.get(fid) if isinstance(owners.get(fid), dict) else {}
                ok = o.get("key") or meta.get("key")
                if not ok:
                    continue
                groups.setdefault(ok, {"fids": [], "owner": o if o.get("key") else {}})
                groups[ok]["fids"].append(fid)
                if o.get("key") and not groups[ok]["owner"].get("key"):
                    groups[ok]["owner"] = o
        else:
            key = meta.get("key")
            if not key:
                continue
            groups[key] = {"fids": list(all_ids), "owner": {}}
        for key, group in groups.items():
            try:
                require_id(key, "stage key")
            except ValueError:
                continue
            owner = group["owner"] or {}
            todo_ids = group["fids"]
            if not todo_ids:
                continue
            if partial:
                resume_branch, resume_pr = owner.get("branch"), owner.get("pr_url")
                next_step = "review" if resume_pr else "build"
                builder = owner.get("builder_provider")
                stage_name = owner.get("stage") or meta.get("stage") or STAGES["cleanup"]
            else:
                resume_branch, resume_pr = meta.get("branch"), meta.get("pr_url")
                next_step = "review" if resume_pr else "build"
                builder = meta.get("builder_provider")
                stage_name = meta.get("stage") or STAGES["cleanup"]
            existing = load(key)
            if existing is None:
                findings = [{"id": fid, "stage": stage_name, "repo": repo, "kind": "ticket",
                             "title": issue.get("title"), "fix": f"see {ident}", "status": "deferred",
                             "ticket": ident, "evidence": [{"source": ident, "quote": issue.get("title")}]}
                            for fid in todo_ids]
                st = new_stage(key, stage_name, repo, meta.get("origin"), stamp=meta.get("stamp"),
                               item=meta.get("item"), recovered_from=ident,
                               ticket_body=(issue.get("description") or "")[:6000],
                               status="deferred", branch=resume_branch, pr_url=resume_pr, scanned=True,
                               builder_provider=builder, owned_tickets=[ident],
                               ticket_meta_at=meta.get("updated_at"), findings=findings,
                               next={"step": next_step, "round": 1})
                if st:
                    log(f"{key}: rebuilt from {ident} (no local state on this machine)")
                continue
            if repo and existing.get("repo") and existing.get("repo") != repo:
                log(f"{key}: refusing recovery from {ident}: stage repo "
                    f"{existing.get('repo')!r} != ticket repo {repo!r}")
                continue
            c = claim_stage(key)
            if not c:
                continue
            try:
                st = load(key) or existing
                if repo and st.get("repo") and st.get("repo") != repo:
                    continue
                changed = False
                own = meta.get("key") == key or owner.get("key") == key
                if own and ident not in st.setdefault("owned_tickets", []):
                    st["owned_tickets"].append(ident)
                    changed = True
                newer = own and isinstance(meta.get("updated_at"), (int, float)) \
                    and meta["updated_at"] > (st.get("ticket_meta_at") or 0)
                if newer and st.get("status") != RUNNING:
                    # Owner fields from finding_owners win over ticket-level branch/PR when partial.
                    src_builder = owner.get("builder_provider") if partial else meta.get("builder_provider")
                    src_branch = owner.get("branch") if partial else meta.get("branch")
                    src_pr = owner.get("pr_url") if partial else meta.get("pr_url")
                    if src_builder and src_builder != st.get("builder_provider"):
                        log(f"{key}: {ident} says {src_builder} built "
                            f"{src_branch or 'the branch'} "
                            f"(local state said {st.get('builder_provider')})")
                        st["builder_provider"] = src_builder
                        st["reviewed_sha"] = None
                        changed = True
                    if src_branch != st.get("branch") or src_pr != st.get("pr_url"):
                        st["branch"] = src_branch
                        st["pr_url"] = src_pr
                        nxt = dict(st.get("next") or {})
                        nxt["step"] = "review" if src_pr else "build"
                        st["next"] = nxt
                        changed = True
                    st["ticket_meta_at"] = meta["updated_at"]
                    if issue.get("description"):
                        st["ticket_body"] = (issue.get("description") or "")[:6000]
                    changed = True
                elif not partial and meta.get("builder_provider") and not st.get("builder_provider") \
                        and st.get("status") != RUNNING:
                    st.update(builder_provider=meta["builder_provider"], reviewed_sha=None)
                    changed = True
                for f in st.get("findings") or []:
                    if f["id"] in (meta.get("finding_ids") or []) and f.get("ticket") != ident:
                        f["ticket"] = ident
                        changed = True
                # Restore only this owner's open findings (never a sibling's). Reopen locals that
                # Linear still marks open so a Done stage with a stale "fixed" row resumes —
                # except a verified fix after a failed Linear rewrite: keep fixed and retry meta.
                known = {f["id"]: f for f in st.get("findings") or []}
                needs_work = False
                verified_stale = []
                for fid in todo_ids:
                    if fid in known:
                        cur = known[fid].get("status")
                        if cur in ("fixed", "duplicate", DONE):
                            if cur == "fixed" and stage_repair_verified(st):
                                if known[fid].get("ticket") != ident:
                                    known[fid]["ticket"] = ident
                                    changed = True
                                verified_stale.append(fid)
                                continue
                            known[fid]["status"] = "deferred"
                            known[fid]["ticket"] = ident
                            needs_work = True
                            changed = True
                        continue
                    st.setdefault("findings", []).append({
                        "id": fid, "stage": st.get("stage") or stage_name, "repo": st.get("repo") or repo,
                        "kind": "ticket", "title": issue.get("title"), "fix": f"see {ident}",
                        "status": "deferred", "ticket": ident,
                        "evidence": [{"source": ident, "quote": issue.get("title")}],
                    })
                    needs_work = True
                    changed = True
                if verified_stale:
                    tracked = ticket_tracked_ids(ident, current=st, extras=todo_ids)
                    status_map = {fid: ("fixed" if finding_verified_fixed(fid, current=st) else "open")
                                  for fid in tracked}
                    if tracked and all(s == "fixed" for s in status_map.values()):
                        st.setdefault("pending_close", [])
                        if not any(e.get("ticket") == ident for e in st["pending_close"]):
                            st["pending_close"].append({"ticket": ident, "commented": False})
                        st.setdefault("close_note", (
                            f"Fixed findings on `{ident}` verified locally; retrying close after "
                            f"stale Linear metadata."))
                        close_tickets(st, issues=issues)
                        changed = True
                    else:
                        pending = st.setdefault("pending_partial", [])
                        if not any(e.get("ticket") == ident and not e.get("done") for e in pending):
                            pending.append({"ticket": ident, "status": status_map, "done": False,
                                            "commented": False})
                        apply_partial_progress(st, issues=issues)
                        changed = True
                if needs_work and st.get("status") in (DONE, BLOCKED) and st.get("status") != RUNNING:
                    st.update(status="deferred", next={"step": "build", "round": 1}, scanned=True)
                    changed = True
                if changed:
                    save(st)
            finally:
                c.close()
    return issues


def _issue_repo(issue):
    """Repo identity for an open issue: metadata.repo or the single repo/* label."""
    meta = followticket.parse_meta(issue.get("description") or "") or {}
    return meta.get("repo") or followticket.repo_label(issue)


def require_ticket_repo(ident, want_repo, issues=None):
    """Shared write-path guard: return the open issue after enforcing repo match, or raise."""
    if not want_repo:
        raise followticket.LinearError(f"stage has no repo; refusing ticket write to {ident}")
    pool = list(issues) if issues is not None else None
    if pool is None:
        pool = followticket.fetch_open()
    target = next((i for i in pool if i.get("identifier") == ident), None)
    if not target and issues is not None:
        pool = followticket.fetch_open()
        target = next((i for i in pool if i.get("identifier") == ident), None)
    if not target:
        raise followticket.LinearError(f"{ident} not in open issues; refusing ticket write")
    ticket_repo = _issue_repo(target)
    if ticket_repo != want_repo:
        raise followticket.LinearError(
            f"{ident} repo {ticket_repo!r} != stage repo {want_repo!r}; refusing ticket write")
    return target


def dedupe(st, issues):
    """Attach existing tickets to fresh findings and fold this run's evidence into them once."""
    if not issues:
        return
    posted = set(st.setdefault("evidence_posted", []))
    commented = set(st.setdefault("evidence_commented", []))
    for f in st["findings"]:
        # Include duplicate: ownership may flip before evidence lands; retries must still finish.
        if f.get("status") not in ("open", "deferred", "duplicate"):
            continue
        want_repo = f.get("repo") or st.get("repo")
        issue = None
        if f.get("ticket"):
            hit = next((i for i in issues if i.get("identifier") == f["ticket"]), None)
            # Reject stale/cross-repo/unlabeled attachments before any Linear write.
            if hit and want_repo and _issue_repo(hit) == want_repo:
                issue = hit
            else:
                f.pop("ticket", None)
        if not issue:
            issue = followticket.match(issues, f)
        if not issue:
            continue
        if want_repo and _issue_repo(issue) != want_repo:
            continue
        f["ticket"] = issue["identifier"]
        meta = followticket.parse_meta(issue.get("description")) or {}
        per = meta.get("finding_owners") or {}
        owned = per.get(f["id"]) if isinstance(per.get(f["id"]), dict) else None
        owner = (owned or {}).get("key") or meta.get("key")
        other = owner and owner != st["key"] and load(owner)
        if owner and owner != st["key"]:
            # Stay "duplicate" (not "fixed") until evidence sync finishes so retries remain eligible.
            f.update(status="duplicate", duplicate_of=owner)
            if other and finding_verified_fixed(f["id"], current=other, owner_key=owner):
                f["owner_verified"] = True
        tag = f"{issue['identifier']}:{f['id']}:{st['key']}"
        if tag in posted:
            continue
        # recovered_from: skip the redundant "saw this again" comment, but still rewrite metadata.
        skip_comment = issue.get("identifier") == st.get("recovered_from")
        body = (f"advance-roadmap follow-up `{st['key']}` saw this again:\n\n"
                + followticket.render_findings([f]))
        try:
            if not skip_comment and tag not in commented:
                require_ticket_repo(issue["identifier"], want_repo, issues=issues)
                followticket.comment(issue["identifier"], body)
                commented.add(tag)
                st["evidence_commented"] = sorted(commented)
            rewrite_ticket_description(
                st, issue["identifier"], [f], quota=st.get("quota"),
                next_step=f"see evidence on {issue['identifier']}", issues=issues)
            posted.add(tag)
        except followticket.LinearError as e:
            report(st, "linear", f"could not add evidence to {issue['identifier']}: {e}")
    st["evidence_posted"] = sorted(posted)
    st["evidence_commented"] = sorted(commented)
    refresh_ticket_sync_flags(st)


def next_step_text(st, step):
    where = f" on branch `{st.get('branch')}`" if st.get("branch") else ""
    pr = f" (PR {st['pr_url']})" if st.get("pr_url") else ""
    return (f"`python3 ~/.claude/automations/advance-roadmap/followup.py drain --repo {st['repo']}` "
            f"after the reset: it resumes at {step}{where}{pr}.")


def deferral_note(st, quota, step):
    return (f"{step}|{quota.get('provider')}|{quota.get('reset_at')}|{st.get('builder_provider')}"
            f"|{st.get('branch')}|{st.get('pr_url')}")


def ticket_description_payload(st, findings, ident, issues=None, finding_status=None, finding_owners=None):
    """Merge findings with a ticket's current metadata: preserve every ID already tracked."""
    by_id = {f["id"]: dict(f) for f in findings if f.get("id")}
    status = dict(finding_status or {})
    owners = {fid: followticket.owner_fields(o) for fid, o in (finding_owners or {}).items()
              if followticket.owner_fields(o)}
    meta = None
    live_desc = st.get("ticket_body") or ""
    for issue in issues or []:
        if issue.get("identifier") == ident:
            live_desc = issue.get("description") or live_desc
            meta = followticket.parse_meta(live_desc)
            break
    if meta is None and live_desc:
        meta = followticket.parse_meta(live_desc)
    desc_rows = {}
    for m in re.finditer(r"- \*\*(.+?)\*\* \(`([^`]+)`, ([^)]+)\) — fix: (.+)", live_desc):
        title, fid, kind, fix = m.group(1), m.group(2), m.group(3), m.group(4)
        desc_rows[fid] = {"id": fid, "title": title, "kind": kind, "fix": fix, "evidence": []}
    already_on_ticket = set()
    if meta:
        prior_status = dict(meta.get("finding_status") or {})
        prior_owners = meta.get("finding_owners") or {}
        ticket_key = meta.get("key")
        already_on_ticket = set(meta.get("finding_ids") or []) | set(prior_status)
        # Invariant (description-write owner): never demote a finding Linear already marks fixed.
        for fid, prior in prior_status.items():
            if prior == "fixed":
                status[fid] = "fixed"
        for fid in already_on_ticket:
            status.setdefault(fid, prior_status.get(fid, "open"))
            if fid not in by_id:
                # Prefer live finding details from stages or the ticket description over an ID stub.
                row = None
                for ost in all_stages():
                    for fr in ost.get("findings") or []:
                        if fr.get("id") == fid:
                            row = dict(fr)
                            break
                    if row:
                        break
                by_id[fid] = row or desc_rows.get(fid) or {
                    "id": fid, "title": fid, "kind": "ticket", "fix": f"see {ident}", "evidence": []}
                by_id[fid].update(status=status.get(fid, "open"), ticket=ident)
            if fid not in owners and isinstance(prior_owners.get(fid), dict):
                owners[fid] = followticket.owner_fields(prior_owners[fid])
            elif fid not in owners and ticket_key:
                # Metadata named another stage as ticket key — preserve it over this stage.
                owners[fid] = {"key": ticket_key, "stage": meta.get("stage"),
                               "branch": meta.get("branch"), "pr_url": meta.get("pr_url"),
                               "builder_provider": meta.get("builder_provider")}
    for f in by_id.values():
        status.setdefault(f["id"], f.get("status") or "open")
    for f in findings:
        fid = f.get("id")
        if not fid:
            continue
        # Duplicates / IDs already on the ticket stay under the ticket's existing owner.
        if f.get("status") == "duplicate" or f.get("duplicate_of") or fid in already_on_ticket:
            status.setdefault(fid, f.get("status") or status.get(fid) or "open")
            continue
        owners.setdefault(fid, stage_owner_snapshot(st))
        status[fid] = f.get("status") or status.get(fid) or "open"
    return list(by_id.values()), status, owners


def rewrite_ticket_description(st, ident, findings, quota=None, next_step="", validation=None,
                               finding_status=None, finding_owners=None, issues=None):
    """Description-write owner: under lock, require open ticket + matching repo, then merge/write."""
    want_repo = st.get("repo")
    with TicketIndex():
        live = followticket.fetch_open()
        require_ticket_repo(ident, want_repo, issues=live)
        merged, status, owners = ticket_description_payload(
            st, findings, ident, issues=live, finding_status=finding_status,
            finding_owners=finding_owners)
        st["ticket_meta_at"] = time.time()
        body = followticket.description(st, merged, quota, next_step, validation=validation,
                                        finding_status=status, finding_owners=owners)
        followticket.update_description(ident, body)
    for snapshot in (live, issues or []):
        for issue in snapshot:
            if issue.get("identifier") == ident:
                issue["description"] = body
    st["ticket_body"] = body[:6000]
    return body


def sync_deferral_tickets(st, findings, quota, next_step, note, issues=None):
    """Create/update Linear tickets for a deferral. Sets last_deferral only after sync succeeds.

    A failed update leaves pending_deferral_sync so a later drain can retry without treating the
    deferral as already recorded (which would suppress identical retries and leave metadata stale).
    """
    with TicketIndex() as index:
        # Live fetch once under the lock so index/ticket ids are checked against real repos.
        try:
            live = followticket.fetch_open()
        except followticket.LinearError:
            live = None
        by_ident = {i.get("identifier"): i for i in (live or [])}
        attached = set()
        for f in findings:
            want = f.get("repo") or st.get("repo")
            if f.get("ticket"):
                hit = by_ident.get(f["ticket"]) if live is not None else None
                if live is not None and (not hit or (want and _issue_repo(hit) != want)):
                    f.pop("ticket", None)
                    index.drop_finding(f["id"])
            if not f.get("ticket") and index.ticket(f["id"]):
                ident = index.ticket(f["id"])
                hit = by_ident.get(ident) if live is not None else None
                if live is not None and hit and (not want or _issue_repo(hit) == want):
                    f["ticket"] = ident
                    attached.add(ident)
                else:
                    # Stale/cross-repo index entry — discard and fall through to match/create.
                    index.drop_finding(f["id"])
        ticketed = {}
        for f in findings:
            if f.get("ticket"):
                ticketed.setdefault(f["ticket"], []).append(f)
        if note == st.get("last_deferral"):
            # Same stop: the ticket already says so (unless it is new to this stage).
            ticketed = {k: v for k, v in ticketed.items() if k in attached}
        owned = set(st.setdefault("owned_tickets", []))
        commented = set(st.setdefault("deferral_commented", []))
        try:
            st["ticket_meta_at"] = time.time()
            for ident, fs in ticketed.items():
                # Always rewrite metadata (owned or attached) so recovery sees every finding ID.
                body = rewrite_ticket_description(
                    st, ident, fs, quota=quota, next_step=next_step, issues=issues)
                ctag = f"{ident}:{note}"
                if ctag not in commented:
                    followticket.comment(
                        ident, "advance-roadmap follow-up deferred again for quota.\n\n" + body)
                    commented.add(ctag)
                    st["deferral_commented"] = sorted(commented)
            fresh = [f for f in findings if not f.get("ticket")]
            if fresh:
                # Recheck live Linear immediately before create — another machine may have filed.
                if live is None:
                    live = followticket.fetch_open()
                    by_ident = {i.get("identifier"): i for i in live}
                late = {}
                remaining = []
                for f in fresh:
                    hit = followticket.match(live, f)
                    if hit:
                        ident = hit["identifier"]
                        f["ticket"] = ident
                        index.add(f["id"], ident, st["key"])
                        late.setdefault(ident, []).append(f)
                    else:
                        remaining.append(f)
                for ident, fs in late.items():
                    body = rewrite_ticket_description(
                        st, ident, fs, quota=quota, next_step=next_step, issues=issues)
                    followticket.comment(
                        ident, "advance-roadmap follow-up deferred again for quota.\n\n" + body)
                if remaining:
                    ident = followticket.create(st, remaining, quota, next_step)
                    st.setdefault("owned_tickets", []).append(ident)
                    for f in remaining:
                        f["ticket"] = ident
                        index.add(f["id"], ident, st["key"])
                    live_after = followticket.fetch_open()
                    winner = None
                    for f in remaining:
                        for issue in live_after:
                            if issue.get("identifier") == ident:
                                continue
                            if followticket.match([issue], f):
                                winner = issue
                                break
                        if winner:
                            break
                    if winner:
                        win_id = winner["identifier"]
                        log(f"{st['key']}: concurrent ticket {win_id} won over just-created "
                            f"{ident}; attaching and closing the duplicate")
                        for f in remaining:
                            f["ticket"] = win_id
                            index.add(f["id"], win_id, st["key"])
                        index.drop_tickets({ident})
                        st["owned_tickets"] = [t for t in st["owned_tickets"] if t != ident]
                        body = rewrite_ticket_description(
                            st, win_id, remaining, quota=quota, next_step=next_step, issues=issues)
                        followticket.comment(
                            win_id,
                            "advance-roadmap follow-up deferred again for quota.\n\n" + body)
                        pending = st.setdefault("pending_cancel", [])
                        if not any(e.get("ticket") == ident for e in pending):
                            pending.append({"ticket": ident, "winner": win_id, "commented": False})
                        cancel_duplicate_tickets(st, issues=live_after)
                    else:
                        if issues is not None:
                            issues.append({
                                "identifier": ident,
                                "title": followticket.title_for(st, remaining),
                                "description": followticket.description(
                                    st, remaining, quota, next_step),
                                "labels": {"nodes": [{"name": st["repo"],
                                                      "parent": {"name": "repo"}}]}})
                        log(f"{st['key']}: filed {ident} for {len(remaining)} deferred finding(s)")
        except followticket.LinearError as e:
            st["pending_deferral_sync"] = {
                "note": note, "next_step": next_step,
                "quota": {k: quota.get(k) for k in ("provider", "reset_at", "reset_epoch", "cause", "role")
                          if quota.get(k) is not None},
            }
            report(st, "linear", f"could not ticket the quota deferral: {e}", next_step)
            return False
        st["last_deferral"] = note
        st.pop("pending_deferral_sync", None)
        return True


def defer(st, findings, quota, step, issues=None):
    """No provider under its gate: ticket the remaining findings (create or update) and wait.

    A ticket this creates goes into the local index and the drain's `issues` snapshot, so a later
    stage carrying the same finding — in this drain or a concurrent one — comments instead.
    """
    st.update(status="deferred", quota=quota, deferred_until=quota.get("reset_epoch"))
    # Quota pauses are not failed attempts — refund so a later post-merge retry isn't capped out.
    st["attempts"] = max(0, st.get("attempts", 1) - 1)
    next_step = next_step_text(st, step)
    for f in findings:
        f["status"] = "deferred"
    log(f"{st['key']}: deferred at {step} — {quota.get('provider')} {quota.get('cause') or ''} "
        f"resets {quota.get('reset_at')}")
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") == "1":
        sync_deferral_tickets(st, findings, quota, next_step, deferral_note(st, quota, step),
                              issues=issues)
    save(st)


def block(st, kind, reason, retry=True):
    """A non-quota stop: local report only (never a new ticket), plus progress on owned tickets.

    `retry` stages become `pending` (eligible next drain) up to MAX_ATTEMPTS; otherwise `blocked`.
    Once a PR is merged, keep retrying deploy/install — the attempt cap must not strand main.
    """
    next_step = next_step_text(st, (st.get("next") or {}).get("step", "build"))
    report(st, kind, reason, next_step)
    under_cap = st.get("attempts", 0) < MAX_ATTEMPTS or st.get("merged")
    st["status"] = ("pending" if retry and under_cap else BLOCKED)
    tickets = sorted({f["ticket"] for f in (st.get("findings") or []) if f.get("ticket")})
    if tickets and os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") == "1":
        body = (f"advance-roadmap follow-up progress on `{st['key']}`: "
                f"{followticket.render_validation(st.get('validation'))}. "
                + (f"Branch `{st['branch']}`" if st.get("branch") else "")
                + (f", PR {st['pr_url']}" if st.get("pr_url") else "")
                + f". Stopped: {reason}.")
        for ident in tickets:
            try:
                followticket.comment(ident, body)
            except followticket.LinearError as e:
                report(st, "linear", f"could not update {ident}: {e}")
    save(st)


def cancel_duplicate_tickets(st, issues=None):
    """Cancel concurrent-create losers; retried until Linear confirms Canceled."""
    done = set()
    for entry in st.get("pending_cancel") or []:
        ident = entry.get("ticket")
        if not ident:
            continue
        try:
            require_ticket_repo(ident, st.get("repo"), issues=issues)
            if not entry.get("commented"):
                followticket.comment(
                    ident,
                    f"Duplicate of `{entry.get('winner')}` (concurrent deferral create); canceling.")
                entry["commented"] = True
            followticket.linear("issue", "update", ident, "--state", "Canceled")
            done.add(ident)
        except followticket.LinearError as e:
            # Already absent from open issues means a prior cancel landed; clear the pending entry.
            if "not in open issues" in str(e):
                done.add(ident)
            else:
                report(st, "linear", f"could not cancel duplicate deferral ticket {ident}: {e}")
    st["pending_cancel"] = [e for e in st.get("pending_cancel") or [] if e.get("ticket") not in done]
    return done


def retry_cancel_duplicates(issues=None):
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1" or issues is None:
        return
    for snapshot in all_stages():
        if not snapshot.get("pending_cancel"):
            continue
        c = claim_stage(snapshot["key"])
        if not c:
            continue
        try:
            st = load(snapshot["key"]) or snapshot
            if st.get("pending_cancel"):
                cancel_duplicate_tickets(st, issues=issues)
                save(st)
        finally:
            c.close()


def retry_deferral_syncs():
    """Retry Linear description/comment sync a prior defer left pending after a transient failure."""
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        return
    for snapshot in all_stages():
        if not snapshot.get("pending_deferral_sync"):
            continue
        c = claim_stage(snapshot["key"])
        if not c:
            continue
        try:
            st = load(snapshot["key"]) or snapshot
            pending = st.get("pending_deferral_sync")
            if not pending:
                continue
            findings = [f for f in st.get("findings") or [] if f.get("status") == "deferred"]
            if not findings:
                findings = list(st.get("findings") or [])
            quota = pending.get("quota") or st.get("quota") or {}
            next_step = pending.get("next_step") or next_step_text(
                st, (st.get("next") or {}).get("step", "build"))
            note = pending.get("note") or deferral_note(
                st, quota, (st.get("next") or {}).get("step", "build"))
            if sync_deferral_tickets(st, findings, quota, next_step, note):
                log(f"{st['key']}: retried deferral ticket sync")
            save(st)
        finally:
            c.close()


def evidence_sync_pending(st):
    """True when an evidence comment and/or description rewrite is still unfinished.

    Duplicates count: dedupe may flip status to duplicate before the comment lands, and a
    retry must still finish that evidence even though repair is owned elsewhere.
    """
    posted = set(st.get("evidence_posted") or [])
    commented = set(st.get("evidence_commented") or [])
    if commented - posted:
        return True
    for f in st.get("findings") or []:
        if f.get("status") not in ("open", "deferred", "duplicate"):
            continue
        ident = f.get("ticket")
        if not ident or ident == st.get("recovered_from"):
            continue
        if f"{ident}:{f['id']}:{st['key']}" not in posted:
            return True
    return False


def refresh_ticket_sync_flags(st):
    """Keep pending_evidence_sync accurate after dedupe so parked stages still retry."""
    if evidence_sync_pending(st):
        st["pending_evidence_sync"] = True
    else:
        st.pop("pending_evidence_sync", None)


def retry_ticket_syncs(issues=None):
    """Retry dedupe/evidence for stages that parked with unfinished Linear sync.

    Runs at drain start (outside eligible()), so a parked repair stage still finishes metadata.
    When issues is None (Linear unread), leave pending flags alone.
    """
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        return
    if issues is None:
        return
    for snapshot in all_stages():
        if not (snapshot.get("pending_ticket_sync") or snapshot.get("pending_evidence_sync")
                or evidence_sync_pending(snapshot)):
            continue
        c = claim_stage(snapshot["key"])
        if not c:
            continue
        try:
            st = load(snapshot["key"]) or snapshot
            if not (st.get("pending_ticket_sync") or st.get("pending_evidence_sync")
                    or evidence_sync_pending(st)):
                continue
            dedupe(st, issues)
            unmatched = [f for f in st.get("findings") or []
                         if f.get("status") in ("open", "deferred") and not f.get("ticket")]
            if unmatched:
                st["pending_ticket_sync"] = True
            else:
                st.pop("pending_ticket_sync", None)
            refresh_ticket_sync_flags(st)
            save(st)
            if not st.get("pending_ticket_sync") and not st.get("pending_evidence_sync"):
                log(f"{st['key']}: finished pending ticket/evidence sync")
        finally:
            c.close()


# Deploy evidence must be structured (validation.deploy). "skipped" is never success.
DEPLOY_OK = frozenset({"success", "none"})


def _sha_match(a, b):
    """True when two git SHAs name the same commit (full or abbreviated, case-insensitive)."""
    if not a or not b:
        return False
    a, b = str(a).lower(), str(b).lower()
    return a == b or (len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a)))


def stage_repair_verified(ost):
    """Close-gate owner: merge_commit + checks + final clean review SHA match + deploy in DEPLOY_OK."""
    v = ost.get("validation") or {}
    merged = ost.get("merge_commit")
    if not merged:
        return False
    if v.get("checks") not in ("passed", "none"):
        return False
    reviews = list(v.get("reviews") or [])
    if not reviews:
        return False
    # Final review = highest round when present, else last entry (chronological append order).
    def _round(r):
        try:
            return int(r.get("round"))
        except (TypeError, ValueError):
            return None
    numbered = [(_round(r), i, r) for i, r in enumerate(reviews)]
    if any(n is not None for n, _, _ in numbered):
        final = max(numbered, key=lambda t: (-1 if t[0] is None else t[0], t[1]))[2]
    else:
        final = reviews[-1]
    if final.get("verdict") != "clean":
        return False
    review_sha = final.get("head") or final.get("sha") or final.get("reviewed_sha")
    if not _sha_match(merged, review_sha):
        return False
    return v.get("deploy") in DEPLOY_OK


def finding_verified_fixed(fid, current=None, owner_key=None):
    """A finding is fixed only after end-to-end repair verification.

    When metadata records an owner, only that owner's stage counts — the completing stage
    cannot approve another stage's finding.
    """
    seen = set()
    for ost in ([current] if current else []) + all_stages():
        key = ost.get("key")
        if key in seen:
            continue
        seen.add(key)
        if owner_key is not None and key != owner_key:
            continue
        if not stage_repair_verified(ost):
            continue
        for f in ost.get("findings") or []:
            if f.get("id") == fid and f.get("status") == "fixed":
                return True
    return False


def ticket_tracked_ids(ident, current=None, extras=()):
    """Every finding_id the ticket still tracks: index, metadata on sibling stages, and extras."""
    ids = set(extras)
    with TicketIndex() as index:
        for fid, e in index.entries.items():
            if e.get("ticket") == ident:
                ids.add(fid)
    for ost in ([current] if current else []) + all_stages():
        for f in ost.get("findings") or []:
            if f.get("ticket") == ident and f.get("id"):
                ids.add(f["id"])
        for owned in ost.get("owned_tickets") or []:
            if owned != ident:
                continue
            meta = followticket.parse_meta(ost.get("ticket_body") or "")
            if meta:
                ids.update(meta.get("finding_ids") or [])
                ids.update((meta.get("finding_status") or {}).keys())
    return ids


def stage_owner_snapshot(ost):
    """Owning stage/branch/PR for a finding still tracked by ost (may have no PR yet)."""
    return {"key": ost.get("key"), "stage": ost.get("stage"), "branch": ost.get("branch"),
            "pr_url": ost.get("pr_url"), "builder_provider": ost.get("builder_provider")}


def finding_owners_for_partial(st, status_map, prev_meta=None):
    """Owners for partial rewrite: credit this stage only for findings it verified."""
    prev_meta = prev_meta or {}
    prev_owners = prev_meta.get("finding_owners") or {}
    prev_ticket = {k: prev_meta.get(k) for k in ("key", "stage", "branch", "pr_url", "builder_provider")}
    index_keys = {}
    with TicketIndex() as index:
        for fid in status_map:
            entry = index.entries.get(fid) or {}
            if entry.get("key"):
                index_keys[fid] = entry["key"]
    by_stage = {ost["key"]: ost for ost in all_stages() if ost.get("key")}
    by_stage[st["key"]] = st
    owners = {}
    for fid, status in status_map.items():
        # Credit this stage only for findings it verified. Live-fixed rows owned elsewhere keep
        # their recorded owner so the close gate still checks the right stage.
        if status == "fixed" and finding_verified_fixed(fid, current=st, owner_key=st["key"]):
            owners[fid] = stage_owner_snapshot(st)
            continue
        prior = prev_owners.get(fid) if isinstance(prev_owners.get(fid), dict) else None
        owner_key = (prior or {}).get("key") or index_keys.get(fid) or prev_ticket.get("key")
        if owner_key and owner_key != st["key"] and owner_key in by_stage:
            owners[fid] = stage_owner_snapshot(by_stage[owner_key])
        elif prior and prior.get("key") and prior["key"] != st["key"]:
            owners[fid] = followticket.owner_fields(prior)
        elif owner_key and owner_key != st["key"]:
            owners[fid] = {"key": owner_key, "stage": prev_ticket.get("stage") or st.get("stage"),
                           "branch": None, "pr_url": None,
                           "builder_provider": (prior or {}).get("builder_provider")
                           or prev_ticket.get("builder_provider")}
        elif status == "fixed":
            # Fixed in Linear but not by us and no prior owner — keep ticket-level key if any.
            owners[fid] = followticket.owner_fields(prior) if prior else {
                "key": prev_ticket.get("key") or st["key"],
                "stage": prev_ticket.get("stage") or st.get("stage"),
                "builder_provider": prev_ticket.get("builder_provider"),
            }
        else:
            snap = stage_owner_snapshot(st)
            snap.update(branch=None, pr_url=None)
            owners[fid] = snap
    return owners


def note_partial_progress(st, ident, status_map, issues=None, already_commented=False, on_commented=None):
    """Comment partial progress and rewrite metadata; never mark Done while findings remain open.

    Description rewrite goes through ticket_description_payload so Linear's current IDs/owners
    and any already-fixed statuses are preserved (same owner as sync_deferral_tickets / dedupe).
    Comment and rewrite are tracked apart via already_commented / on_commented so a failed
    rewrite retry does not re-post the comment.
    """
    by_id = {}
    for ost in [st] + all_stages():
        for f in ost.get("findings") or []:
            if f.get("id") in status_map:
                by_id.setdefault(f["id"], dict(f))
    # Prefer a live Linear snapshot; fall back to fetch so we never rewrite from stale ticket_body alone.
    if issues is None:
        try:
            issues = followticket.fetch_open()
        except followticket.LinearError:
            issues = None
    prev_meta = None
    for issue in issues or []:
        if issue.get("identifier") == ident:
            prev_meta = followticket.parse_meta(issue.get("description") or "")
            break
    if prev_meta is None:
        prev_meta = followticket.parse_meta(st.get("ticket_body") or "")
    # Never demote a finding another machine already marked fixed in Linear.
    live_status = (prev_meta or {}).get("finding_status") or {}
    status_map = dict(status_map)
    for fid, prior in live_status.items():
        if prior == "fixed":
            status_map[fid] = "fixed"
    findings = []
    for fid, status in status_map.items():
        row = by_id.get(fid) or {"id": fid, "title": fid, "kind": "ticket", "fix": f"see {ident}",
                                 "evidence": []}
        findings.append({**row, "status": status, "ticket": ident})
    remaining = sorted(fid for fid, s in status_map.items() if s != "fixed")
    fixed_ids = sorted(fid for fid, s in status_map.items() if s == "fixed")
    # Comment text must use the reconciled status map (after live fixed merge).
    body = (f"advance-roadmap follow-up `{st['key']}` verified partial progress on `{ident}`: "
            f"fixed {', '.join(f'`{i}`' for i in fixed_ids) or '(none)'}; "
            f"still open {', '.join(f'`{i}`' for i in remaining)}. "
            f"PR {st.get('pr_url')}, merge `{st.get('merge_commit')}`; "
            f"{followticket.render_validation(st.get('validation'))}. "
            "Ticket stays open until every tracked finding_id is verified fixed.")
    owners = finding_owners_for_partial(st, status_map, prev_meta)
    if not already_commented:
        require_ticket_repo(ident, st.get("repo"), issues=issues)
        followticket.comment(ident, body)
        if on_commented:
            on_commented()
    rewrite_ticket_description(
        st, ident, findings, quota=st.get("quota"),
        next_step=(f"repair remaining finding(s): {', '.join(remaining)}"
                   if remaining else "close when ready"),
        validation=st.get("validation"), finding_status=status_map, finding_owners=owners,
        issues=issues)
    log(f"{st['key']}: partial progress on {ident} (fixed {len(fixed_ids)}/{len(status_map)})")


def finish(st, findings):
    for f in findings:
        f["status"] = "fixed"
    st.update(status=DONE, quota=None, deferred_until=None, next=None)
    st["close_note"] = (f"Fixed by advance-roadmap follow-up `{st['key']}`: PR {st.get('pr_url')}, merge "
                        f"`{st.get('merge_commit')}`, deploy {(st.get('validation') or {}).get('deploy')}; "
                        f"{followticket.render_validation(st.get('validation'))}.")
    close, partial = [], []
    for ident in sorted({f["ticket"] for f in findings if f.get("ticket")}):
        tracked = ticket_tracked_ids(ident, current=st,
                                     extras={f["id"] for f in findings if f.get("ticket") == ident})
        status_map = {fid: ("fixed" if finding_verified_fixed(fid, current=st) else "open")
                      for fid in tracked}
        if tracked and all(s == "fixed" for s in status_map.values()):
            close.append(ident)
        else:
            partial.append((ident, status_map))
    st["pending_close"] = [{"ticket": ident, "commented": False} for ident in close]
    st["pending_partial"] = [{"ticket": ident, "status": status_map, "done": False,
                              "commented": False}
                             for ident, status_map in partial]
    apply_partial_progress(st, findings)
    close_tickets(st, issues=None)  # ticket_ready_to_close still reads ticket_body / index
    save(st)


def apply_partial_progress(st, findings=None, issues=None):
    """Persist partial-progress comments/metadata; retried by later drains like pending_close.

    Each attempt recomputes status from live Linear metadata and verified stage state so a retry
    cannot rewrite a finding from fixed back to open after another stage completed it. Comment
    completion is stored on the pending entry so a failed rewrite does not re-comment.
    """
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        st["pending_partial"] = []
        return
    for entry in st.get("pending_partial") or []:
        if entry.get("done"):
            continue
        ident = entry["ticket"]
        try:
            ok, meta_ids = load_ticket_meta_ids(ident, issues)
            if not ok:
                report(st, "partial-unread",
                       f"could not read Linear metadata for {ident}; retaining pending_partial",
                       "the next drain retries after a successful ticket fetch")
                continue
            tracked = ticket_tracked_ids(ident, current=st, extras=meta_ids)
            live_status = {}
            for issue in issues or []:
                if issue.get("identifier") == ident:
                    live_status = (followticket.parse_meta(issue.get("description") or "")
                                   or {}).get("finding_status") or {}
                    break
            # No owner_key here: a completing stage may verify a finding it just fixed before
            # finding_owners is rewritten. Close still enforces recorded owners. Never demote
            # a finding Linear already marks fixed (description-write invariant).
            status_map = {}
            for fid in tracked:
                if live_status.get(fid) == "fixed" or finding_verified_fixed(fid, current=st):
                    status_map[fid] = "fixed"
                else:
                    status_map[fid] = "open"
            entry["status"] = status_map

            def _mark_commented():
                entry["commented"] = True

            note_partial_progress(
                st, ident, status_map, issues=issues,
                already_commented=bool(entry.get("commented")),
                on_commented=_mark_commented)
            entry["done"] = True
        except followticket.LinearError as e:
            report(st, "linear", f"could not record partial progress on {ident}: {e}",
                   "the next drain retries the partial-progress update")
    st["pending_partial"] = [e for e in st.get("pending_partial") or [] if not e.get("done")]


def load_ticket_meta_ids(ident, issues=None):
    """(ok, ids) from the ticket's Linear metadata block.

    ok is True only after a successful read of that ticket's metadata. A missing snapshot, a failed
    fetch, or an issue without the follow-up block is not ok — callers must retain pending_close.
    """
    if issues is None:
        if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
            return False, set()
        try:
            issues = followticket.fetch_open()
        except followticket.LinearError:
            return False, set()
    for issue in issues:
        if issue.get("identifier") != ident:
            continue
        meta = followticket.parse_meta(issue.get("description") or "")
        if not meta:
            return False, set()
        ids = set(meta.get("finding_ids") or []) | set((meta.get("finding_status") or {}).keys())
        return True, ids
    return False, set()  # not in the open-issue snapshot


def ticket_close_verdict(ident, current=None, issues=None):
    """Close-gate owner: ready|incomplete|unread. Owner-verified findings; ticket repo matches stage."""
    ok, meta_ids = load_ticket_meta_ids(ident, issues)
    if not ok:
        return "unread"
    tracked = ticket_tracked_ids(ident, current=current, extras=meta_ids)
    if not tracked:
        return "incomplete"
    owners = {}
    ticket_repo = None
    ticket_key = None
    for issue in issues or []:
        if issue.get("identifier") == ident:
            meta = followticket.parse_meta(issue.get("description") or "") or {}
            owners = meta.get("finding_owners") or {}
            ticket_repo = meta.get("repo") or followticket.repo_label(issue)
            ticket_key = meta.get("key")
            break
    want_repo = (current or {}).get("repo")
    if not want_repo or ticket_repo != want_repo:
        return "incomplete"
    for fid in tracked:
        per = owners.get(fid) if isinstance(owners.get(fid), dict) else None
        owner_key = (per or {}).get("key") or ticket_key
        if not finding_verified_fixed(fid, current=current, owner_key=owner_key):
            return "incomplete"
    return "ready"


def ticket_ready_to_close(ident, current=None, issues=None):
    """True only when Linear metadata was read and every tracked finding is verified fixed."""
    return ticket_close_verdict(ident, current=current, issues=issues) == "ready"


def close_tickets(st, issues=None):
    """Persisted in pending_close so a Linear failure is retried by every later drain.

    The closing comment and the move to Done are tracked apart, so a retry never posts twice.
    Under the tickets lock, fetches current Linear metadata immediately before mark_done.
    """
    closed = set()
    dropped = set()
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        return closed
    for entry in st.get("pending_close") or []:
        ident = entry["ticket"]
        try:
            with TicketIndex() as index:
                # Fresh fetch under the same lock as description rewrites — ignore drain snapshot.
                live = followticket.fetch_open()
                verdict = ticket_close_verdict(ident, current=st, issues=live)
                if verdict == "unread":
                    report(st, "close-unread",
                           f"could not read Linear metadata for {ident}; retaining pending_close",
                           "the next drain retries after a successful ticket fetch")
                    continue  # retain
                if verdict != "ready":
                    report(st, "close-recheck",
                           f"refusing to Done {ident}: not every tracked finding is verified fixed",
                           "partial progress or a sibling still owns open work")
                    dropped.add(ident)
                    continue
                if not entry.get("commented"):
                    followticket.comment(ident, st.get("close_note")
                                         or f"Fixed by advance-roadmap follow-up `{st['key']}`.")
                    entry["commented"] = True
                followticket.mark_done(ident)
                index.drop_tickets({ident})
                closed.add(ident)
                log(f"{st['key']}: closed {ident}")
                # Drop from the drain's shared open-issue snapshot so a later stage cannot
                # legacy-title-match the closed ticket and fail a description rewrite.
                if issues is not None:
                    issues[:] = [i for i in issues if i.get("identifier") != ident]
        except followticket.LinearError as e:
            report(st, "linear", f"merged and deployed, but could not close {ident}: {e}",
                   "the next drain retries closing it")
    st["pending_close"] = [e for e in st.get("pending_close") or []
                           if e["ticket"] not in closed and e["ticket"] not in dropped]
    return closed


def retry_closes(issues=None):
    """Close tickets an earlier drain verified but could not close in Linear."""
    closed = set()
    # None means "fetch inside close_tickets / load_ticket_meta_ids"; never substitute [].
    for snapshot in all_stages():
        if not snapshot.get("pending_close") and not snapshot.get("pending_partial"):
            continue
        c = claim_stage(snapshot["key"])
        if not c:
            continue
        try:
            st = load(snapshot["key"]) or snapshot
            if st.get("pending_partial"):
                apply_partial_progress(st, issues=issues)
                save(st)
            if st.get("pending_close"):
                closed |= close_tickets(st, issues=issues)
                save(st)
        finally:
            c.close()
    return closed


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


# ── process / drain (ANALYZE + REPORT + tickets + repair) ─────────────────────

def process(st, issues=None):
    """ANALYZE, dedupe tickets, then repair (when enabled) or park/report under the stage claim.

    issues=None means Linear could not be read — keep pending_ticket_sync so a later drain retries.
    issues=[] means a successful read found no open tickets.
    """
    linear_unread = issues is None
    live = [] if issues is None else issues
    st.pop("waiting_reason", None)
    # Preserve an in-flight repair step across recoveries; otherwise start at analyze.
    nxt = st.get("next") if isinstance(st.get("next"), dict) else None
    if not (nxt and nxt.get("step") in ("build", "review", "land")):
        nxt = {"step": "analyze"}
    if not cas_status(st, READY, RUNNING, attempts=st.get("attempts", 0) + 1, next=nxt):
        return
    if not st.get("scanned"):
        scan(st)
        if st.get("status") != RUNNING:
            return
        save(st)
    if st.get("status") != RUNNING:
        return
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") == "1":
        if linear_unread:
            st["pending_ticket_sync"] = True
        else:
            dedupe(st, live)
            unmatched = [f for f in st.get("findings") or []
                         if f.get("status") in ("open", "deferred") and not f.get("ticket")]
            if unmatched:
                st["pending_ticket_sync"] = True
            else:
                st.pop("pending_ticket_sync", None)
            refresh_ticket_sync_flags(st)
        if st.get("status") != RUNNING:
            return
        save(st)
    fs = st.get("findings") or []
    quiet = st.get("non_findings") or []
    todo = [f for f in fs if f.get("status") in ("open", "deferred")]
    open_n = sum(1 for f in fs if f.get("status") == "open")
    if repair_enabled() and (todo or awaits_repair(st)):
        step = (st.get("next") or {}).get("step")
        if not todo and awaits_repair(st):
            # Resume mid-flight without re-owning another stage's duplicates.
            owned = [f for f in fs if f.get("status") in ("open", "deferred")]
            if owned:
                todo = owned
            elif step in ("land", "review"):
                # Findings already fixed/duplicate; finish land/review without a builder brief.
                todo = [f for f in fs if f.get("status") != "duplicate"] or list(fs)
            else:
                report(st, "repair-empty", "no owned open findings left to repair")
                cas_status(st, RUNNING, DONE, next=None, reported_at=now_iso())
                return
        repo_dir, why = safe_repo(st.get("repo"))
        if not repo_dir:
            block(st, "unsafe-repo", why, retry=False)
            return
        busy = ("its builder from an earlier drain is still running" if builder_alive(st["key"])
                else "a worker or follow-up builder is still running in it"
                if st.get("repo") in shared.inflight_repos(ROOT) else None)
        lock = None if busy else take_repo_lock(st.get("repo"))
        if busy or not lock or not lock.ok:
            # Not an attempt: someone else is in the repo. Wait so --wait-s keeps polling.
            why = busy or "its repo lock is held"
            st["attempts"] = max(0, st.get("attempts", 1) - 1)
            st["waiting_reason"] = why
            cas_status(st, RUNNING, WAITING, attempts=st["attempts"])
            log(f"{st['key']}: {st.get('repo')} is busy ({why}); waiting")
            if lock and lock.ok:
                lock.close()
            return
        try:
            repair(st, repo_dir, todo, issues)
        finally:
            lock.close()
        return
    if todo and not repair_enabled():
        if cas_status(st, RUNNING, "deferred", reported_at=now_iso(), open_findings=open_n,
                      next={"step": "build", "round": 1}):
            log(f"{st['key']}: reported {len(fs)} finding(s), {len(quiet)} non-finding(s); "
                f"parked for repair ({st.get('mode') or 'tickets'})")
        return
    if cas_status(st, RUNNING, DONE, next=None, reported_at=now_iso(), open_findings=open_n):
        log(f"{st['key']}: reported {len(fs)} finding(s), {len(quiet)} non-finding(s) "
            f"({st.get('mode') or 'tickets'})")


def repair(st, repo_dir, todo, issues=None):
    """Builder → independent review → land loop. Mutates st under the caller's stage claim."""
    st.setdefault("validation", {"reviews": []})
    st.setdefault("sessions", [])
    nxt = st.get("next") or {"step": "build", "round": 1}
    if nxt.get("step") == "analyze":
        nxt = {"step": "build", "round": 1}
    while True:
        st["next"] = nxt
        save(st)
        if st.get("status") != RUNNING:
            return
        step, rnd = nxt["step"], nxt.get("round", 1)
        if step == "build":
            kind, detail = feasibility(st, "build")
            if kind == "unknown":
                return block(st, "unknown-usage", detail)
            if kind == "quota":
                return defer(st, todo, detail, f"builder round {rnd}", issues)
            kind, detail = run_builder(st, repo_dir, todo, rnd, detail, nxt.get("review_text"))
            if kind == "quota":
                return defer(st, todo, detail, f"builder round {rnd}", issues)
            if kind == "unknown":
                return block(st, "unknown-usage", detail)
            if kind == "self-merged":
                # Merge owner is land(); a builder that shipped is a hard stop (never retry).
                return block(st, "builder-self-merged", detail, retry=False)
            if kind != "ok":
                return block(st, "builder", detail, retry=True)
            nxt = {"step": "review", "round": rnd}
            # Persist before the next loop save so a killed drain cannot re-enter build
            # against already-consumed builder artifacts.
            st["next"] = nxt
            save(st)
        elif step == "review":
            if st.get("builder_provider") not in followrepair.REVIEWERS:
                return block(
                    st, "unknown-builder",
                    f"which provider built {st.get('branch') or 'the branch'} is not recorded, so no "
                    "independent reviewer can be chosen; a human must name it (builder_provider in "
                    "state.json) or close the PR", retry=False)
            kind, detail = feasibility(st, "review", st["builder_provider"])
            if kind == "unknown":
                return block(st, "unknown-usage", detail)
            if kind == "quota":
                return defer(st, todo, detail, f"review round {rnd}", issues)
            verdict, text = run_review(st, repo_dir, rnd)
            if verdict == "unavailable":
                reviews = (st.get("validation") or {}).get("reviews") or []
                limited = reviews[-1].get("limited") if reviews else None
                # Same exclusion review.sh applied: a prior builder's gate is irrelevant.
                eligible = followrepair.eligible_reviewers(
                    st["builder_provider"], followrepair.prior_builders(st))
                q = followrepair.reviewer_quota(eligible, limited)
                if q:
                    return defer(st, todo, q, f"review round {rnd}", issues)
                return block(st, "review-unavailable",
                             "every reviewer was at capacity or missing")
            if verdict == "error":
                return block(st, "review", f"review.sh failed: {text}")
            if verdict == "clean":
                nxt = {"step": "land", "round": rnd}
            elif rnd < MAX_ROUNDS:
                nxt = {"step": "build", "round": rnd + 1, "review_text": text}
            else:
                return block(
                    st, "review",
                    f"the review still reports findings after {MAX_ROUNDS} rounds; "
                    f"branch {st.get('branch')} and PR {st.get('pr_url')} are left for a human",
                    retry=False)
        else:
            ok, reason, retry = land(st, repo_dir, todo)
            if not ok:
                return block(st, "land", reason, retry=retry)
            return finish(st, todo)


def order(st):
    # Prefer stages with unfinished ticket/repair work, then cleanups, oldest first.
    unfinished = bool(st.get("branch") or st.get("pr_url")
                      or any(f.get("ticket") for f in st.get("findings") or []))
    return (0 if unfinished else 1, 0 if st["stage"] == STAGES["cleanup"] else 1,
            st.get("created_at") or "")


def drain(repo=None, origin=None, stage=None, wait_s=0.0):
    """Drain ready stages. Every status transition is claim → reload → eligibility → act → save.

    When --origin is set, only that origin's stages are processed (never siblings' stages).
    """
    STATE.mkdir(parents=True, exist_ok=True)
    recover_interrupted(repo=repo, origin=origin)
    issues = recover_tickets()  # None = Linear read failed; [] = no open issues
    # Pass the real snapshot (including None) into close/ticket retries — never substitute [].
    retry_closes(issues=issues)
    retry_deferral_syncs()
    retry_cancel_duplicates(issues=issues)
    retry_ticket_syncs(issues=issues)
    deadline = time.time() + wait_s
    while True:
        waiting, progressed = [], False
        for st in sorted(all_stages(), key=order):
            if repo and st.get("repo") != repo or stage and st["stage"] != STAGES[stage]:
                continue
            if origin and st.get("origin") != origin:
                continue
            if not eligible(st):
                continue
            c = claim_stage(st["key"])
            if not c:
                continue   # another drain holds this stage
            try:
                st = load(st["key"]) or st
                # Recheck origin under the claim — a concurrent rewrite must not leak in.
                if origin and st.get("origin") != origin:
                    continue
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
                    # issues may be None (unread) — process must not treat that as "no tickets".
                    process(st, issues)
                except Exception as e:  # one broken stage must never stop the rest
                    report(st, "crash", f"{type(e).__name__}: {e}")
                    # CAS: only demote if we still own the running transition.
                    # A merged stage must stay retryable until deploy/install finish
                    # (same rule as block()'s merged bypass of the attempt cap).
                    live = load(st["key"]) or st
                    dest = (RETRYABLE if (live.get("attempts", 0) < MAX_ATTEMPTS or live.get("merged"))
                            else BLOCKED)
                    cas_status(st, RUNNING, dest)
                # Repair may release back to waiting (repo lock / live builder) mid-claim.
                live = load(st["key"]) or st
                if live.get("status") == WAITING:
                    waiting.append(f"{live['key']}: {live.get('waiting_reason') or 'waiting'}")
                else:
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
                     "mode": st.get("mode") or "tickets",
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
