#!/usr/bin/env python3
"""Plan approval for a repo that has more than one thread planned in a daily batch.

daily_batch.py calls this at the planning barrier, once per repo whose ready plans number two or
more. A read-only model (Codex Sol, then Claude Opus — never Claude first) reads the plans and
PLAN_APPROVAL.md, then rules on every thread after the first: approve (run in parallel), serialize
(start only after an earlier thread has finished and merged) or defer (don't run today). A few
mechanical checks run first and can only make the ruling stricter. Thread 1 always proceeds, so a
failure here costs the extra threads and nothing else.

  planapproval.py --root R approve --request req.json --out result.json
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from usage import (  # noqa: E402
    ROLE_PROVIDERS, limit_hit, live_limit_banner, read_attempts, saved_quota_record,
)

SEVERITY = {"approve": 0, "serialize": 1, "defer": 2}
FENCE = "PLAN_APPROVAL_JSON"

# Overlap every pair of threads in one repo has, whatever their touchpoints say. The approver need
# not rediscover these; the result always lists them as acknowledged, with how each is handled.
STANDING_CONFLICTS = [
    {"area": "ROADMAP.md / docs/plans status moves",
     "handling": "Each thread moves only its own item to Shipped and renumbers; after merging "
                 "origin/main into the branch, re-read the roadmap and redo the renumber."},
    {"area": "lockfiles",
     "handling": "Never hand-merge a lockfile; take origin/main's and re-run the install."},
    {"area": "second PR needs origin/main merged in before it can merge",
     "handling": "WORKER.md Step 7: merge origin/main into the branch, re-run Step 5, push, retry."},
    {"area": "back-to-back production deploys",
     "handling": "Step 7b watches the deploy of your own merge commit; a sibling's newer deploy "
                 "failing is not yours to fix forward."},
]


def paths_overlap(a, b):
    """Touchpoints are repo-relative files, directories or globs; two overlap if one covers the other."""
    a, b = a.strip().strip("/"), b.strip().strip("/")
    if not a or not b:
        return False
    if a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def mechanical_checks(plans):
    """{stamp: (verdict, reason, after)} for every plan after the first. Strictest-wins with the model."""
    out = {}
    for index, plan in enumerate(plans[1:], 1):
        earlier = plans[:index]
        touch = [t for t in plan.get("touchpoints") or [] if isinstance(t, str) and t.strip()]
        if not touch:
            out[plan["stamp"]] = ("defer", "declares no touchpoints, so no overlap can be ruled out", None)
            continue
        same = next((p for p in earlier if (plan.get("linear_id") and p.get("linear_id") == plan.get("linear_id"))
                     or (plan.get("branch") and p.get("branch") == plan.get("branch"))), None)
        if same:
            out[plan["stamp"]] = ("defer", f"same ticket or branch as thread {same.get('thread')}", None)
            continue
        hit = None
        for prior in earlier:
            for mine in touch:
                for theirs in prior.get("touchpoints") or []:
                    if isinstance(theirs, str) and paths_overlap(mine, theirs):
                        hit = (prior, mine, theirs)
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            prior, mine, theirs = hit
            out[plan["stamp"]] = ("serialize",
                                  f"touches {mine} which thread {prior.get('thread')} also touches ({theirs})",
                                  prior["stamp"])
    return out


def build_prompt(request, doc, repo_dir):
    return f"""Read and follow {doc} exactly. You are the PLAN APPROVER for repo `{request['repo']}`
(checkout: {repo_dir}). You are READ-ONLY: do not edit, commit, push or call any ticket system.

These plans were made independently by separate planners and will run as parallel threads in this
repo. Judge each thread after the first against the earlier ones.

Plans (JSON):
{json.dumps(request['plans'], indent=2)}

Emit exactly one ```{FENCE} fence as {doc} describes, then stop."""


def extract_fence(text):
    start = f"```{FENCE}"
    end = len(text)
    while (i := text.rfind(start, 0, end)) >= 0:
        end = i
        nl = text.find("\n", i)
        j = text.find("```", nl + 1) if nl >= 0 else -1
        if j < 0:
            continue
        try:
            return json.loads(text[nl + 1:j].strip())
        except ValueError:
            continue
    return None


