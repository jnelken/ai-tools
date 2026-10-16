#!/usr/bin/env python3
"""Evidence for advance-roadmap follow-up stages — read-only recognition tests.

cleanup_scan(run_dir, repo_dir) → project findings (markers, deploy, unresolved review)
tooling_scan(root, stamps) → automation friction for a batch/run/conductor session

Each finding cites a file and a verbatim quote. No model, Linear, or GitHub.
"""
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

SHIPPED = ("shipped", "shipped-deploy-failed")
# Artifact SHAs passed into git argv — reject anything that could be parsed as an option.
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
MARKER = re.compile(r"\b(FIXME|TODO)\(advance-roadmap review\):\s*(.+)")
LIMIT_BANNER = re.compile(
    r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
    r"|quota exceeded|out of (?:usage|credits|quota)\b)", re.I | re.M)
EXEC_HEADER = re.compile(r"^(?:\[[^\]]*\]\s*)?exec(?:\s+(\S.*))?$")
GIT_OPTS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path")
QUOTA_OUTCOMES = ("quota-paused", "skipped-quota")
ERROR_OUTCOMES = ("error", "failed", "incomplete")
TOOLING_REPO = "ai-tools"
ORCH_FAILURE = re.compile(
    r"orchestrator|worker\.sh|ORCHESTRATOR_RESULT|left no status|"
    r"without (?:a |writing a )?result|unknown worker outcome|parse_error|planner",
    re.I)
SAFETY_STOP = re.compile(
    r"\.noroadmap|opted out|dirty (?:tree|working)|live session|"
    r"git-safe-to-autocommit|Needs Input|human-only|"
    r"origin is not a jnelken|not a jnelken GitHub|"
    r"plan approval|quota.?paus",
    re.I)
REPAIR_BLOCKER_KINDS = frozenset({
    "builder", "review", "review-unavailable", "land", "crash", "analyst",
    "unknown-usage", "unknown-builder", "linear",
})


def finding_id(stage, repo, kind, key):
    """Stable across runs and machines: the same problem gets the same id, so tickets dedupe."""
    raw = "\x1f".join([stage, repo or "", kind, " ".join(str(key).lower().split())])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _span_in_file(path, raw_span):
    """True when raw_span is an exact substring of the file (streamed for *.jsonl)."""
    path = Path(path)
    raw = str(raw_span)
    if not raw:
        return False
    if path.name.endswith(".jsonl") or path.name == "runs.jsonl":
        return any(raw in line or raw == line for line in iter_jsonl_lines(path)) \
            or raw in read_text(path)
    return raw in read_text(path)


def evidence(path, raw_span):
    """Absolute corpus path + verbatim span (must appear in the file)."""
    path = Path(path).resolve()
    raw = str(raw_span)
    if not path.is_file():
        raise ValueError(f"evidence source is not a file: {path}")
    if not _span_in_file(path, raw):
        raise ValueError(f"evidence span not found in {path}")
    return {"source": str(path), "quote": " ".join(raw.split())[:400]}


def source_key_span(text, key):
    """Raw `"key": <value>` span as stored in text (pretty or compact JSON). None if absent."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:', text)
    if not m:
        return None
    start, i = m.start(), m.end()
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text):
        return text[start:].rstrip()
    ch = text[i]
    if ch in "{[":
        open_ch, close_ch, depth, j = ch, "}" if ch == "{" else "]", 1, i + 1
        in_str, esc = None, False
        while j < len(text) and depth:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == in_str:
                    in_str = None
            elif c in "\"'":
                in_str = c
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
            j += 1
        return text[start:j]
    if ch == '"':
        j, esc = i + 1, False
        while j < len(text):
            c = text[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                return text[start:j + 1]
            j += 1
        return text[start:]
    j = i
    while j < len(text) and text[j] not in ",}\n\r":
        j += 1
    return text[start:j].rstrip()


def source_key_evidence(path, key):
    """Evidence quoting a JSON object's key exactly as stored on disk — never re-serialized."""
    path = Path(path).resolve()
    if not path.is_file():
        return None
    span = source_key_span(read_text(path), key)
    if span is None:
        return None
    return evidence(path, span)


