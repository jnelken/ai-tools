#!/usr/bin/env python3
"""Survey local Superset workspaces for advance-roadmap reaps.

Paths:
  banner     — finished-banner evidence on live tabs
  dead-pane  — zero terminals + diffmain empty net-new
  dead-pane  — merge-tree conflicts + PR MERGED or Linear Done/Canceled

Prints reapable / keep buckets. Does not delete anything — the skill drives
reap-workspace.sh after reviewing this output.

  survey.py [--project NAME] [--json] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

FINISHED_RE = re.compile(
    r"===\s*worker used=(\S+)\s+exit=(\d+)\s+finished\s+(.+?)\s*===\s*$",
    re.M,
)
TICKET_RE = re.compile(r"\b((?:DEV|CON|ENG|FE|BE)-\d+)\b")
SETUP_STRONG = [
    re.compile(p, re.I | re.M)
    for p in (
        r"^npm install\b",
        r"^npm ci\b",
        r"^pnpm install\b",
        r"^yarn install\b",
        r"^bun install\b",
        r"added \d+ packages",
        r"up to date, audited \d+",
        r"Done in \d+\.\d+s",
    )
]
MAIN_CANDIDATES = ("origin/main", "origin/master", "main", "master")
LINEAR_DONE = {"done", "canceled", "cancelled"}
# Reasons that Path B2 (PR / Linear finished) may override.
CONFLICT_OVERRIDE_REASONS = {"dead-pane-merge-conflicts"}


def run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def sjson(*args: str) -> Any:
    p = run("superset", *args, "--json")
    if p.returncode != 0:
        raise RuntimeError(f"superset {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    data = json.loads(p.stdout)
    if isinstance(data, dict) and "data" in data and len(data) <= 2:
        return data["data"]
    return data


def parse_finished_date(s: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(s.strip())
        return dt if dt and dt.tzinfo is not None else None
    except (TypeError, ValueError, IndexError):
        return None


def last_finished(raw: str) -> tuple[str, int, str, datetime | None] | None:
    matches = list(FINISHED_RE.finditer(raw))
    if not matches:
        return None
    m = matches[-1]
    return m.group(1), int(m.group(2)), m.group(3), parse_finished_date(m.group(3))


def is_setup(raw: str) -> bool:
    if not raw.strip():
        return False
    tail = raw[-2500:]
    if FINISHED_RE.search(tail) or re.search(r"===\s*worker (used|provider)=", tail):
        return False
    if any(r.search(raw) for r in SETUP_STRONG):
        return True
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) <= 40 and any(
        re.search(r"dotfiles: (applied|fast-forwarded)", ln) for ln in lines
    ):
        return True
    return False


def tab_text(stdout: str) -> str:
    """The buffer text of a `superset terminals read`.

    The CLI prints JSON with the buffer under `text`, where newlines are escaped;
    matching banners against the raw JSON never sees a line end, so every
    finished tab used to classify as `other`. Fall back to the raw output when
    it is not that JSON.
    """
    try:
        payload = json.loads(stdout)
    except ValueError:
        return stdout
    text = payload.get("text") if isinstance(payload, dict) else None
    return text if isinstance(text, str) else stdout


def classify_tab(raw: str) -> dict[str, Any]:
    fin = last_finished(raw)
    if fin:
        provider, code, raw_date, dt = fin
        kind = "finished0" if code == 0 else f"finished{code}"
        return {
            "kind": kind,
            "provider": provider,
            "exit": code,
            "finished_raw": raw_date,
            "finished_dt": dt.isoformat(sep=" ") if dt else None,
            "tickets_tail": sorted(set(TICKET_RE.findall(raw[-8000:]))),
        }
    if is_setup(raw):
        return {
            "kind": "setup",
            "exit": None,
            "finished_raw": None,
            "finished_dt": None,
            "tickets_tail": [],
        }
    return {
        "kind": "other",
        "exit": None,
        "finished_raw": None,
        "finished_dt": None,
        "tickets_tail": [],
    }


def ticket_from_label(*parts: str) -> str | None:
    blob = " ".join(p for p in parts if p)
    m = TICKET_RE.search(blob)
    return m.group(1).upper() if m else None


def github_repo(root: str) -> str | None:
    if not root or not os.path.isdir(root):
        return None
    p = run("git", "-C", root, "remote", "get-url", "origin")
    if p.returncode != 0:
        return None
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", (p.stdout or "").strip())
    return m.group(1) if m else None


def bulk_prs_by_branch(repos: set[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(repo, headRefName) -> PRs from one gh call per repo."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for repo in sorted(r for r in repos if r):
        p = run(
            "gh", "pr", "list", "--repo", repo, "--state", "all", "--limit", "500",
            "--json", "number,state,title,headRefName,mergedAt,closedAt,url,isDraft",
        )
        if p.returncode != 0:
            continue
        try:
            prs = json.loads(p.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(prs, list) or len(prs) >= 500:
            continue
        out[(repo, "")] = []  # Successful, complete lookup.
        for pr in prs:
            key = (repo, pr.get("headRefName") or "")
            out.setdefault(key, []).append(pr)
    return out


def best_pr(prs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not prs:
        return None

    def rank(pr: dict[str, Any]) -> int:
        return {"MERGED": 0, "OPEN": 1, "CLOSED": 2}.get((pr.get("state") or "").upper(), 9)

    return sorted(prs, key=rank)[0]


def linear_state_name(ticket: str) -> str | None:
    """Return Linear state.name for DEV-### via `linear issue view --json`."""
    if not ticket:
        return None
    p = run("linear", "issue", "view", ticket, "--json")
    if p.returncode != 0 or not (p.stdout or "").strip():
        return None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    st = data.get("state")
    if isinstance(st, dict):
        return st.get("name") or st.get("type")
    if isinstance(st, str):
        return st
    return None


