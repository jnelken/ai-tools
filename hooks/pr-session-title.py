#!/usr/bin/env python3
"""Name the Claude Code session after the PR it just opened: `woodrow#1345`.

Two hook events, one script (Claude Code only lets UserPromptSubmit set a
session title, so the rename is two-phase):

  --event posttool   PostToolUse on Bash. When a PR-create command succeeds
                     and its output carries a github.com/<owner>/<repo>/pull/<n>
                     URL, stash `<repo>#<n>` as the pending title for this
                     session. Prints nothing.
  --event prompt     UserPromptSubmit. If a title is pending, emit it as
                     hookSpecificOutput.sessionTitle (same effect as /rename)
                     and clear it. So the new title lands on the next prompt
                     after the PR opens, not mid-turn.

Overwrite rule, checked at both phases against ~/.claude/sessions/<pid>.json:
  - no name, or Claude's own derived/auto name  -> rename
  - a name that is itself `<repo>#<n>` (an older PR, even typed by hand) -> rename
  - any other name Jake set with /rename        -> leave it alone
  - session file not found                      -> leave it alone

Always exits 0. Stdout is empty or a single JSON object (UserPromptSubmit
stdout is parsed, so nothing else may be printed).

Env overrides (tests): PR_SESSION_TITLE_SESSIONS_DIR, PR_SESSION_TITLE_STATE_DIR,
PR_SESSION_TITLE_DEBUG=<path>.
"""

import json
import os
import re
import sys
import time

# Commands that open a PR: gh, woodrow's `pnpm run pr:create`, and `pr-create`
# style wrappers. `gh pr view|edit|checks` also print PR URLs, so the URL alone
# is never enough.
CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b|\bpr:create\b|\bpr-create\b")
PR_URL_RE = re.compile(r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)")
PR_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+#\d+$")
RENAME_RE = re.compile(r"^\s*/rename\b")


def debug(msg):
    path = os.environ.get("PR_SESSION_TITLE_DEBUG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass


def read_stdin():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def strings_in(value):
    """Every string inside a tool_response, whatever its field names are."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings_in(v)


def pr_title(data):
    """`repo#n` for the PR a successful create command just opened, or ''."""
    if data.get("tool_name") != "Bash":
        return ""
    tool_input = data.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not CREATE_RE.search(command):
        return ""
    matches = PR_URL_RE.findall("\n".join(strings_in(data.get("tool_response"))))
    if not matches:
        return ""
    _, repo, number = matches[-1]  # last URL: wrappers may print a parent PR first
    return "%s#%s" % (repo, number)


def session_name(session_id):
    """(found, name, source) from ~/.claude/sessions/<pid>.json for this session."""
    d = os.environ.get("PR_SESSION_TITLE_SESSIONS_DIR") or os.path.expanduser("~/.claude/sessions")
    try:
        names = os.listdir(d)
    except OSError:
        return False, "", ""
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(s, dict) and s.get("sessionId") == session_id:
            name, source = s.get("name"), s.get("nameSource")
            return True, name.strip() if isinstance(name, str) else "", source if isinstance(source, str) else ""
    return False, "", ""


def may_rename(session_id, title):
    """True when `title` should replace the session's current name."""
    found, name, source = session_name(session_id)
    if not found:
        return False
    if name == title:
        return False
    return source != "user" or not name or bool(PR_NAME_RE.match(name))


def pending_path(session_id):
    d = os.environ.get("PR_SESSION_TITLE_STATE_DIR") or os.path.expanduser("~/.cache/pr-session-title")
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_-]", "_", session_id) + ".json"), d


def on_posttool(data):
    session_id = data.get("session_id")
    title = pr_title(data)
    if not session_id or not title:
        return
    path, d = pending_path(session_id)
    if not may_rename(session_id, title):
        debug("posttool %s: not renaming to %s" % (session_id, title))
        try:
            os.remove(path)  # a stale pending title from an earlier PR
        except OSError:
            pass
        return
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": title, "ts": int(time.time())}, fh)
        debug("posttool %s: pending %s" % (session_id, title))
    except OSError:
        pass


def on_prompt(data):
    session_id = data.get("session_id")
    if not session_id:
        return
    path, _ = pending_path(session_id)
    try:
        with open(path, encoding="utf-8") as fh:
            title = json.load(fh).get("title", "")
    except (OSError, ValueError, AttributeError):
        return
    try:
        os.remove(path)
    except OSError:
        pass
    prompt = data.get("prompt")
    if isinstance(prompt, str) and RENAME_RE.match(prompt):
        return  # Jake is naming it himself this turn
    if not title or not may_rename(session_id, title):
        return
    debug("prompt %s: sessionTitle %s" % (session_id, title))
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title}}) + "\n")


def main(argv):
    event = ""
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--event" and args:
            event = args.pop(0)
    data = read_stdin()
    if event == "posttool":
        on_posttool(data)
    elif event == "prompt":
        on_prompt(data)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 — never block a prompt or a tool call
        debug("crashed: %r" % (exc,))
        sys.exit(0)
