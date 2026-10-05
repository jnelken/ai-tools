#!/usr/bin/env python3
"""Per-repo run memory for advance-roadmap / conductor, written under a lock.

Run memory used to be one hand-edited markdown file. Two workers finishing together would each read
the old file and the last write silently dropped the other's ledger row. Now every write goes
through this script: one file per repo under <memory dir>/advance-roadmap-runs/, each rewritten
atomically (temp file + rename) while holding one directory-wide flock.

    runmemory.py append-ledger --repo R --stamp S --outcome O [--date D] [--detail T]
    runmemory.py write-notes   --repo R            (new notes on stdin; replaces the ## Notes section)
    runmemory.py has-stamp     --stamp S           (exit 0 and print the repo if any ledger has it)
    runmemory.py ledger        [--repo R]          (every row as JSON, newest stamp last)
    runmemory.py show          [--repo R]          (print one repo's file, or all of them)
    runmemory.py migrate                           (one-time: split the legacy single file)

Repo is the checkout's directory name. A run that names no repo (bookkeeping, nothing qualified)
uses `none`, stored as `_none.md`. Each repo's ledger keeps its last 10 rows. The newest run in the
whole machine is always the newest row of some repo's ledger, so Step 0's "did the previous run
finish?" lookup (has-stamp) never loses the row it needs.

Override the directory with ADVANCE_ROADMAP_RUNS_DIR (tests do).
"""
import argparse
import contextlib
import fcntl
import glob
import json
import os
import re
import sys
import tempfile
from datetime import date

KEEP_ROWS = 10
LEGACY_NAME = "project_advance-roadmap-runs.md"
ROW_RE = re.compile(r"^\|\s*`(\d{8}-\d{6})`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")
HEADER = "| Stamp | Date | Repo | Outcome |\n|---|---|---|---|\n"
SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _personal_code_dir_module():
    d = os.path.dirname(os.path.realpath(__file__))
    while d != os.path.dirname(d) and not os.path.isfile(os.path.join(d, "lib", "personal_code_dir.py")):
        d = os.path.dirname(d)
    if d == os.path.dirname(d):
        d = os.environ.get("AI_TOOLS_HOME", os.path.expanduser("~/.ai-tools"))
    sys.path.insert(0, os.path.join(d, "lib"))
    import personal_code_dir
    return personal_code_dir


def memory_dir():
    pcd = _personal_code_dir_module()
    code = pcd.code_dir()
    if not code:
        sys.exit("runmemory: no personal code dir on this machine (set PERSONAL_CODE_DIR)")
    return pcd.memory_dir(code)


def runs_dir():
    return os.environ.get("ADVANCE_ROADMAP_RUNS_DIR") or os.path.join(memory_dir(), "advance-roadmap-runs")


def slug(repo):
    s = SLUG_RE.sub("-", (repo or "").strip()).strip("-.")
    return s if s and s.lower() != "none" else "none"


def path_for(d, repo):
    s = slug(repo)
    return os.path.join(d, "_none.md" if s == "none" else f"{s}.md")


@contextlib.contextmanager
def locked(d):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return None


def parse(text):
    """-> (rows, notes). Rows are the ledger lines verbatim; notes is the ## Notes body."""
    ledger, found, notes = text.partition("\n## Notes")
    rows = [ln.rstrip() for ln in ledger.splitlines() if ROW_RE.match(ln.strip())]
    return rows, notes.strip() if found else ""


def render(repo, rows, notes):
    s = slug(repo)
    return (
        f"---\nname: advance-roadmap-runs-{s}\n"
        f"description: \"advance-roadmap / conductor run memory for {s}: the run ledger and what was learned there. "
        f"Written only by runmemory.py.\"\nmetadata:\n  type: project\n---\n\n"
        f"## Run ledger\n\n"
        f"Last {KEEP_ROWS} runs that reached the final bookkeeping step. A stamp with **no row** means that run was killed "
        f"mid-flight. Match on the stamp, not the date.\n\n"
        f"{HEADER}" + "".join(r + "\n" for r in rows) + "\n## Notes\n\n" + (notes.strip() + "\n" if notes.strip() else "")
    )


def load(d, repo):
    text = read(path_for(d, repo))
    return parse(text) if text else ([], "")


def row_stamp(row):
    return ROW_RE.match(row.strip()).group(1)


def all_rows(d):
    """Every ledger row on disk, as dicts, ordered by stamp. Includes the legacy single file."""
    out = {}
    paths = sorted(glob.glob(os.path.join(d, "*.md")))
    legacy = os.path.join(os.path.dirname(d), LEGACY_NAME)
    if os.path.exists(legacy):
        paths.append(legacy)
    for p in paths:
        text = read(p) or ""
        section = text.split("## Run ledger", 1)[1].split("\n## ", 1)[0] if "## Run ledger" in text else ""
        for ln in section.splitlines():
            m = ROW_RE.match(ln.strip())
            if m:
                out.setdefault(m.group(1), {"stamp": m.group(1), "date": m.group(2), "repo": m.group(3),
                                            "outcome": m.group(4), "file": os.path.basename(p)})
    return [out[k] for k in sorted(out)]


