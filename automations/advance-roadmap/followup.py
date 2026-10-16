#!/usr/bin/env python3
"""advance-roadmap's follow-up runner — report-only (DEV-232 Phase B slice 2).

After work ships, scan what its logs prove went wrong and record findings. This slice does
**not** repair, launch builders, open PRs, merge, install, or read/write Linear. Slice 3 adds
that lifecycle on top of the same stage state (stable `finding_id`s, no ticket fields yet).

Two stages, each a durable record under $ROOT/followups/<key>/ (runtime state, never ~/.ai-tools):

  project-cleanup    one per shipped item (`shipped` / `shipped-deploy-failed`), once its builder
                     and reviewers have exited and nothing else holds the repo. Corpus: that item's
                     run dir — worker result, review log, the review markers its merge left, deploy.
                     An item that did not ship is recoverable work for the next run, not a cleanup
                     trigger.
  tooling-hardening  one per batch / lone run / conductor session, once every run and every
                     cleanup stage it started has finished. Corpus: those runs' orchestration,
                     routing, review and reporting records. Findings are ai-tools' own.

Findings come from lib/followscan.py's recognition tests (each cites a file and a quote) plus an
optional read-only analyst call whose findings are kept only when their quotes are really in the
corpus. Drain runs ANALYZE then REPORT only: findings land in stage state and the run/batch
summary.

  followup.py hook item-done --stamp S [--run-dir D] [--origin O]   queue S's cleanup (idempotent)
  followup.py hook run-end --origin O [--stamp S ...] [--run-dir D ...]   queue O's tooling stage
  followup.py drain [--repo R] [--origin O] [--wait-s N]           analyze+report what is ready
  followup.py scan --stage cleanup|tooling (--stamp S | --origin O --stamp S ...)   dry run
  followup.py status [--origin O] [--json]

Hooks seeing ADVANCE_ROADMAP_FOLLOWUP=1 do nothing, so a follow-up child never starts follow-ups.
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
import followscan  # noqa: E402
import procrun  # noqa: E402
import supervisor as shared  # noqa: E402
import usage as usagelib  # noqa: E402

ROOT = shared.ROOT
STATE = ROOT / "followups"
FOLLOWUP_ENV = "ADVANCE_ROADMAP_FOLLOWUP"
SHIPPED = ("shipped", "shipped-deploy-failed")
STAGES = {"cleanup": "project-cleanup", "tooling": "tooling-hardening"}
# Stage state machine: queued → waiting(reason) → ready → running → done | blocked | retryable.
QUEUED, WAITING, READY, RUNNING, DONE, BLOCKED, RETRYABLE = (
    "queued", "waiting", "ready", "running", "done", "blocked", "retryable")
ELIGIBLE = {QUEUED, WAITING, READY, RETRYABLE}
COMPLETE_MARKER = "complete"  # explicit run-dir marker when no worker-result/status
MAX_ATTEMPTS = int(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_MAX_ATTEMPTS", "3"))
STALE_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_STALE_H", "12")) * 3600
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_POLL_S", "15"))
CODEX = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
CLAUDE = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")
ANALYST_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST_TIMEOUT_S", "1800"))


# ── shared helpers (slice 3 will share these with the repair module) ─────────

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def log(msg):
    print(f"{datetime.now():%H:%M:%S} followup: {msg}", flush=True)


def read_json(path):
    return followscan.read_json(path)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=1) + "\n")
    os.replace(tmp, path)


def stage_dir(key):
    return STATE / key


def repo_path(repo):
    code = shared.code_dir()
    return Path(code) / repo if code and repo else None


def usage(*args, timeout=180):
    p = subprocess.run([sys.executable, str(ROOT / "lib" / "usage.py"), "--root", str(ROOT), *args],
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout


def gate_one(role, provider):
    try:
        rc, out = usage("gate", "--role", role, "--provider", provider)
        if rc not in (0, 3):
            return None
        return {**json.loads(out), "available": rc == 0}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


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
    # Findings carry stable finding_id via followscan.finding; ticket fields arrive in slice 3.
    st = {"key": key, "stage": stage, "repo": repo, "origin": origin, "status": QUEUED,
          "created_at": now_iso(), "attempts": 0, "findings": [], "non_findings": [], "blockers": [],
          "usage_snapshots": [], "sessions": [], "mode": "report-only", **extra}
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
    run_dir = Path(run_dir) if run_dir else ROOT / "runs" / stamp
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


# ── readiness / locks ────────────────────────────────────────────────────────

def repo_lock_files(repo):
    base = ROOT / "repo-locks"
    return [base / f"{repo}.lock", *sorted(base.glob(f"{glob_escape(repo)}.t*.lock"))]


def glob_escape(s):
    return re.sub(r"([*?\[])", r"[\1]", s)


def quiescent(repo):
    """(True, "") when no lane, thread, timed-out worker or other follow-up holds the repo.

    Never call this while holding the repo's lock yourself: probing it opens and closes the same
    file, and closing any descriptor drops every fcntl lock this process holds on it."""
    if not repo or not isinstance(repo, str):
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


def cleanup_still_active(other):
    """True when a queued/waiting/running cleanup is still being worked (no age-bypass)."""
    repo = other.get("repo")
    if repo and repo in shared.inflight_repos(ROOT):
        return True
    c = Claim(stage_dir(other["key"]) / "claim.lock")
    if not c.ok:
        return True
    c.close()
    return False


def child_complete(stamp, external=None):
    """True when a child run finished: runs.jsonl row, worker-result, terminal worker-status,
    or an explicit `complete` marker. Returns (done, how)."""
    external = external or {}
    if stamp in shared.run_records({stamp}):
        return True, "runs.jsonl"
    run = Path(external[stamp]) if stamp in external else (ROOT / "runs" / stamp)
    if (run / "worker-result.json").exists():
        return True, "worker-result"
    status = read_json(run / "worker-status.json")
    if isinstance(status, dict) and "exit" in status:
        return True, "worker-status"
    if (run / COMPLETE_MARKER).exists():
        return True, "marker"
    return False, ""


def ready(st):
    if st["stage"] == STAGES["cleanup"]:
        return quiescent(st.get("repo"))
    children = st.get("children") or []
    external = st.get("run_dirs") or {}
    unfinished = [s for s in children if not child_complete(s, external)[0]]
    stale = age_s(st) >= STALE_S
    if unfinished and not stale:
        names = ", ".join(unfinished[:5]) + ("…" if len(unfinished) > 5 else "")
        return False, f"{len(unfinished)} run(s) of {st['origin']} have not finished ({names})"
    if unfinished:
        st["unrecorded_children"] = unfinished
    active = {QUEUED, WAITING, READY, RUNNING, RETRYABLE}
    for other in all_stages():
        if other["stage"] != STAGES["cleanup"] or other.get("stamp") not in children:
            continue
        if other.get("status") not in active:
            continue
        if cleanup_still_active(other) or not stale:
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
    st["status"] = WAITING
    st["waiting_reason"] = reason
    # Keep the latest readiness reason visible to status/summary (and as a durable blocker).
    blockers = st.setdefault("blockers", [])
    if not blockers or blockers[-1].get("kind") != "waiting" or blockers[-1].get("reason") != reason:
        blockers.append({"at": now_iso(), "kind": "waiting", "reason": reason})
    save(st)


def set_ready(st):
    st["status"] = READY
    st.pop("waiting_reason", None)
    save(st)


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
            st["status"] = QUEUED
            st.setdefault("blockers", []).append({"at": now_iso(), "kind": "interrupted",
                                                  "reason": "the previous drain stopped mid-stage; resuming"})
            save(st)
            log(f"{st['key']}: recovered an interrupted stage")
        finally:
            c.close()


# ── ANALYZE ──────────────────────────────────────────────────────────────────

def scan(st):
    st.pop("analyst_error", None)
    if st["stage"] == STAGES["cleanup"]:
        run_dir = Path(st.get("run_dir") or ROOT / "runs" / st["stamp"])
        repo_dir = repo_path(st["repo"])
        findings, quiet = followscan.cleanup_scan(run_dir, repo_dir if repo_dir and repo_dir.is_dir() else None)
        files = [p for p in (run_dir / n for n in ("worker-result.json", "reviews.jsonl", "worker-output.log"))
                 if p.exists()]
        child_stamps = [st["stamp"]] if st.get("stamp") else []
    else:
        children = st.get("children") or []
        child_stamps = children
        cleanups = [s for s in all_stages() if s.get("stamp") in children]
        corpus_dir = stage_dir(st["key"]) / "corpus"
        findings, quiet = followscan.tooling_scan(ROOT, children, cleanups, st.get("run_dirs"))
        files = followscan.corpus_files(ROOT, children, st.get("run_dirs"), excerpt_dir=corpus_dir)
    more, rejected = analyze(st, findings, files, child_stamps)
    st["findings"] = findings + more
    st["non_findings"] = quiet + rejected
    st["corpus"] = [str(p) for p in files]
    if st.get("analyst_error"):
        report(st, "analyst", st.get("analyst") or "analyst failed", next_step="analyze")
        return st
    st["scanned"] = True
    return st


ANALYST_FENCE = "FOLLOWUP_FINDINGS_JSON"


def analyze(st, findings, files, stamps=None):
    """Optional read-only model pass over the corpus. Only findings whose every quote is really in
    a corpus file for this stage's stamps are kept; the rest are recorded as rejected non-findings.
    Off with ADVANCE_ROADMAP_FOLLOWUP_ANALYST=0; skipped when no analyst is under its usage gate.

    Missing binary, timeout, or non-zero exit without a live-limit banner fails over to the next
    eligible provider. When every attempt hard-fails, sets analyst_error so the stage stays
    retryable (not scanned/done).
    """
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST", "1") != "1" or not files:
        return [], []
    prompt = analyst_prompt(st, findings, files)
    limited, hard_failures = [], []
    provider = answer = transcript = None
    exit_code, provider_result = 0, None
    for provider in ("codex", "claude"):
        gate = gate_one("analyst", provider)
        st["usage_snapshots"].append({"at": now_iso(), "role": "analyst", "provider": provider, "gate": gate})
        if gate is not None and not gate.get("available"):
            continue
        try:
            transcript, answer, exit_code, provider_result = run_analyst(st, provider, prompt)
        except (OSError, subprocess.TimeoutExpired) as e:
            hard_failures.append(f"{provider}: {e}")
            continue
        # Echoed banners in a successful final answer are not a live limit (shared live-limit rule).
        banner = usagelib.live_limit_banner(
            transcript, exit_code=exit_code, final_answer=answer, provider_result=provider_result)
        if banner:
            record_analyst_limit(st, provider, banner)
            limited.append(provider)
            continue
        if exit_code != 0:
            hard_failures.append(f"{provider}: exit {exit_code}")
            continue
        break
    else:
        if hard_failures:
            detail = "; ".join(hard_failures)
            if limited:
                detail += f"; {' and '.join(limited)} hit {'their limits' if len(limited) > 1 else 'its limit'}"
            st["analyst"] = f"failed: {detail}"
            st["analyst_error"] = True
            return [], []
        st["analyst"] = (f"skipped: {' and '.join(limited)} hit {'their limits' if len(limited) > 1 else 'its limit'}"
                         if limited else "skipped: no analyst under its usage gate")
        return [], []
    # The findings come only from the final answer: the transcript echoes the prompt's example fence.
    m = re.findall(r"```" + ANALYST_FENCE + r"\s*\n(.*?)\n```", answer or "", re.S)
    try:
        doc = json.loads(m[-1]) if m else {}
    except ValueError:
        doc = {}
    st["analyst"] = f"{provider}: {'answered' if m else 'no findings block'}"
    known = {f["id"] for f in findings}
    kept, rejected = [], []
    stage_stamps = list(stamps or st.get("children") or ([st.get("stamp")] if st.get("stamp") else []))
    for raw in doc.get("findings") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        ev = [e for e in raw.get("evidence") or [] if isinstance(e, dict)]
        if not ev or not all(followscan.evidence_holds(e, files, stage_stamps) for e in ev):
            rejected.append(followscan.non_finding(raw["title"], "rejected: its evidence is not in the corpus", ev))
            continue
        f = followscan.finding(st["stage"], st["repo"], str(raw.get("kind") or "analyst"), raw["title"],
                               raw.get("key") or raw["title"],
                               [followscan.evidence(e["source"], e["quote"]) for e in ev],
                               raw.get("fix") or "")
        if f["id"] not in known:
            kept.append(f)
            known.add(f["id"])
    for raw in doc.get("non_findings") or []:
        if isinstance(raw, dict) and raw.get("title"):
            rejected.append(followscan.non_finding(raw["title"], raw.get("why") or "", []))
    return kept, rejected


def run_analyst(st, provider, prompt):
    """(transcript, final answer, exit code, provider_result) of one read-only analyst call.

    Findings come only from the final answer (Codex's -o file, Claude's JSON result). Live limits
    use usage.live_limit_banner — exit status and provider error records, never a quoted banner
    alone.
    """
    d = stage_dir(st["key"])
    final, raw, session = d / f"analyst-{provider}.last", d / f"analyst-{provider}.log", d / f"analyst-{provider}.session"
    for f in (final, session):
        f.unlink(missing_ok=True)
    tokens = [sys.executable, str(HERE / "lib" / "tokens.py")]
    provider_result = None
    # procrun.run: start_new_session + reap the whole tree on timeout before failover.
    if provider == "codex":
        p = procrun.run([CODEX, "exec", "-o", str(final), "-s", "read-only", "-C", str(ROOT),
                         "--skip-git-repo-check", prompt], timeout=ANALYST_TIMEOUT_S)
        raw.write_text((p.stdout or "") + (p.stderr or ""))
        try:
            procrun.run([*tokens, "codex", str(raw), "--role", "analyst",
                         "--log", str(d / "model-usage.jsonl"), "--session-out", str(session)],
                        timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass
        transcript = raw.read_text(errors="replace")
    else:
        p = procrun.run([CLAUDE, "-p", prompt, "--permission-mode", "plan",
                         "--disallowedTools", "Edit,Write,NotebookEdit", "--output-format", "json"],
                        timeout=ANALYST_TIMEOUT_S, cwd=str(ROOT))
        raw_json = d / "analyst-claude.json"
        raw_json.write_text((p.stdout or "") + (p.stderr or ""))
        try:
            procrun.run([*tokens, "claude-json", str(raw_json), str(raw), "--final", str(final),
                         "--role", "analyst", "--log", str(d / "model-usage.jsonl"),
                         "--session-out", str(session)], timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass
        transcript = followscan.read_text(raw) or raw_json.read_text(errors="replace")
        try:
            for line in raw_json.read_text(errors="replace").splitlines():
                if line.startswith("{"):
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "result" in obj:
                        provider_result = obj
                        break
        except (OSError, ValueError):
            provider_result = None
    sid = followscan.read_text(session).strip()
    if sid:
        st.setdefault("sessions", []).append({"role": "analyst", "provider": provider, "session_id": sid})
    return transcript, followscan.read_text(final), p.returncode, provider_result


def record_analyst_limit(st, provider, text):
    """Keep the hit (and its reset) in the stage first, then gate the provider for everyone else:
    a failed shared-state update must not lose it."""
    try:
        rc, out = usage("parse-limit", "--provider", provider, "--text", text, "--scope", "analyst")
        hit = json.loads(out) if rc == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        hit = None
    if not isinstance(hit, dict):
        hit = {"provider": provider, "exact_cli_text": text}
    st.setdefault("limits", []).append({"role": "analyst", "at": now_iso(), **hit})
    write_json(stage_dir(st["key"]) / f"analyst-limit-{provider}.json", hit)
    try:
        usage("record-limit", "--provider", provider, "--text", text, "--scope", "analyst")
    except (OSError, subprocess.TimeoutExpired):
        pass


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


# ── process / drain (ANALYZE + REPORT only) ──────────────────────────────────

def process(st):
    """Run ANALYZE then REPORT. No repair, no builders, no Linear."""
    st["attempts"] = st.get("attempts", 0) + 1
    st["status"] = RUNNING
    st.pop("waiting_reason", None)
    st["next"] = {"step": "analyze"}
    save(st)
    if not st.get("scanned"):
        scan(st)
        save(st)
    if not st.get("scanned"):
        # Analyst hard-failed: retryable (or blocked after MAX_ATTEMPTS). report() already ran.
        st["status"] = RETRYABLE if st.get("attempts", 0) < MAX_ATTEMPTS else BLOCKED
        st["next"] = {"step": "analyze"}
        save(st)
        return
    st["next"] = {"step": "report"}
    fs = st.get("findings") or []
    quiet = st.get("non_findings") or []
    st.update(status=DONE, next=None, reported_at=now_iso(),
              open_findings=sum(1 for f in fs if f.get("status") == "open"))
    log(f"{st['key']}: reported {len(fs)} finding(s), {len(quiet)} non-finding(s) (report-only)")
    save(st)


def order(st):
    # Prefer cleanups (project) before tooling; oldest first. Slice 3 will prefer stages with
    # unfinished repair work (branch/PR/ticket) ahead of fresh ones.
    return (0 if st["stage"] == STAGES["cleanup"] else 1, st.get("created_at") or "")


def drain(repo=None, origin=None, stage=None, wait_s=0.0):
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
            try:
                ok, why = ready(st)
            except Exception as e:  # malformed stage must not abort the drain
                report(st, "readiness", f"{type(e).__name__}: {e}")
                st["status"] = BLOCKED
                save(st)
                progressed = True
                continue
            if not ok:
                set_waiting(st, why)
                waiting.append(f"{st['key']}: {why}")
                continue
            if st.get("status") != READY:
                set_ready(st)
            c = claim_stage(st["key"])
            if not c:
                continue   # another drain is running it
            try:
                st = load(st["key"]) or st
                if not eligible(st) and st.get("status") != READY:
                    continue
                if origin:
                    st["drained_by"] = origin
                try:
                    process(st)
                except Exception as e:  # one broken stage must never stop the rest
                    report(st, "crash", f"{type(e).__name__}: {e}")
                    st["status"] = RETRYABLE if st.get("attempts", 0) < MAX_ATTEMPTS else BLOCKED
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
        rows.append({"key": st["key"], "stage": st["stage"], "repo": st.get("repo"), "status": status,
                     "item": st.get("item"), "findings": len(fs),
                     "open_findings": sum(f.get("status") == "open" for f in fs),
                     "non_findings": len(st.get("non_findings") or []),
                     "mode": st.get("mode") or "report-only",
                     "waiting_reason": st.get("waiting_reason"),
                     "blocker": reason if status in (BLOCKED, WAITING, RETRYABLE, "pending") else None})
    return rows


def summary_line(rows):
    """One sentence for the batch/run summary. Never 'none' while stages are waiting."""
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
        sample = waiting[0].get("waiting_reason") or waiting[0].get("blocker") or "not ready"
        parts.append(f"{len(waiting)} waiting ({sample})")
    if blocked or retryable:
        parts.append(f"{len(blocked) + len(retryable)} blocked (see followups/report.jsonl)")
    return ("Follow-ups: " + "; ".join(parts) + ".") if parts else ""


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_scan(a):
    """Dry run: the findings and non-findings a stage would report. No durable state."""
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
        return drain(a.repo, a.origin, a.stage, a.wait_s)
    if a.cmd == "scan":
        if not a.stamp:
            p.error("scan needs --stamp")
        return cmd_scan(a)
    rows = summary(a.origin)
    print(json.dumps(rows, indent=1) if a.json else (summary_line(rows) or "Follow-ups: none."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
