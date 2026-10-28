#!/usr/bin/env python3
"""Reserve before Agent/Workflow, release the exact child on SubagentStop.

Session lifecycle events also count conducting/interactive sessions. A fanout
terminal adopts its watcher's reservation instead of being counted twice.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import agent_budget as budget


def owner_pid():
    pid = os.getppid()
    # A hook's immediate parent may be a transient shell. Never assign a lease
    # to that shell: it would be reaped before the subagent starts.
    for _ in range(8):
        result = subprocess.run(['ps', '-o', 'ppid=,comm=', '-p', str(pid)], capture_output=True, text=True, timeout=2)
        parts = result.stdout.strip().split(None, 1)
        if len(parts) != 2 or Path(parts[1]).name not in ('sh', 'bash', 'zsh', 'dash'):
            break
        pid = int(parts[0])
    return pid


def deny(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                  'permissionDecisionReason': reason + ' Use bin/fanout spawn to queue a Superset terminal without spending model quota while waiting.'}}


def handle(data):
    event = data.get('hook_event_name')
    session = data.get('session_id')
    terminal = os.environ.get('SUPERSET_TERMINAL_ID')
    if event == 'PreToolUse':
        if data.get('tool_name') not in ('Agent', 'Workflow'):
            return None
        if not budget.allow('claude', 'subagent', 1):
            return deny('Claude shared budget has no headroom for a subagent.')
        lease = budget.acquire('claude', 'subagent', pid=owner_pid(), label='hook',
                               session_id=session, tool_use_id=data.get('tool_use_id'))
        if not lease:
            return deny('Claude shared budget was reserved by another session.')
    elif event == 'SubagentStop':
        path = data.get('agent_transcript_path')
        if not path and session and data.get('agent_id'):
            matches = list((budget.claude_home() / 'projects').glob(f'*/{session}/subagents/agent-{data["agent_id"]}.jsonl'))
            path = str(matches[0]) if len(matches) == 1 else None
        metadata = budget.read_json(Path(path).with_suffix('.meta.json')) if path else {}
        tool_id = metadata.get('toolUseId')
        # Never release a sibling's lease when hooks arrive out of order.
        if tool_id:
            with budget.lock():
                leases = budget.reap()
                budget.write_json(budget.budget_dir() / 'leases.json', [l for l in leases if not (
                    l.get('label') == 'hook' and l.get('session_id') == session and l.get('tool_use_id') == tool_id)])
    elif event in ('SessionStart', 'SessionEnd') and session:
        model = budget.estimates(*budget.history())['claude']['roles']['interactive'] if event == 'SessionStart' else None
        with budget.lock():
            leases = budget.reap()
            leases = [l for l in leases if not (l.get('label') == 'session' and l.get('session_id') == session)]
            managed = os.environ.get('AGENT_BUDGET_LEASE_ID')
            adopted = any(l['provider'] == 'claude' and (l['id'] == managed or
                          (terminal and l.get('terminal_id') == terminal)) for l in leases)
            if event == 'SessionStart' and not adopted:
                # An already-started conducting session must be accounted even
                # above the cap; admission controls its children, not its launch.
                leases.append(dict(id=uuid.uuid4().hex, provider='claude', role='interactive', pct=model['pct'],
                                   pid=owner_pid(), created=time.time(), ttl=model['ttl'], label='session',
                                   session_id=session, terminal_id=terminal))
            budget.write_json(budget.budget_dir() / 'leases.json', leases)
    return None


def main():
    data = json.load(sys.stdin)
    try:
        result = handle(data)
    except Exception as error:
        if data.get('hook_event_name') == 'PreToolUse':
            result = deny('Shared budget could not be checked (' + type(error).__name__ + ').')
        else:
            print('agent-budget: lifecycle bookkeeping failed', file=sys.stderr)
            return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
