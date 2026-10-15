#!/usr/bin/env python3
"""The repair half of advance-roadmap's follow-ups (followup.py): usage feasibility, the builder,
the independent review and landing.

A repair is delegated, never done here: worker.sh builds on a follow-up branch in its own workspace
(a Superset workspace, or a plain worktree outside ~/.claude when ADVANCE_ROADMAP_SUPERSET=0) and
stops at an open PR; review.sh has a second provider review it; land() merges only when the PR's
checks pass, then verifies the deploy. Usage is re-read (usage.py gates) before every model step.
Shared stage-state helpers live here too, so followup.py and this module agree on the layout.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import followscan  # noqa: E402
import followticket  # noqa: E402
import supervisor as shared  # noqa: E402

ROOT = shared.ROOT
STATE = ROOT / "followups"
FOLLOWUP_ENV = "ADVANCE_ROADMAP_FOLLOWUP"
MAX_ROUNDS = 3
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_POLL_S", "15"))
WORKER_POLL_S = float(os.environ.get("ADVANCE_ROADMAP_WORKER_POLL_S", "10"))
WORKER_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_WORKER_TIMEOUT_MIN", "360")) * 60
CHECKS_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_CHECKS_TIMEOUT_S", "1800"))
DEPLOY_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_TIMEOUT_S", "900"))
DEPLOY_NONE_AFTER_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_DEPLOY_NONE_S", "180"))
GH = os.environ.get("ADVANCE_ROADMAP_GH_BIN", "gh")
AI_TOOLS_HOME = Path(os.environ.get("AI_TOOLS_HOME", str(Path.home() / ".ai-tools")))
# review.sh's reviewer order for each builder; the builder may start only if one of these is free.
REVIEWERS = {"cursor": ("codex", "claude"), "codex": ("cursor", "claude"), "claude": ("codex", "cursor")}
OUT = ("over", "limited")


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


# ── usage ────────────────────────────────────────────────────────────────────

def usage(*args, timeout=180):
    p = subprocess.run([sys.executable, str(ROOT / "lib" / "usage.py"), "--root", str(ROOT), *args],
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout


def gates(role):
    """usage.py gates for a role (refreshed), or None when usage could not be read."""
    try:
        rc, out = usage("gates", "--role", role)
        doc = json.loads(out) if rc == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    g = doc.get("gates") if isinstance(doc, dict) else None
    return g if isinstance(g, list) and g else None


def gate_one(role, provider):
    try:
        rc, out = usage("gate", "--role", role, "--provider", provider)
        if rc not in (0, 3):
            return None
        return {**json.loads(out), "available": rc == 0}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def quota_record(role, primary=None):
    try:
        rc, out = usage("quota-record", "--role", role, *(["--primary", primary] if primary else []))
        doc = json.loads(out) if rc == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        doc = None
    return doc if isinstance(doc, dict) else None


def feasibility(st, step, builder=None):
    """Who may take the next step: ("ok", chain) | ("quota", quota_record) | ("unknown", reason).

    A builder is only usable when a reviewer review.sh would pick for it is under its gate too —
    the required review is part of whether a repair can start at all."""
    gw, gr = gates("worker"), gates("reviewer")
    st["usage_snapshots"] = (st.get("usage_snapshots") or [])[-9:] + [
        {"at": now_iso(), "step": step, "worker": gw, "reviewer": gr}]
    if gw is None or gr is None:
        return "unknown", "usage.py could not read provider usage"
    free_reviewers = {g["provider"] for g in gr if g.get("available")}
    if step == "build":
        free_builders = [g["provider"] for g in gw if g.get("available")]
        chain = [p for p in free_builders if free_reviewers.intersection(REVIEWERS.get(p, ()))]
        if chain:
            return "ok", chain
        if not free_builders:
            if any(g.get("state") in OUT for g in gw + gr):
                q = quota_record("worker")
                if q and q.get("provider"):
                    return "quota", q
            return "unknown", "no worker is available and none reports a usage gate"
        eligible = {r for p in free_builders for r in REVIEWERS.get(p, ())}
    else:
        eligible = set(REVIEWERS.get(builder, ()))
        if free_reviewers.intersection(eligible):
            return "ok", [builder]
    q = reviewer_quota(eligible)
    if q:
        return "quota", q
    return "unknown", "no reviewer is available and none reports a usage gate"


def reviewer_quota(eligible):
    """usage.py's reviewer quota record narrowed to the reviewers review.sh would actually try,
    or None when none of them is over its gate or limited. An excluded reviewer (the builder
    itself) being out says nothing about when this review can run."""
    q = quota_record("reviewer")
    rows = [p for p in (q or {}).get("providers") or []
            if isinstance(p, dict) and p.get("provider") in eligible]
    out = [p for p in rows if p.get("state") in OUT]
    if not out:
        return None
    resets = [p for p in out if p.get("reset_epoch")]
    first = min(resets, key=lambda p: p["reset_epoch"]) if resets else out[0]
    return {**q, "provider": first["provider"], "reset_epoch": first.get("reset_epoch"),
            "reset_at": first.get("reset_at"), "providers": rows,
            "cause": "; ".join(f"{p['provider']}: {p.get('cause') or p.get('reason') or p['state']}" for p in out)}


# ── repos and workspaces ─────────────────────────────────────────────────────

def repo_path(repo):
    code = shared.code_dir()
    return Path(code) / repo if code and repo else None


def git(*args, cwd=None, check=False):
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, stdin=subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p


def origin_slug(repo_dir):
    """jnelken/<name> from the configured origin URL (not insteadOf-expanded), or None."""
    url = git("-C", str(repo_dir), "config", "--get", "remote.origin.url").stdout.strip()
    m = re.fullmatch(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)(jnelken/[^/\s]+?)(?:\.git)?",
                     url)
    return m.group(1) if m else None


def inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def safe_repo(repo):
    """(repo_dir, "") for a personal repo a follow-up may repair, else (None, why)."""
    if not repo or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo):
        return None, f"invalid repo {repo!r}"
    d = repo_path(repo)
    if not d or not (d / ".git").exists():
        return None, f"no checkout for {repo} under the personal code dir"
    if (d / ".noroadmap").exists():
        return None, f"{repo} has opted out (.noroadmap)"
    if not origin_slug(d):
        return None, f"{repo}'s origin is not a jnelken GitHub repo"
    for forbidden in (AI_TOOLS_HOME, Path.home() / ".claude"):
        if inside(d, forbidden):
            return None, f"{d} resolves into {forbidden}: never repair through the deploy clone or installed symlinks"
    return d, ""


def default_base(repo_dir):
    head = git("-C", str(repo_dir), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if head:
        return head
    ok = git("-C", str(repo_dir), "rev-parse", "--verify", "--quiet", "origin/main").returncode == 0
    return "origin/main" if ok else "origin/master"


def branch_for(st):
    return st.get("branch") or f"roadmap/followup-{st['key']}"


def worktree_of(repo_dir, branch):
    out = git("-C", str(repo_dir), "worktree", "list", "--porcelain").stdout
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[9:]
        elif line == f"branch refs/heads/{branch}" and path:
            return Path(path)
    return None


def worktree_home(st):
    """Where a non-Superset follow-up builds: outside ~/.claude, so no edit lands near the deploy."""
    base = Path(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_WORKTREES",
                               str(Path.home() / ".cache" / "advance-roadmap-followups")))
    return base / st["key"]


def ensure_worktree(st, repo_dir, branch):
    """A plain worktree for the follow-up branch (ADVANCE_ROADMAP_SUPERSET=0)."""
    existing = worktree_of(repo_dir, branch)
    if existing and existing.is_dir():
        return existing
    path = worktree_home(st)
    path.parent.mkdir(parents=True, exist_ok=True)
    git("-C", str(repo_dir), "fetch", "--quiet", "origin")
    if git("-C", str(repo_dir), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        git("-C", str(repo_dir), "worktree", "add", str(path), branch, check=True)
    elif git("-C", str(repo_dir), "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}").returncode == 0:
        git("-C", str(repo_dir), "worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}", check=True)
    else:
        git("-C", str(repo_dir), "worktree", "add", "-b", branch, str(path), default_base(repo_dir), check=True)
    return path


# ── builder ──────────────────────────────────────────────────────────────────

BUILDER_RULES = """HARD RULES for this follow-up repair (they override WORKER.md where they differ):
- You are a follow-up builder (advance-roadmap followup.py). Repair ONLY the evidenced findings below,
  each with a test where it is code. No speculative improvements, no unrelated backlog.
