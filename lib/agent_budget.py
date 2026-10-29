"""Account-wide admission control. Stdlib only; importing never reads account state.

A reservation is a conservative percentage-point estimate, charged to every known
window. It is not a hard provider limit: other machines and estimation error are
visible only in subsequent readings. All admissions are serialized under flock.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import claude_quota

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('agent_budget_usage', ROOT / 'automations/advance-roadmap/lib/usage.py')
usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage)
PROVIDERS = ('claude', 'codex', 'cursor')
ROLES = ('planner', 'worker', 'reviewer', 'subagent', 'interactive')
SEED_TOKENS = {
    'claude': dict(zip(ROLES, (251000, 264000, 100000, 70000, 251000))),
    'codex': dict(zip(ROLES, (70000, 100000, 29000, 70000, 140000))),
    'cursor': dict(zip(ROLES, (70000, 59000, 59000, 59000, 118000))),
}
# F9: ~2 points/min for three sessions; reserve 15 minutes/session = 10 points.
# Cursor has no measured conversion yet: explicitly conservative provisional 5/job.
SEED_FACTORS = {'claude': 10 / 251000, 'codex': 3 / 70000, 'cursor': 5 / 59000}
MAX_AGE = 15 * 60
ESTIMATE_MAX_AGE = 10 * 60
ESTIMATE_VERSION = 3
HISTORY_VERSION = 3


def claude_home():
    return Path(os.environ.get('AGENT_BUDGET_CLAUDE_HOME', Path.home() / '.claude'))


def budget_dir():
    return claude_home() / 'state/agent-budget'


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def rows(path):
    try:
        with path.open(encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        yield row
                except ValueError:
                    continue
    except OSError:
        return


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle)
    os.replace(tmp, path)


@contextmanager
def lock(name='leases'):
    path = budget_dir() / (name + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def epoch(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pctl(values, quantile=.8):
    values = sorted(v for v in values if number(v) and v > 0)
    return values[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def token_total(row):
    if number(row.get('total')):
        return max(0, row['total'])
    u = row.get('usage') or row.get('tokens') or row
    if number(u.get('total')):
        return max(0, u['total'])
    if any(number(u.get(k)) for k in ('input_tokens', 'output_tokens', 'cache_write_input_tokens')):
        return (max(0, (u.get('input_tokens', 0) or 0) - (u.get('cached_input_tokens', 0) or 0))
                + sum(u.get(k, 0) or 0 for k in ('cache_creation_input_tokens', 'cache_write_input_tokens', 'output_tokens')))
    if number(u.get('total_tokens')):
        return max(0, u['total_tokens'] - u.get('cached_input_tokens', 0))
    if any(k in u for k in ('input', 'cache_write', 'output')):
        return sum(u.get(k, 0) or 0 for k in ('input', 'cache_write', 'output'))
    return 0


def role_name(role):
    return {'orchestrator': 'planner', 'approver': 'reviewer', 'review': 'reviewer'}.get(role, role)


def transcript_record(path, role='subagent', since=None, until=None):
    # Streaming rows can repeat a message id: count the maximum usage per field,
    # once per API message, rather than summing successive output snapshots.
    messages, stamps = {}, []
    for row in rows(path):
        stamp = epoch(row.get('timestamp'))
        if (since is not None or until is not None) and (stamp is None or
                (since is not None and stamp < since) or (until is not None and stamp > until)):
            continue
        message = row.get('message') or {}
        if row.get('type') != 'assistant' or not isinstance(message.get('usage'), dict):
            continue
        key = message.get('id') or row.get('uuid')
        if not key:
            continue
        old = messages.setdefault(key, {})
        for field in ('input_tokens', 'cache_creation_input_tokens', 'output_tokens'):
            old[field] = max(old.get(field, 0), message['usage'].get(field, 0) or 0)
        if epoch(row.get('timestamp')):
            stamps.append(epoch(row['timestamp']))
    return {'provider': 'claude', 'role': role, 'agent_type': read_json(path.with_suffix('.meta.json')).get('agentType'), 'total': sum(token_total(m) for m in messages.values()),
            'duration': max(stamps) - min(stamps) if len(stamps) > 1 else None}


def codex_observations(path):
    """Cumulative *new* tokens and account-wide, integer quota observations.

    Parallel rollouts can carry older quota readings. Keep the raw observation
    here; only the account merge below may take a percentage delta.
    """
    previous, result = 0, []
    for i, row in enumerate(rows(path)):
        payload = row.get('payload') or {}
        total = (payload.get('info') or {}).get('total_token_usage') or {}
        window = (usage.parse_codex_rate_limits(payload.get('rate_limits')) or {}).get('five_hour') or {}
        if not total or not number(window.get('used_pct')) or not window.get('reset_epoch'):
            continue
        tokens = token_total(total)
        result.append(dict(id=f'codex:{path}:{i}', at=epoch(row.get('timestamp')),
                           tokens=max(0, tokens - previous), pct=window['used_pct'],
                           reset=window['reset_epoch'], sequence=i))
        previous = tokens
    return result


def codex_calibration(observations):
    """Pair one account delta with all local tokens in the same active interval.

    Do not drop flat quota intervals: a rounded point belongs to many turns.
    Do not count the same account increase once per concurrent rollout. Stale
    parallel readings may go backwards; take a high-water mark within a reset.
    A 15-minute gap ends an interval so idle/unobserved work is never charged to
    the next tiny turn. The first observation is a baseline, not fresh usage.
    """
    result, first, last, tokens, high = [], None, None, 0, 0

    def finish():
        if first and tokens > 0 and high > first['pct']:
            result.append(dict(id=first['id'], provider='codex', tokens=tokens,
                               pct=high - first['pct'], source='rate_limits'))

    for current in sorted(observations, key=lambda r: (r['at'] or 0, r['id'].rsplit(':', 1)[0], r['sequence'])):
        if last and (current['reset'] != last['reset'] or
                     (current['at'] is not None and last['at'] is not None and
                      current['at'] - last['at'] > MAX_AGE)):
            finish()
            first = None
        if first is None:
            first, tokens, high = current, 0, current['pct']
        else:
            tokens += current['tokens']
            high = max(high, current['pct'])
        last = current
    finish()
    return result


def codex_samples(path):
    return codex_calibration(codex_observations(path))


def history():
    """Keep job-cost totals separate from interval/session calibration usage."""
    home = claude_home()
    records, calibration_records = [], []
    for row in rows(home / 'automations/advance-roadmap/runs.jsonl'):
        grouped = {}
        for call in (row.get('tokens') or {}).get('calls') or []:
            # Preserve each round's session/token association for calibration.
            calibration_records.append(call)
            key = (call.get('provider'), role_name(call.get('role')))
            record = grouped.setdefault(key, dict(provider=key[0], role=key[1], total=0,
                                                   duration=row.get('duration_s')))
            record['total'] += token_total(call)
        records.extend(grouped.values())
    for path in (home / 'state/agent-usage.jsonl', budget_dir() / 'completions.jsonl'):
        source = list(rows(path))
        records.extend(source)
        calibration_records.extend(source)
    samples, codex_rows = [], []
    with lock('samples'):
        cache = read_json(budget_dir() / 'history-cache.json')

        def cached_transcript(path, role, since=None, until=None):
            try:
                stat = path.stat()
            except FileNotFoundError:
                return None
            key = str(path) + (f':interval:{since}:{until}' if since is not None else '')
            stamp = [HISTORY_VERSION, stat.st_mtime_ns, stat.st_size]
            if cache.get(key, {}).get('stamp') != stamp:
                cache[key] = {'stamp': stamp, 'records': [transcript_record(path, role, since, until)]}
            return cache[key]['records'][0]

        for path in (home / 'projects').glob('*/**/subagents/agent-*.jsonl'):
            record = cached_transcript(path, 'subagent')
            if record:
                records.append(record)
        for path in Path(os.environ.get('ADVANCE_ROADMAP_CODEX_SESSIONS', Path.home() / '.codex/sessions')).glob('*/*/*/rollout-*.jsonl'):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            stamp = [HISTORY_VERSION, stat.st_mtime_ns, stat.st_size]
            if cache.get(str(path), {}).get('stamp') != stamp:
                cache[str(path)] = {'stamp': stamp, 'observations': codex_observations(path)}
            codex_rows.extend(cache[str(path)]['observations'])
        samples.extend(codex_calibration(codex_rows))

        indexed = {}
        for record in calibration_records:
            sessions = set([record.get('session_id'), *(record.get('sessions') or [])]) - {None}
            # A summarized row with several sessions cannot supply individual
            # session usage. Prefer a bounded transcript; never guess a split.
            keys = sessions if len(sessions) == 1 else set()
            if record.get('launch_id'):
                keys = keys | {record['launch_id']}
            for key in keys:
                indexed.setdefault(key, []).append(record)
        starts, pairs, counts = {}, [], {}
        for row in rows(home / 'state/quota-snapshots.jsonl'):
            key = row.get('launch_id') or row.get('session_id')
            if not key:
                continue
            if row.get('event') == 'start':
                starts[key] = row
            elif row.get('event') == 'finish' and key in starts:
                pairs.append((key, starts.pop(key), row))
                counts[key] = counts.get(key, 0) + 1
        for key, start, finish in pairs:
            since, until = epoch(start.get('timestamp')), epoch(finish.get('timestamp'))
            bounded = since is not None and until is not None and since <= until
            matched = [r for r in indexed.get(key, []) if r.get('provider') == 'claude']
            # Resumed sessions can have multiple snapshot intervals. Untimed
            # aggregate ledger totals cannot be reused for each interval.
            if counts[key] > 1:
                matched = [r for r in matched if bounded and since <= (epoch(r.get('at') or r.get('timestamp')) or -1) <= until]
            tokens = sum(token_total(r) for r in matched)
            path = finish.get('transcript_path') or start.get('transcript_path')
            if path and bounded:
                record = cached_transcript(Path(path), 'interactive', since, until)
                if record is not None:
                    tokens = record['total']
            before = (start.get('quota') or {}).get('windows', {}).get('five_hour', {})
            after = (finish.get('quota') or {}).get('windows', {}).get('five_hour', {})
            a, b = before.get('used_percentage'), after.get('used_percentage')
            reset_a, reset_b = epoch(before.get('resets_at')), epoch(after.get('resets_at'))
            same_window = reset_a and reset_b and abs(reset_a - reset_b) < 60
            fresh = all(number((r.get('quota') or {}).get('age_seconds')) and
                        r['quota']['age_seconds'] <= MAX_AGE for r in (start, finish))
            if tokens > 0 and number(a) and number(b) and b > a and same_window and fresh:
                samples.append({'id': f'quota:{key}:{start.get("timestamp")}', 'provider': 'claude',
                                'tokens': tokens, 'pct': b - a, 'source': 'quota_snapshots'})
        # Rebuild generated samples so upgrades also repair previously persisted
        # bad denominators. Preserve manually supplied/provider-specific samples.
        existing = {r.get('id'): r for r in rows(budget_dir() / 'samples.jsonl')
                    if r.get('source') not in ('quota_snapshots', 'rate_limits')}
        existing.update({r['id']: r for r in samples})
        combined = list(existing.values())
        path = budget_dir() / 'samples.jsonl'
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            for sample in combined:
                handle.write(json.dumps(sample) + '\n')
        os.replace(temporary, path)
        write_json(budget_dir() / 'history-cache.json', cache)
    return records, combined


def estimates(records, samples):
    result = {}
    for provider in PROVIDERS:
        ratios = [s['pct'] / s['tokens'] for s in samples if s.get('provider') == provider
                  and number(s.get('pct')) and s['pct'] > 0 and number(s.get('tokens')) and s['tokens'] > 0]
        calibrated = len(ratios) >= 5
        factor = pctl(ratios) if calibrated else SEED_FACTORS[provider]
        if calibrated and provider == 'codex':
            # Weight account intervals by exposure, not by how often a tiny
            # session happened to straddle an integer percentage boundary.
            weighted = sorted((s['pct'] / s['tokens'], s['tokens']) for s in samples
                              if s.get('provider') == provider and number(s.get('tokens'))
                              and s['tokens'] > 0 and number(s.get('pct')) and s['pct'] > 0)
            threshold, cumulative = .8 * sum(t for _, t in weighted), 0
            for ratio, tokens in weighted:
                cumulative += tokens
                if cumulative >= threshold:
                    factor = ratio
                    break
        roles = {}
        for role in ROLES:
            matched = [r for r in records if r.get('provider') == provider and role_name(r.get('role')) == role]
            tokens = pctl([token_total(r) for r in matched]) or SEED_TOKENS[provider][role]
            duration = pctl([r.get('duration', (r.get('duration_ms') or 0) / 1000) for r in matched], .9)
            roles[role] = {'tokens_p80': tokens, 'pct': max(.1, tokens * factor),
                           'ttl': max(60, duration * 1.5) if duration else 1800,
                           'observations': len(matched)}
        result[provider] = {'factor': factor, 'factor_source': 'calibrated' if calibrated else 'seed',
                            'factor_samples': len(ratios), 'roles': roles}
    return result


def cached_estimates():
    """Share models across short-lived hooks; scan history at most every 10m."""
    with lock('estimates'):
        path = budget_dir() / 'estimates.json'
        cached = read_json(path)
        if (isinstance(cached, dict) and cached.get('version') == ESTIMATE_VERSION
                and number(cached.get('generated_at'))
                and 0 <= time.time() - cached['generated_at'] < ESTIMATE_MAX_AGE
                and isinstance(cached.get('models'), dict)):
            return cached['models']
        models = estimates(*history())
        write_json(path, dict(version=ESTIMATE_VERSION, generated_at=time.time(), models=models))
        return models


def window_live(window, fresh, now):
    if not fresh or not number(window.get('used_pct')):
        return None
    normalized = dict(window, reset_epoch=epoch(window.get('reset_epoch')))
    return usage._zero_if_reset(normalized, now)['used_pct']


def readings(provider):
    now = time.time()
    if provider == 'claude':
        q = claude_quota.quota()
        fresh = number(q.get('age_seconds')) and q['age_seconds'] <= MAX_AGE
        return {'source': q.get('source'), 'observed_at': q.get('observed_at'), 'windows': {
            name: {'live': value.get('used_percentage') if fresh else None, 'cap': cap}
            for name, cap in [('five_hour', usage.MAX_FIVE_HOUR_PCT), ('seven_day', usage.MAX_SEVEN_DAY_PCT)]
            for value in [q.get('windows', {}).get(name, {})]}}
    state = usage.load(claude_home() / 'automations/advance-roadmap')
    prov = state['providers'][provider]
    observed = (prov.get('observed_epoch') or epoch(prov.get('observed_at'))) if provider == 'codex' else epoch(prov.get('included', {}).get('observed_at'))
    if not observed or now - observed > MAX_AGE:
        if provider == 'codex':
            usage.probe_codex(state)
        else:
            usage.probe_cursor_into(state)
        prov = state['providers'][provider]
        observed = (prov.get('observed_epoch') or epoch(prov.get('observed_at'))) if provider == 'codex' else epoch(prov.get('included', {}).get('observed_at'))
    fresh = observed is not None and now - observed <= MAX_AGE
    if provider == 'cursor':
        inc = prov.get('included') or {}
        windows = {pool: {'live': window_live({'used_pct': inc.get(pool + '_pct'), 'reset_epoch': inc.get('reset_epoch')}, fresh, now), 'cap': usage.MAX_CURSOR_PCT}
                   for pool in ('auto', 'api')}
    else:
        windows = {key: {'live': window_live(prov.get(key) or {}, fresh, now), 'cap': cap}
                   for key, cap in [('five_hour', usage.MAX_FIVE_HOUR_PCT), ('weekly', usage.MAX_SEVEN_DAY_PCT)]}
    return {'source': prov.get('source'), 'observed_at': observed, 'windows': windows}


def alive(pid):
    try:
        if int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False


def process_start(pid):
    """Identify this process incarnation, without a psutil dependency."""
    if sys.platform.startswith('linux'):
        try:
            # comm can contain spaces/parentheses; field 22 is starttime ticks.
            fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
            return 'linux:' + fields[19]
        except FileNotFoundError:
            return None
    result = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)],
                            capture_output=True, text=True, timeout=1)
    value = result.stdout.strip()
    return 'ps:' + value if result.returncode == 0 and value else None


def reap():
    """Caller holds leases.lock, including reads (which persist the reaping)."""
    leases = read_json(budget_dir() / 'leases.json', [])
    now = time.time()
    identities = {}
    active = []
    for lease in leases:
        pid = lease.get('pid')
        if (lease.get('label') == 'session' or not lease.get('pid_started_at') or not alive(pid)
                or lease.get('created', 0) + lease.get('ttl', 0) <= now):
            continue
        if pid not in identities:
            identities[pid] = process_start(pid)
        if identities[pid] == lease['pid_started_at']:
            active.append(lease)
    leases = active
    write_json(budget_dir() / 'leases.json', leases)
    return leases


def capacity(reading, leases, provider, role, cost, want, unknown_limit=True):
    active = [l for l in leases if l['provider'] == provider]
    reserved = sum(l['pct'] for l in active)
    windows = reading['windows']
    if provider == 'cursor':
        model = os.environ.get(usage.CURSOR_MODEL_ENV.get(role, usage.CURSOR_MODEL_ENV['worker']), 'auto')
        windows = {k: v for k, v in windows.items() if k == ('auto' if model == 'auto' else 'api')}
    known = [w['cap'] - w['live'] - reserved for w in windows.values() if number(w.get('live'))]
    unknown = any(not number(w.get('live')) for w in windows.values())
    fit = min(want, max(0, math.floor((min(known) + 1e-9) / cost))) if known else want
    if unknown and unknown_limit:
        fit = min(fit, 0 if active else 1)
    return max(0, fit)


def view(provider, reading, model, leases):
    reserved = sum(l['pct'] for l in leases if l['provider'] == provider)
    return dict(reading, reserved_pct=reserved, estimates=model,
                windows={k: dict(v, headroom=v['cap'] - v['live'] - reserved if number(v.get('live')) else None)
                         for k, v in reading['windows'].items()})


def allow(provider, role, want=1):
    model = cached_estimates()[provider]
    reading = readings(provider)
    with lock():
        leases = reap()
        count = capacity(reading, leases, provider, role, model['roles'][role]['pct'], want)
    return count


def acquire(provider, role, pid=None, ttl=None, label=None, reading=None, unknown_limit=True, **extra):
    model = cached_estimates()[provider]['roles'][role]
    reading = readings(provider) if reading is None else reading
    with lock():
        leases = reap()
        if not capacity(reading, leases, provider, role, model['pct'], 1, unknown_limit=unknown_limit):
            return None
        owner = pid or os.getpid()
        started = process_start(owner)
        if started is None:
            raise ProcessLookupError('lease owner exited')
        lease = dict(id=uuid.uuid4().hex, provider=provider, role=role, pct=model['pct'],
                     pid=owner, pid_started_at=started, created=time.time(), ttl=ttl or model['ttl'], label=label, **extra)
        leases.append(lease)
        write_json(budget_dir() / 'leases.json', leases)
        return lease


def release(lease_id):
    with lock():
        leases = reap()
        write_json(budget_dir() / 'leases.json', [l for l in leases if l['id'] != lease_id])


def child_env():
    # Keep workspace/account attribution for leaderboard binding, but remove the
    # parent agent identity and all hook endpoints. Never print environment values.
    return {k: v for k, v in os.environ.items() if not (
        k.startswith(('SUPERSET_AGENT_', 'SUPERSET_HOOK_', 'SUPERSET_HOST_AGENT_')) or k in (
        'SUPERSET_TERMINAL_ID', 'SUPERSET_TAB_ID', 'SUPERSET_PANE_ID', 'SUPERSET_PORT', 'SUPERSET_DEBUG_HOOKS',
        'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'))}


def run(provider, role, command, wait=False, label=None):
    child, lease = None, None
    received, previous = [], {}

    def forward(signum, _frame):
        received.append(signum)
        if child:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, forward)
    try:
        backoff = 1
        while not received:
            lease = acquire(provider, role, label=label)
            if lease or not wait:
                break
            print(f'agent-budget: {provider}/{role} queued; waiting {backoff}s', file=sys.stderr)
            # Short sleeps make cancellation prompt even during long backoffs.
            until = time.monotonic() + backoff
            while not received and time.monotonic() < until:
                time.sleep(min(.2, max(0, until - time.monotonic())))
            backoff = min(30, backoff * 2)
        if received:
            return 128 + received[-1]
        if not lease:
            print('agent-budget: no headroom; use bin/fanout to queue a terminal', file=sys.stderr)
            return 3
        child = subprocess.Popen(command, env=dict(child_env(), AGENT_BUDGET_LEASE_ID=lease['id']), start_new_session=True)
        if received:
            forward(received[-1], None)
        code = child.wait()
        return code if code >= 0 else 128 - code
    except OSError as error:
        print(f'agent-budget: unable to start command: {error.strerror}', file=sys.stderr)
        return 127
    finally:
        if lease:
            release(lease['id'])
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true')
    for name in ('allow', 'lease', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--provider', choices=PROVIDERS, required=True)
        p.add_argument('--role', choices=ROLES, required=True)
        if name == 'allow':
            p.add_argument('--want', type=int, default=1)
        if name != 'run':
            p.add_argument('--json', action='store_true')
        if name in ('lease', 'run'):
            p.add_argument('--label')
        if name == 'lease':
            p.add_argument('--pid', type=int, required=True, help='long-lived lease owner, not this CLI')
            p.add_argument('--ttl', type=float)
        if name == 'run':
            p.add_argument('--wait', action='store_true')
            p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('release')
    p.add_argument('id')
    a = parser.parse_args(argv)
    if a.cmd == 'release':
        release(a.id)
    elif a.cmd == 'status':
        models = cached_estimates()
        live = {p: readings(p) for p in PROVIDERS}
        with lock():
            leases = reap()
            result = {p: view(p, live[p], models[p], leases) for p in PROVIDERS}
        if a.json:
            print(json.dumps(result))
        else:
            for p, v in result.items():
                print(f'{p}: reserved {v["reserved_pct"]:.2f}% ({v["estimates"]["factor_source"]})')
                for w, value in v['windows'].items():
                    print(f'  {w}: live={value["live"]} cap={value["cap"]} headroom={value["headroom"]}')
                print('  job %: ' + ', '.join(f'{r}={c["pct"]:.2f}' for r, c in models[p]['roles'].items()))
    elif a.cmd == 'allow':
        if a.want < 0:
            parser.error('--want must be nonnegative')
        count = allow(a.provider, a.role, a.want)
        print(json.dumps({'allowed': count}) if a.json else count)
    elif a.cmd == 'lease':
        if a.pid <= 0 or (a.ttl is not None and a.ttl <= 0):
            parser.error('--pid and --ttl must be positive')
        lease = acquire(a.provider, a.role, a.pid, a.ttl, a.label)
        print(json.dumps(lease) if a.json else lease['id'] if lease else '')
        return 0 if lease else 3
    else:
        command = a.command[1:] if a.command[:1] == ['--'] else a.command
        if not command:
            parser.error('run requires -- CMD...')
        return run(a.provider, a.role, command, a.wait, a.label)
    return 0
