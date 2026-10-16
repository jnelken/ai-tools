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
import shlex
import subprocess
from pathlib import Path

SHIPPED = ("shipped", "shipped-deploy-failed")
MARKER = re.compile(r"\b(FIXME|TODO)\(advance-roadmap review\):\s*(.+)")
# Same banner shape run.sh, worker.sh, review.sh and usage.py match: a CLI's own words at a line start.
LIMIT_BANNER = re.compile(
    r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
    r"|quota exceeded|out of (?:usage|credits|quota)\b)", re.I | re.M)
# Codex's log of a command it executed: an `exec` header, then `<shell> -lc <command> in <cwd>`
# (older CLIs put the command on the header line). Cursor and Claude run with --output-format json
# and log no commands, so anything else that mentions `git stash` is prose, not an execution.
EXEC_HEADER = re.compile(r"^(?:\[[^\]]*\]\s*)?exec(?:\s+(\S.*))?$")
GIT_OPTS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path")
QUOTA_OUTCOMES = ("quota-paused", "skipped-quota")
ERROR_OUTCOMES = ("error", "failed", "incomplete")
TOOLING_REPO = "ai-tools"
# Detail text from runrecord.derive (and similar) that names an automation-side failure.
ORCH_FAILURE = re.compile(
    r"orchestrator|worker\.sh|ORCHESTRATOR_RESULT|left no status|"
    r"without (?:a |writing a )?result|unknown worker outcome|parse_error|planner",
    re.I)
# Deliberate SAFETY.md / gate stops — correct behavior, never a tooling repair finding.
SAFETY_STOP = re.compile(
    r"\.noroadmap|opted out|dirty (?:tree|working)|live session|"
    r"git-safe-to-autocommit|Needs Input|human-only|"
    r"origin is not a jnelken|not a jnelken GitHub|"
    r"plan approval|quota.?paus",
    re.I)
# Follow-up blocker kinds that are the repair pipeline itself failing (worth a tooling finding).
# "analyst" is a hard tooling defect (missing binary, crash, non-zero exit) — not a safety stop.
REPAIR_BLOCKER_KINDS = frozenset({
    "builder", "review", "review-unavailable", "land", "crash", "analyst",
    "unknown-usage", "unknown-builder", "linear",
})


def finding_id(stage, repo, kind, key):
    """Stable across runs and machines: the same problem gets the same id, so tickets dedupe."""
    raw = "\x1f".join([stage, repo or "", kind, " ".join(str(key).lower().split())])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def evidence(source, quote):
    return {"source": str(source), "quote": " ".join(str(quote).split())[:400]}


def iter_jsonl_lines(path):
    """Yield raw lines from a JSONL file with no size cap (streamed end-to-end)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\n")
    except OSError:
        return


def jsonl_stamp_line(path, stamp):
    """The raw JSONL line whose stamp field matches, or None. Streams the whole file."""
    for line in iter_jsonl_lines(path):
        try:
            if json.loads(line).get("stamp") == stamp:
                return line
        except ValueError:
            continue
    return None


def row_evidence(src, stamp, needle=None):
    """Evidence citing a verbatim source line for this stamp (never a synthetic summary).

    For runs.jsonl (and other stamp-keyed JSONL), stream the whole file and cite only that
    stamp's row. A missing stamp is missing evidence — never fall back to another row.
    """
    src = Path(src)
    if src.name.endswith(".jsonl") or src.name == "runs.jsonl":
        line = jsonl_stamp_line(src, stamp)
        if line:
            return evidence(src, line)
        return evidence(src, stamp)
    text = read_text(src)
    if needle and " ".join(str(needle).split()) in " ".join(text.split()):
        for raw in text.splitlines():
            if str(needle) in raw:
                return evidence(src, raw)
        return evidence(src, needle)
    for raw in text.splitlines():
        if raw.strip():
            return evidence(src, raw)
    return evidence(src, stamp)


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


def run_dir(root, stamp, run_dirs=None):
    return Path((run_dirs or {}).get(stamp) or Path(root) / "runs" / stamp)


def external_row(stamp, run):
    """A runs.jsonl-shaped row for a run outside root/runs (conductor): result, else terminal
    worker-status, else an explicit `complete` marker. (row, source path)."""
    run = Path(run)
    path = run / "worker-result.json"
    result = read_json(path)
    if isinstance(result, dict) and result.get("outcome") is not None:
        return {"stamp": stamp, "outcome": result.get("outcome"),
                "detail": (result.get("summary") or "")[:200], "worker": result.get("provider")}, path
    status_path = run / "worker-status.json"
    status = read_json(status_path)
    if isinstance(status, dict) and "exit" in status:
        if status.get("quota") or status.get("limited"):
            outcome = "limit_hit"
        elif status.get("fatal") or (status.get("exit") not in (0, None)):
            outcome = "failed"
        else:
            outcome = "incomplete"
        detail = status.get("fatal") or (
            f"quota exhausted" if status.get("quota") else f"exit {status.get('exit')}")
        return {"stamp": stamp, "outcome": outcome, "detail": str(detail)[:200],
                "worker": status.get("used")}, status_path
    marker = run / "complete"
    if marker.exists():
        return {"stamp": stamp, "outcome": "incomplete", "detail": "completion marker",
                "worker": None}, marker
    return {"stamp": stamp, "outcome": None, "detail": "", "worker": None}, path


def executed_commands(text):
    """The command lines a provider CLI logged as executed (Codex `exec` blocks), verbatim."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = EXEC_HEADER.match(line.strip())
        if not m:
            continue
        cmd = m.group(1) or next((nxt for nxt in lines[i + 1:i + 3] if nxt.strip()), "")
        if cmd.strip():
            yield cmd.strip()


