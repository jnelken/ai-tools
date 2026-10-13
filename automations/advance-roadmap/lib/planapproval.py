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
import subprocess
import sys
import tempfile
from pathlib import Path

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

def run_codex(prompt, code_dir):
    codex = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
    model = os.environ.get("ADVANCE_ROADMAP_ORCH_CODEX_MODEL", "gpt-5.6-sol")
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.txt"
        proc = subprocess.run(
            [codex, "exec", "-o", str(last), "-m", model, "-c", "model_reasoning_effort=high",
             "-s", "read-only", "-C", code_dir, "--add-dir", code_dir, "--skip-git-repo-check", prompt],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1500)
        return (last.read_text(errors="replace") if last.exists() else "") or proc.stdout


def run_claude(prompt, code_dir):
    claude = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")
    model = os.environ.get("ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL", "claude-opus-5")
    proc = subprocess.run(
        [claude, "-p", prompt, "--model", model, "--effort", "high", "--permission-mode", "plan",
         "--permission-prompts", "none", "--disallowedTools", "Edit,Write,NotebookEdit",
         "--add-dir", code_dir, "--output-format", "text"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1500)
    return proc.stdout


RUNNERS = {"codex": run_codex, "claude": run_claude}


def provider_order(root):
    """Codex first, Claude only as the fallback, narrowed by what usage.py says is available."""
    pick = ""
    try:
        pick = subprocess.run([sys.executable, str(Path(root) / "lib" / "usage.py"), "--root", str(root),
                               "pick-orchestrator"], capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if pick == "claude":
        return ["claude", "codex"]
    if pick == "codex":
        return ["codex", "claude"]
    return []


def approve(root, request, doc, code_dir, runners=None, providers=None):
    runners = runners or RUNNERS
    providers = providers if providers is not None else provider_order(root)
    prompt = build_prompt(request, doc, str(Path(code_dir) / request["repo"]))
    model, error = None, "no orchestrator provider is available"
    for name in providers:
        try:
            model = extract_fence(runners[name](prompt, code_dir))
        except (OSError, subprocess.SubprocessError) as exc:
            model, error = None, f"{name}: {exc}"
            continue
        if model:
            break
        error = f"{name}: no {FENCE} fence in its answer"
    result = combine(request, model)
    if model is None:
        result["error"] = error
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
