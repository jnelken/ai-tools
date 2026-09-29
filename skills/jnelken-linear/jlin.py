#!/usr/bin/env python3
"""Create and label issues in the personal `jnelken` Linear workspace (team Dev).

Every Dev issue must carry exactly one `repo/<directory>` label — advance-roadmap
reads it to decide which checkout under the personal code dir to write code in. This
script is the only sanctioned way to create one, so the label can't be skipped:

  jlin.py new --title T [--repo DIR] [--description TEXT | --description-file F]
              [--priority 0-4] [--state backlog|todo] [--label NAME ...] [--dry-run]
      Repo defaults to the git checkout you're standing in. Refuses to create
      an issue when it can't name the repo; creates the repo label if the
      directory exists but has no label yet.

  jlin.py label ISSUE --repo DIR [--evidence TEXT]
      Set an issue's repo label (replacing any other repo/* child).

  jlin.py infer [ISSUE ...] [--apply]
      Propose repo labels for unlabeled open issues (or the ones named) from
      repo names mentioned in their title/description. --apply labels only
      issues with exactly one candidate, and comments the evidence.

  jlin.py repos
      Directory → label mapping, including directories with no label yet.

Talks to Linear only through `linear api` (the CLI's stored credential), never MCP.
"""
import argparse
import json
import os
import re
import subprocess
import sys

def _personal_code_dir_module():
    """ai-tools' lib/personal_code_dir.py is the one place that decides where the
    personal repos live; find it from this file's real (symlink-resolved) location."""
    d = os.path.dirname(os.path.realpath(__file__))
    while d != os.path.dirname(d) and not os.path.isfile(os.path.join(d, "lib", "personal_code_dir.py")):
        d = os.path.dirname(d)
    if d == os.path.dirname(d):  # a copy outside the repo (e.g. a test root): use the deploy clone
        d = os.environ.get("AI_TOOLS_HOME", os.path.expanduser("~/.ai-tools"))
    sys.path.insert(0, os.path.join(d, "lib"))
    import personal_code_dir
    return personal_code_dir


CODE_DIR = _personal_code_dir_module().code_dir(legacy_env="JLIN_CODE_DIR") or ""
TEAM_KEY = "DEV"
REPO_GROUP = "repo"
# CLI name -> (Linear state name, state type). Match by name first: the team has more than one
# state of a type (e.g. "Needs Input" is also `unstarted`, and advance-roadmap skips it), so
# picking the first `unstarted` state files a ready ticket where no automation reads it.
STATES = {"backlog": ("Backlog", "backlog"), "todo": ("Todo", "unstarted")}


def pick_state(states, choice):
    name, typ = STATES[choice]
    return (next((s for s in states if s["name"] == name), None)
            or next((s for s in states if s["type"] == typ), None))


# ── Linear ───────────────────────────────────────────────────────────────────

def gql(query, variables=None):
    cmd = ["linear", "api", query]
    if variables:
        cmd += ["--variables-json", json.dumps(variables)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"linear api failed: {r.stderr.strip() or r.stdout.strip()}")
    data = json.loads(r.stdout)
    if data.get("errors"):
        sys.exit(f"linear api error: {data['errors']}")
    return data["data"]


def team():
    t = gql('query($k:String!){ teams(filter:{key:{eq:$k}}){ nodes{ id states{ nodes{ id name type } } } } }',
            {"k": TEAM_KEY})["teams"]["nodes"]
    if not t:
        sys.exit(f"team {TEAM_KEY} not found")
    return t[0]


def repo_labels():
    """(group_id, [{id, name, description}]) for children of the `repo` group."""
    g = gql('query($g:String!){ issueLabels(filter:{name:{eq:$g}, isGroup:{eq:true}}){ nodes{ id } } }',
            {"g": REPO_GROUP})["issueLabels"]["nodes"]
    if not g:
        sys.exit(f"label group {REPO_GROUP!r} not found")
    kids = gql('query($g:ID!){ issueLabels(first:250, filter:{parent:{id:{eq:$g}}}){ nodes{ id name description } } }',
               {"g": g[0]["id"]})["issueLabels"]["nodes"]
    return g[0]["id"], kids


# ── directory ↔ label mapping (pure; unit-tested) ────────────────────────────

