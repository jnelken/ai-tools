#!/usr/bin/env python3
"""Keep one pending advance-roadmap directive per repo, merging duplicates before planning.

A pending Linear directive is a bare `## Directive` heading right after its
`<!-- advance-roadmap:directive:<repo> -->` marker. The structured directive store is authoritative
when present; check falls back to these sections for repos without a store file.
This module folds duplicate Linear sections onto one carrier
ticket (lowest DEV number). Every other heading is rewritten to `## Directive (consumed <date>,
merged into <carrier>)`, so no answer is lost and nothing is deleted.

A directive with a dirty-tree resolution names a tree state, so it is never merged here: that
repo is reported as needing a human or the worker.

    directive_slot.py check                 exit 1 when any repo has duplicate pending directives
    directive_slot.py merge                 dry run: print the merge plan
    directive_slot.py merge --apply         write the merge to Linear through the `linear` CLI
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# python3 -I omits the script directory; load only our trusted sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

LINEAR_SNAP_PY = Path(__file__).resolve().parent / "linearsnap.py"
MARKER_RE = re.compile(r"<!--\s*advance-roadmap:directive:(?P<repo>[^\s>]+)\s*-->")
DEV_RE = re.compile(r"^DEV-(\d+)$")


def pending_entries(description):
    """Return (repo, start) for each pending directive: a marker followed by a bare heading."""
    out = []
    text = description or ""
    for m in MARKER_RE.finditer(text):
        if re.match(r"\s*\n\s*##\s+Directive[ \t]*(\n|$)", text[m.end():]):
            out.append((m.group("repo"), m.start()))
    return out


def _dev_number(identifier):
    m = DEV_RE.match(identifier or "")
    return int(m.group(1)) if m else 10**9


def _section_after(tail, heading):
    i = tail.find("### " + heading)
    if i == -1:
        return None
    body = tail[i + len("### " + heading):]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    cut = re.search(r"^(### |<!--)", body, re.M)
    return (body[: cut.start()] if cut else body).strip()


def _trailer(tail):
    """Marker comments that sit after the directive body, such as a Slack-mode marker."""
    return "\n".join(re.findall(r"^<!--.*-->$", tail, re.M))


def parse_directive(description, start):
    """Return the parsed pieces of the pending directive at START, or the reason it can't merge."""
    tail = description[start:]
    if "### Dirty-tree resolution" in tail:
        return None, "dirty-tree resolution: needs a human or the worker"
    m = re.search(r"### Answered decisions\n\n(.*?)\n\n### Go-ahead", tail, re.S)
    if not m:
        return None, "no parseable Answered decisions / Go-ahead sections"
    answered = m.group(1).strip()
    go = _section_after(tail, "Go-ahead")
    if not answered or not go:
        return None, "empty Answered decisions or Go-ahead section"
    return {"answered": answered, "go": go, "trailer": _trailer(tail)}, None


def _consume(description, start, note, today):
    m = re.compile(r"^\s*##\s+Directive[ \t]*$", re.M).search(description, start)
    if not m:
        raise ValueError("pending heading not found")
    return description[: m.start()] + f"## Directive (consumed {today}, {note})" + description[m.end():]


def merge_plan(issues, today=None):
    """Compute merges for every repo with more than one pending directive.

    Returns (plans, refusals). Each plan is {repo, carrier, updates: {identifier: new_description}}.
    """
    today = today or date.today().isoformat()
    by_repo = {}
    for issue in issues:
        desc = issue.get("description") or ""
        for repo, start in pending_entries(desc):
            by_repo.setdefault(repo, []).append((issue.get("identifier") or issue.get("id"), desc, start))

    plans, refusals = [], []
    for repo, entries in sorted(by_repo.items()):
        if len(entries) < 2:
            continue
        parsed, bad = [], None
        for ident, desc, start in entries:
            info, reason = parse_directive(desc, start)
            if reason:
                bad = f"{ident}: {reason}"
                break
            parsed.append((ident, desc, start, info))
        if bad:
            refusals.append({"repo": repo, "reason": bad, "tickets": [e[0] for e in entries]})
            continue
        parsed.sort(key=lambda p: _dev_number(p[0]))
        carrier_ident, carrier_desc, carrier_start, carrier_info = parsed[0]

        gos = []
        for ident, _, _, info in parsed:
            for line in info["go"].splitlines():
                text = line[1:].strip() if line.startswith("*") else line.strip()
                if text:
                    gos.append(f"* {ident}: {text}")
        tickets = ", ".join(p[0] for p in parsed)
        answered = "\n\n".join(p[3]["answered"] for p in parsed)
        body = (
            "## Directive\n\n"
            f"**Recorded:** {today} by directive_slot (merged) · **Repo:** `{repo}`\n\n"
            f"One pending {repo} directive now covers {tickets}. The other headings are marked consumed. "
            "The answers are carried over verbatim.\n\n"
            "### Answered decisions\n\n" + answered + "\n\n"
            "### Go-ahead\n\n" + "\n".join(gos) + "\n"
        )
        marker = MARKER_RE.search(carrier_desc, carrier_start).group(0)
        prefix = carrier_desc[:carrier_start].rstrip()
        trailer = carrier_info["trailer"]
        carrier_new = prefix + "\n\n" + marker + "\n" + body + (("\n" + trailer + "\n") if trailer else "")
        updates = {carrier_ident: carrier_new}
        for ident, desc, start, _ in parsed[1:]:
            updates[ident] = _consume(desc, start, f"merged into {carrier_ident}", today)
        plans.append({"repo": repo, "carrier": carrier_ident, "updates": updates})
    return plans, refusals


