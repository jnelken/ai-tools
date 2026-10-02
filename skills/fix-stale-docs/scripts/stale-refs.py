#!/usr/bin/env python3
"""List backticked file paths in a repo's markdown that don't exist on disk.

usage: stale-refs.py [REPO_DIR] [--code-root ~/code]

A path is `a/b.ext` (must contain "/" and a file extension; globs and URLs are
skipped). It resolves against REPO_DIR (also under src/, and relative to the doc),
or against a sibling repo named just before it ("folio-platform `infra/x.tf`" ->
<code-root>/folio-platform/infra/x.tf). It is reported only if no candidate exists. Output: file:line  ref  (why).
Skipped by default (historical records, not live docs): CHANGELOG*, docs/plans/,
dated files (YYYY-MM-DD-*), and any decisions/ directory. Pass --all to include them.
When the basename exists elsewhere in the target repo, the hit names it ("moved?").
A hit is a *candidate* — confirm by reading the line; the file may be generated,
gitignored, or deliberately historical.
"""
import os, re, subprocess, sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
repo = os.path.abspath(args[0] if args else ".")
root = os.path.expanduser(
    sys.argv[sys.argv.index("--code-root") + 1] if "--code-root" in sys.argv else "~/code"
)
siblings = {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d, ".git")) or os.path.isfile(os.path.join(root, d, ".git"))}
tick = re.compile(r"`([^`\s]+)`")
pathlike = re.compile(r"^[\w@.\-/]+/[\w.\-]+\.[A-Za-z0-9]{1,6}$")

def tracked(d):
    return subprocess.run(["git", "-C", d, "ls-files"], capture_output=True, text=True).stdout.split()

index = {}
def basenames(d):
    if d not in index:
        m = {}
        for t in tracked(d):
            m.setdefault(os.path.basename(t), []).append(t)
        index[d] = m
    return index[d]

historical = re.compile(r"(^|/)(CHANGELOG[^/]*|decisions/.*|docs/plans/.*|\d{4}-\d{2}-\d{2}-[^/]*)$")
files = [f for f in tracked(repo) if f.endswith(".md") and ("--all" in sys.argv or not historical.search(f))]
for f in files:
    try:
        lines = open(os.path.join(repo, f), encoding="utf-8").read().split("\n")
    except OSError:
        continue
    for n, line in enumerate(lines, 1):
        for m in tick.finditer(line):
            ref = m.group(1).rstrip(".,:;)")
            if not pathlike.match(ref) or ref.startswith(("http", "/", "~")) or "*" in ref:
                continue
            before = line[: m.start()][-40:]  # the repo name must sit right before the path
            named = [x for x in siblings if x != os.path.basename(repo) and re.search(rf"\b{re.escape(x)}'?s?\b", before)]
            docdir = os.path.dirname(os.path.join(repo, f))
            cands = [os.path.join(repo, ref), os.path.join(repo, "src", ref), os.path.join(docdir, ref)]
            base = repo
            if named:
                base = os.path.join(root, named[-1])
                cands += [os.path.join(base, ref), os.path.join(base, "src", ref)]
            if not any(os.path.exists(c) for c in cands):
                where = named[-1] if named else "this repo"
                moved = [t for t in basenames(base).get(os.path.basename(ref), []) if t != ref][:3]
                hint = f"; moved? {', '.join(moved)}" if moved else ""
                print(f"{f}:{n}  {ref}  (not found in {where}{hint})")
