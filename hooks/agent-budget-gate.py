#!/usr/bin/env python3
"""Reserve before Agent/Workflow, release the exact child on SubagentStop.

The hook is advisory: unknown quota, errors, and timeout allow the tool.
Only admitted children are leased; interactive sessions use the cap reserve.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import agent_budget as budget

# Finish before Claude's 30s hook timeout, including a blocked stdin or flock.
HOOK_TIMEOUT_SECONDS = 25


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
    if event == 'PreToolUse':
        if data.get('tool_name') not in ('Agent', 'Workflow'):
            return None
        reading = budget.readings('claude')
        # A missing window must never turn the hook into the fanout queue's
        # single-unknown-lease gate. Known windows still enforce their caps.
        if not any(budget.number(w.get('live')) for w in reading['windows'].values()):
            return None
        lease = budget.acquire('claude', 'subagent', pid=owner_pid(), label='hook', reading=reading,
                               session_id=session, tool_use_id=data.get('tool_use_id'), unknown_limit=False)
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
    return None


def main():
    def timeout(_signum, _frame):
        raise TimeoutError('budget hook deadline')

    previous = {}
    try:
        for sig in (signal.SIGALRM, signal.SIGTERM):
            previous[sig] = signal.signal(sig, timeout)
        signal.setitimer(signal.ITIMER_REAL, HOOK_TIMEOUT_SECONDS)
        data = json.load(sys.stdin)
        result = handle(data)
        if result:
            print(json.dumps(result))
    except Exception as error:
        print('agent-budget: check skipped (' + type(error).__name__ + '); allowing tool', file=sys.stderr)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == '__main__':
    sys.exit(main())
