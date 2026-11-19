#!/usr/bin/env python3
"""Same-ticket stacking helpers for create-pr.

decide  Pick the base for a new PR into a Concentro repo's default branch:
        stack onto the open, not-yet-approved PR that carries the same (CON-N),
        or stay on the default branch. Read-only apart from `git fetch`.
note    Add or update the "Stacked follow-ups" line for a sub-PR in the parent
        PR's body, edited in place through a file.
"""

import argparse
import json
import re
import subprocess
import sys

PR_REVIEW_REPOS = {
    "Concentro-Inc/woodrow",
    "Concentro-Inc/api",
    "Concentro-Inc/folio-platform",
}
TICKET_RE = re.compile(r"^CON-\d+$")
SECTION = "## Stacked follow-ups"
SECTION_INTRO = "This PR grew after it was first opened:"
BADGE_RE = re.compile(r"\n+---\n+📣 \*\*Posted to Slack\*\*\s*$")


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, text=True, capture_output=True)


def list_prs(repo, default, ticket):
    out = subprocess.run(
        [
            "gh", "pr", "list", "--repo", repo, "--base", default, "--state", "open",
            "--author", "@me", "--search", f"{ticket} in:title",
            "--json", "number,headRefName,title,reviewDecision,isDraft",
        ],
        check=True, text=True, capture_output=True,
    ).stdout
    return json.loads(out)


def matching(prs, ticket, branch):
    # Exact id inside the title's parentheses: (CON-44) never matches (CON-441).
    tag = re.compile(r"\(" + re.escape(ticket) + r"\)", re.IGNORECASE)
    return sorted(
        (p for p in prs if tag.search(p["title"]) and p["headRefName"] != branch),
        key=lambda p: p["number"],
    )


def fork_is_in_parent(default, parent_head, head, fetch):
    """True when merging the parent adds no default-branch commits it lacks."""
    if fetch:
        git("fetch", "--quiet", "origin", default, parent_head)
    fork = git("merge-base", head, f"origin/{default}").stdout.strip()
    return git("merge-base", "--is-ancestor", fork, f"origin/{parent_head}", check=False).returncode == 0


def decide(args):
    def result(decision, base_line=None, pr=None, head=None, reason=None):
        return {"decision": decision, "base": head or args.default, "pr": pr,
                "head": head, "reason": reason, "base_line": base_line}

    ticket = args.ticket.upper()
    if args.repo not in PR_REVIEW_REPOS:
        return result("none", reason=f"{args.repo} does not go through #pr-review")
    if not TICKET_RE.match(ticket):
        return result("none", reason=f"{args.ticket} is not a CON ticket")

    if args.prs_json:
        with open(args.prs_json) as f:
            prs = json.load(f)
    else:
        prs = list_prs(args.repo, args.default, ticket)
    found = matching(prs, ticket, args.branch)
    if not found:
        return result("none", reason=f"no other open PR carries ({ticket})")

    open_ones = [p for p in found if p.get("reviewDecision") != "APPROVED"]
    if not open_ones:
        n = found[0]["number"]
        return result("main", base_line=f"Base: {args.default} (#{n} already approved)",
                      pr=n, reason="approved")

    parent = open_ones[0]
    n, parent_head = parent["number"], parent["headRefName"]
    if not args.skip_ancestry and not fork_is_in_parent(
        args.default, parent_head, args.head, fetch=not args.no_fetch
    ):
        reason = f"branch is cut from a newer {args.default} than #{n}"
        return result("main", pr=n, reason=reason,
                      base_line=f"Base: {args.default} (could not stack onto #{n}: {reason})")
    return result("stack", pr=n, head=parent_head,
                  base_line=f"Base: stacked onto #{n} (same {ticket}, not yet approved)")


def add_note(body, pr, title):
    line = f"- #{pr}: {title}, merges into this PR after its own review"
    line_re = re.compile(rf"^- #{pr}\b.*$", re.MULTILINE)
    if line_re.search(body):
        return line_re.sub(lambda _: line, body, count=1)

    lines = body.split("\n")
    if SECTION in lines:
        i = lines.index(SECTION) + 1
        while i < len(lines) and not lines[i].startswith("## ") and lines[i].strip() != "---":
            i += 1
        while i > 0 and not lines[i - 1].strip():
            i -= 1
        lines.insert(i, line)
        return "\n".join(lines)

    block = [SECTION, SECTION_INTRO, line]
    if "## Changes" in lines:
        i = lines.index("## Changes") + 1
        while i < len(lines) and not lines[i].startswith("## ") and lines[i].strip() != "---":
            i += 1
        while i > 0 and not lines[i - 1].strip():
            i -= 1
        rest = lines[i:]
        tail = rest if not rest or not rest[0].strip() else [""] + rest
        return "\n".join(lines[:i] + [""] + block + tail)

    badge = BADGE_RE.search(body)
    if badge:
        return body[: badge.start()].rstrip() + "\n\n" + "\n".join(block) + body[badge.start():]
    return body.rstrip() + "\n\n" + "\n".join(block) + "\n"


def note(args):
    with open(args.body_file) as f:
        body = f.read()
    updated = add_note(body, args.pr, args.title)
    if updated != body:
        with open(args.body_file, "w") as f:
            f.write(updated)
    print("changed" if updated != body else "unchanged")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decide", help="pick the base for a new PR")
    d.add_argument("--repo", required=True, help="OWNER/REPO")
    d.add_argument("--default", required=True, help="the repo's default branch")
    d.add_argument("--ticket", required=True, help="CON-N")
    d.add_argument("--branch", required=True, help="the branch the new PR is opened from")
    d.add_argument("--head", default="HEAD", help="revision of the new work (default HEAD)")
    d.add_argument("--prs-json", help="read the PR list from a file instead of gh (tests)")
    d.add_argument("--no-fetch", action="store_true", help="use the origin refs as they are")
    d.add_argument("--skip-ancestry", action="store_true", help="skip the git check (tests)")

    n = sub.add_parser("note", help="record a stacked sub-PR in the parent's body file")
    n.add_argument("--body-file", required=True)
    n.add_argument("--pr", required=True, type=int, help="the sub-PR's number")
    n.add_argument("--title", required=True, help="the sub-PR's title")

    args = p.parse_args(argv)
    if args.cmd == "decide":
        print(json.dumps(decide(args)))
    else:
        note(args)


if __name__ == "__main__":
    sys.exit(main())