def jsonl_last_evidence(path):
    """Evidence quoting the last non-empty JSONL line verbatim (never json.dumps of a parse)."""
    path = Path(path).resolve()
    if not path.is_file():
        return None
    last = None
    for line in iter_jsonl_lines(path):
        if line.strip():
            last = line
    if last is None:
        return None
    return evidence(path, last)


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
    src = Path(src).resolve()
    if not src.is_file():
        return None
    if src.name.endswith(".jsonl") or src.name == "runs.jsonl":
        line = jsonl_stamp_line(src, stamp)
        return evidence(src, line) if line else None
    text = read_text(src)
    if needle and " ".join(str(needle).split()) in " ".join(text.split()):
        for raw in text.splitlines():
            if str(needle) in raw:
                return evidence(src, raw)
        # Needle matches collapsed text but not a single line — find its JSON encoding.
        encoded = json.dumps(str(needle))
        if encoded in text:
            return evidence(src, encoded)
        return None
    for raw in text.splitlines():
        if raw.strip():
            return evidence(src, raw)
    return None


def stage_state_path(root, stage):
    """Absolute path to a follow-up stage's persisted state.json, or None."""
    key = stage.get("key") if isinstance(stage, dict) else None
    if not key or not isinstance(key, str) or "/" in key or key in (".", ".."):
        return None
    return (Path(root).resolve() / "followups" / key / "state.json")


def blocker_reason_span(text, reason):
    """Raw `"reason": <json>` span for this reason as stored (preserves escapes)."""
    encoded = json.dumps(reason)
    m = re.search(r'"reason"\s*:\s*' + re.escape(encoded), text)
    if m:
        return m.group(0)
    return encoded if encoded in text else None


def finding(stage, repo, kind, title, key, ev, fix):
    return {"id": finding_id(stage, repo, kind, key), "stage": stage, "repo": repo, "kind": kind,
            "title": title, "evidence": ev, "fix": fix, "status": "open"}


def non_finding(title, why, ev=()):
    return {"title": title, "why": why, "evidence": [e for e in ev if e]}


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

def require_sha(value, name="commit"):
    """Validate a git object name from run artifacts before it enters a subprocess argv."""
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r} (expected 7–40 lowercase hex chars)")
    return value


