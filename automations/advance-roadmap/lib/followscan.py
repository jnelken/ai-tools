#!/usr/bin/env python3
"""Evidence for advance-roadmap's follow-up stages (followup.py), read from what runs left on disk.

Two corpora, each run through recognition tests that cite the file and the line behind every
finding (session-hardening's evidence bar: no quote, no finding):

  cleanup_scan(run_dir, repo_dir)   one shipped item: its worker result, review log, the
                                    FIXME/TODO(advance-roadmap review) markers its merge added,
                                    and its deploy result. Project-specific bugs only.
  tooling_scan(root, stamps)        one batch / run / conductor session: run rows, orchestrator
                                    results, saved verdicts, review logs, worker output and the
                                    follow-up stages it started. Orchestration/routing/reporting
                                    friction only; the findings are always ai-tools'.

Both return (findings, non_findings). A non-finding is something that looked like friction but
is the system working as designed — recorded so a later pass doesn't "fix" it. Nothing here
calls a model, Linear or GitHub.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

SHIPPED = ("shipped", "shipped-deploy-failed")
MARKER = re.compile(r"\b(FIXME|TODO)\(advance-roadmap review\):\s*(.+)")
# Same banner shape run.sh, worker.sh, review.sh and usage.py match: a CLI's own words at a line start.
LIMIT_BANNER = re.compile(
    r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
    r"|quota exceeded|out of (?:usage|credits|quota)\b)", re.I | re.M)
STASH = re.compile(r"^.*\bgit\s+(?:-C\s+\S+\s+)?stash\b(?!\s+list).*$", re.M)
QUOTA_OUTCOMES = ("quota-paused", "skipped-quota")
ERROR_OUTCOMES = ("error", "failed", "incomplete")
TOOLING_REPO = "ai-tools"


def finding_id(stage, repo, kind, key):
    """Stable across runs and machines: the same problem gets the same id, so tickets dedupe."""
    raw = "\x1f".join([stage, repo or "", kind, " ".join(str(key).lower().split())])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def evidence(source, quote):
    return {"source": str(source), "quote": " ".join(str(quote).split())[:400]}


def finding(stage, repo, kind, title, key, ev, fix):
    return {"id": finding_id(stage, repo, kind, key), "stage": stage, "repo": repo, "kind": kind,
            "title": title, "evidence": ev, "fix": fix, "status": "open"}


def non_finding(title, why, ev=()):
    return {"title": title, "why": why, "evidence": list(ev)}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, (dict, list)) else None
    except (OSError, ValueError):
        return None


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    out.append(v)
    except OSError:
        pass
    return out


def read_text(path, limit=2_000_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


# ── project cleanup ──────────────────────────────────────────────────────────

def added_markers(repo_dir, merge_commit):
    """[(file, line)] for review markers the merge added (first parent → merge)."""
    if not repo_dir or not merge_commit:
        return []
    p = subprocess.run(["git", "-C", str(repo_dir), "diff", "-U0", f"{merge_commit}^1", merge_commit],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return []
    out, path = [], None
    for line in p.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and not line.startswith("+++") and path and MARKER.search(line):
            out.append((path, line[1:].strip()))
    return out


def cleanup_scan(run_dir, repo_dir=None):
    """Findings for one item's project-cleanup stage. An item that didn't ship is not a trigger."""
    run_dir = Path(run_dir)
    result_path = run_dir / "worker-result.json"
    result = read_json(result_path) or {}
    repo = result.get("repo")
    stage = "project-cleanup"
    if result.get("outcome") not in SHIPPED:
        return [], [non_finding("item did not ship",
                                f"outcome {result.get('outcome')!r} is recoverable work for the next run, "
                                "not a completed-ticket cleanup trigger", [evidence(result_path, result.get("outcome"))])]
    findings, quiet = [], []
    markers = added_markers(repo_dir, result.get("merge_commit"))
    for path, line in markers:
        m = MARKER.search(line)
        findings.append(finding(
            stage, repo, "unresolved-review", f"{m.group(1)} left by the review: {m.group(2)[:120]}",
            f"{path}|{m.group(2)}", [evidence(f"git:{result.get('merge_commit')}:{path}", line)],
            "Fix what the marker describes if it is a real bug, test it, and delete the marker."))
    unresolved = (result.get("review") or {}).get("unresolved") or 0
    if unresolved and not markers:
        findings.append(finding(
            stage, repo, "unresolved-review", f"{unresolved} review finding(s) merged unresolved with no marker",
            f"unmarked|{result.get('merge_commit')}",
            [evidence(result_path, f'"review": {json.dumps(result.get("review"))}')],
            "Re-review the merged diff, fix the real findings, and test them."))
    reviews = read_jsonl(run_dir / "reviews.jsonl")
    if reviews and reviews[-1].get("verdict") == "clean" and not unresolved:
        quiet.append(non_finding("review closed clean", "the last review round was clean",
                                 [evidence(run_dir / "reviews.jsonl", json.dumps(reviews[-1]))]))
    deploy = result.get("deploy") or {}
    if deploy.get("status") in ("failed", "timeout"):
        findings.append(finding(
            stage, repo, "red-deploy", f"production deploy {deploy['status']} after the merge",
            f"deploy|{result.get('merge_commit')}",
            [evidence(result_path, f'"deploy": {json.dumps(deploy)}')],
            "Read the deployment's build log, fix the build forward, and confirm the new deploy is green."))
    elif deploy.get("status") in ("success", "none"):
        quiet.append(non_finding(f"deploy {deploy['status']}", "nothing to repair in production",
                                 [evidence(result_path, f'"deploy": {json.dumps(deploy)}')]))
    return findings, quiet


# ── tooling hardening ────────────────────────────────────────────────────────

def run_rows(root, stamps):
    wanted = set(stamps)
    return {r["stamp"]: r for r in read_jsonl(Path(root) / "runs.jsonl") if r.get("stamp") in wanted}


def tooling_scan(root, stamps, stages=()):
    """Findings for one finished batch/run: what in the automation itself got in the way.

    `stages` are the follow-up stage states this run's cleanup started (their blockers count as
    friction too). Each recognition test names the moment and the file it was read from."""
    root = Path(root)
    stage, repo = "tooling-hardening", TOOLING_REPO
    rows = run_rows(root, stamps)
    groups = {}   # kind/key → finding being built, so one cause across lanes is one finding
    quiet = []

    def hit(kind, key, title, fix, ev):
        f = groups.get((kind, key))
        if f is None:
            f = groups[(kind, key)] = finding(stage, repo, kind, title, key, [], fix)
        f["evidence"].append(ev)

    for stamp in stamps:
        run = root / "runs" / stamp
        row = rows.get(stamp) or {}
        orch_path = run / "orchestrator-result.json"
        orch = read_json(orch_path) or {}
        planner_failed = bool(orch.get("planner_error") or orch.get("parse_error"))
        banner = LIMIT_BANNER.search(orch.get("summary") or "")
        # 1. Misfiring automation: a worker spent tokens on a run that had no plan.
        if planner_failed and row.get("worker") not in (None, "none"):
            hit("misfiring-automation", "worker-after-failed-plan",
                "a worker ran after the planner returned no usable plan",
                "Stop before worker dispatch when ORCHESTRATOR_RESULT_JSON is missing or invalid.",
                evidence(root / "runs.jsonl", f'{stamp}: action={row.get("action")} worker={row.get("worker")} '
                                              f'parse_error={orch.get("parse_error") or orch.get("planner_error")}'))
        # 2. Wrong knowledge recorded: quota exhaustion reported (and alerted) as an error.
        if row.get("outcome") in ERROR_OUTCOMES and banner:
            hit("misleading-surface", "quota-recorded-as-error",
                "a planner's usage limit was recorded as an error",
                "Record a limit banner as quota-paused with its provider and reset, not error.",
                evidence(orch_path, banner.group(0)))
        # 3. Fragile tooling: a run failed for a reason that is not quota.
        elif row.get("outcome") in ERROR_OUTCOMES:
            detail = row.get("detail") or row.get("outcome")
            hit("fragile-tooling", re.sub(r"\d+", "N", detail), f"runs ended {row['outcome']}: {detail[:100]}",
                "Find the failure signature in the run log and make the step fail safe or retry it.",
                evidence(root / "runs.jsonl", f"{stamp}: {row['outcome']} — {detail}"))
        # 4. A review the worker could not read.
        for r in read_jsonl(run / "reviews.jsonl"):
            if r.get("verdict") == "unparsed":
                hit("misfiring-automation", "review-unparsed",
                    "a review round ended with no VERDICT line (read as findings)",
                    "Classify the reviewer's failure (limit, capacity, crash) and fall back instead.",
                    evidence(run / "reviews.jsonl", json.dumps(r)))
        # 5. Ships the record flags as unreviewed.
        if row.get("outcome") in SHIPPED and str(row.get("detail") or "").startswith("merged"):
            hit("misfiring-automation", "merged-unreviewed", "an item merged without a completed review",
                "Find why the reviewer was unavailable and make the worker wait or defer instead.",
                evidence(root / "runs.jsonl", f"{stamp}: {row['detail']}"))
        # 6. A builder touched the shared stash.
        for path in (run / "worker-output.log", root / "logs" / f"run-{stamp}.log"):
            m = STASH.search(read_text(path))
            if m:
                hit("misfiring-automation", "git-stash", "a builder ran git stash (shared across worktrees)",
                    "Keep the never-stash rule in SAFETY.md and the builder prompt; commit on the branch instead.",
                    evidence(path, m.group(0)))
                break
        # Deliberate non-findings: the parts that look like friction but are the design.
        if row.get("outcome") in QUOTA_OUTCOMES and (row.get("quota") or {}).get("provider"):
            q = row["quota"]
            quiet.append(non_finding(f"{stamp} paused for quota",
                                     "a quota pause that names its provider and reset is the gate working, "
                                     "not a tooling failure",
                                     [evidence(root / "runs.jsonl", f'{stamp}: {row["outcome"]} {q.get("provider")} '
                                                                    f'resets {q.get("reset_at")}')]))
        elif row.get("outcome") == "skipped-unchanged":
            quiet.append(non_finding(f"{stamp} skipped unchanged", "the unchanged-world gate saved a planner call",
                                     [evidence(root / "runs.jsonl", f"{stamp}: {row.get('detail')}")]))
        elif row.get("action") in ("blocked_no_item", "nothing_qualified") and not planner_failed \
                and row.get("outcome") not in ERROR_OUTCOMES:
            quiet.append(non_finding(f"{stamp} found no work", "a valid no-work plan with its bookkeeping",
                                     [evidence(root / "runs.jsonl", f"{stamp}: {row.get('outcome')}")]))
        if row.get("worker_result_source") == "stdout-fence":
            hit("fragile-tooling", "stdout-fence", "a worker skipped its result file",
                "Make the worker write worker-result.json before its summary.",
                evidence(root / "runs.jsonl", f"{stamp}: worker_result_source=stdout-fence"))

    # 7. Saved no-work verdicts whose text is a limit banner (cached as if triage ran).
    for path in sorted((root / "batch-verdicts").glob("*.json")):
        v = read_json(path) or {}
        if v.get("stamp") in rows or v.get("stamp") in stamps:
            m = LIMIT_BANNER.search(v.get("summary") or "")
            if m:
                hit("wrong-knowledge", "contaminated-verdict", "a no-work verdict was saved from a limit banner",
                    "Refuse to save (and invalidate) verdicts whose source run had a planner error.",
                    evidence(path, m.group(0)))

    # 8. Follow-up stages this run started that stopped on something other than quota.
    for st in stages:
        for b in st.get("blockers") or []:
            if b.get("kind") == "quota":
                continue
            hit("fragile-tooling", f"followup-{b.get('kind')}", f"a follow-up stage was blocked: {b.get('reason', '')[:100]}",
                "Fix the step that blocked the follow-up so it can finish unattended.",
                evidence(f"followups/{st.get('key')}/state.json", b.get("reason")))

    findings = list(groups.values())
    # One stdout-fence fallback is the recovery path working; only a repeat is friction.
    for f in [f for f in findings if f["kind"] == "fragile-tooling" and f["id"] == finding_id(stage, repo, "fragile-tooling", "stdout-fence")]:
        if len(f["evidence"]) < 2:
            findings.remove(f)
            quiet.append(non_finding("one result recovered from stdout", "the fallback exists for this; one is not a pattern",
                                     f["evidence"]))
    return findings, quiet


def corpus_files(root, stamps):
    """Every file the tooling corpus reads, for an analyst's evidence check."""
    root = Path(root)
    files = [root / "runs.jsonl"]
    for stamp in stamps:
        run = root / "runs" / stamp
        files += [run / n for n in ("orchestrator-result.json", "worker-result.json", "worker-status.json",
                                    "reviews.jsonl", "worker-output.log")]
        files.append(root / "logs" / f"run-{stamp}.log")
    files += sorted((root / "batch-verdicts").glob("*.json"))
    return [f for f in files if f.exists()]


def evidence_holds(ev, files):
    """True when the quoted text really is in the cited corpus file (whitespace-insensitive)."""
    allowed = {str(Path(f)): f for f in files}
    path = allowed.get(str(Path(ev.get("source") or "")))
    quote = " ".join(str(ev.get("quote") or "").split())
    if not path or len(quote) < 8:
        return False
    return quote in " ".join(read_text(path).split())
