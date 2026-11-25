#!/usr/bin/env python3
# <xbar.title>J3u OSX</xbar.title>
# <xbar.desc>Unarchived Linear issues in the personal workspace (jnelken) against the 250 cap.</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
"""Menu bar readout of the linear-issue-count history; "Refresh now" re-counts live.

Reads the last record the weekly job (or a manual refresh) wrote, so the 15-minute plugin
interval makes no API calls. Refresh runs count.py, which appends a fresh record.
"""
import json
import os
from pathlib import Path

HOME = Path.home()
HISTORY = HOME / ".claude/automations/linear-issue-count/state/history.jsonl"
SCRIPT = HOME / ".claude/skills/linear-issue-count/scripts/count.py"

last = None
if HISTORY.exists():
    for line in HISTORY.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("workspace") == "jnelken":
            last = r

if not last:
    print("J3u ?")
    print("---")
else:
    n, cap = last["count"], last["cap"]
    pct = n / cap
    color = "#e5484d" if pct >= 0.9 else "#f5a524" if pct >= 0.8 else ""
    print(f"J3u {n}/{cap}" + (f" | color={color}" if color else ""))
    print("---")
    print(f"Linear (jnelken): {n} of {cap} unarchived ({pct:.0%}) | disabled=true")
    print(f"As of {last['date']} | disabled=true")
    for k, v in sorted(last.get("by_type", {}).items()):
        print(f"--{k}: {v} | disabled=true")
print(f"Refresh now | bash=/usr/bin/python3 param1={SCRIPT} terminal=false refresh=true")
print("Open Linear | href=https://linear.app/jnelken")