def added_markers(repo_dir, merge_commit):
    """[(file, line)] for review markers the merge added (first parent → merge)."""
    if not repo_dir or not merge_commit:
        return []
    try:
        sha = require_sha(merge_commit, "merge_commit")
    except ValueError:
        return []
    p = subprocess.run(["git", "-C", str(repo_dir), "diff", "-U0", f"{sha}^1", sha],
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
    result_path = (run_dir / "worker-result.json").resolve()
    result = read_json(result_path) or {}
    repo = result.get("repo")
    stage = "project-cleanup"
    if result.get("outcome") not in SHIPPED:
        ev = source_key_evidence(result_path, "outcome")
        return [], [non_finding("item did not ship",
                                f"outcome {result.get('outcome')!r} is recoverable work for the next run, "
                                "not a completed-ticket cleanup trigger",
                                [ev] if ev else [])]
    findings, quiet = [], []
    markers = added_markers(repo_dir, result.get("merge_commit"))
    # Cite repo file when present; else worker-result. Never write excerpts.
    for path, line in markers:
        m = MARKER.search(line)
        if not m:
            continue
        ev = None
        if repo_dir:
            src = Path(repo_dir).resolve() / path
            try:
                src.relative_to(Path(repo_dir).resolve())
            except ValueError:
                src = None
            if src is not None and src.is_file() and _span_in_file(src, line):
                ev = evidence(src, line)
        if ev is None:
            ev = source_key_evidence(result_path, "review")
        if ev:
            findings.append(finding(
                stage, repo, "unresolved-review", f"{m.group(1)} left by the review: {m.group(2)[:120]}",
                f"{path}|{m.group(2)}", [ev],
                "Fix what the marker describes if it is a real bug, test it, and delete the marker."))
    unresolved = (result.get("review") or {}).get("unresolved") or 0
    if unresolved and not markers:
        ev = source_key_evidence(result_path, "review")
        if ev:
            findings.append(finding(
                stage, repo, "unresolved-review", f"{unresolved} review finding(s) merged unresolved with no marker",
                f"unmarked|{result.get('merge_commit')}", [ev],
                "Re-review the merged diff, fix the real findings, and test them."))
    reviews_path = (run_dir / "reviews.jsonl").resolve()
    reviews = read_jsonl(reviews_path)
    if reviews and reviews[-1].get("verdict") == "clean" and not unresolved:
        ev = jsonl_last_evidence(reviews_path)
        if ev:
            quiet.append(non_finding("review closed clean", "the last review round was clean", [ev]))
    deploy = result.get("deploy") or {}
    if deploy.get("status") in ("failed", "timeout"):
        ev = source_key_evidence(result_path, "deploy")
        if ev:
            findings.append(finding(
                stage, repo, "red-deploy", f"production deploy {deploy['status']} after the merge",
                f"deploy|{result.get('merge_commit')}", [ev],
                "Read the deployment's build log, fix the build forward, and confirm the new deploy is green."))
    elif deploy.get("status") in ("success", "none"):
        ev = source_key_evidence(result_path, "deploy")
        if ev:
            quiet.append(non_finding(f"deploy {deploy['status']}", "nothing to repair in production", [ev]))
    return findings, quiet


# ── tooling hardening ────────────────────────────────────────────────────────

def run_rows(root, stamps):
    wanted = set(stamps)
    return {r["stamp"]: r for r in read_jsonl(Path(root) / "runs.jsonl") if r.get("stamp") in wanted}


def run_dir(root, stamp, run_dirs=None):
    return Path((run_dirs or {}).get(stamp) or Path(root) / "runs" / stamp)


def external_row(stamp, run):
    """runs.jsonl-shaped row for a conductor run dir. (row, source path)."""
    run = Path(run)
    path = run / "worker-result.json"
    result = read_json(path)
    if isinstance(result, dict) and result.get("outcome") is not None:
        return {"stamp": stamp, "outcome": result.get("outcome"),
                "detail": (result.get("summary") or "")[:200], "worker": result.get("provider")}, path
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
        return source_key_evidence(orch_path, "parse_error")
    if orch.get("planner_error"):
        return source_key_evidence(orch_path, "planner_error")
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


def stage_blocker_evidence(root, stage, blocker):
    """Evidence citing the absolute persisted state file's raw reason/kind span. None if absent."""
    path = stage_state_path(root, stage)
    if path is None or not path.is_file():
        return None
    text = read_text(path)
    reason = blocker.get("reason")
    if reason is not None and str(reason) != "":
        span = blocker_reason_span(text, str(reason))
        if span:
            return evidence(path, span)
    kind = blocker.get("kind")
    if kind:
        encoded = json.dumps(kind)
        m = re.search(r'"kind"\s*:\s*' + re.escape(encoded), text)
        if m:
            return evidence(path, m.group(0))
        if encoded in text:
            return evidence(path, encoded)
    return None


def blocker_repair_evidence(blocker, stage, root):
    """Evidence a follow-up blocker is an orchestration/repair defect. None = expected safety stop."""
    kind = blocker.get("kind") or ""
    reason = str(blocker.get("reason") or "")
    if kind == "quota" or SAFETY_STOP.search(reason):
        return None
    if kind in ("unsafe-repo", "interrupted"):
        return None
    if kind in REPAIR_BLOCKER_KINDS or ORCH_FAILURE.search(reason):
        return stage_blocker_evidence(root, stage, blocker)
    return None


def missing_run_record_evidence(run):
    """Evidence an internal run left artifacts but no runs.jsonl row."""
    run = Path(run)
    for name, key in (("worker-result.json", "outcome"), ("worker-status.json", "exit"),
                      ("orchestrator-result.json", "action")):
        path = (run / name).resolve()
        if path.is_file():
            ev = source_key_evidence(path, key)
            if ev:
                return ev
            for raw in read_text(path).splitlines():
                if raw.strip():
                    return evidence(path, raw)
    return None
    return None


def tooling_scan(root, stamps, stages=(), run_dirs=None):
    """Automation-friction findings for one finished batch/run/conductor session."""
    root = Path(root).resolve()
    stage, repo = "tooling-hardening", TOOLING_REPO
    wanted = set(stamps)
    external = run_dirs or {}
    rows = run_rows(root, stamps)
    row_src = {s: (root / "runs.jsonl").resolve() for s in rows}
    for stamp in stamps:
        if stamp not in rows and stamp in external:
            rows[stamp], row_src[stamp] = external_row(stamp, run_dir(root, stamp, external))
            row_src[stamp] = Path(row_src[stamp]).resolve()
    groups = {}   # kind/key → finding being built, so one cause across lanes is one finding
    quiet = []

    def hit(kind, key, title, fix, *evs):
        f = groups.get((kind, key))
        if f is None:
            f = groups[(kind, key)] = finding(stage, repo, kind, title, key, [], fix)
        for ev in evs:
            if ev:
                f["evidence"].append(ev)

    def evs(*xs):
        return [x for x in xs if x]

    for stamp in stamps:
        run = run_dir(root, stamp, external)
        if stamp not in rows and stamp not in external:
            ev = missing_run_record_evidence(run)
            if ev:
                hit("fragile-tooling", "missing-run-record",
                    f"internal run {stamp} has artifacts but no runs.jsonl row",
                    "Ensure every internal run writes a runs.jsonl row before follow-up tooling scans it.",
                    ev)
        row = rows.get(stamp) or {}
        src = row_src.get(stamp, (root / "runs.jsonl").resolve())
        orch_path = (run / "orchestrator-result.json").resolve()
        orch = read_json(orch_path) or {}
        planner_failed = bool(orch.get("planner_error") or orch.get("parse_error"))
        orch_raw = read_text(orch_path)
        banner = LIMIT_BANNER.search(orch_raw) or LIMIT_BANNER.search(orch.get("summary") or "")
        if planner_failed and row.get("worker") not in (None, "none"):
            err_key = "parse_error" if orch.get("parse_error") else "planner_error"
            orch_ev = source_key_evidence(orch_path, err_key)
            hit("misfiring-automation", "worker-after-failed-plan",
                "a worker ran after the planner returned no usable plan",
                "Stop before worker dispatch when ORCHESTRATOR_RESULT_JSON is missing or invalid.",
                row_evidence(src, stamp), orch_ev)
        if row.get("outcome") in ERROR_OUTCOMES and banner:
            banner_ev = None
            if orch_path.is_file() and banner.group(0) in orch_raw:
                banner_ev = evidence(orch_path, banner.group(0))
            elif orch_path.is_file():
                banner_ev = source_key_evidence(orch_path, "summary")
            hit("misleading-surface", "quota-recorded-as-error",
                "a planner's usage limit was recorded as an error",
                "Record a limit banner as quota-paused with its provider and reset, not error.",
                banner_ev, row_evidence(src, stamp))
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
                    evs(row_evidence(src, stamp, detail))))
        reviews_path = (run / "reviews.jsonl").resolve()
        if reviews_path.is_file():
            for line in iter_jsonl_lines(reviews_path):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("verdict") == "unparsed":
                    hit("misfiring-automation", "review-unparsed",
                        "a review round ended with no VERDICT line (read as findings)",
                        "Classify the reviewer's failure (limit, capacity, crash) and fall back instead.",
                        evidence(reviews_path, line))
        if row.get("outcome") in SHIPPED and str(row.get("detail") or "").startswith("merged"):
            hit("misfiring-automation", "merged-unreviewed", "an item merged without a completed review",
                "Find why the reviewer was unavailable and make the worker wait or defer instead.",
                row_evidence(src, stamp, row.get("detail")))
        for path in ((run / "worker-output.log").resolve(), (root / "logs" / f"run-{stamp}.log").resolve()):
            if not path.is_file():
                continue
            text = read_text(path)
            line = next((c for c in executed_commands(text) if runs_git_stash(c)), None)
            if line and line in text:
                hit("misfiring-automation", "git-stash", "a builder ran git stash (shared across worktrees)",
                    "Keep the never-stash rule in SAFETY.md and the builder prompt; commit on the branch instead.",
                    evidence(path, line))
                break
        if row.get("outcome") in QUOTA_OUTCOMES and (row.get("quota") or {}).get("provider"):
            quiet.append(non_finding(f"{stamp} paused for quota",
                                     "a quota pause that names its provider and reset is the gate working, "
                                     "not a tooling failure",
                                     evs(row_evidence(src, stamp))))
        elif row.get("outcome") == "skipped-unchanged":
            quiet.append(non_finding(f"{stamp} skipped unchanged", "the unchanged-world gate saved a planner call",
                                     evs(row_evidence(src, stamp, row.get("detail")))))
        elif row.get("action") in ("blocked_no_item", "nothing_qualified") and not planner_failed \
                and row.get("outcome") not in ERROR_OUTCOMES:
            quiet.append(non_finding(f"{stamp} found no work", "a valid no-work plan with its bookkeeping",
                                     evs(row_evidence(src, stamp))))
        if row.get("worker_result_source") == "stdout-fence":
            hit("fragile-tooling", "stdout-fence", "a worker skipped its result file",
                "Make the worker write worker-result.json before its summary.",
                row_evidence(src, stamp, "stdout-fence"))

    for path in sorted((root / "batch-verdicts").glob("*.json")):
        path = path.resolve()
        v = read_json(path) or {}
        if v.get("stamp") in wanted:
            raw = read_text(path)
            m = LIMIT_BANNER.search(raw) or LIMIT_BANNER.search(v.get("summary") or "")
            if m and path.is_file():
                span = m.group(0) if m.group(0) in raw else None
                ev = evidence(path, span) if span else source_key_evidence(path, "summary")
                hit("wrong-knowledge", "contaminated-verdict", "a no-work verdict was saved from a limit banner",
                    "Refuse to save (and invalidate) verdicts whose source run had a planner error.",
                    ev)

    for st in stages:
        for b in st.get("blockers") or []:
            ev = blocker_repair_evidence(b, st, root)
            if ev:
                hit("fragile-tooling", f"followup-{b.get('kind')}",
                    f"a follow-up stage was blocked: {(b.get('reason') or '')[:100]}",
                    "Fix the step that blocked the follow-up so it can finish unattended.", ev)
            elif b.get("kind") != "quota":
                quiet_ev = stage_blocker_evidence(root, st, b)
                quiet.append(non_finding(
                    f"{st.get('key')} blocked ({b.get('kind')})",
                    "an expected safety stop or deliberate gate is not a tooling defect",
                    [quiet_ev] if quiet_ev else []))

    findings = list(groups.values())
    for f in [f for f in findings if f["kind"] == "fragile-tooling" and f["id"] == finding_id(stage, repo, "fragile-tooling", "stdout-fence")]:
        if len(f["evidence"]) < 2:
            findings.remove(f)
            quiet.append(non_finding("one result recovered from stdout", "the fallback exists for this; one is not a pattern",
                                     f["evidence"]))
    return findings, quiet