def structured_result_from_blobs(*blobs):
    """First Claude/Cursor-shaped JSON object found in the given answer/log blobs, or None.

    Runners may return --output-format json on stdout (or a path written into a temp file that
    then becomes the answer). load_provider_result reads a file; here the blob is already text.
    """
    for blob in blobs:
        if not blob or not str(blob).strip():
            continue
        text = str(blob)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and ("result" in obj or "is_error" in obj or "outcome" in obj):
                return obj
        except ValueError:
            pass
        for line in reversed(text.splitlines()):
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "is_error" in obj or "outcome" in obj):
                return obj
    return None


def combine(request, model):
    """Final ruling per plan: the stricter of the mechanical checks and the model; thread 1 always approves."""
    plans = request["plans"]
    mech = mechanical_checks(plans)
    by_stamp = {d.get("stamp"): d for d in (model or {}).get("decisions", []) if isinstance(d, dict)}
    decisions = []
    for index, plan in enumerate(plans):
        stamp = plan["stamp"]
        if index == 0:
            decisions.append({"stamp": stamp, "thread": plan.get("thread"), "verdict": "approve",
                              "reason": "first thread always proceeds", "after": None})
            continue
        m = by_stamp.get(stamp) or {}
        verdict, reason, after = "defer", "the approver did not rule on this plan", None
        if m.get("verdict") in SEVERITY:
            verdict, reason, after = m["verdict"], str(m.get("reason") or ""), m.get("after")
        if model is None:
            verdict, reason = "defer", "no approver ruling was obtained"
        mv = mech.get(stamp)
        if mv and SEVERITY[mv[0]] > SEVERITY[verdict]:
            verdict, reason, after = mv[0], mv[1], mv[2]
        elif mv and mv[0] == verdict and not reason:
            reason = mv[1]
        if verdict == "serialize":
            valid = {p["stamp"] for p in plans[:index]}
            after = after if after in valid else plans[index - 1]["stamp"]
        else:
            after = None
        conflicts = [c for c in m.get("acknowledged_conflicts") or [] if isinstance(c, dict)]
        decisions.append({"stamp": stamp, "thread": plan.get("thread"), "verdict": verdict,
                          "reason": reason, "after": after, "acknowledged_conflicts": conflicts})
    return {"ok": True, "repo": request["repo"], "decisions": decisions,
            "standing_conflicts": STANDING_CONFLICTS,
            "summary": str((model or {}).get("summary") or "")}


# ── model calls ───────────────────────────────────────────────────────────────

def default_codex_model():
    models = Path(__file__).with_name("models.sh")
    try:
        contents = models.read_text()
    except OSError as exc:
        raise RuntimeError(f"planapproval: cannot read model defaults: {models}: {exc}") from exc
    match = re.search(r"^CODEX_MODEL_DEFAULT=([A-Za-z0-9._-]+)[ \t]*$", contents, re.MULTILINE)
    if not match:
        raise RuntimeError(f"planapproval: missing CODEX_MODEL_DEFAULT assignment in {models}")
    return match.group(1)


def run_codex(prompt, code_dir):
    codex = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
    model = os.environ.get("ADVANCE_ROADMAP_ORCH_CODEX_MODEL", default_codex_model())
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.txt"
        proc = subprocess.run(
            [codex, "exec", "-o", str(last), "-m", model, "-c", "model_reasoning_effort=high",
             "-s", "read-only", "-C", code_dir, "--add-dir", code_dir, "--skip-git-repo-check", prompt],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1500)
        # Only the -o file is the answer: stdout echoes the prompt and PLAN_APPROVAL.md's examples.
        answer = last.read_text(errors="replace") if last.exists() else ""
        return answer, f"{proc.stdout}\n{proc.stderr}"


