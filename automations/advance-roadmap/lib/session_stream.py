#!/usr/bin/env python3
"""Capture exact provider session ownership while a worker is still running."""
import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import continuation
import workqueue


def quota_decision(reading, baseline, reservation, other_reserved):
    """Conservative account-wide observation; deltas may include other sessions."""
    observations, reason = {}, None
    for name, window in (reading.get('windows') or {}).items():
        live, cap = window.get('live'), window.get('cap')
        old = ((baseline.get('windows') or {}).get(name) or {}).get('live')
        other, target = other_reserved.get(name, 0), (reservation or {}).get(name, 0)
        observed = live - old if isinstance(live, (int, float)) and isinstance(old, (int, float)) and live >= old else None
        observations[name] = {'live': live, 'cap': cap, 'other_reserved': other,
                              'account_delta': observed, 'target': target}
        if isinstance(live, (int, float)) and isinstance(cap, (int, float)) and live + other >= cap:
            reason = 'quota-ceiling'
        elif reason is None and observed is not None and target > 0 and observed >= target:
            reason = 'quota-target'
    return reason, observations


def cached_quota(args, budget):
    # Do not start model calls for telemetry. The batch's existing refresh
    # updates these account-wide cached readings while the worker runs.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'lib'))
    import agent_budget
    reading = agent_budget.readings(args.provider, root=Path(args.root), probe=False)
    leases = agent_budget.read_json(agent_budget.budget_dir() / 'leases.json', [])
    own = set(budget.get('lease_ids') or [])
    now = time.time()
    active = [lease for lease in leases if lease.get('provider') == args.provider
              and lease.get('id') not in own and lease.get('created', 0) + lease.get('ttl', 0) > now]
    other = {name: sum(agent_budget.lease_pct(lease, name) for lease in active)
             for name in reading.get('windows', {})}
    for name, value in (budget.get('baseline', {}).get('windows') or {}).items():
        if name in reading.get('windows', {}) and isinstance(value.get('cap'), (int, float)):
            reading['windows'][name]['cap'] = value['cap']
    reason, observations = quota_decision(reading, budget.get('baseline') or {},
                                          budget.get('worker_reservation') or {}, other)
    return reason, {'at': now, 'source': reading.get('source'), 'observed_at': reading.get('observed_at'),
                    'error': reading.get('error'),
                    'windows': observations, 'other_reservations': len(active),
                    'account_delta_quality': 'account-wide, isolation unverified'}


def session_event(line, provider):
    try:
        event = json.loads(line)
        if provider == 'codex' and event.get('type') == 'thread.started':
            value = event.get('thread_id')
        elif provider == 'claude' and event.get('type') == 'system' and event.get('subtype') == 'init':
            value = event.get('session_id')
        else:
            return None
        return str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None