def corpus_files(root, stamps, run_dirs=None, stages=None):
    """Corpus files for this stage's stamps (+ cleanup states). Original runs.jsonl only."""
    root = Path(root).resolve()
    wanted = set(stamps)
    files = []
    runs_path = root / "runs.jsonl"
    if runs_path.exists():
        for line in iter_jsonl_lines(runs_path):
            try:
                if json.loads(line).get("stamp") in wanted:
                    files.append(runs_path.resolve())
                    break
            except ValueError:
                continue
    for stamp in stamps:
        run = run_dir(root, stamp, run_dirs)
        files += [run / n for n in ("orchestrator-result.json", "worker-result.json", "worker-status.json",
                                    "reviews.jsonl", "worker-output.log", "complete")]
        files.append(root / "logs" / f"run-{stamp}.log")
    for path in sorted((root / "batch-verdicts").glob("*.json")):
        v = read_json(path) or {}
        if v.get("stamp") in wanted:
            files.append(path.resolve())
    for st in stages or []:
        path = stage_state_path(root, st)
        if path is not None and path.is_file():
            files.append(path)
    return [f.resolve() for f in files if Path(f).exists()]


def evidence_holds(ev, files, stamps=None):
    """True when the quoted text is in the cited corpus file (and belongs to these stamps)."""
    allowed = {str(Path(f).resolve()): Path(f).resolve() for f in files}
    try:
        source = Path(ev.get("source") or "").resolve()
    except OSError:
        return False
    path = allowed.get(str(source))
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