def shell_words(script):
    """Shell words with each separator (&&, ;, |, parentheses, redirections) its own word, or None
    when the script does not tokenize (unbalanced quotes)."""
    lex = shlex.shlex(script, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None


SHELL_PREFIXES = ("command", "exec", "env", "nohup", "time", "sudo", "if", "then", "else", "elif",
                  "do", "while", "until", "!", "{")


def runs_git_stash(command, depth=0):
    """True when the command line runs `git [options] stash` (anything but `stash list`) as a
    command — not when `git stash` is only an argument (a grep pattern, an echo, a quoted rule)."""
    words = shell_words(command) if depth <= 3 else None
    segment = []
    for w in (words or []) + [";"]:
        if w and set(w) <= set("();<>|&"):
            if segment_runs_git_stash(segment, depth):
                return True
            segment = []
        else:
            segment.append(w)
    return False


def segment_runs_git_stash(words, depth):
    while words and (words[0] in SHELL_PREFIXES or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
        words = words[1:]
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1]
    if name in ("sh", "bash", "zsh") and len(words) > 2 and words[1] in ("-c", "-lc"):
        return runs_git_stash(words[2], depth + 1)
    if name != "git":
        return False
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_OPTS_WITH_VALUE else 1
    return i < len(words) and words[i] == "stash" and words[i + 1:i + 2] != ["list"]


def orchestration_failure_evidence(row, orch, run, src):
    """Evidence the automation itself failed. None = a project/item failure recorded correctly."""
    stamp = row.get("stamp") or "?"
    detail = str(row.get("detail") or "")
    orch_path = Path(run) / "orchestrator-result.json"
    if orch.get("parse_error"):
        return evidence(orch_path, orch["parse_error"])
    perr = orch.get("planner_error")
    if perr:
        return evidence(orch_path, perr if isinstance(perr, str) else json.dumps(perr))
    if detail and ORCH_FAILURE.search(detail):
        return row_evidence(src, stamp, detail)
    # Incomplete always means the automation did not finish recording a result.
    if row.get("outcome") == "incomplete":
        return row_evidence(src, stamp, detail or "incomplete")
    # A dispatched worker left no result file (and no stdout-fence recovery either).
    if (row.get("action") in ("dispatch_worker", "resume_worker")
            and row.get("outcome") in ERROR_OUTCOMES
            and row.get("worker_result_source") not in ("agent", "stdout-fence")
            and not (Path(run) / "worker-result.json").exists()):
        return row_evidence(src, stamp, row.get("outcome"))
    return None


def blocker_repair_evidence(blocker, stage):
    """Evidence a follow-up blocker is an orchestration/repair defect. None = expected safety stop."""
    kind = blocker.get("kind") or ""
    reason = str(blocker.get("reason") or "")
    if kind == "quota" or SAFETY_STOP.search(reason):
        return None
    # safe_repo / eligibility gates are deliberate SAFETY.md stops, not defects to repair.
    if kind in ("unsafe-repo", "interrupted"):
        return None
    src = f"followups/{stage.get('key')}/state.json"
    if kind in REPAIR_BLOCKER_KINDS or ORCH_FAILURE.search(reason):
        return evidence(src, reason or kind)
    return None


def tooling_scan(root, stamps, stages=(), run_dirs=None):
    """Findings for one finished batch/run: what in the automation itself got in the way.

    `stages` are the follow-up stage states this run's cleanup started (their blockers count as
    friction too); `run_dirs` maps a stamp to a run dir outside root/runs (no runs.jsonl row).
    Each recognition test cites a verbatim line from the file that supplied it."""
    root = Path(root)
    stage, repo = "tooling-hardening", TOOLING_REPO
    wanted = set(stamps)
    rows = run_rows(root, stamps)
    row_src = {s: root / "runs.jsonl" for s in rows}
    for stamp in stamps:
        if stamp not in rows and stamp in (run_dirs or {}):
            rows[stamp], row_src[stamp] = external_row(stamp, run_dir(root, stamp, run_dirs))
    groups = {}   # kind/key → finding being built, so one cause across lanes is one finding
    quiet = []

    def hit(kind, key, title, fix, *evs):
        f = groups.get((kind, key))
        if f is None:
            f = groups[(kind, key)] = finding(stage, repo, kind, title, key, [], fix)
        for ev in evs:
            if ev:
                f["evidence"].append(ev)

    for stamp in stamps:
        run = run_dir(root, stamp, run_dirs)
        row = rows.get(stamp) or {}
        src = row_src.get(stamp, root / "runs.jsonl")
        orch_path = run / "orchestrator-result.json"
        orch = read_json(orch_path) or {}
        planner_failed = bool(orch.get("planner_error") or orch.get("parse_error"))
        banner = LIMIT_BANNER.search(orch.get("summary") or "")
        # 1. Misfiring automation: a worker spent tokens on a run that had no plan.
        if planner_failed and row.get("worker") not in (None, "none"):
            err = orch.get("parse_error") or orch.get("planner_error")
            orch_ev = evidence(orch_path, err if isinstance(err, str) else json.dumps(err)) if err else None
            hit("misfiring-automation", "worker-after-failed-plan",
                "a worker ran after the planner returned no usable plan",
                "Stop before worker dispatch when ORCHESTRATOR_RESULT_JSON is missing or invalid.",
                row_evidence(src, stamp), orch_ev)
        # 2. Wrong knowledge recorded: quota exhaustion reported (and alerted) as an error.
        if row.get("outcome") in ERROR_OUTCOMES and banner:
            hit("misleading-surface", "quota-recorded-as-error",
                "a planner's usage limit was recorded as an error",
                "Record a limit banner as quota-paused with its provider and reset, not error.",
                evidence(orch_path, banner.group(0)), row_evidence(src, stamp))
        # 3. Fragile tooling: only when evidence shows the automation itself failed.
        elif row.get("outcome") in ERROR_OUTCOMES:
            detail = row.get("detail") or row.get("outcome")
            ev = orchestration_failure_evidence(row, orch, run, src)
            if ev:
                hit("fragile-tooling", re.sub(r"\d+", "N", detail),
                    f"runs ended {row['outcome']}: {detail[:100]}",
                    "Find the failure signature in the run log and make the step fail safe or retry it.",
                    ev)
            else:
                quiet.append(non_finding(
                    f"{stamp} ended {row['outcome']}",
                    "a project or item failure with correct automation behavior is not a tooling defect",
                    [row_evidence(src, stamp, detail)]))
        # 4. A review the worker could not read.
        reviews_path = run / "reviews.jsonl"
        for line in read_text(reviews_path).splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("verdict") == "unparsed":
                hit("misfiring-automation", "review-unparsed",
                    "a review round ended with no VERDICT line (read as findings)",
                    "Classify the reviewer's failure (limit, capacity, crash) and fall back instead.",
                    evidence(reviews_path, line))
        # 5. Ships the record flags as unreviewed.
        if row.get("outcome") in SHIPPED and str(row.get("detail") or "").startswith("merged"):
            hit("misfiring-automation", "merged-unreviewed", "an item merged without a completed review",
                "Find why the reviewer was unavailable and make the worker wait or defer instead.",
                row_evidence(src, stamp, row.get("detail")))
        # 6. A builder touched the shared stash (an executed command, never echoed docs or prose).
        for path in (run / "worker-output.log", root / "logs" / f"run-{stamp}.log"):
            line = next((c for c in executed_commands(read_text(path)) if runs_git_stash(c)), None)
            if line:
                hit("misfiring-automation", "git-stash", "a builder ran git stash (shared across worktrees)",
                    "Keep the never-stash rule in SAFETY.md and the builder prompt; commit on the branch instead.",
                    evidence(path, line))
                break
        # Deliberate non-findings: the parts that look like friction but are the design.
        if row.get("outcome") in QUOTA_OUTCOMES and (row.get("quota") or {}).get("provider"):
            quiet.append(non_finding(f"{stamp} paused for quota",
                                     "a quota pause that names its provider and reset is the gate working, "
                                     "not a tooling failure",
                                     [row_evidence(src, stamp)]))
        elif row.get("outcome") == "skipped-unchanged":
            quiet.append(non_finding(f"{stamp} skipped unchanged", "the unchanged-world gate saved a planner call",
                                     [row_evidence(src, stamp, row.get("detail"))]))
        elif row.get("action") in ("blocked_no_item", "nothing_qualified") and not planner_failed \
                and row.get("outcome") not in ERROR_OUTCOMES:
            quiet.append(non_finding(f"{stamp} found no work", "a valid no-work plan with its bookkeeping",
                                     [row_evidence(src, stamp)]))
        if row.get("worker_result_source") == "stdout-fence":
            hit("fragile-tooling", "stdout-fence", "a worker skipped its result file",
                "Make the worker write worker-result.json before its summary.",
                row_evidence(src, stamp, "stdout-fence"))

    # 7. Saved no-work verdicts whose text is a limit banner (cached as if triage ran).
    for path in sorted((root / "batch-verdicts").glob("*.json")):
        v = read_json(path) or {}
        if v.get("stamp") in wanted:
            m = LIMIT_BANNER.search(v.get("summary") or "")
            if m:
                hit("wrong-knowledge", "contaminated-verdict", "a no-work verdict was saved from a limit banner",
                    "Refuse to save (and invalidate) verdicts whose source run had a planner error.",
                    evidence(path, m.group(0)))

    # 8. Follow-up stages this run started: only blockers that evidence an orchestration defect.
    for st in stages:
        for b in st.get("blockers") or []:
            ev = blocker_repair_evidence(b, st)
            if ev:
                hit("fragile-tooling", f"followup-{b.get('kind')}",
                    f"a follow-up stage was blocked: {(b.get('reason') or '')[:100]}",
                    "Fix the step that blocked the follow-up so it can finish unattended.", ev)
            elif b.get("kind") != "quota":
                reason = b.get("reason") or b.get("kind")
                quiet.append(non_finding(
                    f"{st.get('key')} blocked ({b.get('kind')})",
                    "an expected safety stop or deliberate gate is not a tooling defect",
                    [evidence(f"followups/{st.get('key')}/state.json", reason)] if reason else []))

    findings = list(groups.values())
    # One stdout-fence fallback is the recovery path working; only a repeat is friction.
    for f in [f for f in findings if f["kind"] == "fragile-tooling" and f["id"] == finding_id(stage, repo, "fragile-tooling", "stdout-fence")]:
        if len(f["evidence"]) < 2:
            findings.remove(f)
            quiet.append(non_finding("one result recovered from stdout", "the fallback exists for this; one is not a pattern",
                                     f["evidence"]))
    return findings, quiet


def corpus_files(root, stamps, run_dirs=None, excerpt_dir=None):
    """Files the tooling corpus may cite: only this stage's child stamps.

    runs.jsonl rows and batch-verdicts for other stamps are excluded. When the on-disk
    runs.jsonl still holds history, a stamp-scoped excerpt is written under excerpt_dir.
    """
    root = Path(root)
    wanted = set(stamps)
    files = []
    runs_path = root / "runs.jsonl"
    if runs_path.exists():
        lines, other = [], False
        for line in iter_jsonl_lines(runs_path):
            try:
                stamp = json.loads(line).get("stamp")
            except ValueError:
                continue
            if stamp in wanted:
                lines.append(line)
            elif stamp:
                other = True
        if lines:
            if other and excerpt_dir is not None:
                excerpt_dir = Path(excerpt_dir)
                excerpt_dir.mkdir(parents=True, exist_ok=True)
                excerpt = excerpt_dir / "runs.jsonl"
                excerpt.write_text("\n".join(lines) + "\n")
                files.append(excerpt)
            else:
                files.append(runs_path)
    for stamp in stamps:
        run = run_dir(root, stamp, run_dirs)
        files += [run / n for n in ("orchestrator-result.json", "worker-result.json", "worker-status.json",
                                    "reviews.jsonl", "worker-output.log")]
        files.append(root / "logs" / f"run-{stamp}.log")
    for path in sorted((root / "batch-verdicts").glob("*.json")):
        v = read_json(path) or {}
        if v.get("stamp") in wanted:
            files.append(path)
    return [f for f in files if f.exists()]


def evidence_holds(ev, files, stamps=None):
    """True when the quoted text is in the cited corpus file (and belongs to these stamps)."""
    allowed = {str(Path(f)): f for f in files}
    path = allowed.get(str(Path(ev.get("source") or "")))
    quote = " ".join(str(ev.get("quote") or "").split())
    if not path or len(quote) < 8:
        return False
    path = Path(path)
    # runs.jsonl is stamp-keyed and can exceed the capped read_text budget — stream it.
    if path.name == "runs.jsonl":
        if not stamps:
            return any(quote in " ".join(line.split()) for line in iter_jsonl_lines(path))
        return _runs_quote_in_stamps(path, quote, set(stamps))
    if quote not in " ".join(read_text(path).split()):
        return False
    if not stamps:
        return True
    if path.parent.name == "batch-verdicts":
        return (read_json(path) or {}).get("stamp") in set(stamps)
    return True


def _runs_quote_in_stamps(path, quote, wanted):
    for line in iter_jsonl_lines(path):
        if quote not in " ".join(line.split()):
            continue
        try:
            if json.loads(line).get("stamp") in wanted:
                return True
        except ValueError:
            continue
    return False
