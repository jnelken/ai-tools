#!/usr/bin/env python3
"""Survey local Superset workspaces for advance-roadmap finished-banner reaps.

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


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


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
        return {"kind": "setup", "exit": None, "finished_raw": None, "finished_dt": None, "tickets_tail": []}
    return {"kind": "other", "exit": None, "finished_raw": None, "finished_dt": None, "tickets_tail": []}


def ticket_from_label(*parts: str) -> str | None:
    blob = " ".join(p for p in parts if p)
    m = TICKET_RE.search(blob)
    return m.group(1) if m else None


def same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


def survey(project_filter: str | None = None) -> dict[str, Any]:
    projects = {p["id"]: p for p in sjson("projects", "list")}
    workspaces = sjson("workspaces", "list", "--local")
    reapable: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    roots = 0

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
            roots += 1
            continue

        try:
            term_payload = sjson("terminals", "list", "--local", "--workspace", w["id"])
        except RuntimeError as e:
            keep.append({**_base(w, name, root, wt), "reason": f"terminals-list-failed: {e}"})
            continue
        sessions = term_payload.get("sessions") if isinstance(term_payload, dict) else term_payload
        sessions = sessions or []
        if not sessions:
            keep.append({**_base(w, name, root, wt), "reason": "no-terminals"})
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
            **_base(w, name, root, wt),
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
        nonzeros = [t for t in tabs if t["kind"].startswith("finished") and t["kind"] != "finished0"]
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
            # finished0 present but unparsable dates — require all zeros only
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
        entry["reason"] = "reapable"
        reapable.append(entry)

    return {
        "roots_skipped": roots,
        "reapable": reapable,
        "keep": keep,
    }


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
            kinds = ",".join(e["kinds"])
            print(
                f"  {e['id']}  {e['project']:20}  {e['branch']}  "
                f"tabs=[{kinds}]  last0={e.get('last0')}  ticket={e.get('label_ticket')}"
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
