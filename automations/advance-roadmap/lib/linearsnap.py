#!/usr/bin/env python3
"""Snapshot the Dev team's active Linear issues for the read-only planning pass.

Linear is reached only through the `linear` CLI, which authenticates with the API
key it keeps in the macOS keychain (service `linear-cli`). The planning pass runs
in Codex's read-only sandbox, which can't read the keychain, so run.sh takes this
snapshot *before* launching it and hands over the file path instead.

Always writes OUT, and exits 0 either way: on failure the file carries
{"ok": false, "error": ...} so the planner falls back to file-based sources
knowingly rather than guessing why Linear is silent.

    linearsnap.py --out FILE
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime

FIELDS = """
    pageInfo { hasNextPage }
    nodes {
      identifier title url priority priorityLabel updatedAt
      state { name type }
      labels { nodes { name parent { name } } }
      relations { nodes { type relatedIssue { identifier } } }
      inverseRelations { nodes { type issue { identifier } } }
      description
    }"""
# Active work, plus every pending directive whatever its ticket's state: a directive on a
# closed ticket still gates its repo's dirty tree (Step 1a), and the planner must see it.
QUERY = """query {
  active: issues(first: 250, filter: {
    team: { key: { eq: "DEV" } },
    state: { type: { nin: ["completed", "canceled"] } }
  }) {%s}
  directives: issues(first: 50, filter: {
    team: { key: { eq: "DEV" } },
    labels: { name: { eq: "roadmap-directive" } }
  }) {%s}
}""" % (FIELDS, FIELDS)


def label_name(label):
    # Group children come back as bare names; `repo/<dir>` is what the docs match on.
    parent = (label.get("parent") or {}).get("name")
    return f"{parent}/{label['name']}" if parent else label["name"]


def flatten(node):
    return {
        "id": node["identifier"],
        "title": node["title"],
        "url": node["url"],
        "state": node["state"]["name"],
        "state_type": node["state"]["type"],
        "priority": node["priority"],
        "priority_label": node["priorityLabel"],
        "updated_at": node["updatedAt"],
        "labels": sorted(label_name(l) for l in node["labels"]["nodes"]),
        "blocks": [r["relatedIssue"]["identifier"] for r in node["relations"]["nodes"] if r["type"] == "blocks"],
        "blocked_by": [r["issue"]["identifier"] for r in node["inverseRelations"]["nodes"] if r["type"] == "blocks"],
        "description": node.get("description") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    snap = {"fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": "linear-cli"}
    try:
        p = subprocess.run(["linear", "api", QUERY], capture_output=True, text=True, timeout=90)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip()[:500] or f"linear exited {p.returncode}")
        data = json.loads(p.stdout)["data"]
        by_id = {}
        for conn in (data["active"], data["directives"]):
            for n in conn["nodes"]:
                by_id.setdefault(n["identifier"], flatten(n))
        nodes = list(by_id.values())
        truncated = data["active"]["pageInfo"]["hasNextPage"] or data["directives"]["pageInfo"]["hasNextPage"]
        snap.update(ok=True, truncated=truncated, count=len(nodes), issues=nodes)
        print(f"linear: snapshot ok — {len(nodes)} DEV issues (active + pending directives)")
    except Exception as e:  # noqa: BLE001 — any failure degrades to file-only triage
        snap.update(ok=False, error=str(e), issues=[])
        print(f"linear: snapshot FAILED — {e}")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