def dir_key(dirname):
    """Label name a directory maps to: '[archived]x' → 'x', 'praxis (…)' → 'praxis'."""
    k = re.sub(r"^\[archived\]", "", dirname)
    k = re.sub(r"\s*\(.*\)\s*$", "", k)
    return k.strip()


def label_for_dir(dirname, labels):
    """The label for a directory, or None. Matches by name, then by a label
    description that records the directory explicitly (the mismatch cases)."""
    key = dir_key(dirname)
    for lb in labels:
        if lb["name"] == key:
            return lb
    for lb in labels:
        desc = lb.get("description") or ""
        if f'"{dirname}"' in desc or f"Directory: {dirname}" in desc:
            return lb
    return None


def code_dirs(code_dir=CODE_DIR):
    try:
        return sorted(d for d in os.listdir(code_dir)
                      if os.path.isdir(os.path.join(code_dir, d)) and not d.startswith("."))
    except OSError:
        return []


def candidates(text, labels):
    """Repo labels whose name appears as a whole token in text. A name that is
    a prefix of a longer matched name (mailcrush vs mailcrush-2023) yields to it."""
    low = text.lower()
    hits = []
    for lb in labels:
        name = lb["name"].lower()
        if len(name) < 4:
            continue
        if re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\w)", low):
            hits.append(lb)
    names = {h["name"].lower() for h in hits}
    return [h for h in hits if not any(n != h["name"].lower() and n.startswith(h["name"].lower() + "-")
                                       for n in names)]


# ── helpers ──────────────────────────────────────────────────────────────────

def cwd_repo_dir():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    top = os.path.realpath(r.stdout.strip())
    base = os.path.realpath(CODE_DIR)
    if os.path.dirname(top) != base:
        return None
    return os.path.basename(top)


def resolve_dir(repo):
    """Accept a directory name or a label name; return the directory."""
    dirs = code_dirs()
    if repo in dirs:
        return repo
    for d in dirs:
        if dir_key(d) == repo:
            return d
    sys.exit(f"no directory {repo!r} under {CODE_DIR or 'a personal code dir (none on this machine; set PERSONAL_CODE_DIR)'}. "
             "Every Dev issue needs a repo — "
             "ask Jake which one (or whether it needs a new repo) instead of guessing.")