def run_claude(prompt, code_dir):
    claude = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")
    model = os.environ.get("ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL", "claude-opus-5")
    proc = subprocess.run(
        [claude, "-p", prompt, "--model", model, "--effort", "high", "--permission-mode", "plan",
         "--permission-prompts", "none", "--disallowedTools", "Edit,Write,NotebookEdit",
         "--add-dir", code_dir, "--output-format", "text"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1500)
    return proc.stdout, proc.stderr


# Each runner returns (final answer, everything else it printed). The fence is read from the
# answer only; a limit banner can be in either, and the CLIs often print it to stderr alone.
RUNNERS = {"codex": run_codex, "claude": run_claude}


def usage_cli(root, *args):
    return subprocess.run([sys.executable, str(Path(root) / "lib" / "usage.py"), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def provider_order():
    """Codex first, Claude only as the fallback. Every one is listed: whether it is under its gate
    is read right before it is tried, since a gate can open or close while the one before it runs."""
    return [n for n in ROLE_PROVIDERS["approver"] if n in RUNNERS]


def still_under_gate(root, name):
    """Exit 3 is the only "over its gate"; a usage.py failure is no reading and never blocks."""
    try:
        return usage_cli(root, "gate", "--role", "approver", "--provider", name).returncode != 3
    except (OSError, subprocess.SubprocessError):
        return True


def quota_record(root, hits, primary):
    """`usage.py quota-record --role approver`: every approver provider, with the limits hit here
    (`hits`: (provider, hit file)). When that fails, the same record from the gates as last saved.
    None when nobody is out."""
    specs = [f"{name}={path}" for name, path in hits]
    args = [a for spec in specs for a in ("--hit", spec)]
    if primary:
        args += ["--primary", primary]
    try:
        record = json.loads(usage_cli(root, "quota-record", "--role", "approver", *args).stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        record = None
    if not isinstance(record, dict):
        try:
            record = saved_quota_record(Path(root), "approver", read_attempts(None, specs), primary)
        except (OSError, ValueError, TypeError, KeyError):
            return None
    return record if isinstance(record, dict) and record.get("provider") else None


def approve(root, request, doc, code_dir, runners=None, providers=None):
    with tempfile.TemporaryDirectory() as tmp:
        return _approve(root, request, doc, code_dir, runners, providers, Path(tmp))


def _approve(root, request, doc, code_dir, runners, providers, tmp):
    runners = runners or RUNNERS
    providers = providers if providers is not None else provider_order()
    prompt = build_prompt(request, doc, str(Path(code_dir) / request["repo"]))
    model, error = None, "no orchestrator provider is under its usage gate"
    hits, gated = [], False
    for name in providers:
        if not still_under_gate(root, name):
            error = f"{error}; {name}: over its usage gate"
            gated = True
            continue
        try:
            answer, log = runners[name](prompt, code_dir)
        except (OSError, subprocess.SubprocessError) as exc:
            model, error = None, f"{name}: {exc}"
            continue
        model = extract_fence(answer)
        if isinstance(model, dict) and model:
            break
        model = None
        # Live-limit rule: banners only count when there is no successful fence (above) — same as
        # usage.live_limit_banner with no final answer. Pass structured JSON so is_error:true is a
        # live limit even on a zero-exit answer that is only the error result text.
        provider_result = structured_result_from_blobs(answer, log)
        banner = live_limit_banner(
            f"{answer or ''}\n{log or ''}", final_answer="", provider_result=provider_result)
        if banner:
            # Saved before the shared usage file is touched: the quota record keeps the banner and
            # its reset even if that update fails. Then gate this provider for every other role too.
            hit = tmp / f"limit-{name}.json"
            hit.write_text(json.dumps(limit_hit(name, banner, "approver")))
            try:
                usage_cli(root, "record-limit", "--provider", name, "--text", banner, "--scope", "approver")
            except (OSError, subprocess.SubprocessError):
                pass
            hits.append((name, hit))
            error = f"{name}: {banner}"
            continue
        error = f"{name}: no {FENCE} fence in its answer"
    result = combine(request, model)
    if model is None:
        result["error"] = error
        if hits or gated:
            quota = quota_record(root, hits, hits[-1][0] if hits else None)
            if quota:
                result["quota"] = quota
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get(
        "ADVANCE_ROADMAP_ROOT", "/Users/jake/.claude/automations/advance-roadmap"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("approve")
    a.add_argument("--request", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--doc", default=os.environ.get(
        "ADVANCE_ROADMAP_PLAN_APPROVAL_DOC",
        str(Path.home() / ".claude/skills/advance-roadmap/PLAN_APPROVAL.md")))
    a.add_argument("--code-dir", default=os.environ.get("PERSONAL_CODE_DIR", ""))
    args = ap.parse_args(argv)
    request = json.loads(Path(args.request).read_text())
    result = approve(args.root, request, args.doc, args.code_dir)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
