#!/usr/bin/env python3
"""Group one window of merged PRs (woodrow, api, folio-platform) by ticket.

Usage: cluster_week.py --since 'YYYY-MM-DD HH:MM' --until 'YYYY-MM-DD HH:MM' --out clusters.json
Env:   CODE_ROOT (default ~/code) holds the three repos; each is fetched, then
       origin/main is read along --first-parent so one merged PR is one unit.
Output: {"since","until","clusters":[{"id","repos","prs":[{repo,pr,url,title,author,date,children}]}]}
Cluster id is the first CON-/DEV- ticket key in a PR title (or its child commits),
else "<repo>#<pr>". Clusters are sorted largest first; woodrow PRs lead each cluster.
"""
import argparse, collections, json, os, re, subprocess

REPOS = {"woodrow": "Concentro-Inc/woodrow", "api": "Concentro-Inc/api",
         "folio-platform": "Concentro-Inc/folio-platform"}
TICKET = re.compile(r"\b(?:CON|DEV)-\d+\b")


def git(repo, *args):
    root = os.path.expanduser(os.environ.get("CODE_ROOT", "~/code"))
    return subprocess.run(["git", *args], cwd=os.path.join(root, repo),
                          capture_output=True, text=True, check=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    clusters = collections.OrderedDict()
    for repo, slug in REPOS.items():
        git(repo, "fetch", "origin", "--quiet")
        raw = git(repo, "log", "origin/main", "--first-parent", "--since", a.since, "--until", a.until,
                  "--pretty=format:%H\x1f%P\x1f%cd\x1f%an\x1f%s", "--date=iso-local")
        for line in raw.splitlines():
            sha, parents, date, author, subject = line.split("\x1f", 4)
            if "[bot]" in author:
                continue
            ps = parents.split()
            m = re.search(r"\(#(\d+)\)\s*$", subject) or re.search(r"Merge pull request #(\d+)", subject)
            children = []
            if len(ps) > 1:
                children = [c for c in git(repo, "log", f"{ps[0]}..{ps[1]}", "--no-merges",
                                           "--pretty=format:%s").splitlines() if c][:25]
            keys = TICKET.findall(subject) or [k for c, _ in collections.Counter(
                k for s in children for k in TICKET.findall(s)).most_common(1) for k in [c]]
            pr = int(m.group(1)) if m else None
            cid = keys[0] if keys else f"{repo}#{pr or sha[:9]}"
            url = f"https://github.com/{slug}/pull/{pr}" if pr else f"https://github.com/{slug}/commit/{sha[:9]}"
            clusters.setdefault(cid, []).append(dict(repo=repo, pr=pr, url=url, title=subject,
                                                    author=author, date=date[:10], children=children[:8]))
    out = []
    for cid, prs in clusters.items():
        prs.sort(key=lambda p: (p["repo"] != "woodrow", p["date"]))
        out.append(dict(id=cid, repos=sorted({p["repo"] for p in prs}), prs=prs))
    out.sort(key=lambda c: -len(c["prs"]))
    json.dump(dict(since=a.since, until=a.until, clusters=out), open(a.out, "w"), ensure_ascii=False, indent=0)
    print(f"{len(out)} clusters from {sum(len(c['prs']) for c in out)} PRs")


if __name__ == "__main__":
    main()
