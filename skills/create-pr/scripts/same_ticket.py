#!/usr/bin/env python3
"""Same-work stacking helpers for create-pr.

decide  Pick the base for a new PR into a Concentro repo's default branch:
        stack onto the open, not-yet-approved PR that carries the same (CON-N),
        or failing that one whose ticket is related (same parent issue, parent and
        sub-issue, or blocks/blocked by), then one in the same Linear project, or
        stay on the default branch. Read-only apart from `git fetch`.
note    Add or update the "Stacked follow-ups" line for a sub-PR in the parent
        PR's body, edited in place through a file.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

PR_REVIEW_REPOS = {
    "Concentro-Inc/woodrow",
    "Concentro-Inc/api",
    "Concentro-Inc/folio-platform",
}
TICKET_RE = re.compile(r"^CON-\d+$")
TITLE_TICKET_RE = re.compile(r"\((CON-\d+)\)", re.IGNORECASE)
LINEAR_WORKSPACE = "concentro"
LINEAR_TIMEOUT = 20
CONFIG = os.path.join("~", ".claude", "state", "create-pr-stack.json")
SECTION = "## Stacked follow-ups"
SECTION_INTRO = "This PR grew after it was first opened:"
BADGE_RE = re.compile(r"\n+---\n+📣 \*\*Posted to Slack\*\*\s*$")


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, text=True, capture_output=True)


def list_prs(repo, default):
    out = subprocess.run(
        [
            "gh", "pr", "list", "--repo", repo, "--base", default, "--state", "open",
            "--author", "@me", "--limit", "200",
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


def title_tickets(title):
    return {t.upper() for t in TITLE_TICKET_RE.findall(title)}


def linear_query(query):
    """Run a GraphQL query: the `linear` CLI when installed, else the API key over HTTPS.

    A failing CLI falls through to the API key when one is set, so a CLI whose flags
    or output differ from what this expects doesn't turn project stacking off.
    """
    cli_error = None
    if shutil.which("linear"):
        try:
            return linear_cli(query)
        except (LookupError, ValueError, KeyError, subprocess.SubprocessError) as e:
            cli_error = e
    key = os.environ.get("LINEAR_API_TOKEN") or os.environ.get("LINEAR_API_KEY")
    if not key:
        raise cli_error or LookupError("no linear CLI and no LINEAR_API_KEY")
    req = urllib.request.Request(
        "https://api.linear.app/graphql", data=json.dumps({"query": query}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=LINEAR_TIMEOUT) as resp:
        return graphql_data(json.loads(resp.read()))


def linear_cli(query):
    cmd = ["linear", "api", "--workspace", LINEAR_WORKSPACE, query]
    r = subprocess.run(cmd, text=True, capture_output=True, timeout=LINEAR_TIMEOUT)
    if r.returncode == 2:  # an older CLI without --workspace: use its default workspace
        r = subprocess.run(["linear", "api", query], text=True, capture_output=True,
                           timeout=LINEAR_TIMEOUT)
    if r.returncode:
        raise LookupError(f"linear api exited {r.returncode}: {r.stderr.strip()[:160]}")
    data = graphql_data(json.loads(r.stdout))
    data["issues"]["nodes"]  # a shape this doesn't understand counts as a failure
    return data


def graphql_data(out):
    if out.get("errors"):
        raise LookupError(f"GraphQL error: {out['errors'][0].get('message', '?')}")
    return out.get("data", out)


def ticket_info(tickets):
    """Look up each CON-N's project, parent and blocks relations in one batched query.

    Returns {ticket: {"project": {id, name, status} | None, "parent": "CON-P" | None,
    "blocks": {...}, "blocked_by": {...}}}. Raises LookupError when Linear can't be
    reached or doesn't know a ticket, which usually means the wrong workspace: the
    caller must not read that as "no project".
    """
    numbers = sorted({int(t.split("-")[1]) for t in tickets})
    query = (
        '{ issues(filter: { team: { key: { eq: "CON" } }, number: { in: %s } }, '
        "first: 250, includeArchived: true) { nodes { identifier parent { identifier } "
        "project { id name status { name } } "
        "relations { nodes { type relatedIssue { identifier } } } "
        "inverseRelations { nodes { type issue { identifier } } } } } }"
        % json.dumps(numbers)
    )
    found = {}
    for n in linear_query(query)["issues"]["nodes"]:
        project = n.get("project")
        if project:
            project = {"id": project["id"], "name": project["name"],
                       "status": (project.get("status") or {}).get("name")}
        found[n["identifier"].upper()] = {
            "project": project,
            "parent": ((n.get("parent") or {}).get("identifier") or "").upper() or None,
            # "A blocks B" is a `blocks` relation on A and an inverse one on B.
            # Plain `related` (and duplicate/similar) links never count.
            "blocks": {r["relatedIssue"]["identifier"].upper()
                       for r in (n.get("relations") or {}).get("nodes", []) if r["type"] == "blocks"},
            "blocked_by": {r["issue"]["identifier"].upper()
                           for r in (n.get("inverseRelations") or {}).get("nodes", []) if r["type"] == "blocks"},
        }
    missing = sorted(set(tickets) - set(found))
    if missing:
        raise LookupError(f"Linear did not return {', '.join(missing)}")
    return found


def related_how(ticket, other, info):
    """How `other` is related to `ticket`, or None: same parent, parent and sub-issue, or blocks."""
    me, them = info[ticket], info.get(other) or {}
    if me["parent"] and me["parent"] == them.get("parent"):
        return f"parent {me['parent']}"
    if me["parent"] == other:
        return f"parent {other}"
    if them.get("parent") == ticket:
        return f"parent {ticket}"
    if other in me["blocks"]:
        return f"blocks {other}"
    if other in me["blocked_by"]:
        return f"blocked by {other}"
    return None


def project_denylist(path):
    """Names (case-insensitive) or ids that never project-stack. Missing file: empty."""
    try:
        with open(os.path.expanduser(path)) as f:
            items = json.load(f).get("projectDenylist") or []
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, AttributeError) as e:
        raise LookupError(f"unreadable {path}: {e}") from e
    return {str(i).strip().casefold() for i in items}


def fork_is_in_parent(default, parent_head, head, fetch):
    """True when merging the parent adds no default-branch commits it lacks."""
    if fetch:
        git("fetch", "--quiet", "origin", default, parent_head)
    fork = git("merge-base", head, f"origin/{default}").stdout.strip()
    return git("merge-base", "--is-ancestor", fork, f"origin/{parent_head}", check=False).returncode == 0


def decide(args):
    note = None

    def result(decision, base_line=None, pr=None, head=None, reason=None, match=None, project=None):
        return {"decision": decision, "base": head or args.default, "pr": pr, "head": head,
                "reason": reason, "base_line": base_line, "match": match,
                "project": project, "note": note}

    ticket = args.ticket.upper()
    if args.repo not in PR_REVIEW_REPOS:
        return result("none", reason=f"{args.repo} does not go through #pr-review")
    if not TICKET_RE.match(ticket):
        return result("none", reason=f"{args.ticket} is not a CON ticket")

    if args.prs_json:
        with open(args.prs_json) as f:
            prs = json.load(f)
    else:
        prs = list_prs(args.repo, args.default)

    def stack_onto(found, match, why, project=None):
        """The shared rules: skip approved, lowest number first, fork ancestry gate."""
        open_ones = [p for p in found if p.get("reviewDecision") != "APPROVED"]
        if not open_ones:
            n = found[0]["number"]
            label = f"#{n} already approved" if match == "ticket" else f"#{n} {why}, already approved"
            return result("main", base_line=f"Base: {args.default} ({label})", pr=n,
                          reason="approved", match=match, project=project)
        parent = open_ones[0]
        n, parent_head = parent["number"], parent["headRefName"]
        if not args.skip_ancestry and not fork_is_in_parent(
            args.default, parent_head, args.head, fetch=not args.no_fetch
        ):
            reason = f"branch is cut from a newer {args.default} than #{n}"
            return result("main", pr=n, reason=reason, match=match, project=project,
                          base_line=f"Base: {args.default} (could not stack onto #{n}: {reason})")
        return result("stack", pr=n, head=parent_head, match=match, project=project,
                      base_line=f"Base: stacked onto #{n} ({why}, not yet approved)")

    # Each tier settles the decision as soon as it has any match, approved or not:
    # same ticket, then related ticket, then same project.
    found = matching(prs, ticket, args.branch)
    if found:
        return stack_onto(found, "ticket", f"same {ticket}")

    others = [p for p in prs if p["headRefName"] != args.branch and title_tickets(p["title"])]
    if not others:
        return result("none", reason=f"no other open PR carries ({ticket}) or any CON ticket")
    try:
        denied = project_denylist(args.config)
        info = ticket_info({ticket} | set().union(*(title_tickets(p["title"]) for p in others)))
    except Exception as e:  # never block PR creation on Linear
        note = " ".join(f"Related-ticket and project stacking skipped: {e}".split())
        return result("none", reason=f"no other open PR carries ({ticket})")

    # Related ticket: same parent, parent and sub-issue, or blocks / blocked by.
    related = []
    for p in sorted(others, key=lambda p: p["number"]):
        how = next(filter(None, (related_how(ticket, t, info) for t in sorted(title_tickets(p["title"])))), None)
        if how:
            related.append((p, how))
    if related:
        open_ones = [r for r in related if r[0].get("reviewDecision") != "APPROVED"]
        how = (open_ones or related)[0][1]
        return stack_onto([p for p, _ in related], "related", f"related: {how}")

    # Same project, unless its status is Maintenance (evergreen) or it is denylisted.
    project = info[ticket]["project"]
    if not project:
        return result("none", reason=f"no related open PR; {ticket} has no project")
    if (project.get("status") or "").casefold() == "maintenance":
        return result("none", project=project,
                      reason=f'no related open PR; project "{project["name"]}" is in Maintenance')
    if {project["id"].casefold(), project["name"].casefold()} & denied:
        return result("none", project=project,
                      reason=f'no related open PR; project "{project["name"]}" is denylisted')
    found = sorted(
        (p for p in others
         if any(((info.get(t) or {}).get("project") or {}).get("id") == project["id"]
                for t in title_tickets(p["title"]))),
        key=lambda p: p["number"],
    )
    if not found:
        return result("none", project=project,
                      reason=f'no related open PR or open PR in project "{project["name"]}"')
    return stack_onto(found, "project", f'same project "{project["name"]}"', project)


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
    d.add_argument("--config", default=CONFIG, help=f"projectDenylist file (default {CONFIG})")

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
