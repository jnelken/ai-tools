#!/usr/bin/env python3
"""Survey local Superset workspaces for advance-roadmap reaps.

Two paths:
  banner    — finished-banner evidence on live tabs
  dead-pane — zero terminals, clean tree, no diff against main

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
    s = s.strip()
    m = re.match(r"(\w{3} \w{3}\s+\d+ \d+:\d+:\d+) \w+ (\d{4})", s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%a %b %d %H:%M:%S %Y")
        except ValueError:
            return None
    return None


def last_finished(raw: str) -> tuple[str, int, str, datetime | None] | None:
    matches = list(FINISHED_RE.finditer(raw))
    if not matches:
        return None
    m = matches[-1]
    return m.group(1), int(m.group(2)), m.group(3), parse_finished_date(m.group(3))


def is_setup(raw: str) -> bool:
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
    if len(lines) <= 15 and not re.search(r"worker used=", raw):
        return True
    return False


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
    return m.group(1) if m else None


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
            run("git", "-C", root, "fetch", "origin", "--prune", "-q")
        main_refs[root] = resolve_main_ref(root)
    return main_refs


def held_paths() -> list[str]:
    """Best-effort list of cwd paths held by common agent/runtime processes."""
    p = run(
        "bash", "-lc",
        "lsof -a -d cwd -c node -c claude -c codex -c Cursor -Fn 2>/dev/null | "
        "sed -n 's/^n//p' | sort -u",
    )
    return [ln for ln in (p.stdout or "").splitlines() if ln.startswith("/")]


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


def tree_matches_main(wt: str, main_ref: str) -> tuple[bool, str]:
    """Return (matches, detail). Uses git diff against main_ref (tracked + intent)."""
    p = run("git", "-C", wt, "diff", "--quiet", main_ref)
    if p.returncode == 0:
        # Also fail if untracked would be invisible to diff against a ref — caller
        # already requires a clean status --short, so untracked is covered there.
        return True, "empty"
    if p.returncode == 1:
        stat = run("git", "-C", wt, "diff", "--shortstat", main_ref).stdout.strip()
        return False, stat or "non-empty"
    return False, (p.stderr or p.stdout or "diff-failed").strip()[:200]


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
        if wt and same_path(os.path.realpath(wt), cwd):
            keep.append({**base, "reason": "cwd-trap", "path_kind": None})
            continue
        if is_held(wt, held):
            keep.append({**base, "reason": "held", "path_kind": None})
            continue

        try:
            term_payload = sjson("terminals", "list", "--local", "--workspace", w["id"])
        except RuntimeError as e:
            keep.append({**base, "reason": f"terminals-list-failed: {e}", "path_kind": None})
            continue
        sessions = term_payload.get("sessions") if isinstance(term_payload, dict) else term_payload
        sessions = sessions or []

        if not sessions:
            _classify_dead_pane(base, w, wt, root, main_refs.get(root), reapable, keep)
            continue

        tabs = []
        for s in sessions:
            tid = s.get("terminalId") or s.get("id")
            raw_p = run(
                "superset", "terminals", "read", "--local",
                "--workspace", w["id"], "--terminal", tid,
            )
            raw = raw_p.stdout if raw_p.returncode == 0 else (raw_p.stdout or "")
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
        dirty = run("git", "-C", wt, "status", "--short").stdout.strip()
        if dirty:
            entry["reason"] = "dirty"
            entry["dirty"] = dirty.splitlines()[:12]
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
        entry["reason"] = "reapable-banner"
        reapable.append(entry)

    return {
        "roots_skipped": roots_skipped,
        "reapable": reapable,
        "keep": keep,
    }


def _classify_dead_pane(
    base: dict[str, Any],
    w: dict[str, Any],
    wt: str,
    root: str,
    main_ref: str | None,
    reapable: list[dict[str, Any]],
    keep: list[dict[str, Any]],
) -> None:
    entry = {
        **base,
        "path_kind": "dead-pane",
        "tabs": [],
        "kinds": [],
        "label_ticket": ticket_from_label(w.get("name") or "", w.get("branch") or ""),
        "main_ref": main_ref,
    }
    if not wt or not os.path.isdir(wt):
        entry["reason"] = "dead-pane-missing-worktree"
        keep.append(entry)
        return
    dirty = run("git", "-C", wt, "status", "--short").stdout.strip()
    if dirty:
        entry["reason"] = "dead-pane-dirty"
        entry["dirty"] = dirty.splitlines()[:12]
        keep.append(entry)
        return
    if not main_ref:
        entry["reason"] = "dead-pane-no-main-ref"
        keep.append(entry)
        return
    matches, detail = tree_matches_main(wt, main_ref)
    entry["diff"] = detail
    if not matches:
        entry["reason"] = "dead-pane-diff-vs-main"
        keep.append(entry)
        return
    entry["reason"] = "reapable-dead-pane"
    reapable.append(entry)


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
                print(
                    f"  {e['id']}  {e['project']:20}  {e['branch']}  "
                    f"path=dead-pane  main={e.get('main_ref')}  diff={e.get('diff')}"
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
