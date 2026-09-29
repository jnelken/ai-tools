"""Resolve where Jake's personal repos live on this machine.

The parent directory of the personal checkouts differs by machine (the personal
Mac keeps them in ~/Dropbox/code; other machines have no Dropbox at all), so
nothing should hardcode it. This is the one place that decides.

    personal-code-dir                 the personal code dir (the parent that
                                      advance-roadmap / wrapup-repos iterate)
    personal-code-dir --repo NAME     one named personal checkout
    personal-code-dir --memory-dir    Claude Code's project memory dir for a
                                      session whose cwd is the code dir

Exit 1, with the reason on stderr, when nothing resolves. Callers that iterate
repos must treat that as "nothing to do": the code dir is NEVER guessed as
~/code, because on the work laptop that holds the Concentro root checkouts, and
an automation that commits WIP or pushes main must not run over them.

Code dir, first match wins:
  $PERSONAL_CODE_DIR
  $LEGACY_ENV (per caller: --legacy-env HYGIENE_CODE_DIR, JLIN_CODE_DIR)
  ~/Dropbox/code
  ~/Library/CloudStorage/Dropbox/code

A named repo (--repo): <code dir>/NAME, ~/code/NAME, ~/code/others/NAME — the
first that is a git checkout whose origin is owned by `jnelken`. The owner, not
the repo name, is what's checked: a directory's name can differ from its GitHub
repo (openclaw-vps is jnelken/vena-vps).
"""
import argparse
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
CODE_DIR_CANDIDATES = [
    os.path.join(HOME, "Dropbox/code"),
    os.path.join(HOME, "Library/CloudStorage/Dropbox/code"),
]
REPO_PARENTS_EXTRA = [os.path.join(HOME, "code"), os.path.join(HOME, "code/others")]
OWNER_RE = re.compile(r"[:/]jnelken/[^/]+?(\.git)?/?$")


def code_dir(legacy_env=None):
    for var in ("PERSONAL_CODE_DIR", legacy_env):
        value = os.environ.get(var) if var else None
        if value:
            return os.path.abspath(os.path.expanduser(value))
    for candidate in CODE_DIR_CANDIDATES:
        if os.path.isdir(candidate):
            return candidate
    return None


def is_personal_checkout(path):
    if not os.path.exists(os.path.join(path, ".git")):
        return False
    r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(OWNER_RE.search(r.stdout.strip()))


def find_repo(name, legacy_env=None):
    root = code_dir(legacy_env)
    parents = ([root] if root else []) + REPO_PARENTS_EXTRA
    for parent in parents:
        path = os.path.join(parent, name)
        if is_personal_checkout(path):
            return path
    return None


def memory_dir(path):
    # Claude Code names a project's dir after its cwd with every character
    # outside [A-Za-z0-9-] turned into "-" (`/` and `.` observed).
    return os.path.join(HOME, ".claude/projects", re.sub(r"[^A-Za-z0-9-]", "-", path), "memory")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", metavar="NAME")
    ap.add_argument("--memory-dir", action="store_true")
    ap.add_argument("--legacy-env", metavar="VAR",
                    help="an older caller-specific override honored after PERSONAL_CODE_DIR")
    args = ap.parse_args()

    if args.repo:
        path = find_repo(args.repo, args.legacy_env)
        if not path:
            sys.exit(f"personal-code-dir: no jnelken checkout named {args.repo!r} "
                     "(looked in the personal code dir, ~/code, ~/code/others)")
    else:
        path = code_dir(args.legacy_env)
        if not path:
            sys.exit("personal-code-dir: no personal code dir on this machine "
                     "(set PERSONAL_CODE_DIR; ~/Dropbox/code not found)")
    print(memory_dir(path) if args.memory_dir else path)


if __name__ == "__main__":
    main()
