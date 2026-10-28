"""Superset fan-out terminals with durable results and budget-aware supervisors."""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid

import agent_budget as budget

SUPERSET = '/Applications/Superset.app/Contents/Resources/resources/bin/superset'
FANOUT = budget.ROOT / 'bin/fanout'
BUDGET = budget.ROOT / 'bin/agent-budget'


def state_dir():
    return budget.claude_home() / 'state/fanout'


def agent_command(meta, prompt):
    if meta['provider'] == 'claude':
        command = ['claude', '-p', prompt, '--output-format', 'json']
        if meta.get('model'):
            command += ['--model', meta['model']]
        if meta.get('effort'):
            command += ['--effort', meta['effort']]
    else:
        command = ['codex', 'exec', '--json']
        if meta.get('model'):
            command += ['--model', meta['model']]
        if meta.get('effort'):
            command += ['-c', 'model_reasoning_effort=' + json.dumps(meta['effort'])]
        command += [prompt]
    return command


def budget_command(meta, command):
    return [str(BUDGET), 'run', '--wait', '--provider', meta['provider'], '--role', meta['role'],
            '--label', meta['label'], '--', *command]


def superset_command(meta, job):
    return [SUPERSET, 'terminals', 'create', '--workspace', meta['workspace'], '--local',
            '--cwd', meta['cwd'], '--command', shlex.join([str(FANOUT), '_run', str(job)]), '--json']


def interactive_command(meta, prompt):
    command = [SUPERSET, 'agents', 'create', '--workspace', meta['workspace'], '--local',
               '--agent', meta['provider'], '--prompt', prompt, '--json']
    for key in ('model', 'effort'):
        if meta.get(key):
            command += ['--' + key, meta[key]]
    return command


def update(job, **values):
    with budget.lock('fanout-meta-' + job.name):
        meta = budget.read_json(job / 'meta.json')
        meta.update(values)
        budget.write_json(job / 'meta.json', meta)
        return meta


def write_exit(job, code):
    temporary = job / 'exit.tmp'
    temporary.write_text(str(code) + '\n')
    os.replace(temporary, job / 'exit')


def spawn(args):
    workspace = args.workspace or os.environ.get('SUPERSET_WORKSPACE_ID')
    if not workspace:
        raise ValueError('set SUPERSET_WORKSPACE_ID or pass --workspace ID')
    prompt = Path(args.prompt_file).read_text()
    identifier = uuid.uuid4().hex
    job = state_dir() / identifier
    meta = {key: getattr(args, key) for key in ('label', 'provider', 'role', 'model', 'effort', 'interactive')}
    meta.update(id=identifier, workspace=workspace, cwd=os.getcwd(), created=time.time())
    command = interactive_command(meta, prompt) if args.interactive else superset_command(meta, job)
    if args.dry_run:
        print(json.dumps({'id': identifier, 'command': command, 'shell': shlex.join(command),
                          'budget_command': budget_command(meta, agent_command(meta, prompt)),
                          'supervisor': [str(FANOUT), '_interactive', str(job)] if args.interactive else None}))
        return 0
    job.mkdir(parents=True, mode=0o700)
    (job / 'prompt').write_text(prompt)
    budget.write_json(job / 'meta.json', meta)
    if args.interactive:
        update(job, state='queued')
        with (job / 'stderr').open('w') as log:
            # This process waits using no model, then creates and watches the
            # interactive terminal. It owns the lease, not the short-lived CLI.
            watcher = subprocess.Popen([str(FANOUT), '_interactive', str(job)], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
        update(job, watcher_pid=watcher.pid)
    else:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            update(job, terminal=json.loads(result.stdout))
        except (subprocess.CalledProcessError, ValueError, OSError):
            write_exit(job, 127)
            update(job, state='launch_failed')
            raise
    print(json.dumps(budget.read_json(job / 'meta.json')))
    return 0


def attach(job):
    meta = budget.read_json(job / 'meta.json')
    result = subprocess.run(interactive_command(meta, (job / 'prompt').read_text()),
                            capture_output=True, text=True, check=True)
    response = json.loads(result.stdout)
    terminal = response.get('terminalId') or response.get('sessionId')
    if not terminal or response.get('kind', 'terminal') != 'terminal':
        raise ValueError('Superset did not return a terminal session id')
    (job / 'result.json').write_text(json.dumps(response) + '\n')
    update(job, terminal_id=terminal, state='running')
    # Bind the reservation to the new terminal. SessionStart can adopt it rather
    # than adding a second interactive reservation for the same agent.
    with budget.lock():
        leases = budget.reap()
        owned = next((l for l in leases if l['id'] == os.environ.get('AGENT_BUDGET_LEASE_ID')), None)
        if owned:
            owned['terminal_id'] = terminal
            leases = [l for l in leases if l is owned or l.get('terminal_id') != terminal]
            budget.write_json(budget.budget_dir() / 'leases.json', leases)
    while True:
        time.sleep(5)
        try:
            result = subprocess.run([SUPERSET, 'terminals', 'list', '--local', '--workspace', meta['workspace'], '--json'],
                                    capture_output=True, text=True, check=True, timeout=20)
            sessions = json.loads(result.stdout)['sessions']
            current = next((s for s in sessions if s.get('terminalId') == terminal), None)
            if not current or current.get('exited'):
                return (current or {}).get('exitCode', 0) or 0
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            # A transient CLI outage is not terminal completion. TTL bounds the
            # reservation even if the watcher cannot establish terminal state.
            continue


def supervise(job, interactive=False):
    meta = budget.read_json(job / 'meta.json')
    command = [str(FANOUT), '_attach', str(job)] if interactive else [str(FANOUT), '_execute', str(job)]
    code, child = 127, None
    received = []
    previous = {}

    def forward(sig, _frame):
        received.append(sig)
        if child:
            try:
                child.send_signal(sig)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, forward)
    try:
        with (job / ('supervisor.log' if interactive else 'result.json')).open('w') as out, (job / 'stderr').open('a') as err:
            command_line = budget_command(meta, command)
            child = subprocess.Popen(command_line, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                     env=budget.child_env())
            if received:
                child.send_signal(received[-1])
            code = child.wait()
            code = code if code >= 0 else 128 - code
    finally:
        write_exit(job, code)
        update(job, state='complete', finished=time.time())
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return code


