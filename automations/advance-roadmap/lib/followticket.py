#!/usr/bin/env python3
"""Linear tickets for advance-roadmap follow-ups (followup.py): find, dedupe, defer, close.

A follow-up ticket exists only for work a usage gate stopped: it carries the remaining findings,
their evidence, the branch/PR, what was already validated, when the provider resets and the exact
next step. Every one has exactly one repo/* label (created through jnelken-linear's jlin.py) and a
fenced metadata block that later runs — on any machine — read back:

    ```advance-roadmap-followup
    {"workflow": "advance-roadmap-followup", "key": "...", "stage": "...", "finding_ids": [...],
     "branch": "...", "pr_url": "...", "builder_provider": "...", ...}
    ```

Older tickets written before the block existed are matched by finding text instead, so fresh
evidence goes onto them as a comment rather than a duplicate ticket. Linear is reached only through
the personal `linear` CLI (workspace jnelken) — never MCP.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WORKFLOW = "advance-roadmap-followup"
WORKSPACE = os.environ.get("ADVANCE_ROADMAP_LINEAR_WORKSPACE", "jnelken")
LINEAR = os.environ.get("ADVANCE_ROADMAP_LINEAR_BIN", "linear")
META_BLOCK = re.compile(r"```" + re.escape(WORKFLOW) + r"\s*\n(.*?)\n```", re.S)
PAGE = 100
OPEN_ISSUES = """query($after: String, $first: Int!) {
  issues(first: $first, after: $after, filter: {
    team: { key: { eq: "DEV" } },
    state: { type: { nin: ["completed", "canceled"] } }
  }) {
    pageInfo { hasNextPage endCursor }
    nodes { id identifier title description url
            state { name type }
            labels { nodes { name parent { name } } } }
  }
}"""


class LinearError(RuntimeError):
    pass


def linear(*args, timeout=120):
    try:
        p = subprocess.run([LINEAR, "--workspace", WORKSPACE, *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LinearError(f"linear {args[0]}: {e}") from e
    if p.returncode != 0:
        raise LinearError(f"linear {' '.join(args[:2])}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def gql(query, variables):
    out = linear("api", query, "--variables-json", json.dumps(variables))
    try:
        data = json.loads(out)
    except ValueError as e:
        raise LinearError(f"linear api: unparseable output: {out[:200]}") from e
    if data.get("errors"):
        raise LinearError(f"linear api: {data['errors']}")
    return data.get("data") or {}


def fetch_open():
    """Every open DEV issue, following the cursor until the last page (no silent cap)."""
    issues, after = [], None
    for _ in range(200):
        page = (gql(OPEN_ISSUES, {"after": after, "first": PAGE}).get("issues") or {})
        issues += page.get("nodes") or []
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return issues
        after = info.get("endCursor")
        if not after:
            break
    raise LinearError("linear api: pagination did not end")


def parse_meta(description):
    m = META_BLOCK.search(description or "")
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except ValueError:
        return None
    return meta if isinstance(meta, dict) and meta.get("workflow") == WORKFLOW else None


def repo_label(issue):
    names = [l.get("name") for l in ((issue.get("labels") or {}).get("nodes") or [])
             if (l.get("parent") or {}).get("name") == "repo"]
    return names[0] if len(names) == 1 else None


def norm(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


def followup_tickets(issues):
    """Open tickets that carry the follow-up metadata block: [(issue, meta)]."""
    return [(i, m) for i in issues if (m := parse_meta(i.get("description")))]


def match(issues, finding):
    """The open ticket already tracking this finding, or None: by finding id in a metadata block
    first, else (older tickets with no block) by the finding's title text in the same repo."""
    for issue in issues:
        meta = parse_meta(issue.get("description"))
        if meta and finding["id"] in (meta.get("finding_ids") or []):
            return issue
    title = norm(finding["title"])
    if len(title) < 12:
        return None
    for issue in issues:
        if parse_meta(issue.get("description")) or repo_label(issue) not in (None, finding.get("repo")):
            continue
        if title in norm(f"{issue.get('title')} {issue.get('description')}"):
            return issue
    return None