def finished_override(
    entry: dict[str, Any],
    prs_index: dict[tuple[str, str], list[dict[str, Any]]],
    linear_cache: dict[str, str | None],
) -> tuple[bool, str]:
    """Path B2: conflicts + (PR MERGED | Linear Done/Canceled with no OPEN PR)."""
    repo = github_repo(entry.get("root") or "")
    branch = entry.get("branch") or ""
    prs = prs_index.get((repo or "", branch), []) if repo else []
    pr = best_pr(prs)
    pr_state = (pr.get("state") or "").upper() if pr else None
    has_open = any((p.get("state") or "").upper() == "OPEN" for p in prs)

    ticket = entry.get("label_ticket")
    if ticket and ticket not in linear_cache:
        linear_cache[ticket] = linear_state_name(ticket)
    lin = linear_cache.get(ticket) if ticket else None
    lin_l = (lin or "").strip().lower()
    lin_done = lin_l in LINEAR_DONE

    entry["pr_state"] = pr_state
    entry["pr_number"] = pr.get("number") if pr else None
    entry["pr_url"] = pr.get("url") if pr else None
    entry["linear_state"] = lin

    if pr_state == "MERGED":
        return True, f"PR#{pr.get('number')} MERGED"
    if has_open:
        return False, f"PR#{pr.get('number')} OPEN" if pr else "OPEN PR"
    if ticket and lin_done:
        if not repo or (repo, "") not in prs_index:
            return False, "PR lookup unavailable or incomplete"
        return True, f"{ticket}={lin}"
    if ticket and lin:
        return False, f"{ticket}={lin}"
    if pr_state == "CLOSED":
        return False, f"PR#{pr.get('number')} CLOSED (not merged)"
    if ticket:
        return False, f"{ticket}=unknown"
    return False, "no-PR no-ticket"


def same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


def resolve_main_ref(repo: str) -> str | None:
    for ref in MAIN_CANDIDATES:
        p = run("git", "-C", repo, "rev-parse", "--verify", "-q", ref)
        if p.returncode == 0:
            return ref
    return None


