#!/usr/bin/env python3
"""Count distinct channel posts and queue an audit for each group of three."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ.get('CHANNEL_AUDIT_ROOT', Path.home() / '.claude/automations/channel-loop-audit'))
AUDIT = Path(__file__).resolve().with_name('audit.py')


def record(channel, event_id):
    if channel not in ('eng', 'ops') or not event_id:
        raise ValueError('Expected a monitored channel and event ID')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    with (ROOT / 'trigger.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = ROOT / 'trigger-state.json'
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            state = {}
        entry = state.setdefault(channel, {'count': 0, 'seen': []})
        if event_id in entry['seen']:
            return
        entry['seen'] = (entry['seen'] + [event_id])[-500:]
        entry['count'] += 1
        if entry['count'] >= 3:
            with (ROOT / 'audit.log').open('a') as out, (ROOT / 'audit.err').open('a') as err:
                subprocess.Popen([sys.executable, str(AUDIT), channel], stdout=out, stderr=err,
                                 start_new_session=True, close_fds=True)
            entry['count'] -= 3
        temp = ROOT / 'trigger-state.tmp'
        temp.write_text(json.dumps(state) + '\n')
        os.replace(temp, path)


if __name__ == '__main__':
    record(*sys.argv[1:])