- Work only in your workspace checkout on branch {branch}. Never edit through ~/.ai-tools, ~/.claude or
  any installed symlink; ai-tools repairs land in the ai-tools workspace and deploy by push + install.
- Never use `git stash`. Never run git after a `cd` that may have failed: use `git -C <path>`.
- Do NOT write to Linear (the runner owns tickets). No ROADMAP edits, no Step 7c, no Step 8 run memory.
- Do NOT run Step 5b's review and do NOT merge: the runner has a second provider review your branch and
  merges only on a clean review with passing checks. Verify (Step 5), commit, push with -u, open the PR
  if none exists ({pr}), then stop.
- Report outcome `blocked-branch-left` with `branch` and `pr_url`, plus
  "validation": {{"status": "passed"|"failed", "commands": ["<command> → <result>", ...]}}.
  `failed` if any verification failed; never report shipped."""


def builder_brief(st, findings, rnd, review_text):
    lines = [f"Follow-up {st['stage']} `{st['key']}` for repo {st['repo']}"
             + (f", after item {st.get('item')} (run {st.get('stamp')})" if st.get("item") else
                f", for run {st.get('origin')}") + f". Round {rnd} of {MAX_ROUNDS}.",
             "", "Findings to repair (each with its evidence):", followticket.render_findings(findings)]
    if st.get("ticket_body"):
        lines += ["", "Ticket this was recovered from:", st["ticket_body"][:3000]]
    if review_text:
        lines += ["", f"The independent review of round {rnd - 1} reported these; fix the real ones "
                      "(say in your summary which you dismissed and why):", review_text[-6000:]]
    lines += ["", BUILDER_RULES.format(branch=branch_for(st), pr=st.get("pr_url") or "none yet")]
    return "\n".join(lines)


def builder_request(st, findings, rnd, review_text, branch, resume):
    return {"mode": "resume" if resume else "implement", "stamp": f"{st['key']}-r{rnd}",
            "orchestrator": {
                "action": "resume_worker" if resume else "dispatch_worker", "outcome_token": "pending-worker",
                "repo": st["repo"], "item": f"Follow-up {st['stage']}: {findings[0]['title'][:80]}",
                "branch": branch, "roadmap_path": None, "linear_id": None, "worker_mode": "standard",
                "work_kind": "feature", "directive_ticket": None,
                "worker_brief": builder_brief(st, findings, rnd, review_text),
                "archives": [], "blockers": [], "considered": [], "summary": f"follow-up {st['key']}"}}


def child_env(st, extra=None):
    env = {**os.environ, FOLLOWUP_ENV: "1", "ADVANCE_ROADMAP_TARGET_REPO": st["repo"],
           "PERSONAL_CODE_DIR": shared.code_dir() or ""}
    for k in ("ADVANCE_ROADMAP_LANE", "ADVANCE_ROADMAP_BATCH_PID", "ADVANCE_ROADMAP_THREAD"):
        env.pop(k, None)
    env.update(extra or {})
    return env


def run_builder(st, repo_dir, findings, rnd, chain, review_text):
    """("ok"|"quota"|"failed", detail): one worker.sh pass on the follow-up branch."""
    branch = branch_for(st)
    bdir = stage_dir(st["key"]) / f"build-r{rnd}-a{st['attempts']}"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "followup-child").write_text(st["key"] + "\n")
    resume = bool(st.get("pr_url") or rnd > 1 or worktree_of(repo_dir, branch)
                  or git("-C", str(repo_dir), "ls-remote", "--exit-code", "--heads", "origin", branch).returncode == 0)
    req = bdir / "request.json"
    write_json(req, builder_request(st, findings, rnd, review_text, branch, resume))
    result, status = bdir / "worker-result.json", bdir / "worker-status.json"
    cmd = [str(ROOT / "worker.sh"), "--stamp", f"{st['key']}-r{rnd}", "--request-file", str(req),
           "--providers", " ".join(chain), "--result-file", str(result), "--status-file", str(status)]
    st["branch"] = branch
    if os.environ.get("ADVANCE_ROADMAP_SUPERSET", "1") == "1":
        rc, why = run_in_superset(st, repo_dir, branch, bdir, cmd)
        if why:
            return "failed", why
    else:
        wt = ensure_worktree(st, repo_dir, branch)
        with open(bdir / "worker-output.log", "w") as out:
            rc = subprocess.run(["/bin/zsh", *cmd], env=child_env(st, {"SUPERSET_WORKSPACE_PATH": str(wt)}),
                                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
    stat, res = read_json(status) or {}, read_json(result) or {}
    if stat.get("used"):
        st["builder_provider"] = stat["used"]
        st["sessions"].append({"role": "builder", "round": rnd, "provider": stat["used"],
                               "session_id": stat.get("session_id")})
    if (not stat.get("used") and stat.get("limited")) or res.get("outcome") == "limit_hit":
        q = stat.get("quota") or quota_record("worker", stat.get("used"))
        if q and q.get("provider"):
            return "quota", q
        return "unknown", "the builder stopped on a usage limit, but no quota record names the provider"
    if rc == 3 and not stat.get("used"):
        # worker.sh exits 3 whenever no provider ran; only gate/limit evidence makes that quota.
        missing = " ".join(stat.get("missing") or [])
        return "failed", (f"no builder CLI could run (missing: {missing})" if missing
                          else "worker.sh ran no provider and reported no usage gate or limit")
    if res.get("outcome") == "shipped" or res.get("merge_commit"):
        return "failed", "the builder merged on its own; follow-up merges need the runner's clean review"
    validation = res.get("validation") or {}
    st["validation"]["builder"] = validation.get("status")
    st["validation"]["builder_commands"] = validation.get("commands") or []
    if res.get("outcome") != "blocked-branch-left" or not res.get("pr_url"):
        return "failed", f"builder ended {res.get('outcome') or f'exit {rc} with no result'}: {(res.get('summary') or '')[:300]}"
    if validation.get("status") != "passed":
        return "failed", f"builder verification did not pass: {validation.get('commands') or res.get('summary', '')[:300]}"
    st["branch"], st["pr_url"] = res.get("branch") or branch, res["pr_url"]
    return "ok", None


def run_in_superset(st, repo_dir, branch, bdir, cmd):
    """Run worker.sh in a Superset workspace terminal for the branch and wait: (rc, why_not_run)."""
    launcher = bdir / "worker-launch.zsh"
    env = child_env(st)
    lines = ["#!/bin/zsh"]
    for k, v in sorted(env.items()):
        if (k.startswith("ADVANCE_ROADMAP_") or k in ("PERSONAL_CODE_DIR", FOLLOWUP_ENV)) \
                and not re.search(r"WEBHOOK|SECRET|TOKEN|KEY", k):
            lines.append(f"export {k}={shell_quote(v)}")
    # The launcher registers itself in the shared in-flight registry (supervisor.inflight_repos)
    # before the builder starts and unregisters before recording its exit, so every lane, thread
    # and drain stays out of the repo even if the drain that launched it dies first.
    registry = shell_quote(str(inflight_file(st)))
    lines += [f"mkdir -p {shell_quote(str(inflight_file(st).parent))}",
              f"print -r -- $$ > {registry}",
              f"print -r -- $$ > {shell_quote(str(bdir / 'worker.pid'))}",
              " ".join(shell_quote(c) for c in cmd) + f" 2>&1 | tee {shell_quote(str(bdir / 'worker-output.log'))}",
              "rc=${pipestatus[1]}",
              f"rm -f {registry}",
              f"print -r -- $rc > {shell_quote(str(bdir / 'worker.exit'))}"]
    launcher.write_text("\n".join(lines) + "\n")
    launcher.chmod(0o700)
    p = subprocess.run([sys.executable, str(ROOT / "lib" / "workspaces.py"), "launch", "--repo", str(repo_dir),
                        "--branch", branch, "--name", f"followup {st['repo']} {st['key']}",
                        "--command", f"/bin/zsh {shell_quote(str(launcher))}", "--out", str(bdir / "workspace.json")],
                       capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return None, f"could not create the Superset workspace for {branch}: {(p.stderr or p.stdout).strip()[-300:]}"
    waited, pid = 0.0, None
    while not (bdir / "worker.exit").exists():
        time.sleep(WORKER_POLL_S)
        waited += WORKER_POLL_S
        if pid is None and (bdir / "worker.pid").exists():
            pid = int((bdir / "worker.pid").read_text().strip() or 0) or None
        if pid is None and waited >= 180:
            return None, "the workspace terminal never started the builder"
        if pid and not pid_alive(pid) and not (bdir / "worker.exit").exists():
            inflight_file(st).unlink(missing_ok=True)   # its terminal died before it could unregister
            return None, "the builder's terminal exited without an exit code"
        if waited >= WORKER_TIMEOUT_S:
            return None, f"the builder was still running after {WORKER_TIMEOUT_S / 60:.0f}m"
    try:
        return int((bdir / "worker.exit").read_text().strip()), None
    except ValueError:
        return 1, None


def inflight_file(st):
    return ROOT / "inflight" / st["repo"] / f"followup-{st['key']}.pid"


def builder_alive(key):
    """True while a builder this stage launched is still running (its terminal outlived a drain)."""
    for pid_file in stage_dir(key).glob("build-*/worker.pid"):
        if (pid_file.parent / "worker.exit").exists():
            continue
        try:
            if pid_alive(int(pid_file.read_text().strip())):
                return True
        except ValueError:
            continue
    return False


def shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── review ───────────────────────────────────────────────────────────────────

def run_review(st, repo_dir, rnd):
    """(verdict, text) from review.sh on the follow-up branch's checkout. The builder's provider
    must be known (the caller checks): it is what makes review.sh pick a different one."""
    wt = worktree_of(repo_dir, branch_for(st)) or ensure_worktree(st, repo_dir, branch_for(st))
    log_path = stage_dir(st["key"]) / "reviews.jsonl"
    before = len(followscan.read_jsonl(log_path))
    git("-C", str(wt), "fetch", "--quiet", "origin")
    git("-C", str(wt), "merge", "--ff-only", "--quiet", f"origin/{branch_for(st)}")
    sha = git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
    p = subprocess.run(["/bin/zsh", str(ROOT / "review.sh"), "--repo", str(wt), "--worker", st["builder_provider"],
                        "--round", str(rnd), "--log", str(log_path)], capture_output=True, text=True,
                       env=child_env(st), timeout=3600, stdin=subprocess.DEVNULL)
    rows = followscan.read_jsonl(log_path)[before:]
    row = rows[-1] if rows else {}
    verdict = row.get("verdict") or ("unavailable" if p.returncode == 3 else "error")
    # review.sh logs a short head; the full SHA it reviewed is what landing must match.
    reviewed = sha if sha and row.get("head") and sha.startswith(str(row["head"])) else None
    st["validation"]["reviews"].append({"round": rnd, "verdict": verdict, "reviewer": row.get("reviewer"),
                                        "head": row.get("head"), "sha": reviewed})
    st["reviewed_sha"] = reviewed if verdict == "clean" else None
    if row.get("reviewer"):
        st["sessions"].append({"role": "reviewer", "round": rnd, "provider": row["reviewer"],
                               "session_id": row.get("session_id")})
    if verdict == "error":
        return "error", (p.stderr or p.stdout).strip()[-500:]
    return verdict, p.stdout


# ── landing ──────────────────────────────────────────────────────────────────

def gh(*args, timeout=120):
    try:
        return subprocess.run([GH, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([GH, *args], 1, "", str(e))


def pr_checks(url):
    """passed | none | failed | pending (still running after the timeout)."""
    deadline = time.time() + CHECKS_TIMEOUT_S
    while True:
        p = gh("pr", "checks", url)
        text = (p.stdout + p.stderr).lower()
        if "no checks reported" in text:
            return "none"
        if p.returncode == 0:
            return "passed"
        if p.returncode != 8:
            return "failed"
        if time.time() >= deadline:
            return "pending"
        time.sleep(POLL_S)


DEPLOY_NAME = re.compile(r"vercel|netlify|deploy", re.I)


def deploy_state(slug, sha):
    """success | failure | pending | None (both APIs answered; nothing deploy-looking yet) | error
    (an API call failed or answered garbage, so absence is not proven)."""
    states = []
    for path, key in ((f"repos/{slug}/commits/{sha}/status", "statuses"),
                      (f"repos/{slug}/commits/{sha}/check-runs", "check_runs")):
        p = gh("api", path)
        try:
            doc = json.loads(p.stdout) if p.returncode == 0 else None
            rows = doc.get(key) if isinstance(doc, dict) else None
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            return "error"
        for r in rows:
            if key == "statuses" and DEPLOY_NAME.search(r.get("context") or ""):
                states.append({"success": "success", "pending": "pending"}.get(r.get("state"), "failure"))
            elif key == "check_runs" and DEPLOY_NAME.search(r.get("name") or ""):
                if r.get("status") != "completed":
                    states.append("pending")
                else:
                    states.append("success" if r.get("conclusion") in ("success", "neutral", "skipped") else "failure")
    if not states:
        return None
    if "failure" in states:
        return "failure"
    return "pending" if "pending" in states else "success"


def verify_deploy(slug, sha, repo_dir):
    """Step 7b's watch, without fixing forward: success | none | failed | timeout | unverified.

    `none` needs DEPLOY_NONE_AFTER_S of successful, empty API answers in a repo with no deploy
    config; a failed API call restarts that clock, and ending on one is `unverified`."""
    has_config = any((repo_dir / n).exists() for n in ("vercel.json", ".vercel", "netlify.toml"))
    start = time.time()
    absent_since = None
    while True:
        s = deploy_state(slug, sha)
        if s == "success":
            return "success"
        if s == "failure":
            return "failed"
        now = time.time()
        absent_since = (absent_since or now) if s is None else None
        if absent_since is not None and not has_config and now - absent_since >= DEPLOY_NONE_AFTER_S:
            return "none"
        if now - start >= DEPLOY_TIMEOUT_S:
            return "unverified" if s == "error" else "timeout"
        time.sleep(POLL_S)


def land(st, repo_dir, findings):
    """Checks → merge → deploy → install, resumable after the merge: (ok, reason, retry).

    Fail-closed: the PR merges only at the head the clean review saw, and a stage is never done
    until its deploy is verified (and, for ai-tools, this machine's install succeeded)."""
    v = st["validation"]
    if not st.get("merged"):
        reviewed = st.get("reviewed_sha")
        if not reviewed:
            st["next"] = {"step": "review", "round": (st.get("next") or {}).get("round", 1)}
            return False, "no clean review recorded for an exact commit; re-reviewing before any merge", True
        checks = pr_checks(st["pr_url"])
        v["checks"] = checks
        if checks not in ("passed", "none"):
            return False, f"PR checks {checks} on {st['pr_url']}", checks == "pending"
        head = gh("pr", "view", st["pr_url"], "--json", "headRefOid", "--jq", ".headRefOid")
        head_sha = head.stdout.strip() if head.returncode == 0 else ""
        if head_sha != reviewed:
            st["reviewed_sha"] = None
            st["next"] = {"step": "review", "round": (st.get("next") or {}).get("round", 1)}
            return False, (f"PR head {head_sha[:9] or 'unreadable'} is not the reviewed commit {reviewed[:9]}; "
                           "re-reviewing before any merge"), True
        p = gh("pr", "merge", st["pr_url"], "--merge", "--match-head-commit", reviewed)
        if p.returncode != 0 and re.search(r"merge commits? (are|is) not allowed", p.stdout + p.stderr, re.I):
            p = gh("pr", "merge", st["pr_url"], "--squash", "--match-head-commit", reviewed)
        if p.returncode != 0:
            return False, f"could not merge {st['pr_url']}: {(p.stderr or p.stdout).strip()[:300]}", False
        st["merged"] = True
    if not st.get("merge_commit"):
        sha = gh("pr", "view", st["pr_url"], "--json", "mergeCommit", "--jq", ".mergeCommit.oid").stdout.strip()
        if not sha:
            return False, f"merged {st['pr_url']} but could not read its merge commit", True
        st["merge_commit"] = sha
        sync_root_checkout(repo_dir)
    sha = st["merge_commit"]
    if v.get("deploy") not in ("success", "none"):
        v["deploy"] = verify_deploy(origin_slug(repo_dir), sha, repo_dir)
        if v["deploy"] not in ("success", "none"):
            return False, f"merged {sha[:9]} but the deploy is {v['deploy']}", v["deploy"] in ("timeout", "unverified")
    if st["repo"] == followscan.TOOLING_REPO and v.get("install") not in ("installed", "skipped"):
        v["install"] = install_ai_tools(sha)
        if v["install"] not in ("installed", "skipped"):
            return False, f"merged {sha[:9]} and deployed, but this machine's ai-tools install failed: {v['install']}", True
    return True, "", False


def sync_root_checkout(repo_dir):
    """WORKER.md Step 7's post-merge pull: only a clean root checkout on its default branch."""
    st = git("-C", str(repo_dir), "status", "--porcelain")
    branch = git("-C", str(repo_dir), "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if st.returncode == 0 and not st.stdout.strip() and branch == default_base(repo_dir).split("/", 1)[-1]:
        git("-C", str(repo_dir), "pull", "--ff-only", "--quiet")
    else:
        git("-C", str(repo_dir), "fetch", "--quiet", "origin")


def install_ai_tools(merge_commit):
    """ai-tools repairs reach this machine through the deploy clone's own install (clone-lock),
    never by editing it: run its install.sh once the PR is merged.

    install.sh exits 0 even when it skips the update (a dirty deploy clone, a failed fetch), so
    success is the deploy clone's HEAD containing the merge commit — a read-only git query."""
    if os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_INSTALL", "1") != "1":
        return "skipped"
    script = AI_TOOLS_HOME / "install.sh"
    if not script.exists():
        return "no deploy clone"
    p = subprocess.run([str(script)], capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return f"install.sh exit {p.returncode}"
    clone = str(AI_TOOLS_HOME)
    head = git("-C", clone, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        return f"could not read the deploy clone's HEAD: {(head.stderr or head.stdout).strip()[:200]}"
    fetched = git("-C", clone, "cat-file", "-e", f"{merge_commit}^{{commit}}").returncode == 0
    if fetched and git("-C", clone, "merge-base", "--is-ancestor", merge_commit, "HEAD").returncode == 0:
        return "installed"
    return f"install.sh exited 0 but the deploy clone (HEAD {head.stdout.strip()}) does not contain {merge_commit[:9]}"