def fetch_once(roots: set[str]) -> dict[str, str | None]:
    """Fetch each owning clone once; return root -> main_ref (or None)."""
    main_refs: dict[str, str | None] = {}
    for root in sorted(roots):
        if not root or not os.path.isdir(root):
            main_refs[root] = None
            continue
        # Only fetch when origin exists; still resolve a local main otherwise.
        has_origin = run("git", "-C", root, "remote", "get-url", "origin").returncode == 0
        if has_origin:
            if run("git", "-C", root, "fetch", "origin", "--prune", "-q").returncode != 0:
                main_refs[root] = None
                continue
        main_refs[root] = resolve_main_ref(root)
    return main_refs


def held_paths() -> list[str]:
    """List process working directories; fail closed if inspection fails."""
    p = run("lsof", "-d", "cwd", "-Fn")
    if p.returncode != 0 or p.stderr.strip():
        raise RuntimeError("cannot reliably inspect process working directories")
    return [ln[1:] for ln in p.stdout.splitlines() if ln.startswith("n/")]


def is_held(wt: str, held: list[str]) -> bool:
    """True when a live process cwd is the worktree or inside it.

    Do not invert the test (worktree inside a broad cwd like $HOME) — that
    marks every workspace held.
    """
    if not wt:
        return False
    try:
        real = os.path.realpath(wt)
    except OSError:
        real = wt
    for h in held:
        try:
            hr = os.path.realpath(h)
        except OSError:
            hr = h
        if hr in ("/",) or hr == os.path.expanduser("~"):
            continue
        if hr == real or hr.startswith(real + os.sep):
            return True
    return False


def _ps(pid: str) -> tuple[str, str]:
    """(ppid, full command line) for a pid, or ("", "") when it vanished."""
    p = run("ps", "-ww", "-o", "ppid=,command=", "-p", pid)
    parts = p.stdout.strip().split(None, 1) if p.returncode == 0 else []
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def holders(wt: str, live_tabs: int | None = None) -> list[dict[str, Any]]:
    """Processes whose cwd is the worktree (or inside it), each classified.

    `idle_tab_shell` is true only for a Superset tab's own shell at rest: a
    login `zsh -l` reparented to launchd (PPID 1) with no child processes. A
    shell you opened by hand differs (`-zsh` under `login`, or has a running
    child), as does any agent, dev server or test process. When `live_tabs` is
    given, more idle shells than live tabs also disqualifies them all, since
    the extras cannot all be tab shells.
    """
    p = run("lsof", "-d", "cwd", "-Fpcn")
    if p.returncode != 0:
        return [{"pid": "?", "command": "unknown (lsof failed)", "idle_tab_shell": False}]
    real = os.path.realpath(wt)
    out: list[dict[str, Any]] = []
    pid = ""
    for ln in p.stdout.splitlines():
        if ln.startswith("p"):
            pid = ln[1:]
        elif ln.startswith("n/"):
            hr = os.path.realpath(ln[1:])
            if hr == real or hr.startswith(real + os.sep):
                ppid, cmd = _ps(pid)
                kids = run("pgrep", "-P", pid).stdout.split()
                idle = ppid == "1" and cmd.strip() in ("/bin/zsh -l", "zsh -l") and not kids
                out.append({"pid": pid, "ppid": ppid, "command": cmd or "?",
                            "children": len(kids), "idle_tab_shell": idle})
    idle_n = sum(1 for h in out if h["idle_tab_shell"])
    if live_tabs is not None and idle_n > live_tabs:
        for h in out:
            h["idle_tab_shell"] = False
    return out


