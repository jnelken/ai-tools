#!/usr/bin/env python3
"""advance-roadmap's follow-up runner: after work ships, repair what its logs prove went wrong.

Two stages, each a durable record under $ROOT/followups/<key>/ (runtime state, never ~/.ai-tools):

  project-cleanup    one per shipped item (`shipped` / `shipped-deploy-failed`), once its builder
                     and reviewers have exited and nothing else holds the repo. Corpus: that item's
                     run dir — worker result, review log, the review markers its merge left, deploy.
                     Repairs only evidenced, project-specific bugs. An item that did not ship is
                     recoverable work for the next run, not a cleanup trigger.
  tooling-hardening  one per batch / lone run / conductor session, once every run and every
                     cleanup stage it started has finished. Corpus: those runs' orchestration,
                     routing, review and reporting records. Findings are ai-tools' own.

Findings come from lib/followscan.py's recognition tests (each cites a file and a quote) plus an
optional read-only analyst call whose findings are kept only when their quotes are really in the
corpus. Repairs are delegated: worker.sh builds on a follow-up branch in its own workspace (Superset,
or a plain worktree outside ~/.claude when ADVANCE_ROADMAP_SUPERSET=0; lib/followrepair.py), stopping at an open PR;
review.sh has a second provider review it, up to 3 rounds; this runner merges only when the PR's
checks pass and the last review is clean, then verifies the deploy. It never merges past
unresolved findings or an unavailable review — the branch/PR stays for the next drain.

Usage is re-checked (usage.py gates) before every builder and reviewer call. When no provider is
under its gate for a step, the stage is deferred and its remaining findings go on a Linear ticket
(lib/followticket.py) with the reset time and exact next step. Unknown usage and every other
blocker go to $ROOT/followups/report.jsonl, never to a new ticket. Before any new repair, open
follow-up tickets from earlier runs are fetched (all pages) and reconciled, and their branches
resumed first.

  followup.py hook item-done --stamp S [--run-dir D] [--origin O]   queue S's cleanup (idempotent)
  followup.py hook run-end --origin O [--stamp S ...] [--run-dir D ...]   queue O's tooling stage
                                                                   (--run-dir: a run outside $ROOT/runs)
  followup.py drain [--repo R] [--origin O] [--wait-s N]           run what is ready, prior work first
  followup.py scan --stage cleanup|tooling (--stamp S | --origin O --stamp S ...)   dry run, no repairs
  followup.py status [--origin O] [--json]

Follow-up builders and reviewers run with ADVANCE_ROADMAP_FOLLOWUP=1; a hook seeing it does nothing,
so a follow-up never starts follow-ups.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
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
from followrepair import (FOLLOWUP_ENV, MAX_ROUNDS, POLL_S, ROOT, STATE, builder_alive,  # noqa: E402
                          feasibility, gate_one, land, log, now_iso, quota_record, read_json, repo_path,
                          run_builder, run_review, safe_repo, stage_dir, write_json)

SHIPPED = ("shipped", "shipped-deploy-failed")
STAGES = {"cleanup": "project-cleanup", "tooling": "tooling-hardening"}
MAX_ATTEMPTS = int(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_MAX_ATTEMPTS", "3"))
STALE_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_STALE_H", "12")) * 3600
CODEX = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
CLAUDE = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")


# ── durable stage state ──────────────────────────────────────────────────────

def load(key):
    st = read_json(stage_dir(key) / "state.json")
    return st if isinstance(st, dict) else None


def save(st):
    st["updated_at"] = now_iso()
    write_json(stage_dir(st["key"]) / "state.json", st)


def all_stages():
    out = []
    for f in sorted(STATE.glob("*/state.json")):
        st = read_json(f)
        if isinstance(st, dict) and st.get("key"):
            out.append(st)
    return sorted(out, key=lambda s: s.get("created_at") or "")


def new_stage(key, stage, repo, origin, **extra):
    """Create the stage's directory exclusively: a second hook for the same key is a no-op."""
    try:
        stage_dir(key).mkdir(parents=True)
    except FileExistsError:
        return None
    st = {"key": key, "stage": stage, "repo": repo, "origin": origin, "status": "pending",
          "created_at": now_iso(), "attempts": 0, "findings": [], "non_findings": [], "blockers": [],
          "usage_snapshots": [], "sessions": [], "validation": {"reviews": []}, "branch": None,
          "pr_url": None, "merge_commit": None, **extra}
    save(st)
    return st


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
    """The durable local follow-up report: everything that is not a quota deferral."""
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
    run_dir = Path(run_dir) if run_dir else ROOT / "runs" / stamp
    if (run_dir / "followup-child").exists():
        print(f"{stamp} is a follow-up's own run: no follow-ups (recursion suppressed)")
        return 0
    result = read_json(run_dir / "worker-result.json") or {}
    if result.get("outcome") not in SHIPPED:
        print(f"{stamp}: outcome {result.get('outcome')!r} did not ship — recoverable work, not a cleanup")
        return 0
    repo = result.get("repo") or (read_json(run_dir / "orchestrator-result.json") or {}).get("repo")
    st = new_stage(f"cleanup-{stamp}", STAGES["cleanup"], repo, origin, stamp=stamp,
                   item=result.get("item"), run_dir=str(run_dir), merge_commit_item=result.get("merge_commit"))
    print(f"queued cleanup-{stamp} ({repo})" if st else f"cleanup-{stamp} already queued")
    return 0