def execute(job):
    """Run only after admission; capture measured cost for future reservations."""
    meta = budget.read_json(job / 'meta.json')
    started = time.time()
    if meta['provider'] == 'claude':
        budget.claude_quota.snapshot('start', launch_id=meta['id'], label='fanout:' + meta['role'])
    child = None
    previous = {}
    received = []

    def forward(sig, _frame):
        received.append(sig)
        if child:
            try:
                child.send_signal(sig)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, forward)
    code = 127
    try:
        env = dict(budget.child_env(), CLAUDE_QUOTA_MANAGED=meta['id'])
        child = subprocess.Popen(agent_command(meta, (job / 'prompt').read_text()), env=env, stdin=subprocess.DEVNULL)
        if received:
            child.send_signal(received[-1])
        code = child.wait()
        return code if code >= 0 else 128 - code
    except OSError:
        print('fanout: unable to start provider CLI', file=sys.stderr)
        return 127
    finally:
        if meta['provider'] == 'claude':
            budget.claude_quota.snapshot('finish', launch_id=meta['id'], label='fanout:' + meta['role'], exit_code=code)
        try:
            record_completion(job, meta, started)
        except (OSError, TypeError, ValueError):
            print('fanout: usage could not be recorded', file=sys.stderr)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def record_completion(job, meta, started):
    records = list(budget.rows(job / 'result.json'))
    tokens = 0
    if meta['provider'] == 'claude':
        final = next((r for r in reversed(records) if 'result' in r), {})
        models = final.get('modelUsage') or {}
        tokens = sum((u.get('inputTokens', 0) + u.get('cacheCreationInputTokens', 0) + u.get('outputTokens', 0))
                     for u in models.values()) if models else budget.token_total(final)
    else:
        for row in records:
            if row.get('type') == 'turn.completed':
                tokens += budget.token_total(row)
    if tokens:
        with budget.lock('completions'):
            fd = os.open(budget.budget_dir() / 'completions.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as handle:
                handle.write(json.dumps(dict(provider=meta['provider'], role=meta['role'], total=tokens,
                                             launch_id=meta['id'], duration=time.time() - started)) + '\n')


def inspect_jobs(identifier=None, collect=False):
    paths = [state_dir() / identifier] if identifier else sorted(state_dir().glob('*'))
    result = []
    for path in paths:
        if not (path / 'meta.json').is_file():
            continue
        row = budget.read_json(path / 'meta.json')
        complete = (path / 'exit').exists()
        row.update(complete=complete, exit=int((path / 'exit').read_text()) if complete else None)
        if collect and complete:
            row['result'] = (path / 'result.json').read_text() if (path / 'result.json').exists() else ''
        result.append(row)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('spawn')
    p.add_argument('--label', required=True)
    p.add_argument('--provider', choices=('claude', 'codex'), required=True)
    p.add_argument('--role', choices=budget.ROLES, required=True)
    p.add_argument('--prompt-file', required=True)
    p.add_argument('--model')
    p.add_argument('--effort')
    p.add_argument('--interactive', action='store_true')
    p.add_argument('--workspace')
    p.add_argument('--dry-run', action='store_true')
    for name in ('status', 'collect'):
        p = sub.add_parser(name)
        p.add_argument('id', nargs='?')
        p.add_argument('--json', action='store_true')
    for name in ('_run', '_interactive', '_attach', '_execute'):
        p = sub.add_parser(name, help=argparse.SUPPRESS)
        p.add_argument('job', type=Path)
    a = parser.parse_args(argv)
    try:
        if a.cmd == 'spawn':
            return spawn(a)
        if a.cmd == '_execute':
            return execute(a.job)
        if a.cmd == '_attach':
            return attach(a.job)
        if a.cmd in ('_run', '_interactive'):
            return supervise(a.job, a.cmd == '_interactive')
        result = inspect_jobs(a.id, a.cmd == 'collect')
        if a.json or a.cmd == 'collect':
            print(json.dumps(result))
        else:
            for row in result:
                print(f'{row["id"]} {row["label"]}: ' + (f'exit {row["exit"]}' if row['complete'] else 'pending'))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'fanout: {error}', file=sys.stderr)
        return 1
