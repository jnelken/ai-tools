"""Resolve where Jake's personal repos live on this machine.

The parent directory of the personal checkouts differs by machine (the personal
Mac keeps them in ~/Dropbox/code; the Windows PC uses D:\\code, with ~/code as
an alias; other machines have no Dropbox at all), so nothing should hardcode it.
This is the one place that decides.

    personal-code-dir                 the personal code dir (the parent that
                                      advance-roadmap / wrapup-repos iterate)
    personal-code-dir --repo NAME     one named personal checkout
    personal-code-dir --memory-dir    Claude Code's project memory dir for a
                                      session whose cwd is the code dir

Exit 1, with the reason on stderr, when nothing resolves. Callers that iterate
repos must treat that as "nothing to do": the code dir is NEVER guessed as
~/code on non-Windows machines, because on the work laptop that holds the
Concentro root checkouts, and an automation that commits WIP or pushes main
must not run over them. On Windows only, D:\\code and its documented ~/code
alias are checked before Dropbox — that PC's working clones live there, and
Dropbox/code may also exist but is the wrong parent.

Code dir, first match wins:
  $PERSONAL_CODE_DIR
  $LEGACY_ENV (per caller: --legacy-env HYGIENE_CODE_DIR, JLIN_CODE_DIR)
  (Windows only) D:\\code
  (Windows only) ~/code
  ~/Dropbox/code
  ~/Library/CloudStorage/Dropbox/code

A named repo (--repo): <code dir>/NAME if it is a directory (everything there is
personal, git checkout or not), else ~/code/NAME or ~/code/others/NAME if it is a
git checkout whose origin is owned by `jnelken` — those parents also hold work
repos. The owner, not the repo name, is what's checked: a directory's name can
differ from its GitHub repo (openclaw-vps is jnelken/vena-vps).
"""
import argparse
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
DROPBOX_CODE_DIR_CANDIDATES = [
    os.path.join(HOME, "Dropbox/code"),
    os.path.join(HOME, "Library/CloudStorage/Dropbox/code"),
]
# OMEN / Windows personal root. Never consulted on macOS/Linux — ~/code there
# holds Concentro work checkouts and must not become the personal code dir.
WINDOWS_CODE_DIR_CANDIDATES = [
    r"D:\code",
    os.path.join(HOME, "code"),
]
REPO_PARENTS_EXTRA = [os.path.join(HOME, "code"), os.path.join(HOME, "code/others")]
OWNER_RE = re.compile(r"[:/]jnelken/[^/]+?(\.git)?/?$")


def _is_windows():
    return os.name == "nt"


def _code_dir_candidates():
    if _is_windows():
        return WINDOWS_CODE_DIR_CANDIDATES + DROPBOX_CODE_DIR_CANDIDATES
    return list(DROPBOX_CODE_DIR_CANDIDATES)


def code_dir(legacy_env=None):
    for var in ("PERSONAL_CODE_DIR", legacy_env):
        value = os.environ.get(var) if var else None
        if value:
            return os.path.abspath(os.path.expanduser(value))
    for candidate in _code_dir_candidates():
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
    if root and os.path.isdir(os.path.join(root, name)):
        return os.path.join(root, name)
    for parent in REPO_PARENTS_EXTRA:
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
            sys.exit(f"personal-code-dir: no personal repo named {args.repo!r} "
                     "(looked in the personal code dir, and for a jnelken checkout in ~/code, ~/code/others)")
    else:
        path = code_dir(args.legacy_env)
        if not path:
            sys.exit("personal-code-dir: no personal code dir on this machine "
                     "(set PERSONAL_CODE_DIR; no Dropbox/code or Windows D:\\code)")
    print(memory_dir(path) if args.memory_dir else path)


if __name__ == "__main__":
    main()