def duplicate_repos(issues):
    counts = {}
    for issue in issues:
        for repo, _ in pending_entries(issue.get("description") or ""):
            counts[repo] = counts.get(repo, 0) + 1
    return sorted(r for r, n in counts.items() if n > 1)


def _gql(query):
    r = subprocess.run(["linear", "api", query], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    data = json.loads(r.stdout)
    if "errors" in data:
        raise RuntimeError(str(data["errors"]))
    return data


def write_update(identifier, description, runner=_gql):
    mutation = "mutation { issueUpdate(id: %s, input: {description: %s}) { success } }" % (
        json.dumps(identifier), json.dumps(description))
    return bool(runner(mutation)["data"]["issueUpdate"]["success"])


def load_snapshot(path=None):
    if path:
        return json.loads(Path(path).read_text())
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp) / "linear-snapshot.json"
        subprocess.run([sys.executable, "-I", str(LINEAR_SNAP_PY), "--out", str(out)], check=False,
                       capture_output=True, text=True)
        if not out.exists():
            return {"ok": False, "error": "linearsnap wrote no output"}
        return json.loads(out.read_text())


def check_snapshot(snap):
    """Use each repo's store when present; legacy Linear sections are the fallback."""
    import directive_store

    counts = directive_store.pending_repos()
    stored = {p.stem for p in directive_store.store_dir().glob("*.json")}
    for repo in sorted(stored):
        print(f"store: {repo} {counts.get(repo, 0)} pending decision(s)")
    issues = snap.get("issues", [])
    linear_repos = {repo for issue in issues
                    for repo, _ in pending_entries(issue.get("description") or "")}
    fallback = linear_repos - stored
    for repo in sorted(fallback):
        print(f"store absent for {repo}; falling back to Linear scan")
    if not snap.get("ok", False):
        print(f"directive_slot: Linear snapshot unavailable ({snap.get('error', 'unknown')})")
        # With no store at all we still need the legacy scan. With store coverage,
        # only a known repo lacking a file requires the unavailable snapshot.
        return 2 if fallback or not stored else 0
    dups = [repo for repo in duplicate_repos(issues) if repo in fallback]
    for repo in dups:
        print(f"duplicate pending directives: {repo}")
    return 1 if dups else 0


def consume_merged(plan, issues):
    """Best effort after Linear succeeds; import original tickets, not the new carrier."""
    import directive_store

    snapshot = {"ok": True, "issues": [issue for issue in issues
                if (issue.get("identifier") or issue.get("id")) in plan["updates"]]}
    repo = plan["repo"]
    directive_store.import_snapshot(repo, snapshot)
    decisions, skipped = directive_store.parse_snapshot(repo, snapshot)
    for reason in skipped:
        print(f"  store: skipped {reason}")
    # The carrier keeps its decisions pending: its Linear description is still the live merged directive.
    ids = [directive_store.decision_id(d["ticket"], d["question"]) for d in decisions
           if d["ticket"] != plan["carrier"]]
    count = directive_store.consume(repo, ids, f"merged into {plan['carrier']}")
    print(f"  store: {repo} consumed {count} decision(s)")


def main(argv=None, writer=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "merge"])
    ap.add_argument("--apply", action="store_true", help="write the merge to Linear")
    ap.add_argument("--snapshot", help="use this linearsnap JSON instead of fetching")
    args = ap.parse_args(argv)

    try:
        snap = load_snapshot(args.snapshot)
    except (OSError, ValueError) as exc:
        snap = {"ok": False, "error": str(exc)}
    if args.command == "check":
        try:
            return check_snapshot(snap)
        except (OSError, ValueError) as exc:
            print(f"directive_slot: store failure: {exc}", file=sys.stderr)
            return 2
    if not snap.get("ok", False):
        print(f"directive_slot: Linear snapshot unavailable ({snap.get('error', 'unknown')}); nothing changed")
        return 2
    issues = snap.get("issues", [])

    plans, refusals = merge_plan(issues)
    for r in refusals:
        print(f"NOT MERGED {r['repo']}: {r['reason']} (tickets: {', '.join(r['tickets'])})")
    for p in plans:
        print(f"MERGE {p['repo']} onto {p['carrier']}: {', '.join(p['updates'])}")
    if not plans and not refusals:
        print("no duplicate pending directives")
    if not args.apply:
        if plans:
            print("dry run; re-run with --apply to write")
        return 0
    for p in plans:
        for ident, desc in p["updates"].items():
            ok = (writer or write_update)(ident, desc)
            print(f"  {ident}: success={ok}")
            if not ok:
                return 1
        try:
            consume_merged(p, issues)
        except Exception as exc:  # Store failures must not mask successful Linear writes.
            print(f"  store: {p['repo']} failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