def hook_run_end(origin, stamps, run_dirs=()):
    """Queue the tooling pass. `run_dirs` are runs outside $ROOT/runs (a conductor session's), each
    named for its stamp; such a run counts as finished once its worker-result.json exists."""
    if recursion_suppressed():
        return 0
    external = {}
    for d in run_dirs:
        path = Path(d).expanduser().resolve()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", path.name):
            print(f"run-end: {d}: a run dir must be named for its stamp", file=sys.stderr)
            return 2
        external[path.name] = str(path)
    children = list(dict.fromkeys([*stamps, *external]))
    st = new_stage(f"tooling-{origin}", STAGES["tooling"], followscan.TOOLING_REPO, origin,
                   children=children, run_dirs=external)
    print(f"queued tooling-{origin} ({len(children)} run(s))" if st else f"tooling-{origin} already queued")
    return 0


# ── readiness ────────────────────────────────────────────────────────────────

def repo_lock_files(repo):
    base = ROOT / "repo-locks"
    return [base / f"{repo}.lock", *sorted(base.glob(f"{glob_escape(repo)}.t*.lock"))]


def glob_escape(s):
    return re.sub(r"([*?\[])", r"[\1]", s)


def quiescent(repo):
    """(True, "") when no lane, thread, timed-out worker or other follow-up holds the repo.

    Never call this while holding the repo's lock yourself: probing it opens and closes the same
    file, and closing any descriptor drops every fcntl lock this process holds on it."""
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


def take_repo_lock(repo):
    return Claim(ROOT / "repo-locks" / f"{repo}.lock") if repo else None


def ready(st):
    if st["stage"] == STAGES["cleanup"]:
        return quiescent(st["repo"])
    children = st.get("children") or []
    rows = shared.run_records(set(children))
    external = st.get("run_dirs") or {}
    running = [s for s in children if s not in rows
               and not (s in external and (Path(external[s]) / "worker-result.json").exists())]
    stale = age_s(st) >= STALE_S
    if running and not stale:
        return False, f"{len(running)} run(s) of {st['origin']} have not recorded a result"
    if running:
        st["unrecorded_children"] = running   # a crashed run never records; analyze what did
    for other in all_stages():
        if other["stage"] == STAGES["cleanup"] and other.get("stamp") in children \
                and other.get("status") in ("pending", "running") and not stale:
            return False, f"{other['key']} has not finished"
    return True, ""