def cmd_append(a):
    d = runs_dir()
    cell = slug(a.repo) + (f" ({a.detail})" if a.detail else "")
    row = f"| `{a.stamp}` | {a.date or date.today().isoformat()} | {cell.replace('|', '/')} | {a.outcome} |"
    if not ROW_RE.match(row):
        sys.exit(f"runmemory: bad stamp or fields: {row}")
    with locked(d):
        rows, notes = load(d, a.repo)
        rows = [r for r in rows if row_stamp(r) != a.stamp] + [row]   # same stamp twice replaces, never duplicates
        atomic_write(path_for(d, a.repo), render(a.repo, rows[-KEEP_ROWS:], notes))
    print(f"ledger row written: {a.stamp} → {path_for(d, a.repo)}")


def cmd_notes(a):
    d = runs_dir()
    new = sys.stdin.read()
    with locked(d):
        rows, _ = load(d, a.repo)
        atomic_write(path_for(d, a.repo), render(a.repo, rows, new))
    print(f"notes written: {path_for(d, a.repo)}")


def cmd_has_stamp(a):
    for r in all_rows(runs_dir()):
        if r["stamp"] == a.stamp:
            print(slug(r["repo"].split(" (")[0]))
            return 0
    return 1


def cmd_ledger(a):
    rows = all_rows(runs_dir())
    if a.repo:
        rows = [r for r in rows if slug(r["repo"].split(" (")[0]) == slug(a.repo)]
    json.dump(rows, sys.stdout, indent=1)
    print()


def cmd_show(a):
    d = runs_dir()
    files = [path_for(d, a.repo)] if a.repo else sorted(glob.glob(os.path.join(d, "*.md")))
    for p in files:
        text = read(p)
        if text:
            print(f"<!-- {os.path.basename(p)} -->\n{text}\n")


def cmd_migrate(a):
    """Split the legacy file's ledger into per-repo files. Prose stays in the legacy file, which becomes
    the pre-split archive plus the cross-repo notes. Idempotent; writes a .pre-split backup first."""
    d = runs_dir()
    legacy = os.path.join(os.path.dirname(d), LEGACY_NAME)
    text = read(legacy)
    if text is None or "## Run ledger" not in text:
        print("nothing to migrate")
        return 0
    head, rest = text.split("## Run ledger", 1)
    section, sep, tail = rest.partition("\n## ")
    rows = [ln.strip() for ln in section.splitlines() if ROW_RE.match(ln.strip())]
    bak = legacy + ".pre-split"
    if not os.path.exists(bak):
        atomic_write(bak, text)
    for r in rows:
        m = ROW_RE.match(r)
        repo = re.split(r"\s*\(", m.group(3), maxsplit=1)[0]
        detail = m.group(3)[len(repo):].strip()[1:-1] if "(" in m.group(3) else None
        ns = argparse.Namespace(repo=repo, stamp=m.group(1), date=m.group(2), outcome=m.group(4), detail=detail)
        cmd_append(ns)
    # Drop the ledger table and its stale how-to prose; keep "Stamps absent on purpose" onward.
    marker = "Stamps absent on purpose"
    kept = section[section.index(marker):] if marker in section else ""
    atomic_write(legacy, head + "## Run ledger (moved)\n\n**Moved.** The ledger now lives per repo in "
                 "`advance-roadmap-runs/<repo>.md`, written only by `runmemory.py` (WORKER.md Step 8). This file keeps "
                 "cross-repo notes and the pre-split history.\n\n" + kept + (sep + tail if sep else ""))
    print(f"migrated {len(rows)} rows; backup at {bak}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("append-ledger"); p.set_defaults(fn=cmd_append)
    p.add_argument("--repo", required=True); p.add_argument("--stamp", required=True)
    p.add_argument("--outcome", required=True); p.add_argument("--date"); p.add_argument("--detail")
    p = sub.add_parser("write-notes"); p.set_defaults(fn=cmd_notes); p.add_argument("--repo", required=True)
    p = sub.add_parser("has-stamp"); p.set_defaults(fn=cmd_has_stamp); p.add_argument("--stamp", required=True)
    p = sub.add_parser("ledger"); p.set_defaults(fn=cmd_ledger); p.add_argument("--repo")
    p = sub.add_parser("show"); p.set_defaults(fn=cmd_show); p.add_argument("--repo")
    p = sub.add_parser("migrate"); p.set_defaults(fn=cmd_migrate)
    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