def stop_failed_start(child):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def run(args):
    request = workqueue.read(args.request, {})
    budget = workqueue.read(args.budget, {}) if args.budget else {}
    if budget:
        try:
            blocked, check = cached_quota(args, budget)
            if budget.get('job_override') or os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
                fresh = (not check.get('error') and isinstance(check.get('observed_at'), (int, float))
                         and 0 <= time.time() - check['observed_at'] <= (60 if budget.get('job_override') else 120)
                         and bool(check.get('windows'))
                         and all(isinstance(w.get('live'), (int, float)) for w in check['windows'].values()))
                if not fresh:
                    blocked = 'override-stale-usage' if budget.get('job_override') else 'usage-unavailable'
            if blocked:
                if args.lifecycle:
                    Path(args.lifecycle).write_text(json.dumps({'reason': blocked, 'forced': True,
                        'elapsed_seconds': 0, 'provider_exit': None, 'session_captured': False,
                        'last_quota': check}) + '\n')
                return 124
        except (OSError, ValueError, KeyError, ImportError):
            if budget.get('job_override') or os.environ.get('ADVANCE_ROADMAP_CONTINUOUS') == '1':
                if args.lifecycle:
                    Path(args.lifecycle).write_text(json.dumps({'reason': 'override-usage-unavailable' if budget.get('job_override') else 'usage-unavailable',
                        'forced': True, 'elapsed_seconds': 0, 'provider_exit': None,
                        'session_captured': False}) + '\n')
                return 124
    child = subprocess.Popen(args.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, cwd=args.workspace, bufsize=0,
                             start_new_session=True)
    started = time.monotonic()
    initial_head = workqueue.git(args.workspace, 'rev-parse', 'HEAD')
    first_commit_seconds, first_session_seconds, compactions = None, None, 0
    first_model_input = None
    next_head_check = started + 15
    child_start = workqueue.process_start(child.pid)
    guard = None
    if child_start:
        try:
            guard_path = Path(__file__).with_name('session_guard.py')
            if not guard_path.is_file():
                raise FileNotFoundError('independent session watchdog is missing')
            guard_ready = Path(args.output + '.guard-ready')
            guard_ready.unlink(missing_ok=True)
            guard = subprocess.Popen([sys.executable, str(guard_path),
                '--parent', str(os.getpid()), '--child', str(child.pid), '--child-start', child_start,
                '--timeout', str(args.timeout + 5 if args.timeout > 0 else 0), '--ready', str(guard_ready)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            guard_deadline = time.monotonic() + 3
            while not guard_ready.exists() and guard.poll() is None and time.monotonic() < guard_deadline:
                time.sleep(.02)
            if not guard_ready.exists():
                raise RuntimeError('independent session watchdog did not start')
            guard_ready.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            stop_failed_start(child)
            if guard and guard.poll() is None:
                guard.terminate()
                guard.wait()
            raise
    elif child.poll() is None:
        stop_failed_start(child)
        raise RuntimeError('cannot identify worker process for independent watchdog')
    deadline = started + args.timeout if args.timeout > 0 else None
    stop_at, reason = None, None
    next_quota, last_quota = started, None
    previous = {}

    def forward(signum, _frame):
        nonlocal stop_at, reason
        if reason is None:
            reason, stop_at = 'external-signal', time.monotonic()
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, forward)
    recorded = None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    buffer = b''
    try:
        with open(args.output, 'wb') as output:
            while selector.get_map():
                now = time.monotonic()
                if reason is None and guard and guard.poll() is not None and child.poll() is None:
                    reason, stop_at = 'guard-failed', now
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if now >= next_head_check and first_commit_seconds is None and initial_head:
                    next_head_check = now + 15
                    current_head = workqueue.git(args.workspace, 'rev-parse', 'HEAD')
                    if current_head and current_head != initial_head:
                        first_commit_seconds = round(now - started, 3)
                if reason is None and deadline is not None and now >= deadline:
                    reason, stop_at = 'watchdog', now
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if reason is None and budget and now >= next_quota:
                    next_quota = now + max(.1, args.quota_poll)
                    try:
                        reason, last_quota = cached_quota(args, budget)
                    except (OSError, ValueError, KeyError, ImportError):
                        reason = None
                    if reason:
                        stop_at = time.monotonic()
                        try:
                            os.killpg(child.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                if stop_at is not None and now - stop_at >= 10:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    stop_at = None
                for key, _ in selector.select(timeout=.2):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    output.write(data)
                    output.flush()
                    buffer += data
                    lines = buffer.split(b'\n')
                    buffer = lines.pop()
                    for line in lines:
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            event = {}
                        if isinstance(event, dict) and (event.get('type') == 'thread.compacted'
                                or (event.get('type') == 'system' and event.get('subtype') == 'compact_boundary')):
                            compactions += 1
                        if (first_model_input is None and isinstance(event, dict)
                                and event.get('type') == 'assistant'):
                            message = event.get('message') if isinstance(event.get('message'), dict) else {}
                            usage = message.get('usage') or {}
                            if isinstance(usage, dict) and usage:
                                first_model_input = {key: usage.get(key) for key in (
                                    'input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')}
                        sid = session_event(line, args.provider)
                        if not sid or sid == recorded:
                            continue
                        bound = continuation.binding(request, args.provider, args.workspace, sid)
                        if bound:
                            with workqueue.Store(args.root) as store:
                                if store.save_session(bound):
                                    Path(args.session_out).write_text(sid + '\n')
                                    recorded = sid
                                    if first_session_seconds is None:
                                        first_session_seconds = round(time.monotonic() - started, 3)
    finally:
        selector.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    result = child.wait()
    if guard:
        try:
            guard.wait(timeout=2)
        except subprocess.TimeoutExpired:
            guard.terminate()
            guard.wait(timeout=2)
    if recorded:
        bound = continuation.binding(request, args.provider, args.workspace, recorded)
        if bound:
            with workqueue.Store(args.root) as store:
                store.save_session(bound)
    if first_commit_seconds is None and initial_head:
        current_head = workqueue.git(args.workspace, 'rev-parse', 'HEAD')
        if current_head and current_head != initial_head:
            first_commit_seconds = round(time.monotonic() - started, 3)
    if budget:
        try:
            _, last_quota = cached_quota(args, budget)
        except (OSError, ValueError, KeyError, ImportError):
            pass
    if args.lifecycle:
        Path(args.lifecycle).write_text(json.dumps({'reason': reason or 'provider-exit',
            'forced': bool(reason), 'elapsed_seconds': round(time.monotonic() - started, 3),
            'provider_exit': result, 'session_captured': bool(recorded), 'last_quota': last_quota,
            'time_to_session_seconds': first_session_seconds,
            'time_to_first_commit_seconds': first_commit_seconds,
            'first_commit_is_progress_proxy': True, 'compaction_events': compactions,
            'checkpoint_generation_model_calls': 0, 'first_model_input': first_model_input,
            'checkpoint_generation_tokens': None, 'checkpoint_generation_seconds': None,
            'reconstruction_tokens': None, 'compaction_tokens': None}) + '\n')
    return 124 if reason in ('watchdog', 'quota-ceiling', 'quota-target') else 128 + signal.SIGTERM if reason else result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=('claude', 'codex'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--session-out', required=True)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--lifecycle')
    parser.add_argument('--budget')
    parser.add_argument('--quota-poll', type=float, default=15)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 < args.timeout < float('inf'):
        parser.error('a finite positive watchdog timeout is required')
    if args.command[:1] == ['--']:
        args.command = args.command[1:]
    if not args.command:
        parser.error('a provider command is required')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