def age_s(st):
    try:
        return time.time() - datetime.fromisoformat(st["created_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def eligible(st, now=None):
    now = time.time() if now is None else now
    if st.get("status") == "pending":
        return True
    if st.get("status") == "deferred":
        until = st.get("deferred_until")
        return not until or until <= now
    return False


def recover_interrupted():
    """A stage left `running` whose claim nobody holds was killed mid-step: it resumes next."""
    for st in all_stages():
        if st.get("status") != "running":
            continue
        if builder_alive(st["key"]):
            continue   # its Superset builder outlived the drain; never start a second beside it
        c = claim_stage(st["key"])
        if not c:
            continue
        try:
            st = load(st["key"]) or st
            st["status"] = "pending"
            st.setdefault("blockers", []).append({"at": now_iso(), "kind": "interrupted",
                                                  "reason": "the previous drain stopped mid-stage; resuming"})
            save(st)
            log(f"{st['key']}: recovered an interrupted stage (next step {st.get('next')})")
        finally:
            c.close()


# ── the local finding → ticket index ─────────────────────────────────────────

class TicketIndex:
    """followups/tickets.json under a blocking lock: which open ticket tracks each finding id.

    The drain's Linear snapshot is read once, so a ticket filed mid-drain — by this drain or a
    concurrent one — is only visible here. Hold it across the check-then-create."""

    def __init__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.fh = open(STATE / "tickets.lock", "a")
        fcntl.lockf(self.fh, fcntl.LOCK_EX)
        doc = read_json(STATE / "tickets.json")
        self.entries = doc if isinstance(doc, dict) else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        write_json(STATE / "tickets.json", self.entries)
        self.fh.close()

    def ticket(self, fid):
        return (self.entries.get(fid) or {}).get("ticket")

    def add(self, fid, ident, key):
        self.entries[fid] = {"ticket": ident, "key": key, "at": time.time()}

    def drop_tickets(self, idents):
        self.entries = {k: e for k, e in self.entries.items() if e.get("ticket") not in idents}


# ── prior tickets ────────────────────────────────────────────────────────────

def recover_tickets():
    """Open follow-up tickets from earlier runs (any machine), reconciled into local state.

    Returns every open issue (for deduping fresh findings), or None when Linear could not be read —
    which is recorded locally and never stops a repair."""
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
        index.entries = {k: e for k, e in index.entries.items()
                         if e.get("ticket") in open_ids or (e.get("at") or 0) >= fetched_at}
    for issue, meta in followticket.followup_tickets(issues):
        ident = issue.get("identifier")
        key = meta.get("key")
        if not key or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            continue
        st = load(key)
        if st is None:
            st = new_stage(key, meta.get("stage") or STAGES["cleanup"], meta.get("repo") or followticket.repo_label(issue),
                           meta.get("origin"), stamp=meta.get("stamp"), item=meta.get("item"),
                           recovered_from=ident, ticket_body=(issue.get("description") or "")[:6000])
            if st is None:
                continue
            st.update(status="deferred", branch=meta.get("branch"), pr_url=meta.get("pr_url"), scanned=True,
                      builder_provider=meta.get("builder_provider"),
                      findings=[{"id": fid, "stage": st["stage"], "repo": st["repo"], "kind": "ticket",
                                 "title": issue.get("title"), "fix": f"see {ident}", "status": "deferred",
                                 "ticket": ident, "evidence": [{"source": ident, "quote": issue.get("title")}]}
                                for fid in meta.get("finding_ids") or []],
                      next={"step": "review" if meta.get("pr_url") else "build", "round": 1})
            log(f"{key}: rebuilt from {ident} (no local state on this machine)")
            save(st)
            continue
        changed = False
        if not st.get("builder_provider") and meta.get("builder_provider"):
            st["builder_provider"] = meta["builder_provider"]
            changed = True
        for f in st.get("findings") or []:
            if f["id"] in (meta.get("finding_ids") or []) and f.get("ticket") != ident:
                f["ticket"] = ident
                changed = True
        if changed:
            save(st)
    return issues


def dedupe(st, issues):
    """Attach existing tickets to fresh findings and fold this run's evidence into them once."""
    if not issues:
        return
    posted = set(st.setdefault("evidence_posted", []))
    for f in st["findings"]:
        if f.get("status") not in ("open", "deferred"):
            continue
        issue = f.get("ticket") and next((i for i in issues if i.get("identifier") == f["ticket"]), None) \
            or followticket.match(issues, f)
        if not issue:
            continue
        f["ticket"] = issue["identifier"]
        owner = (followticket.parse_meta(issue.get("description")) or {}).get("key")
        other = owner and owner != st["key"] and load(owner)
        if other and other.get("status") not in ("done", "blocked", "skipped"):
            f.update(status="duplicate", duplicate_of=owner)   # the older stage repairs it
        tag = f"{issue['identifier']}:{f['id']}:{st['key']}"
        if tag in posted or issue.get("identifier") == st.get("recovered_from"):
            continue
        body = (f"advance-roadmap follow-up `{st['key']}` saw this again:\n\n"
                + followticket.render_findings([f]))
        try:
            followticket.comment(issue["identifier"], body)
            posted.add(tag)
        except followticket.LinearError as e:
            report(st, "linear", f"could not add evidence to {issue['identifier']}: {e}")
    st["evidence_posted"] = sorted(posted)


# ── scanning ─────────────────────────────────────────────────────────────────

def scan(st):
    if st["stage"] == STAGES["cleanup"]:
        run_dir = Path(st.get("run_dir") or ROOT / "runs" / st["stamp"])
        repo_dir = repo_path(st["repo"])
        findings, quiet = followscan.cleanup_scan(run_dir, repo_dir if repo_dir and repo_dir.is_dir() else None)
        files = [p for p in (run_dir / n for n in ("worker-result.json", "reviews.jsonl", "worker-output.log"))
                 if p.exists()]
    else:
        children = st.get("children") or []
        cleanups = [s for s in all_stages() if s.get("stamp") in children]
        findings, quiet = followscan.tooling_scan(ROOT, children, cleanups, st.get("run_dirs"))
        files = followscan.corpus_files(ROOT, children, st.get("run_dirs"))
    more, rejected = analyze(st, findings, files)
    st["findings"] = findings + more
    st["non_findings"] = quiet + rejected
    st["corpus"] = [str(p) for p in files]
    st["scanned"] = True
    return st


ANALYST_FENCE = "FOLLOWUP_FINDINGS_JSON"


def analyze(st, findings, files):
    """Optional read-only model pass over the corpus. Only findings whose every quote is really in
    a corpus file are kept; the rest are recorded as rejected non-findings. Off with
    ADVANCE_ROADMAP_FOLLOWUP_ANALYST=0; skipped when no analyst is under its usage gate."""
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST", "1") != "1" or not files:
        return [], []
    for provider in ("codex", "claude"):
        gate = gate_one("analyst", provider)
        st["usage_snapshots"].append({"at": now_iso(), "role": "analyst", "provider": provider, "gate": gate})
        if gate is None or gate.get("available"):
            break
    else:
        st["analyst"] = "skipped: no analyst under its usage gate"
        return [], []
    out = stage_dir(st["key"]) / "analyst.txt"
    prompt = analyst_prompt(st, findings, files)
    try:
        if provider == "codex":
            subprocess.run([CODEX, "exec", "-o", str(out), "-s", "read-only", "-C", str(ROOT),
                            "--skip-git-repo-check", prompt], capture_output=True, text=True, timeout=1800,
                           stdin=subprocess.DEVNULL)
        else:
            p = subprocess.run([CLAUDE, "-p", prompt, "--permission-mode", "plan",
                                "--disallowedTools", "Edit,Write,NotebookEdit", "--output-format", "json"],
                               capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL, cwd=str(ROOT))
            (stage_dir(st["key"]) / "analyst.json").write_text(p.stdout)
            out.write_text(str((json.loads(p.stdout.splitlines()[-1]) if p.stdout.strip() else {}).get("result") or ""))
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        st["analyst"] = f"failed: {e}"
        return [], []
    text = followscan.read_text(out)
    if followscan.LIMIT_BANNER.search(text):
        st["analyst"] = f"{provider} hit its limit"
        return [], []
    m = re.findall(r"```" + ANALYST_FENCE + r"\s*\n(.*?)\n```", text, re.S)
    try:
        doc = json.loads(m[-1]) if m else {}
    except ValueError:
        doc = {}
    st["analyst"] = f"{provider}: {'answered' if m else 'no findings block'}"
    known = {f["id"] for f in findings}
    kept, rejected = [], []
    for raw in doc.get("findings") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        ev = [e for e in raw.get("evidence") or [] if isinstance(e, dict)]
        if not ev or not all(followscan.evidence_holds(e, files) for e in ev):
            rejected.append(followscan.non_finding(raw["title"], "rejected: its evidence is not in the corpus", ev))
            continue
        f = followscan.finding(st["stage"], st["repo"], str(raw.get("kind") or "analyst"), raw["title"],
                               raw.get("key") or raw["title"], [followscan.evidence(e["source"], e["quote"]) for e in ev],
                               raw.get("fix") or "")
        if f["id"] not in known:
            kept.append(f)
            known.add(f["id"])
    for raw in doc.get("non_findings") or []:
        if isinstance(raw, dict) and raw.get("title"):
            rejected.append(followscan.non_finding(raw["title"], raw.get("why") or "", []))
    return kept, rejected


def analyst_prompt(st, findings, files):
    return f"""You are the read-only analyst for advance-roadmap's {st['stage']} follow-up ({st['key']}).
Read only these files: {', '.join(str(f) for f in files)}
These findings are already recorded (do not repeat them): {json.dumps([f['title'] for f in findings])}

Apply session-hardening's recognition tests (wrong knowledge, misfiring automation, repeated manual
work, fragile tooling, misleading surfaces, newly dead mechanisms). Report a finding ONLY with
evidence: for each, the exact file path from the list above and a verbatim quote of at least one
line from it. Skip speculative improvements and unrelated backlog. List deliberate non-findings —
things that look like friction but are working as designed.
{'Only project-specific bugs in ' + st['repo'] + ' count here.' if st['stage'] == STAGES['cleanup'] else 'Only orchestration, routing, skills, hooks and reporting count here.'}

End with one fence:
```{ANALYST_FENCE}
{{"findings": [{{"title": "...", "kind": "...", "fix": "...", "evidence": [{{"source": "<path>", "quote": "<verbatim>"}}]}}],
 "non_findings": [{{"title": "...", "why": "..."}}]}}
```"""


# ── one stage ────────────────────────────────────────────────────────────────

def next_step_text(st, step):
    where = f" on branch `{st.get('branch')}`" if st.get("branch") else ""
    pr = f" (PR {st['pr_url']})" if st.get("pr_url") else ""
    return (f"`python3 ~/.claude/automations/advance-roadmap/followup.py drain --repo {st['repo']}` "
            f"after the reset: it resumes at {step}{where}{pr}.")


def defer(st, findings, quota, step, issues=None):
    """No provider under its gate: ticket the remaining findings (create or update) and wait.

    A ticket this creates goes into the local index and the drain's `issues` snapshot, so a later
    stage carrying the same finding — in this drain or a concurrent one — comments instead."""
    st.update(status="deferred", quota=quota, deferred_until=quota.get("reset_epoch"))
    next_step = next_step_text(st, step)
    for f in findings:
        f["status"] = "deferred"
    log(f"{st['key']}: deferred at {step} — {quota.get('provider')} {quota.get('cause') or ''} "
        f"resets {quota.get('reset_at')}")
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
        return
    with TicketIndex() as index:
        attached = set()
        for f in findings:
            if not f.get("ticket") and index.ticket(f["id"]):
                f["ticket"] = index.ticket(f["id"])
                attached.add(f["ticket"])
        ticketed = {}
        for f in findings:
            if f.get("ticket"):
                ticketed.setdefault(f["ticket"], []).append(f)
        note = f"{step}|{quota.get('provider')}|{quota.get('reset_at')}"
        if note == st.get("last_deferral"):
            # Same stop, same reset: the ticket already says so (unless it is new to this stage).
            ticketed = {k: v for k, v in ticketed.items() if k in attached}
        st["last_deferral"] = note
        try:
            for ident, fs in ticketed.items():
                followticket.comment(ident, "advance-roadmap follow-up deferred again for quota.\n\n"
                                     + followticket.description(st, fs, quota, next_step))
            fresh = [f for f in findings if not f.get("ticket")]
            if fresh:
                ident = followticket.create(st, fresh, quota, next_step)
                for f in fresh:
                    f["ticket"] = ident
                    index.add(f["id"], ident, st["key"])
                if issues is not None:
                    issues.append({"identifier": ident, "title": followticket.title_for(st, fresh),
                                   "description": followticket.description(st, fresh, quota, next_step),
                                   "labels": {"nodes": [{"name": st["repo"], "parent": {"name": "repo"}}]}})
                log(f"{st['key']}: filed {ident} for {len(fresh)} deferred finding(s)")
        except followticket.LinearError as e:
            report(st, "linear", f"could not ticket the quota deferral: {e}", next_step)


def block(st, kind, reason, retry=True):
    """A non-quota stop: local report only (never a new ticket), plus verified progress on any
    ticket the stage already has. `retry` stages run again next drain, up to MAX_ATTEMPTS."""
    next_step = next_step_text(st, (st.get("next") or {}).get("step", "build"))
    report(st, kind, reason, next_step)
    st["status"] = "pending" if retry and st.get("attempts", 0) < MAX_ATTEMPTS else "blocked"
    tickets = sorted({f["ticket"] for f in st["findings"] if f.get("ticket")})
    if tickets and os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") == "1":
        body = (f"advance-roadmap follow-up progress on `{st['key']}`: "
                f"{followticket.render_validation(st.get('validation'))}. "
                + (f"Branch `{st['branch']}`" if st.get("branch") else "") + (f", PR {st['pr_url']}" if st.get("pr_url") else "")
                + f". Stopped: {reason}.")
        for ident in tickets:
            try:
                followticket.comment(ident, body)
            except followticket.LinearError as e:
                report(st, "linear", f"could not update {ident}: {e}")


def finish(st, findings):
    for f in findings:
        f["status"] = "fixed"
    st.update(status="done", quota=None, deferred_until=None, next=None)
    closing = (f"Fixed by advance-roadmap follow-up `{st['key']}`: PR {st.get('pr_url')}, merge "
               f"`{st.get('merge_commit')}`, deploy {st['validation'].get('deploy')}; "
               f"{followticket.render_validation(st.get('validation'))}.")
    closed = set()
    for ident in sorted({f["ticket"] for f in findings if f.get("ticket")}):
        if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_LINEAR", "1") != "1":
            break
        try:
            followticket.close(ident, closing)
            closed.add(ident)
            log(f"{st['key']}: closed {ident}")
        except followticket.LinearError as e:
            report(st, "linear", f"merged and deployed, but could not close {ident}: {e}")
    if closed:
        with TicketIndex() as index:
            index.drop_tickets(closed)


def process(st, issues):
    st["attempts"] = st.get("attempts", 0) + 1
    st["status"] = "running"
    save(st)
    if not st.get("scanned"):
        scan(st)
        save(st)
    dedupe(st, issues)
    todo = [f for f in st["findings"] if f.get("status") in ("open", "deferred")]
    if not todo:
        st.update(status="done", next=None)
        log(f"{st['key']}: nothing evidenced to repair ({len(st.get('non_findings') or [])} non-finding(s))")
        return
    repo_dir, why = safe_repo(st["repo"])
    if not repo_dir:
        block(st, "unsafe-repo", why, retry=False)
        return
    busy = ("its builder from an earlier drain is still running" if builder_alive(st["key"])
            else "a worker or follow-up builder is still running in it" if st["repo"] in shared.inflight_repos(ROOT)
            else None)
    lock = None if busy else take_repo_lock(st["repo"])
    if busy or not lock.ok:
        st["status"] = "pending"
        st["attempts"] -= 1   # not an attempt: someone else is in the repo
        log(f"{st['key']}: {st['repo']} is busy ({busy or 'its repo lock is held'}); waiting")
        return
    try:
        repair(st, repo_dir, todo, issues)
    finally:
        lock.close()


def repair(st, repo_dir, todo, issues=None):
    nxt = st.get("next") or {"step": "build", "round": 1}
    while True:
        st["next"] = nxt
        save(st)
        step, rnd = nxt["step"], nxt["round"]
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
            if kind != "ok":
                return block(st, "builder", detail, retry="merged on its own" not in detail)
            nxt = {"step": "review", "round": rnd}
        elif step == "review":
            if st.get("builder_provider") not in followrepair.REVIEWERS:
                return block(st, "unknown-builder",
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
                q = quota_record("reviewer")
                if q and q.get("provider"):
                    return defer(st, todo, q, f"review round {rnd}", issues)
                return block(st, "review-unavailable", "every reviewer was at capacity or missing")
            if verdict == "error":
                return block(st, "review", f"review.sh failed: {text}")
            if verdict == "clean":
                nxt = {"step": "land", "round": rnd}
            elif rnd < MAX_ROUNDS:
                nxt = {"step": "build", "round": rnd + 1, "review_text": text}
            else:
                return block(st, "review", f"the review still reports findings after {MAX_ROUNDS} rounds; "
                                           f"branch {st.get('branch')} and PR {st.get('pr_url')} are left for a human",
                             retry=False)
        else:
            ok, reason, retry = land(st, repo_dir, todo)
            if not ok:
                return block(st, "land", reason, retry=retry)
            return finish(st, todo)


# ── drain ────────────────────────────────────────────────────────────────────

def order(st):
    unfinished = bool(st.get("branch") or st.get("pr_url") or any(f.get("ticket") for f in st.get("findings") or []))
    return (0 if unfinished else 1, 0 if st["stage"] == STAGES["cleanup"] else 1, st.get("created_at") or "")


def drain(repo=None, origin=None, stage=None, wait_s=0.0, recover=True):
    STATE.mkdir(parents=True, exist_ok=True)
    recover_interrupted()
    issues = recover_tickets() if recover else []
    deadline = time.time() + wait_s
    while True:
        waiting, progressed = [], False
        for st in sorted(all_stages(), key=order):
            if repo and st.get("repo") != repo or stage and st["stage"] != STAGES[stage]:
                continue
            if not eligible(st):
                continue
            ok, why = ready(st)
            if not ok:
                waiting.append(f"{st['key']}: {why}")
                continue
            c = claim_stage(st["key"])
            if not c:
                continue   # another drain is running it
            try:
                st = load(st["key"]) or st
                if not eligible(st):
                    continue
                if origin:
                    st["drained_by"] = origin
                try:
                    process(st, issues)
                except Exception as e:  # one broken stage must never stop the rest
                    report(st, "crash", f"{type(e).__name__}: {e}")
                    st["status"] = "pending" if st.get("attempts", 0) < MAX_ATTEMPTS else "blocked"
                save(st)
                progressed = True
            finally:
                c.close()
        if not waiting or time.time() >= deadline:
            for w in waiting:
                log(f"waiting: {w}")
            return 0
        if not progressed:
            time.sleep(min(POLL_S, max(0.0, deadline - time.time())))


# ── summary ──────────────────────────────────────────────────────────────────

def summary(origin=None):
    rows = []
    for st in all_stages():
        if origin and origin not in (st.get("origin"), st.get("drained_by")):
            continue
        fs = st.get("findings") or []
        rows.append({"key": st["key"], "stage": st["stage"], "repo": st.get("repo"), "status": st.get("status"),
                     "item": st.get("item"), "findings": len(fs),
                     "fixed": sum(f.get("status") == "fixed" for f in fs),
                     "tickets": sorted({f["ticket"] for f in fs if f.get("ticket")}),
                     "branch": st.get("branch"), "pr_url": st.get("pr_url"), "merge_commit": st.get("merge_commit"),
                     "reset_at": (st.get("quota") or {}).get("reset_at") if st.get("status") == "deferred" else None,
                     "provider": (st.get("quota") or {}).get("provider") if st.get("status") == "deferred" else None,
                     "blocker": (st.get("blockers") or [{}])[-1].get("reason") if st.get("status") in ("blocked", "pending") and st.get("blockers") else None})
    return rows


def summary_line(rows):
    """One sentence for the batch summary: fixed, quota-deferred and blocked follow-ups."""
    fixed = [r for r in rows if r["status"] == "done" and r["fixed"]]
    deferred = [r for r in rows if r["status"] == "deferred"]
    blocked = [r for r in rows if r["status"] == "blocked" or (r["status"] == "pending" and r.get("blocker"))]
    parts = []
    if fixed:
        parts.append(f"{len(fixed)} follow-up(s) fixed and merged")
    if deferred:
        why = "; ".join(sorted({f"{r['provider']} resets {r['reset_at']}" for r in deferred if r.get("provider")}))
        parts.append(f"{len(deferred)} deferred for quota" + (f" ({why})" if why else "")
                     + (f", tickets {', '.join(sorted({t for r in deferred for t in r['tickets']}))}"
                        if any(r["tickets"] for r in deferred) else ""))
    if blocked:
        parts.append(f"{len(blocked)} blocked (see followups/report.jsonl)")
    return ("Follow-ups: " + "; ".join(parts) + ".") if parts else ""


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_scan(a):
    """Dry run: the findings and non-findings a stage would act on. No state, repairs or tickets."""
    if a.stage == "cleanup":
        st = {"key": f"scan-{a.stamp[0]}", "stage": STAGES["cleanup"], "stamp": a.stamp[0], "usage_snapshots": [],
              "repo": (read_json(ROOT / "runs" / a.stamp[0] / "worker-result.json") or {}).get("repo")}
    else:
        st = {"key": f"scan-{a.origin}", "stage": STAGES["tooling"], "children": a.stamp, "origin": a.origin,
              "repo": followscan.TOOLING_REPO, "usage_snapshots": []}
    stage_dir(st["key"]).mkdir(parents=True, exist_ok=True)
    try:
        scan(st)
    finally:
        shutil.rmtree(stage_dir(st["key"]), ignore_errors=True)
    print(json.dumps({"findings": st["findings"], "non_findings": st["non_findings"],
                      "analyst": st.get("analyst")}, indent=1))
    return 0


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
    d.add_argument("--no-recover", action="store_true", help="skip the Linear ticket recovery")
    s = sub.add_parser("scan")
    s.add_argument("--stage", choices=sorted(STAGES), required=True)
    s.add_argument("--stamp", action="append", default=[])
    s.add_argument("--origin", default="dry-run")
    t = sub.add_parser("status")
    t.add_argument("--origin")
    t.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "hook":
        if a.event == "item-done":
            if len(a.stamp) != 1 or len(a.run_dir_list) > 1:
                p.error("item-done takes exactly one --stamp and at most one --run-dir")
            return hook_item_done(a.stamp[0], (a.run_dir_list or [None])[0], a.origin)
        if not a.origin or not re.fullmatch(r"[A-Za-z0-9._-]+", a.origin):
            p.error("run-end needs --origin (letters, digits, . _ -)")
        return hook_run_end(a.origin, a.stamp, a.run_dir_list)
    if a.cmd == "drain":
        if recursion_suppressed():
            return 0
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        return drain(a.repo, a.origin, a.stage, a.wait_s, not a.no_recover)
    if a.cmd == "scan":
        if not a.stamp:
            p.error("scan needs --stamp")
        return cmd_scan(a)
    rows = summary(a.origin)
    print(json.dumps(rows, indent=1) if a.json else (summary_line(rows) or "Follow-ups: none."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
