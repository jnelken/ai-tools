#!/usr/bin/env python3
"""Recover a planner's result fence from its Claude session transcript.

A planner's plan is the ORCHESTRATOR_RESULT_JSON fence in its final answer. When a turn follows the
fence (a Stop hook asking for a sign-off, a closing remark), the final answer no longer holds it and
the finished triage would be recorded as an error. The transcript still has the message that did.

    lastfence.py --result-json F --label L --out OUT

Reads `session_id` from the `claude -p --output-format json` result F, finds that session's
transcript under the Claude projects directory, and writes the text of the newest assistant message
that contains a ```L fence to OUT. Only assistant text blocks of the main thread count, so the
prompt echo (a user entry) and subagent chatter never qualify. The caller still parses the fence out
of OUT. Exits 0 when a message was written, 1 otherwise.

ADVANCE_ROADMAP_CLAUDE_PROJECTS overrides the projects directory (tests do).
"""
import argparse
import glob
import json
import os
import sys


def projects_dir():
    return os.environ.get("ADVANCE_ROADMAP_CLAUDE_PROJECTS") or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def session_id(result_json):
    try:
        with open(result_json, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):  # stderr is merged into the file; the object is one JSON line
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("session_id"), str):
            sid = data["session_id"].strip()
            return sid if sid and os.sep not in sid and sid != ".." else None
    return None


def assistant_messages(transcript):
    """Each non-sidechain assistant entry's text blocks joined, oldest first."""
    out = []
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, list):
                    out.append("\n".join(b.get("text", "") for b in content
                                         if isinstance(b, dict) and b.get("type") == "text"))
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--result-json", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sid = session_id(a.result_json)
    if not sid:
        return 1
    start = "```" + a.label
    for transcript in glob.glob(os.path.join(projects_dir(), "*", sid + ".jsonl")):
        for text in reversed(assistant_messages(transcript)):
            if start in text:
                with open(a.out, "w", encoding="utf-8") as f:
                    f.write(text)
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
