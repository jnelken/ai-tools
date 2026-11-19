#!/usr/bin/env python3
"""Plain webhook notices when the Slackagent formatter or delivery is unavailable."""
import json
import sys


def notice(_title, *_details):
    # A webhook does not cross Slackagent's shared redaction boundary. Keep its
    # fallback static so untrusted run text can never disclose credentials.
    return "*advance-roadmap · Run update*\n\n• Detailed summary unavailable. Check the roadmap dashboard."


def run_notice(path, stamp):
    try:
        with open(path) as source:
            for line in source:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("stamp") == stamp:
                    repo = row.get("repo") or row.get("assigned_repo") or "advance-roadmap"
                    outcome = str(row.get("outcome") or "unknown").replace("-", " ")
                    detail = row.get("slack_summary") or row.get("summary") or row.get("detail") or ""
                    return notice(f"{repo} · {outcome}", detail)
    except OSError:
        pass
    return ""


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "run":
        print(run_notice(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == "alert":
        print(notice("advance-roadmap · Run needs attention", sys.argv[2]))
    else:
        sys.exit(2)