def ensure_label(dirname, dry_run=False):
    group_id, labels = repo_labels()
    lb = label_for_dir(dirname, labels)
    if lb:
        return lb
    origin = subprocess.run(["git", "-C", os.path.join(CODE_DIR, dirname), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", origin)
    desc = m.group(1) if m else "Local-only (no origin)."
    if dir_key(dirname) != dirname:
        desc += f' Directory: "{dirname}".'
    name = dir_key(dirname)
    if dry_run:
        print(f"(dry run) would create label repo/{name}: {desc}")
        return {"id": None, "name": name, "description": desc}
    d = gql('mutation($i:IssueLabelCreateInput!){ issueLabelCreate(input:$i){ success issueLabel{ id name description } } }',
            {"i": {"name": name, "parentId": group_id, "description": desc}})
    lb = d["issueLabelCreate"]["issueLabel"]
    print(f"created label repo/{lb['name']}")
    return lb


def extra_labels(names, team_id):
    """Ids for non-repo labels by exact name (workspace or team Dev); exit on unknown."""
    out = []
    for name in names or []:
        if name.startswith(REPO_GROUP + "/"):
            sys.exit(f"--label {name}: the repo label comes from --repo / the checkout, not --label")
        d = gql('query($n:String!){ issueLabels(filter:{name:{eq:$n}}){ nodes{ id isGroup team{ id } parent{ name } } } }',
                {"n": name})["issueLabels"]["nodes"]
        d = [x for x in d if not x["isGroup"] and (x.get("parent") or {}).get("name") != REPO_GROUP
             and (x["team"] is None or x["team"]["id"] == team_id)]
        if not d:
            sys.exit(f"no label named {name!r} (check `linear label list --team {TEAM_KEY}`)")
        out.append(d[0]["id"])
    return out


def issue(ident):
    d = gql('query($id:String!){ issue(id:$id){ id identifier title description '
            'labels{ nodes{ id name parent{ name } } } } }', {"id": ident})
    return d["issue"]


def apply_label(iss, lb, evidence=None):
    old = [l["id"] for l in iss["labels"]["nodes"] if (l.get("parent") or {}).get("name") == REPO_GROUP]
    gql('mutation($id:String!,$i:IssueUpdateInput!){ issueUpdate(id:$id, input:$i){ success } }',
        {"id": iss["id"], "i": {"addedLabelIds": [lb["id"]],
                                "removedLabelIds": [x for x in old if x != lb["id"]]}})
    if evidence:
        gql('mutation($i:CommentCreateInput!){ commentCreate(input:$i){ success } }',
            {"i": {"issueId": iss["id"],
                   "body": f"Labelled `repo/{lb['name']}` automatically — {evidence}. "
                           "If that's the wrong repo, change the label; advance-roadmap writes code where it points."}})
    print(f"{iss['identifier']} → repo/{lb['name']}")


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_new(a):
    repo = a.repo or cwd_repo_dir()
    if not repo:
        sys.exit("can't tell which repo this issue belongs to: pass --repo <directory under "
                 f"{CODE_DIR}>. If you don't know, ask Jake — never file a Dev issue without one.")
    dirname = resolve_dir(repo)
    lb = ensure_label(dirname, a.dry_run)
    desc = a.description or ""
    if a.description_file:
        desc = open(a.description_file, encoding="utf-8").read()
    t = team()
    extra = extra_labels(a.label, t["id"])
    state = pick_state(t["states"]["nodes"], a.state)
    inp = {"teamId": t["id"], "title": a.title, "description": desc,
           "labelIds": [lb["id"]] + extra, "stateId": state["id"] if state else None}
    if a.priority is not None:
        inp["priority"] = a.priority
    if a.dry_run:
        print(json.dumps({**inp, "labelIds": [f"repo/{lb['name']}"] + (a.label or [])}, indent=1))
        return
    d = gql('mutation($i:IssueCreateInput!){ issueCreate(input:$i){ success issue{ identifier url } } }',
            {"i": inp})
    i = d["issueCreate"]["issue"]
    print(f"{i['identifier']} {i['url']}  [repo/{lb['name']}]")


def cmd_label(a):
    lb = ensure_label(resolve_dir(a.repo))
    apply_label(issue(a.issue), lb, a.evidence)


def unlabeled_open():
    d = gql('query($k:String!){ issues(first:250, filter:{team:{key:{eq:$k}}, '
            'state:{type:{nin:["completed","canceled","duplicate"]}}}){ nodes{ id identifier title description '
            'labels{ nodes{ id name parent{ name } } } } } }', {"k": TEAM_KEY})
    return [n for n in d["issues"]["nodes"]
            if not any((l.get("parent") or {}).get("name") == REPO_GROUP for l in n["labels"]["nodes"])]


def cmd_infer(a):
    _, labels = repo_labels()
    issues = [issue(i) for i in a.issues] if a.issues else unlabeled_open()
    for iss in issues:
        text = f"{iss['title']}\n{iss.get('description') or ''}"
        c = candidates(text, labels)
        names = ", ".join(f"repo/{x['name']}" for x in c) or "none"
        if len(c) == 1 and a.apply:
            apply_label(iss, c[0], f"the ticket text names `{c[0]['name']}`")
        else:
            verdict = "unique" if len(c) == 1 else ("ambiguous" if c else "no match")
            print(f"{iss['identifier']}: {verdict} ({names}) — {iss['title'][:70]}")


def cmd_repos(a):
    _, labels = repo_labels()
    for d in code_dirs():
        lb = label_for_dir(d, labels)
        print(f"{d:40} {'repo/' + lb['name'] if lb else '(no label yet)'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--title", required=True)
    n.add_argument("--repo")
    n.add_argument("--description")
    n.add_argument("--description-file")
    n.add_argument("--priority", type=int, choices=range(0, 5))
    n.add_argument("--state", choices=sorted(STATES), default="backlog")
    n.add_argument("--label", action="append", help="extra non-repo label by name; repeatable")
    n.add_argument("--dry-run", action="store_true")
    lab = sub.add_parser("label")
    lab.add_argument("issue")
    lab.add_argument("--repo", required=True)
    lab.add_argument("--evidence")
    inf = sub.add_parser("infer")
    inf.add_argument("issues", nargs="*")
    inf.add_argument("--apply", action="store_true")
    sub.add_parser("repos")
    a = p.parse_args(argv)
    {"new": cmd_new, "label": cmd_label, "infer": cmd_infer, "repos": cmd_repos}[a.cmd](a)


if __name__ == "__main__":
    main()