def net_new_vs_main(wt: str, branch: str, main_ref: str) -> tuple[str, str]:
    """Squash-merge-aware check mirroring zsh `diffmain` / `gdiffnew`.

    Simulates merging <branch> into <main_ref> via `git merge-tree --write-tree`,
    then diffs that merged tree against main (excluding conflicted paths).

    Returns (status, detail) where status is one of:
      empty      — clean merge, no net-new work (safe to delete)
      net-new    — clean merge, but delta remains
      conflicts  — merge-tree reported conflicted paths
      error      — merge-tree / diff failed
    """
    if not branch:
        return "error", "missing-branch"
    p = run("git", "-C", wt, "merge-tree", "--write-tree", "--name-only", main_ref, branch)
    lines = (p.stdout or "").splitlines()
    tree = lines[0].strip() if lines else ""
    if p.returncode not in (0, 1) or not tree:
        err = (p.stderr or p.stdout or "merge-tree-failed").strip()[:200]
        return "error", err or f"merge-tree-rc={p.returncode}"

    conflicts: list[str] = []
    for ln in lines[1:]:
        if not ln.strip():
            break
        conflicts.append(ln.strip())

    excludes: list[str] = [f":(top,exclude){f}" for f in conflicts]
    d = run(
        "git", "-C", wt, "diff", "--stat", main_ref, tree, "--", ":/", *excludes,
    )
    if d.returncode != 0:
        return "error", "diff-failed: " + (d.stderr or "unknown error").strip()[:160]
    delta = (d.stdout or "").strip()
    if p.returncode != 0 and not conflicts:
        return "error", "merge-tree-failed-without-conflicts"
    if delta:
        summary = delta.splitlines()[-1]
        return "net-new", summary[:200]

    if conflicts:
        preview = ", ".join(conflicts[:5])
        more = f" (+{len(conflicts) - 5})" if len(conflicts) > 5 else ""
        return "conflicts", f"{len(conflicts)} files: {preview}{more}"
    return "empty", "empty"


