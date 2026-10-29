#!/usr/bin/env python3
"""Narrow the Linear snapshot to one repo's issues.

A daily batch plans one repo per planner, but the snapshot holds every DEV issue (~500 KB, 199
issues): each planner read all of it, and the verdict fingerprint hashed all of it, so a ticket
edit in one repo reopened triage in every other.

An issue is in a repo's scope when it carries that repo's `repo/<dir>` label or no `repo/*` label
at all (the planner may be the one to notice it names this repo). Issues that an in-scope issue is
blocked by come along whatever their label or state, so a blocker finishing elsewhere still moves
this repo's fingerprint and the planner never sees a blocker id with no state.

    linearscope.py --snapshot S --repo R --out OUT

OUT is the planner's copy: scoped further to open work (Done and Canceled issues dropped) except the
blockers above and any closed issue carrying a `## Directive`, which can still gate its repo's dirty
tree. The fingerprint keeps closed in-scope issues (`scope_issues(..., open_only=False)`).
"""
import argparse
import json
import sys

CLOSED_TYPES = {"completed", "canceled"}


def repo_labels(issue):
    return [l for l in issue.get("labels") or [] if isinstance(l, str) and l.startswith("repo/")]


def in_repo(issue, repo):
    labels = repo_labels(issue)
    return not labels or f"repo/{repo}" in labels


def scope_issues(issues, repo, open_only=False):
    """The issues one repo's planner and fingerprint depend on, in the snapshot's order."""
    issues = [i for i in issues if isinstance(i, dict) and i.get("id")]
    by_id = {i["id"]: i for i in issues}
    mine = [i for i in issues if in_repo(i, repo)]
    keep = {i["id"] for i in mine
            if not open_only or i.get("state_type") not in CLOSED_TYPES or "## Directive" in (i.get("description") or "")}
    for i in mine:  # blockers ride along whatever their repo or state
        keep.update(b for b in i.get("blocked_by") or [] if b in by_id)
    return [i for i in issues if i["id"] in keep]


def scoped_snapshot(snap, repo):
    """The planner's copy of a snapshot: same shape, this repo's open work only."""
    issues = scope_issues(snap.get("issues") or [], repo, open_only=True)
    out = dict(snap)
    out.update(issues=issues, count=len(issues), scoped_to=repo,
               omitted=len(snap.get("issues") or []) - len(issues))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        with open(a.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return 1
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(scoped_snapshot(snap, a.repo), f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