def render_findings(findings):
    lines = []
    for f in findings:
        lines.append(f"- **{f['title']}** (`{f['id']}`, {f['kind']}) — fix: {f.get('fix') or 'see evidence'}")
        for ev in f.get("evidence") or []:
            lines.append(f"  - `{ev.get('source')}`: {ev.get('quote')}")
    return "\n".join(lines)


def description(stage, findings, quota=None, next_step="", validation=None):
    meta = {"workflow": WORKFLOW, "key": stage["key"], "stage": stage["stage"], "repo": stage["repo"],
            "origin": stage.get("origin"), "stamp": stage.get("stamp"), "item": stage.get("item"),
            "finding_ids": [f["id"] for f in findings], "branch": stage.get("branch"),
            "pr_url": stage.get("pr_url"),
            # Which provider built the branch: the reviewer on another machine must be a different one.
            "builder_provider": stage.get("builder_provider")}
    q = quota or {}
    parts = [
        f"advance-roadmap's {stage['stage']} follow-up for "
        + (f"`{stage.get('item')}` ({stage.get('stamp')})" if stage.get("item") else f"run `{stage.get('origin')}`")
        + " found these and could not finish them: a provider usage gate stopped it.",
        "## Remaining findings", render_findings(findings),
        "## State",
        f"- Branch: `{stage.get('branch') or 'not created yet'}`" + (f" · PR: {stage['pr_url']}" if stage.get("pr_url") else ""),
        f"- Validation so far: {render_validation(validation or stage.get('validation'))}",
        f"- Stopped on: {q.get('provider') or '?'} ({q.get('role') or '?'}) — {q.get('cause') or 'over its usage gate'}"
        + (f"; resets {q['reset_at']}" if q.get("reset_at") else ""),
        f"- Next step: {next_step}",
        "Owned by `followup.py` (not the roadmap planner): it resumes this on its own once the provider resets, "
        "and closes the ticket only after checks pass, an independent review is clean, the PR merges and the "
        "deploy is verified.",
        f"```{WORKFLOW}\n{json.dumps(meta)}\n```",
    ]
    return "\n\n".join(parts) + "\n"


def render_validation(v):
    if not v:
        return "none yet"
    bits = []
    if v.get("builder"):
        bits.append(f"builder verification {v['builder']}")
    if v.get("reviews"):
        bits.append("reviews " + ", ".join(f"r{r['round']} {r['verdict']}" for r in v["reviews"]))
    if v.get("checks"):
        bits.append(f"PR checks {v['checks']}")
    if v.get("deploy"):
        bits.append(f"deploy {v['deploy']}")
    return "; ".join(bits) or "none yet"


def title_for(stage, findings):
    head = findings[0]["title"][:90]
    more = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    return f"Follow-up ({stage['stage']}): {head}{more}"


def with_file(text, fn):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return fn(path)
    finally:
        os.unlink(path)


def jlin_path():
    skill = Path(os.environ.get("ADVANCE_ROADMAP_SKILL_DIR", str(Path.home() / ".claude/skills/advance-roadmap")))
    return Path(os.environ.get("ADVANCE_ROADMAP_JLIN", str(skill.parent / "jnelken-linear" / "jlin.py")))


def create(stage, findings, quota, next_step):
    """File one ticket for the stage's remaining findings, through jlin.py (exactly one repo label)."""
    body = description(stage, findings, quota, next_step)

    def run(path):
        try:
            p = subprocess.run([sys.executable, str(jlin_path()), "new", "--repo", stage["repo"],
                                "--title", title_for(stage, findings), "--description-file", path,
                                "--state", "backlog"], capture_output=True, text=True, timeout=180,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LinearError(f"jlin.py new: {e}") from e
        if p.returncode != 0:
            raise LinearError(f"jlin.py new: exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
        m = re.search(r"\b(DEV-\d+)\b", p.stdout)
        if not m:
            raise LinearError(f"jlin.py new: no issue id in {p.stdout.strip()[:200]!r}")
        return m.group(1)
    return with_file(body, run)


def comment(identifier, body):
    with_file(body, lambda path: linear("issue", "comment", "add", identifier, "--body-file", path))


def close(identifier, body):
    comment(identifier, body)
    linear("issue", "update", identifier, "--state", "Done")