def survey(project_filter: str | None = None) -> dict[str, Any]:
    projects = {p["id"]: p for p in sjson("projects", "list")}
    workspaces = sjson("workspaces", "list", "--local")
    cwd = os.path.realpath(os.getcwd())
    held = held_paths()

    # Pre-collect roots we may need to fetch for dead-pane checks.
    candidate_roots: set[str] = set()
    filtered: list[tuple[dict[str, Any], dict[str, Any], str, str, str]] = []
    roots_skipped = 0

    for w in workspaces:
        proj = projects.get(w.get("projectId") or "", {})
        root = proj.get("path") or ""
        wt = w.get("worktreePath") or ""
        name = w.get("projectName") or proj.get("name") or "?"
        if project_filter and project_filter.lower() not in {
            name.lower(),
            (proj.get("name") or "").lower(),
            os.path.basename(root).lower(),
        }:
            continue
        if root and wt and same_path(wt, root):
            roots_skipped += 1
            continue
        if root:
            candidate_roots.add(root)
        filtered.append((w, proj, name, root, wt))

    main_refs = fetch_once(candidate_roots)

    reapable: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []

    for w, _proj, name, root, wt in filtered:
        base = _base(w, name, root, wt)
        if wt and (cwd == os.path.realpath(wt) or cwd.startswith(os.path.realpath(wt) + os.sep)):
            keep.append({**base, "reason": "cwd-trap", "path_kind": None})
            continue
        is_held_wt = is_held(wt, held)

        try:
            term_payload = sjson("terminals", "list", "--local", "--workspace", w["id"])
        except RuntimeError as e:
            keep.append({**base, "reason": f"terminals-list-failed: {e}", "path_kind": None})
            continue
        sessions = term_payload.get("sessions") if isinstance(term_payload, dict) else term_payload
        if not isinstance(sessions, list):
            keep.append({**base, "reason": "invalid-terminals-list", "path_kind": None})
            continue

        if is_held_wt and not sessions:
            keep.append({**base, "reason": "held", "path_kind": None})
            continue
        if not sessions:
            _classify_dead_pane(
                base, w, wt, root, main_refs.get(root), reapable, keep,
            )
            continue

        tabs = []
        for s in sessions:
            tid = s.get("terminalId") or s.get("id")
            raw_p = run(
                "superset", "terminals", "read", "--local",
                "--workspace", w["id"], "--terminal", tid,
            )
            raw = tab_text(raw_p.stdout) if raw_p.returncode == 0 else ""
            info = classify_tab(raw)
            info["terminal_id"] = tid
            info["title"] = (s.get("title") or s.get("customTitle") or "")[:80]
            tabs.append(info)

        kinds = [t["kind"] for t in tabs]
        entry = {
            **base,
            "path_kind": "banner",
            "tabs": tabs,
            "kinds": kinds,
            "label_ticket": ticket_from_label(w.get("name") or "", w.get("branch") or ""),
        }

        if any(k == "other" for k in kinds):
            entry["reason"] = "other-tab"
            keep.append(entry)
            continue
        zeros = [t for t in tabs if t["kind"] == "finished0" and t.get("finished_dt")]
        zeros_any = [t for t in tabs if t["kind"] == "finished0"]
        if not zeros_any:
            entry["reason"] = "no-exit-0"
            keep.append(entry)
            continue
        nonzeros = [
            t for t in tabs
            if t["kind"].startswith("finished") and t["kind"] != "finished0"
        ]
        if nonzeros and any(t.get("finished_dt") is None for t in zeros_any + nonzeros):
            entry["reason"] = "unparsable-finished-date"
            keep.append(entry)
            continue
        if zeros and nonzeros:
            last0 = max(datetime.fromisoformat(t["finished_dt"]) for t in zeros)
            bad = [
                t for t in nonzeros
                if t.get("finished_dt") and datetime.fromisoformat(t["finished_dt"]) > last0
            ]
            if bad:
                b = bad[0]
                entry["reason"] = (
                    f"nonzero-after-success (exit={b['exit']} @ {b['finished_raw']} > last0)"
                )
                keep.append(entry)
                continue
        elif nonzeros and not zeros:
            if any(t.get("finished_dt") is None for t in zeros_any + nonzeros):
                entry["reason"] = "unparsable-finished-date"
                keep.append(entry)
                continue

        if not wt or not os.path.isdir(wt):
            entry["reason"] = "missing-worktree"
            keep.append(entry)
            continue
        status_check = run("git", "-C", wt, "status", "--short")
        if status_check.returncode != 0:
            entry["reason"] = "status-failed"
            keep.append(entry)
            continue
        dirty = significant_dirt(status_check.stdout)
        if dirty:
            entry["reason"] = "dirty"
            entry["dirty"] = dirty[:12]
            keep.append(entry)
            continue

        success_tickets = sorted({
            tix for t in zeros_any for tix in (t.get("tickets_tail") or [])
        })
        entry["success_tickets"] = success_tickets
        if entry["label_ticket"] and success_tickets and entry["label_ticket"] not in success_tickets:
            entry["reason"] = (
                f"ticket-mismatch label={entry['label_ticket']} tails={success_tickets}"
            )
            keep.append(entry)
            continue

        last0_raw = None
        if zeros:
            last0_raw = max(zeros, key=lambda t: t["finished_dt"])["finished_raw"]
        entry["last0"] = last0_raw
        if is_held_wt:
            # Held, but every other standard says reapable. Tab buffers are
            # only scanned for held worktrees (the one case it can change the
            # outcome). Never auto-reap: surface holders for a decision.
            entry["reason"] = "held-but-finished"
            entry["holders"] = holders(wt, len(entry.get("tabs") or []))
            entry["tab_shells_only"] = all(h["idle_tab_shell"] for h in entry["holders"])
            if not entry["tab_shells_only"]:
                entry["reason"] = "held-live-holder"
            keep.append(entry)
            continue
        entry["reason"] = "reapable-banner"
        reapable.append(entry)

    _promote_finished_conflicts(reapable, keep)
    _promote_stale_failures(reapable, keep, held)

    return {
        "roots_skipped": roots_skipped,
        "reapable": reapable,
        "keep": keep,
    }


def _promote_finished_conflicts(
    reapable: list[dict[str, Any]],
    keep: list[dict[str, Any]],
) -> None:
    """Path B2: move merge-conflict keeps to reapable when PR/Linear say finished."""
    candidates = [e for e in keep if e.get("reason") in CONFLICT_OVERRIDE_REASONS]
    if not candidates:
        return
    repos = {github_repo(e.get("root") or "") for e in candidates}
    prs_index = bulk_prs_by_branch({r for r in repos if r})
    linear_cache: dict[str, str | None] = {}
    still_keep: list[dict[str, Any]] = []
    for e in keep:
        if e.get("reason") not in CONFLICT_OVERRIDE_REASONS:
            still_keep.append(e)
            continue
        ok, detail = finished_override(e, prs_index, linear_cache)
        e["finished_evidence"] = detail
        if ok:
            e["reason"] = "reapable-dead-pane-finished"
            reapable.append(e)
        else:
            e["reason"] = f"dead-pane-merge-conflicts ({detail})"
            still_keep.append(e)
    keep[:] = still_keep


STALE_FAILURE_REASONS = ("no-exit-0", "nonzero-after-success")


def _promote_stale_failures(
    reapable: list[dict[str, Any]],
    keep: list[dict[str, Any]],
    held: list[str],
) -> None:
    """Failed or timed-out last banner is stale once the branch's PR is MERGED.

    A non-zero exit only matters while work is in progress. With a MERGED PR on
    the branch, a clean tree, and only finished/setup tabs, the failure is
    superseded. Held worktrees still go to `held-but-finished`, never reapable.
    """
    candidates = [
        e for e in keep
        if str(e.get("reason", "")).split(" ")[0] in STALE_FAILURE_REASONS
        and e.get("kinds")
        and all(k.startswith("finished") or k == "setup" for k in e["kinds"])
    ]
    if not candidates:
        return
    repos = {github_repo(e.get("root") or "") for e in candidates}
    prs_index = bulk_prs_by_branch({r for r in repos if r})
    done: list[dict[str, Any]] = []
    for e in candidates:
        wt = e.get("path") or ""
        if not wt or not os.path.isdir(wt):
            continue
        status = run("git", "-C", wt, "status", "--short")
        if status.returncode != 0 or significant_dirt(status.stdout):
            continue
        ok, detail = finished_override(e, prs_index, {})
        if not (ok and e.get("pr_state") == "MERGED"):
            continue
        e["finished_evidence"] = f"{detail}; last banner stale ({e['reason']})"
        if is_held(wt, held):
            e["reason"] = "held-but-finished"
            e["holders"] = holders(wt, len(e.get("tabs") or []))
            e["tab_shells_only"] = all(h["idle_tab_shell"] for h in e["holders"])
            if not e["tab_shells_only"]:
                e["reason"] = "held-live-holder"
        else:
            e["reason"] = "reapable-banner-stale-failure"
            reapable.append(e)
            done.append(e)
    keep[:] = [e for e in keep if not any(e is d for d in done)]


IGNORABLE_UNTRACKED = {"?? node_modules", "?? node_modules/"}


def significant_dirt(status_stdout: str) -> list[str]:
    """`git status --short` lines that count as real changes.

    A lone untracked `node_modules` is install residue, not work. Everything
    else (including an IN_PROGRESS.md marker, which needs approval to discard)
    still counts.
    """
    return [ln for ln in status_stdout.splitlines() if ln.strip() and ln.strip() not in IGNORABLE_UNTRACKED]


def branch_pr_states(root: str, branch: str) -> set[str] | None:
    """PR states for a head branch, or None when the lookup failed or is incomplete."""
    repo = github_repo(root)
    if not repo or not branch:
        return None
    p = run("gh", "pr", "list", "--repo", repo, "--state", "all", "--head", branch,
            "--json", "state", "--limit", "100")
    if p.returncode != 0:
        return None
    try:
        prs = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(prs, list) or len(prs) >= 100:
        return None
    return {(pr.get("state") or "").upper() for pr in prs}


def _classify_dead_pane(
    base: dict[str, Any],
    w: dict[str, Any],
    wt: str,
    root: str,
    main_ref: str | None,
    reapable: list[dict[str, Any]],
    keep: list[dict[str, Any]],
) -> None:
    branch = w.get("branch") or ""
    entry = {
        **base,
        "path_kind": "dead-pane",
        "tabs": [],
        "kinds": [],
        "label_ticket": ticket_from_label(w.get("name") or "", branch),
        "main_ref": main_ref,
    }
    if not wt or not os.path.isdir(wt):
        entry["reason"] = "dead-pane-missing-worktree"
        keep.append(entry)
        return
    status_check = run("git", "-C", wt, "status", "--short")
    if status_check.returncode != 0:
        entry["reason"] = "dead-pane-status-failed"
        keep.append(entry)
        return
    dirty = significant_dirt(status_check.stdout)
    if dirty:
        entry["reason"] = "dead-pane-dirty"
        entry["dirty"] = dirty[:12]
        keep.append(entry)
        return
    if not main_ref:
        entry["reason"] = "dead-pane-no-main-ref"
        keep.append(entry)
        return
    states = branch_pr_states(root, branch)
    if states and states == {"CLOSED"}:
        # Closed without merging: the work was abandoned or superseded
        # elsewhere, so the merge-tree simulation cannot clear it. Keep and
        # skip the (slow) simulation.
        entry["pr_state"] = "CLOSED"
        entry["reason"] = "dead-pane-pr-closed-unmerged"
        keep.append(entry)
        return
    status, detail = net_new_vs_main(wt, branch, main_ref)
    entry["net_new"] = detail
    if status == "empty":
        entry["reason"] = "reapable-dead-pane"
        reapable.append(entry)
        return
    if status == "conflicts":
        entry["reason"] = "dead-pane-merge-conflicts"
        keep.append(entry)
        return
    if status == "net-new":
        entry["reason"] = "dead-pane-net-new"
        keep.append(entry)
        return
    entry["reason"] = f"dead-pane-merge-error: {detail}"
    keep.append(entry)


def _base(w: dict[str, Any], name: str, root: str, wt: str) -> dict[str, Any]:
    return {
        "id": w["id"],
        "project": name,
        "name": w.get("name"),
        "branch": w.get("branch"),
        "root": root,
        "path": wt,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", help="Filter by project name (substring / exact)")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="Only print counts + reapable ids")
    args = ap.parse_args()

    try:
        result = survey(args.project)
    except Exception as e:
        print(f"survey failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0

    r, k = result["reapable"], result["keep"]
    print(
        f"Surveyed non-root → {len(r)} reapable, {len(k)} keep "
        f"(roots skipped: {result['roots_skipped']})"
    )
    if r:
        print("\nReapable:")
        for e in r:
            if e.get("path_kind") == "dead-pane":
                extra = (
                    f"finished={e.get('finished_evidence')}"
                    if e.get("finished_evidence")
                    else f"net_new={e.get('net_new')}"
                )
                print(
                    f"  {e['id']}  {e['project']:20}  {e['branch']}  "
                    f"path=dead-pane  main={e.get('main_ref')}  {extra}"
                )
            else:
                kinds = ",".join(e.get("kinds") or [])
                print(
                    f"  {e['id']}  {e['project']:20}  {e['branch']}  "
                    f"path=banner  tabs=[{kinds}]  last0={e.get('last0')}  "
                    f"ticket={e.get('label_ticket')}"
                )
    if k and not args.quiet:
        print("\nKeep:")
        for e in k:
            print(
                f"  {e['id'][:8]}…  {e['project']:20}  {e.get('branch')}  "
                f"reason={e.get('reason')}"
            )
            for h in e.get("holders") or []:
                tag = "tab-shell" if h.get("idle_tab_shell") else "LIVE"
                print(f"      holder[{tag}] {h['pid']} {h['command'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
