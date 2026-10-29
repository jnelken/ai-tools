"""Account-wide admission control. Stdlib only; importing never reads account state.

A reservation is a conservative percentage-point estimate, charged to every known
window. It is not a hard provider limit: other machines and estimation error are
visible only in subsequent readings. All admissions are serialized under flock.
"""
import argparse
from contextlib import contextmanager, nullcontext
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
ESTIMATE_VERSION = 4
HISTORY_VERSION = 3
USAGE_FIELDS = ('input', 'cache_write', 'cache_read', 'output')
OUTCOMES = ('ok', 'error', 'limit', 'timeout', 'cancelled')
LEDGER_MIN_ROWS = 3        # settled rows a (agent, role) or kind bucket needs before it beats other sources
MAX_BYPASS = 3             # times smaller waiters may overtake a head that draining leases could admit
WATCH_FRACTION = .8        # extend a reservation once actual tokens pass this share of it
WATCH_INTERVAL = 60        # seconds between transcript polls (AGENT_BUDGET_WATCH_INTERVAL overrides)
REAPED_GRACE = 24 * 3600   # a reaped lease stays settle-able this long
WAITER_MAX_AGE = 12 * 3600
EXIT_NO_HEADROOM, EXIT_WOULD_EXCEED, EXIT_NO_LEASE = 3, 4, 5
WINDOWS = {'claude': ('five_hour', 'seven_day'), 'codex': ('five_hour', 'weekly'), 'cursor': ('auto', 'api')}


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


def transcript_usage(path, since=None, until=None):
    """Per-field token sums for one Claude transcript, plus message timestamps.

    Streaming rows can repeat a message id: count the maximum usage per field,
    once per API message, rather than summing successive output snapshots.
    """
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
        for field in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens'):
            old[field] = max(old.get(field, 0), message['usage'].get(field, 0) or 0)
        if epoch(row.get('timestamp')):
            stamps.append(epoch(row['timestamp']))
    return messages, stamps


def transcript_record(path, role='subagent', since=None, until=None):
    messages, stamps = transcript_usage(path, since, until)
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


def history(persist=True):
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
    ledger = list(rows(ledger_path()))
    records.extend(ledger_records(ledger))
    samples, codex_rows = [], []
    with lock('samples') if persist else nullcontext():
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
        samples.extend(ledger_samples(ledger))
        # Rebuild generated samples so upgrades also repair previously persisted
        # bad denominators. Preserve manually supplied/provider-specific samples.
        existing = {r.get('id'): r for r in rows(budget_dir() / 'samples.jsonl')
                    if r.get('source') not in ('quota_snapshots', 'rate_limits', 'ledger')}
        existing.update({r['id']: r for r in samples})
        combined = list(existing.values())
        if persist:
            path = budget_dir() / 'samples.jsonl'
            temporary = path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as handle:
                for sample in combined:
                    handle.write(json.dumps(sample) + '\n')
            os.replace(temporary, path)
            write_json(budget_dir() / 'history-cache.json', cache)
    return records, combined


def ledger_path():
    return budget_dir() / 'ledger.jsonl'


def actual_total(actual):
    """New tokens: what the model processed, with cache reads tracked apart."""
    return sum(max(0, (actual or {}).get(k, 0) or 0) for k in ('input', 'cache_write', 'output'))


def ledger_records(ledger):
    """Completed, measured rows as cost records. Failed or cut-short runs say nothing about a full job."""
    result = []
    for row in ledger:
        total = actual_total(row.get('actual'))
        if row.get('outcome') == 'ok' and total > 0 and row.get('agent') in PROVIDERS:
            result.append(dict(provider=row['agent'], role=row.get('role'), kind=row.get('kind') or row.get('role'),
                               total=total, duration=row.get('duration_s'), source='ledger'))
    return result


def ledger_samples(ledger):
    """Pair a settled row's tokens with the window % before and after, when both are fresh readings."""
    result = []
    for row in ledger:
        before, after = row.get('window_before') or {}, row.get('window_after') or {}
        window = next((w for w in ('five_hour', 'auto') if number(before.get(w)) and number(after.get(w))), None)
        total = actual_total(row.get('actual'))
        fresh = all(number(row.get(seen)) and number(row.get(edge)) and abs(row[seen] - row[edge]) <= MAX_AGE
                    for seen, edge in (('observed_before', 'started_at'), ('observed_after', 'at')))
        if (window and fresh and total > 0 and after[window] > before[window] and row.get('agent') in PROVIDERS
                and row.get('lease_id')):
            result.append(dict(id='ledger:' + row['lease_id'], provider=row['agent'], tokens=total,
                               pct=after[window] - before[window], source='ledger'))
    return result


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
            mine = [r for r in records if r.get('provider') == provider and role_name(r.get('role')) == role]
            settled = [r for r in mine if r.get('source') == 'ledger']
            # Per bucket, never pooled: enough settled rows replace the older sources outright.
            if len(settled) >= LEDGER_MIN_ROWS:
                matched, source = settled, 'ledger'
            else:
                matched = [r for r in mine if r.get('source') != 'ledger']
                source = 'history'
                if not matched:
                    matched, source = settled, 'ledger' if settled else 'seed'
            tokens = pctl([token_total(r) for r in matched]) or SEED_TOKENS[provider][role]
            duration = pctl([r.get('duration', (r.get('duration_ms') or 0) / 1000) for r in matched], .9)
            kinds = {}
            for kind in {r.get('kind') for r in settled} - {None}:
                of_kind = [r for r in settled if r.get('kind') == kind]
                if len(of_kind) >= LEDGER_MIN_ROWS:
                    kinds[kind] = {'tokens_p80': pctl([token_total(r) for r in of_kind]), 'observations': len(of_kind)}
            roles[role] = {'tokens_p80': tokens, 'pct': max(.1, tokens * factor),
                           'ttl': max(60, duration * 1.5) if duration else 1800,
                           'observations': len(matched), 'source': source, 'kinds': kinds}
        result[provider] = {'factor': factor, 'factor_source': 'calibrated' if calibrated else 'seed',
                            'factor_samples': len(ratios), 'roles': roles}
    return result


def cached_estimates(read_only=False):
    """Share models across short-lived hooks; scan history at most every 10m."""
    with lock('estimates') if not read_only else nullcontext():
        path = budget_dir() / 'estimates.json'
        cached = read_json(path)
        if (isinstance(cached, dict) and cached.get('version') == ESTIMATE_VERSION
                and number(cached.get('generated_at'))
                and 0 <= time.time() - cached['generated_at'] < ESTIMATE_MAX_AGE
                and isinstance(cached.get('models'), dict)):
            return cached['models']
        models = estimates(*history(persist=not read_only))
        if not read_only:
            write_json(path, dict(version=ESTIMATE_VERSION, generated_at=time.time(), models=models))
        return models


def window_live(window, fresh, now):
    if not fresh or not number(window.get('used_pct')):
        return None
    normalized = dict(window, reset_epoch=epoch(window.get('reset_epoch')))
    return usage._zero_if_reset(normalized, now)['used_pct']


def readings(provider, root=None, probe=True):
    now = time.time()
    if provider == 'claude':
        if probe:
            q = claude_quota.quota()
        else:
            q = read_json(Path.home() / '.claude/state/quota-snapshot-cache.json')
            q['age_seconds'] = now - q.get('observed_at', 0)
            if q.get('source') != 'oauth_usage':
                q['windows'] = {}
        fresh = number(q.get('age_seconds')) and q['age_seconds'] <= MAX_AGE
        return {'source': q.get('source'), 'observed_at': q.get('observed_at'), 'windows': {
            name: {'live': value.get('used_percentage') if fresh else None, 'cap': cap}
            for name, cap in [('five_hour', usage.MAX_FIVE_HOUR_PCT), ('seven_day', usage.MAX_SEVEN_DAY_PCT)]
            for value in [q.get('windows', {}).get(name, {})]}}
    state = usage.load(Path(root) if root else claude_home() / 'automations/advance-roadmap')
    prov = state['providers'][provider]
    observed = (prov.get('observed_epoch') or epoch(prov.get('observed_at'))) if provider == 'codex' else epoch(prov.get('included', {}).get('observed_at'))
    if probe and (not observed or now - observed > MAX_AGE):
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


def reap(persist=True):
    """Caller holds leases.lock, including reads (which persist the reaping).

    A reaped reservation that carries ledger metadata is archived for REAPED_GRACE,
    so a late `settle` still writes its one row instead of losing it.
    """
    leases = read_json(budget_dir() / 'leases.json', [])
    now = time.time()
    identities = {}
    active, dropped = [], []
    for lease in leases:
        pid = lease.get('pid')
        if lease.get('label') == 'session' or not lease.get('pid_started_at'):
            continue
        if not alive(pid):
            dropped.append((lease, 'owner exited'))
        elif max(lease.get('created', 0), lease.get('updated', 0)) + lease.get('ttl', 0) <= now:
            dropped.append((lease, 'ttl expired'))
        else:
            if pid not in identities:
                identities[pid] = process_start(pid)
            if identities[pid] == lease['pid_started_at']:
                active.append(lease)
            else:
                dropped.append((lease, 'owner pid reused'))
    leases = active
    if persist:
        write_json(budget_dir() / 'leases.json', leases)
        saved = read_json(budget_dir() / 'reaped.json', [])
        archive = [e for e in saved if e.get('reaped_at', 0) + REAPED_GRACE > now]
        archive += [dict(lease=l, reaped_at=now, reason=why) for l, why in dropped if 'reserved_tokens' in l]
        if archive != saved:
            write_json(budget_dir() / 'reaped.json', archive)
    return leases


def capacity(reading, leases, provider, role, cost, want, unknown_limit=True):
    active = [l for l in leases if l['provider'] == provider]
    reserved = sum(l['pct'] for l in active)
    windows = reading['windows']
    if provider == 'cursor':
        model = os.environ.get(usage.CURSOR_MODEL_ENV.get(role, usage.CURSOR_MODEL_ENV['worker']), 'auto')
        windows = {k: v for k, v in windows.items() if k == ('auto' if model == 'auto' else 'api')}
    known = [w['cap'] - w['live'] - reserved for w in windows.values() if number(w.get('live'))]
    unknown = not windows or any(not number(w.get('live')) for w in windows.values())
    fit = min(want, max(0, math.floor((min(known) + 1e-9) / cost))) if known else want
    if unknown and unknown_limit:
        fit = min(fit, 0 if active else 1)
    return max(0, fit)


def allow(provider, role, want=1):
    model = cached_estimates()[provider]
    reading = readings(provider)
    with lock():
        leases = reap()
        count = capacity(reading, leases, provider, role, model['roles'][role]['pct'], want)
    return count


def resolve_cost(provider, role, tokens=None, kind=None, models=None):
    """(reserved new tokens, derived % per window, ttl) for one reservation."""
    models = cached_estimates() if models is None else models
    cost = models[provider]['roles'][role]
    if tokens is None:
        tokens = (cost.get('kinds', {}).get(kind) or {}).get('tokens_p80') or cost['tokens_p80']
    return tokens, tokens_to_pct(provider, tokens, models), cost['ttl']


def tokens_to_pct(provider, tokens, models=None):
    models = cached_estimates() if models is None else models
    return max(.1, tokens * models[provider]['factor'])


def lease_tokens(lease, models=None):
    """Reserved tokens; a pct-only lease from before the ledger converts at the provider's factor."""
    if number(lease.get('reserved_tokens')):
        return lease['reserved_tokens']
    provider = lease.get('agent') or lease['provider']
    models = cached_estimates() if models is None else models
    return lease.get('pct', 0) / models[provider]['factor']


def window_snapshot(reading):
    windows = {k: v.get('live') for k, v in (reading or {}).get('windows', {}).items()}
    return windows, (reading or {}).get('observed_at')


def waiters_path():
    return budget_dir() / 'waiters.json'


def reap_waiters():
    """Waiting entries whose process is still the one that queued. Caller holds leases.lock."""
    now = time.time()
    kept = []
    for waiter in read_json(waiters_path(), []):
        if (now - waiter.get('arrived', 0) < WAITER_MAX_AGE and alive(waiter.get('pid'))
                and process_start(waiter['pid']) == waiter.get('pid_started_at')):
            kept.append(waiter)
    return kept


def new_waiter(provider, role, tokens, pct, task=None):
    pid = os.getpid()
    return dict(id=uuid.uuid4().hex, provider=provider, role=role, tokens=tokens, pct=pct, task=task,
                pid=pid, pid_started_at=process_start(pid), arrived=time.time(), bypassed=0)


def dequeue(waiter_id):
    with lock():
        queue = reap_waiters()
        write_json(waiters_path(), [w for w in queue if w['id'] != waiter_id])


def queue_may_admit(queue, me, leases, reading, provider):
    """FIFO with bounded bypass. Caller holds leases.lock and checks its own fit afterwards.

    An earlier waiter that fits goes first. A later one may overtake a head that does
    not fit, up to MAX_BYPASS times; after that the head is protected, but only while
    releases could actually admit it. A head blocked by live usage, not by other
    reservations, protects nothing, so it never starves jobs that fit around it.
    """
    mine = [w for w in queue if w['provider'] == provider]
    ahead = mine[:[w['id'] for w in mine].index(me['id'])]
    for w in ahead:
        if capacity(reading, leases, provider, w['role'], w['pct'], 1):
            return False
    if ahead:
        head = ahead[0]
        if (head.get('bypassed', 0) >= MAX_BYPASS
                and capacity(reading, [], provider, head['role'], head['pct'], 1)):
            return False
    return True


def acquire(provider, role, pid=None, ttl=None, label=None, reading=None, unknown_limit=True,
            tokens=None, kind=None, waiter=None, force=False, models=None, **extra):
    """Reserve `tokens` (default: the role's p80) or return None when they do not fit.

    `waiter` makes admission queue-aware (FIFO, bounded bypass). Without it the call
    ignores the queue: batch bundles and the subagent hook do, until G2 routes them.
    `force` records the reservation even when short (interactive sessions never block)
    and marks it `over_capacity`.
    """
    models = cached_estimates() if models is None else models
    tokens, pct, default_ttl = resolve_cost(provider, role, tokens, kind, models)
    reading = readings(provider) if reading is None else reading
    with lock():
        leases = reap()
        fits = capacity(reading, leases, provider, role, pct, 1, unknown_limit=unknown_limit)
        if waiter:
            queue = reap_waiters()
            if waiter['id'] not in [w['id'] for w in queue]:
                queue.append(waiter)
            fits = fits and queue_may_admit(queue, waiter, leases, reading, provider)
        if not fits and not force:
            if waiter:
                write_json(waiters_path(), queue)
            return None
        owner = pid or os.getpid()
        started = process_start(owner)
        if started is None:
            raise ProcessLookupError('lease owner exited')
        before, observed = window_snapshot(reading)
        now = time.time()
        lease = dict(id=uuid.uuid4().hex, provider=provider, agent=provider, role=role, kind=kind or role,
                     pct=pct, reserved_tokens=tokens, initial_tokens=tokens,
                     window_pct={w: pct for w in WINDOWS[provider]},
                     pid=owner, pid_started_at=started, created=now, updated=now, ttl=ttl or default_ttl,
                     label=label, session_ids=[], window_before=before, observed_before=observed,
                     over_capacity=not fits)
        lease.update(extra)
        lease.setdefault('interactive', False)
        leases.append(lease)
        write_json(budget_dir() / 'leases.json', leases)
        if waiter:
            head = next((w for w in queue if w['provider'] == provider), None)
            if head and head['id'] != waiter['id']:
                head['bypassed'] = head.get('bypassed', 0) + 1
            write_json(waiters_path(), [w for w in queue if w['id'] != waiter['id']])
        return lease


def find_lease(leases, lease_id):
    return next((l for l in leases if l['id'] == lease_id), None)


def get_lease(lease_id):
    with lock():
        return find_lease(reap(), lease_id)


def update_lease(lease_id, tokens=None, add_tokens=None, session_ids=(), force=False, models=None, meta=None):
    """Revise a reservation. Returns (status, lease): ok | would_exceed | over_run | missing.

    An increase that does not fit is refused unless `force`, which applies it anyway and
    marks `over_run`: the tokens are being spent regardless, and other admitters must see
    them. Nothing here ever signals the job. Session ids, metadata and the heartbeat
    that keeps the lease from expiring are applied either way.
    """
    peek = get_lease(lease_id)
    if not peek:
        return 'missing', None
    provider = peek.get('agent') or peek['provider']
    wants_tokens = tokens is not None or add_tokens is not None
    if wants_tokens and models is None:
        models = cached_estimates()
    reading = readings(provider) if wants_tokens else None
    with lock():
        leases = reap()
        lease = find_lease(leases, lease_id)
        if not lease:
            return 'missing', None
        status = 'ok'
        if wants_tokens:
            current = lease_tokens(lease, models)
            new = tokens if tokens is not None else current + add_tokens
            if new < 0:
                raise ValueError('reservation cannot be negative')
            pct = tokens_to_pct(provider, new, models)
            if pct > lease.get('pct', 0) + 1e-9:
                others = [l for l in leases if l['id'] != lease_id]
                if not capacity(reading, others, provider, lease['role'], pct, 1, unknown_limit=False):
                    status = 'over_run' if force else 'would_exceed'
            if status != 'would_exceed':
                lease.update(reserved_tokens=new, pct=pct, window_pct={w: pct for w in WINDOWS[provider]})
                if status == 'over_run':
                    lease['over_run'] = True
        for sid in session_ids:
            if sid and sid not in lease.setdefault('session_ids', []):
                lease['session_ids'].append(sid)
        for key, value in (meta or {}).items():
            if value is not None and not lease.get(key):
                lease[key] = value
        lease['updated'] = time.time()
        write_json(budget_dir() / 'leases.json', leases)
        return status, dict(lease)


def parent_lease(agent, environ=None):
    """The live lease a nested run should adopt, or None. Only a lease for the same agent:
    a Cursor job's Codex review spends Codex's windows, so it takes its own lease."""
    environ = os.environ if environ is None else environ
    ids = [pair.partition('=')[2] or pair for pair in environ.get('AGENT_BUDGET_LEASES', '').split(',') if pair]
    if environ.get('AGENT_BUDGET_LEASE_ID'):
        ids.insert(0, environ['AGENT_BUDGET_LEASE_ID'])
    with lock():
        leases = reap()
    return next((l for i in ids for l in [find_lease(leases, i)] if l and (l.get('agent') or l['provider']) == agent), None)


def adopt(lease, tokens=None, session_ids=(), meta=None, models=None):
    """Fold a nested run into its parent's lease: raise the estimate (never shrink it), add
    session ids, fill metadata the parent left empty. The owner alone settles."""
    if tokens is not None and tokens > lease_tokens(lease, models):
        update_lease(lease['id'], tokens=tokens, session_ids=session_ids, force=True, meta=meta, models=models)
    else:
        update_lease(lease['id'], session_ids=session_ids, meta=meta, models=models)
    return get_lease(lease['id']) or lease


def child_lease_env(lease, environ=None):
    environ = os.environ if environ is None else environ
    pairs = [p for p in environ.get('AGENT_BUDGET_LEASES', '').split(',') if p]
    agent = lease.get('agent') or lease['provider']
    pairs = [p for p in pairs if not p.startswith(agent + '=')] + [f'{agent}={lease["id"]}']
    return {'AGENT_BUDGET_LEASE_ID': lease['id'], 'AGENT_BUDGET_LEASES': ','.join(pairs),
            'AGENT_BUDGET_SESSION_FILE': str(session_file(lease['id']))}


def session_file(lease_id):
    return budget_dir() / 'sessions' / lease_id


def read_session_file(path):
    try:
        return [l.strip() for l in Path(path).read_text().split() if l.strip()]
    except OSError:
        return []


def reserve_bundle(requests, *, replace_ids=(), reading_by_provider=None, models=None,
                   label=None, pid=None, ttl=None, commit=True, extra_leases=(), reduce_only=False):
    """Atomically admit/replace a whole job, or leave its existing leases intact.

    One lease per provider includes all roles of this job. Unknown quota still
    allows only one job/provider, including a job with worker+review on Cursor.
    `commit=False` is a read-only forecast; callers may include previous forecast
    leases to simulate a wave. It never creates budget files or takes a lock.
    """
    models = cached_estimates(read_only=not commit) if models is None else models
    providers = {r['provider'] for r in requests}
    reading_by_provider = ({p: readings(p) for p in providers}
                           if reading_by_provider is None else reading_by_provider)
    grouped = {}
    for request in requests:
        provider, role = request['provider'], request['role']
        scale = request.get('scale', 1)
        if not number(scale) or scale < 0:
            raise ValueError('reservation scale must be nonnegative')
        if not scale:
            continue
        cost = models[provider]['roles'][role]
        group = grouped.setdefault(provider, dict(pct=0, ttl=0, roles=[]))
        group['pct'] += cost['pct'] * scale
        group['ttl'] += cost['ttl']
        group['roles'].append(role)
    with lock() if commit else nullcontext():
        existing = reap(persist=commit) + list(extra_leases)
        remaining = [l for l in existing if l['id'] not in replace_ids]
        for provider, group in grouped.items():
            prior = sum(l['pct'] for l in existing if l['id'] in replace_ids and l['provider'] == provider)
            if reduce_only:
                if group['pct'] > prior + 1e-9:
                    raise ValueError('a reduction cannot add reserved cost')
                continue
            if not all(capacity(reading_by_provider[provider], remaining, provider,
                                role, group['pct'], 1) for role in group['roles']):
                return None
        owner = pid or os.getpid()
        started = process_start(owner)
        if started is None:
            raise ProcessLookupError('lease owner exited')
        admitted = [dict(id=uuid.uuid4().hex, provider=p, role=g['roles'][0],
                         roles=g['roles'], pct=g['pct'], pid=owner, pid_started_at=started,
                         created=time.time(), ttl=ttl or g['ttl'], label=label)
                    for p, g in grouped.items()]
        if commit:
            write_json(budget_dir() / 'leases.json', remaining + admitted)
        return admitted


def transfer(lease_ids, pid):
    started = process_start(pid)
    if started is None:
        raise ProcessLookupError('lease owner exited')
    with lock():
        leases = reap()
        for lease in leases:
            if lease['id'] in lease_ids:
                lease.update(pid=pid, pid_started_at=started)
        write_json(budget_dir() / 'leases.json', leases)


def release_many(lease_ids):
    with lock():
        leases = reap()
        write_json(budget_dir() / 'leases.json', [l for l in leases if l['id'] not in lease_ids])


def release(lease_id):
    with lock():
        leases = reap()
        write_json(budget_dir() / 'leases.json', [l for l in leases if l['id'] != lease_id])


_tokens_module = None
_usage_memo = {}
SHELLS = ('sh', 'bash', 'zsh', 'dash')


def tokens_lib():
    """automations/advance-roadmap/lib/tokens.py: the roadmap's result-JSON and Codex rollout parsers."""
    global _tokens_module
    if _tokens_module is None:
        spec = importlib.util.spec_from_file_location('agent_budget_tokens', ROOT / 'automations/advance-roadmap/lib/tokens.py')
        _tokens_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tokens_module)
    # tokens.py reads this once at import; callers (and tests) set the env later.
    _tokens_module.CODEX_SESSIONS = codex_sessions_dir()
    return _tokens_module


def codex_sessions_dir():
    return os.environ.get('ADVANCE_ROADMAP_CODEX_SESSIONS', str(Path.home() / '.codex/sessions'))


def blank_usage():
    return dict.fromkeys(USAGE_FIELDS, 0)


def whole(value):
    return int(value) if number(value) and value > 0 else 0


def usage_from_obj(obj):
    """{input, cache_write, cache_read, output} from a tokens.py record, a Claude/Cursor result
    JSON, a Codex usage event, or an explicit actual; None when it holds no usage."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get('actual'), dict):
        obj = obj['actual']
    if any(k in obj for k in USAGE_FIELDS):
        return {k: whole(obj.get(k)) for k in USAGE_FIELDS}
    if isinstance(obj.get('modelUsage'), dict) and obj['modelUsage']:
        total = blank_usage()
        for entry in obj['modelUsage'].values():
            for field, key in zip(USAGE_FIELDS, ('inputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens', 'outputTokens')):
                total[field] += whole((entry or {}).get(key))
        return total
    u = obj.get('usage') if isinstance(obj.get('usage'), dict) else obj
    pick = lambda *names: next((whole(u[n]) for n in names if n in u), 0)
    cached = pick('cached_input_tokens')
    found = {'input': max(0, pick('input_tokens', 'inputTokens') - cached),
             'cache_write': pick('cache_creation_input_tokens', 'cache_write_input_tokens', 'cacheWriteTokens'),
             'cache_read': pick('cache_read_input_tokens', 'cached_input_tokens', 'cacheReadTokens'),
             'output': pick('output_tokens', 'outputTokens')}
    return found if any(found.values()) else None


def add_usage(total, part):
    for field in USAGE_FIELDS:
        total[field] += part.get(field, 0)


def claude_session_usage(session_id):
    projects = claude_home() / 'projects'
    files = list(projects.glob(f'*/{session_id}.jsonl')) + list(projects.glob(f'*/{session_id}/subagents/agent-*.jsonl'))
    if not files:
        return None
    total = blank_usage()
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        stamp = (stat.st_mtime_ns, stat.st_size)
        if _usage_memo.get(path, (None,))[0] != stamp:
            messages, _ = transcript_usage(path)
            part = blank_usage()
            for m in messages.values():
                part['input'] += m.get('input_tokens', 0)
                part['cache_write'] += m.get('cache_creation_input_tokens', 0)
                part['cache_read'] += m.get('cache_read_input_tokens', 0)
                part['output'] += m.get('output_tokens', 0)
            _usage_memo[path] = (stamp, part)
        add_usage(total, _usage_memo[path][1])
    return total


def codex_session_usage(session_id):
    last = tokens_lib().codex_rollout_usage(session_id)
    if not last:
        return None
    cached = whole(last.get('cached_input_tokens'))
    return {'input': max(0, whole(last.get('input_tokens')) - cached), 'cache_write': whole(last.get('cache_write_input_tokens')),
            'cache_read': cached, 'output': whole(last.get('output_tokens'))}


def result_usage(agent, path):
    """(usage, session id) from a file holding the child's stdout: Claude/Cursor result JSON,
    Codex --json events, or Codex's plain header and closing total."""
    import re
    if agent in ('claude', 'cursor'):
        data, _ = tokens_lib().load_result(str(path))
        return (usage_from_obj(data), data.get('session_id')) if data else (None, None)
    try:
        text = Path(path).read_text(errors='replace')
    except OSError:
        return None, None
    header = re.search(r'^session id: ([0-9a-f-]{36})\s*$', text, re.M)
    session_id = header.group(1) if header else None
    total, seen = blank_usage(), False
    for row in rows(Path(path)):
        session_id = session_id or row.get('thread_id')
        if row.get('type') == 'turn.completed' and usage_from_obj(row.get('usage')):
            add_usage(total, usage_from_obj(row['usage']))
            seen = True
    if not seen:
        closing = re.findall(r'^tokens used\s*\n\s*([\d,]+)\s*$', text, re.M)
        if closing:
            total['output'], seen = int(closing[-1].replace(',', '')), True
    return (total if seen else None), session_id


def usage_for_sessions(agent, session_ids, result_file=None):
    """(usage, source, extra session ids). Transcripts and rollouts are the truth; a result
    file counts only when it names a session they did not already cover."""
    part, result_sid = result_usage(agent, result_file) if result_file else (None, None)
    total, covered = blank_usage(), set()
    by_session = {'claude': claude_session_usage, 'codex': codex_session_usage}.get(agent, lambda _sid: None)
    for sid in dict.fromkeys([*session_ids, *([result_sid] if result_sid else [])]):
        found = by_session(sid)
        if found:
            add_usage(total, found)
            covered.add(sid)
    sources = ['sessions'] if covered else []
    if part and result_sid not in covered:
        add_usage(total, part)
        sources.append('result')
    return total, '+'.join(sources) or 'none', [result_sid] if result_sid else []


def discover_sessions(agent, cwd, since, exclude=()):
    """Last resort: the one transcript or rollout the child started in its cwd. Ambiguity yields nothing."""
    import re
    found = []
    if agent == 'claude':
        for path in (claude_home() / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', cwd)).glob('*.jsonl'):
            first = next((epoch(r.get('timestamp')) for r in rows(path) if r.get('timestamp')), None)
            if first is not None and first >= since - 1 and path.stem not in exclude:
                found.append(path.stem)
    elif agent == 'codex':
        for path in Path(codex_sessions_dir()).glob('*/*/*/rollout-*.jsonl'):
            try:
                if path.stat().st_mtime < since - 1:
                    continue
            except OSError:
                continue
            meta = next(rows(path), {}).get('payload') or {}
            if meta.get('cwd') == cwd and (epoch(meta.get('timestamp')) or 0) >= since - 1 and meta.get('id') not in exclude:
                found.append(meta['id'])
    return found if len(found) == 1 else []


def owner_pid():
    """The long-lived process a CLI-made reservation belongs to: the caller, past transient shells."""
    pid = os.getppid()
    for _ in range(8):
        result = subprocess.run(['ps', '-o', 'ppid=,comm=', '-p', str(pid)], capture_output=True, text=True, timeout=2)
        parts = result.stdout.strip().split(None, 1)
        if len(parts) != 2 or Path(parts[1]).name not in SHELLS:
            break
        pid = int(parts[0])
    return pid


def ledger_row(lease_id):
    return next((r for r in rows(ledger_path()) if r.get('lease_id') == lease_id), None)


def drop_lease(lease_id):
    """Caller holds leases.lock."""
    write_json(budget_dir() / 'leases.json', [l for l in read_json(budget_dir() / 'leases.json', []) if l['id'] != lease_id])
    archive = read_json(budget_dir() / 'reaped.json', [])
    if any(e['lease'].get('id') == lease_id for e in archive):
        write_json(budget_dir() / 'reaped.json', [e for e in archive if e['lease'].get('id') != lease_id])


def settle(lease_id, actual=None, session_ids=(), outcome='ok', result_file=None, lease_hint=None,
           discover_in=None, models=None):
    """Release a reservation and append its ONE completed row to ledger.jsonl.

    Returns (status, row): settled | already_settled | missing. Idempotent on the ledger, not on
    the lease, so a lease reaped meanwhile (archived) or lost still yields exactly one row. The
    row append precedes the lease removal under one flock; a crash between them leaves a lease
    that the next settle removes without a second row. Actual tokens come from `actual`, else
    from every session id on the lease (transcripts/rollouts), else from `result_file`.
    """
    if outcome not in OUTCOMES:
        raise ValueError('outcome must be one of ' + ', '.join(OUTCOMES))
    with lock():
        leases = reap()
        row = ledger_row(lease_id)
        if row:
            drop_lease(lease_id)
            return 'already_settled', row
        archived = next((e['lease'] for e in read_json(budget_dir() / 'reaped.json', []) if e['lease'].get('id') == lease_id), None)
        lease = find_lease(leases, lease_id) or archived or lease_hint
        if not lease:
            return 'missing', None
        lease = dict(lease)
    agent = lease.get('agent') or lease['provider']
    sids = list(dict.fromkeys([*lease.get('session_ids', []), *session_ids, *read_session_file(session_file(lease_id))]))
    source = 'explicit'
    if actual is None:
        actual, source, found = usage_for_sessions(agent, sids, result_file)
        sids = list(dict.fromkeys(sids + found))
        if source == 'none' and discover_in:
            guess = discover_sessions(agent, discover_in, lease.get('created', time.time()), exclude=other_session_ids(lease_id))
            actual, source, _ = usage_for_sessions(agent, guess)
            sids = list(dict.fromkeys(sids + guess))
            source = 'discovered' if source != 'none' else 'none'
    else:
        actual = {k: whole(actual.get(k)) for k in USAGE_FIELDS}
    try:
        after, observed_after = window_snapshot(readings(agent) if agent == 'claude' else readings(agent, probe=False))
    except Exception:
        after, observed_after = None, None
    if 'reserved_tokens' not in lease and models is None:
        models = cached_estimates()
    with lock():
        leases = reap()
        row = ledger_row(lease_id)
        if row:
            drop_lease(lease_id)
            return 'already_settled', row
        lease = find_lease(leases, lease_id) or lease
        now = time.time()
        total, reserved = actual_total(actual), round(lease_tokens(lease, models))
        row = dict(lease_id=lease_id, at=now, started_at=lease.get('created'), agent=agent, model=lease.get('model'),
                   role=lease.get('role'), kind=lease.get('kind'), task=lease.get('task') or lease.get('label'),
                   linear_id=lease.get('linear_id'), repo=lease.get('repo'),
                   session_ids=list(dict.fromkeys(sids + lease.get('session_ids', []))),
                   parent_lease_id=lease.get('parent_lease_id'), interactive=bool(lease.get('interactive')),
                   reserved_tokens=reserved, initial_tokens=round(lease.get('initial_tokens', reserved)),
                   actual=actual, actual_total=total, actual_source=source,
                   window_before=lease.get('window_before'), window_after=after,
                   observed_before=lease.get('observed_before'), observed_after=observed_after,
                   duration_s=round(now - lease.get('created', now), 1), outcome=outcome,
                   over_run=total > reserved or bool(lease.get('over_run')))
        fd = os.open(ledger_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as handle:
            handle.write(json.dumps(row) + '\n')
        drop_lease(lease_id)
        return 'settled', row


def other_session_ids(lease_id):
    with lock():
        return {s for l in reap() if l['id'] != lease_id for s in l.get('session_ids', [])}


def child_env():
    # Keep workspace/account attribution for leaderboard binding, but remove the
    # parent agent identity and all hook endpoints. Never print environment values.
    return {k: v for k, v in os.environ.items() if not (
        k.startswith(('SUPERSET_AGENT_', 'SUPERSET_HOOK_', 'SUPERSET_HOST_AGENT_')) or k in (
        'SUPERSET_TERMINAL_ID', 'SUPERSET_TAB_ID', 'SUPERSET_PANE_ID', 'SUPERSET_PORT', 'SUPERSET_DEBUG_HOOKS',
        'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'))}


def pause(seconds, received):
    # Short sleeps make cancellation prompt even during long backoffs.
    until = time.monotonic() + seconds
    while not received and time.monotonic() < until:
        time.sleep(min(.2, max(0, until - time.monotonic())))


def admit(provider, role, wait=False, interactive=False, received=None, **kw):
    """The lease, or None (no headroom without --wait, or cancelled while waiting).

    Interactive reservations record and return at once, never queueing. Waiters queue in
    arrival order and re-poll with backoff up to 30s, using no model while they wait.
    """
    received = [] if received is None else received
    if interactive:
        return acquire(provider, role, force=True, interactive=True, **kw)
    waiter = None
    if wait:
        tokens, pct, _ = resolve_cost(provider, role, kw.get('tokens'), kw.get('kind'))
        waiter = new_waiter(provider, role, tokens, pct, kw.get('task'))
    try:
        backoff = 1
        while not received:
            lease = acquire(provider, role, waiter=waiter, **kw)
            if lease or not wait:
                return lease
            print(f'agent-budget: {provider}/{role} queued; waiting {backoff}s', file=sys.stderr)
            pause(backoff, received)
            backoff = min(30, backoff * 2)
        return None
    finally:
        if waiter:
            dequeue(waiter['id'])


def watch_tick(lease_id, session_out=None, result_file=None):
    """Heartbeat the lease and, past WATCH_FRACTION of the reservation, extend it. Never touches the child.

    An extension that does not fit is applied anyway and marked over_run: the tokens are being
    spent, and other admitters must stop counting headroom that is gone.
    """
    lease = get_lease(lease_id)
    if not lease:
        return
    agent = lease.get('agent') or lease['provider']
    known = [*lease.get('session_ids', []), *read_session_file(session_file(lease_id)), *read_session_file(session_out or '')]
    usage, _, found = usage_for_sessions(agent, known, result_file)
    sessions = [sid for sid in dict.fromkeys(known + found) if sid not in lease.get('session_ids', [])]
    total, reserved = actual_total(usage), lease_tokens(lease)
    if total <= WATCH_FRACTION * reserved:
        update_lease(lease_id, session_ids=sessions)
        return
    target = max(math.ceil(reserved * 1.5), math.ceil(total * 1.25))
    status, _ = update_lease(lease_id, tokens=target, session_ids=sessions)
    if status == 'would_exceed':
        update_lease(lease_id, tokens=target, session_ids=sessions, force=True)
        print(f'agent-budget: {agent} job used {total} of {round(reserved)} reserved tokens and the '
              f'extension to {target} does not fit; recorded as an over-run, job left running', file=sys.stderr)


def run(provider, role, command, wait=False, label=None, *, tokens=None, model=None, task=None, ticket=None,
        repo=None, kind=None, interactive=False, session_out=None, result_file=None):
    """reserve -> exec -> watch -> settle.

    A nested run (AGENT_BUDGET_LEASE_ID names a live lease of the same agent) adopts it
    instead of reserving again, and leaves settling to the owner. The command's stdout and
    stdin are untouched: session ids come from --session-out, AGENT_BUDGET_SESSION_FILE or
    --result-file, never a pipe.
    """
    child, lease, adopted, spawned = None, None, False, False
    received, previous = [], {}
    started = time.time()
    meta = dict(model=model, task=task or label or Path(command[0]).name, linear_id=ticket, repo=repo)
    interval = float(os.environ.get('AGENT_BUDGET_WATCH_INTERVAL', WATCH_INTERVAL))

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
        parent = parent_lease(provider)
        if parent:
            lease, adopted = adopt(parent, tokens, meta=meta), True
        else:
            other = os.environ.get('AGENT_BUDGET_LEASE_ID')
            lease = admit(provider, role, wait=wait, interactive=interactive, received=received, label=label,
                          tokens=tokens, kind=kind, parent_lease_id=other, **meta)
        if received:
            return 128 + received[-1]
        if not lease:
            print('agent-budget: no headroom; use bin/fanout to queue a terminal', file=sys.stderr)
            return 3
        if interactive and lease.get('over_capacity'):
            print(f'agent-budget: {provider} headroom is short for this session; reserved anyway', file=sys.stderr)
        session_file(lease['id']).parent.mkdir(parents=True, exist_ok=True)
        child = subprocess.Popen(command, env=dict(child_env(), **child_lease_env(lease)), start_new_session=True)
        spawned = True
        if received:
            forward(received[-1], None)
        next_tick = time.monotonic() + interval
        while True:
            try:
                code = child.wait(timeout=min(.2, interval))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= next_tick:
                    next_tick = time.monotonic() + interval
                    try:
                        watch_tick(lease['id'], session_out, result_file)
                    except Exception as error:  # the watch must never hurt the job
                        print(f'agent-budget: watch skipped ({type(error).__name__})', file=sys.stderr)
        return code if code >= 0 else 128 - code
    except OSError as error:
        print(f'agent-budget: unable to start command: {error.strerror}', file=sys.stderr)
        return 127
    finally:
        if lease:
            finish(lease, adopted, spawned, received, child, started, session_out, result_file)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def finish(lease, adopted, spawned, received, child, started, session_out, result_file):
    sessions = read_session_file(session_out or '')
    try:
        if adopted:
            _, _, found = usage_for_sessions(lease.get('agent') or lease['provider'], sessions, result_file)
            update_lease(lease['id'], session_ids=[*sessions, *found])
        elif not spawned:
            release(lease['id'])  # nothing ran, so there is no completed output to log
        else:
            code = child.returncode
            outcome = 'cancelled' if received else 'ok' if code == 0 else 'error'
            settle(lease['id'], session_ids=sessions, outcome=outcome, result_file=result_file,
                   lease_hint=lease, discover_in=os.getcwd())
    except Exception as error:
        print(f'agent-budget: settle failed ({type(error).__name__}); releasing without a ledger row', file=sys.stderr)
        if not adopted:
            release(lease['id'])


def view(provider, reading, model, leases, waiters=()):
    mine = [l for l in leases if l['provider'] == provider]
    reserved = sum(l['pct'] for l in mine)
    now = time.time()
    return dict(reading, reserved_pct=reserved, estimates=model,
                reserved_tokens=round(sum(lease_tokens(l, {provider: model}) for l in mine)),
                leases=[dict(id=l['id'], task=l.get('task') or l.get('label'), role=l.get('role'),
                             tokens=round(lease_tokens(l, {provider: model})), pct=l['pct'],
                             interactive=bool(l.get('interactive')), over_run=bool(l.get('over_run')))
                        for l in mine],
                waiting=[dict(task=w.get('task'), role=w['role'], tokens=round(w['tokens']), pct=w['pct'],
                              waited_s=round(now - w['arrived']), bypassed=w.get('bypassed', 0))
                         for w in waiters if w['provider'] == provider],
                windows={k: dict(v, headroom=v['cap'] - v['live'] - reserved if number(v.get('live')) else None)
                         for k, v in reading['windows'].items()})


def add_lease_args(p, required_role):
    p.add_argument('--provider', '--agent', dest='provider', choices=PROVIDERS, required=True)
    p.add_argument('--role', choices=ROLES, required=required_role)
    p.add_argument('--tokens', type=int, help='reserve this many new tokens instead of the role estimate')
    p.add_argument('--model')
    p.add_argument('--ticket', help='Linear ticket, e.g. DEV-7')
    p.add_argument('--repo')
    p.add_argument('--kind', help='task kind for estimates (default: the role)')
    p.add_argument('--interactive', action='store_true', help='never block: record the reservation and warn if short')


def emit(a, value, text):
    print(json.dumps(value) if a.json else text)


def cmd_reserve(a, parser):
    if a.tokens is not None and a.tokens <= 0:
        parser.error('--tokens must be positive')
    meta = dict(model=a.model, task=a.task, linear_id=a.ticket, repo=a.repo)
    parent = parent_lease(a.provider)
    if parent:
        lease = adopt(parent, a.tokens, meta=meta)
        emit(a, dict(lease, adopted=True), lease['id'])
        return 0
    received, previous = [], {}
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, lambda signum, _f: received.append(signum))
    try:
        lease = admit(a.provider, a.role or 'worker', wait=a.wait, interactive=a.interactive, received=received,
                      tokens=a.tokens, kind=a.kind, pid=a.pid or owner_pid(), ttl=a.ttl, parent_lease_id=os.environ.get('AGENT_BUDGET_LEASE_ID'), **meta)
    except Exception as error:
        if not a.interactive:
            raise
        print(f'agent-budget: reservation skipped ({type(error).__name__}); session continues', file=sys.stderr)
        return 0  # an interactive session is never blocked, even by a broken ledger
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if received:
        return 128 + received[-1]
    if not lease:
        print('agent-budget: no headroom; retry with --wait', file=sys.stderr)
        return EXIT_NO_HEADROOM
    if a.interactive and lease.get('over_capacity'):
        print(f'agent-budget: {a.provider} headroom is short for this session; reserved anyway', file=sys.stderr)
    emit(a, lease, lease['id'])
    return 0


def read_actual_file(path):
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise ValueError(f'cannot read {path}: {error.strerror}')
    try:
        data = json.loads(text)
    except ValueError:
        data, _ = tokens_lib().load_result(str(path))
    parts = [usage_from_obj(d) for d in (data if isinstance(data, list) else [data])]
    total = blank_usage()
    for part in parts:
        if part:
            add_usage(total, part)
    if not any(parts):
        raise ValueError(f'{path} holds no token usage')
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true')
    for name in ('allow', 'lease', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--provider', '--agent', dest='provider', choices=PROVIDERS, required=True)
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
            p.add_argument('--tokens', type=int, help='reserve this many new tokens instead of the role estimate')
            p.add_argument('--model')
            p.add_argument('--task')
            p.add_argument('--ticket', help='Linear ticket, e.g. DEV-7')
            p.add_argument('--repo')
            p.add_argument('--kind', help='task kind for estimates (default: the role)')
            p.add_argument('--interactive', action='store_true', help='never block: record the reservation and warn if short')
            p.add_argument('--session-out', help='file the command writes its session id(s) to')
            p.add_argument('--result-file', help='file holding the command\'s stdout, for its result JSON or Codex events')
            p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('reserve', help='reserve tokens in the shared ledger; prints the lease id')
    add_lease_args(p, required_role=False)
    p.add_argument('--task', required=True)
    p.add_argument('--wait', action='store_true')
    p.add_argument('--pid', type=int, help='long-lived owner of the reservation (default: the calling process, past transient shells)')
    p.add_argument('--ttl', type=float)
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('update', help='revise a reservation; exits 4 when an increase does not fit')
    p.add_argument('--lease', required=True)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--tokens', type=int)
    group.add_argument('--add-tokens', type=int)
    p.add_argument('--session-id', action='append', default=[])
    p.add_argument('--force', action='store_true', help='apply an increase that does not fit and record an over-run')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('settle', help='release a reservation and log its one completed ledger row')
    p.add_argument('--lease', required=True)
    p.add_argument('--actual-json', help='file with token counts, a Claude/Cursor result JSON or a Codex usage object')
    for field in ('input', 'cache-write', 'cache-read', 'output'):
        p.add_argument('--' + field, type=int)
    p.add_argument('--session-id', action='append', default=[])
    p.add_argument('--result-file', help='file holding the job\'s stdout, to read usage from when none is given')
    p.add_argument('--outcome', choices=OUTCOMES, default='ok')
    p.add_argument('--json', action='store_true')
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
            waiting = reap_waiters()
            result = {p: view(p, live[p], models[p], leases, waiting) for p in PROVIDERS}
        if a.json:
            print(json.dumps(result))
        else:
            for p, v in result.items():
                print(f'{p}: reserved {v["reserved_pct"]:.2f}% / {v["reserved_tokens"]} tokens ({v["estimates"]["factor_source"]})')
                for w, value in v['windows'].items():
                    print(f'  {w}: live={value["live"]} cap={value["cap"]} headroom={value["headroom"]}')
                print('  job %: ' + ', '.join(f'{r}={c["pct"]:.2f}' for r, c in models[p]['roles'].items()))
                for w in v['waiting']:
                    print(f'  waiting: {w["task"] or w["role"]} needs {w["tokens"]} tokens ({w["pct"]:.2f}%), '
                          f'queued {w["waited_s"]}s, overtaken {w["bypassed"]}x')
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
    elif a.cmd == 'reserve':
        return cmd_reserve(a, parser)
    elif a.cmd == 'update':
        if a.tokens is None and a.add_tokens is None and not a.session_id:
            parser.error('update needs --tokens, --add-tokens or --session-id')
        status_, lease = update_lease(a.lease, a.tokens, a.add_tokens, a.session_id, a.force)
        if status_ == 'missing':
            print(f'agent-budget: no live lease {a.lease}', file=sys.stderr)
            return EXIT_NO_LEASE
        emit(a, dict(lease, status=status_), f'{status_}: {round(lease_tokens(lease))} tokens reserved')
        if status_ == 'would_exceed':
            print('agent-budget: the increase would exceed headroom; the reservation is unchanged and nothing was stopped', file=sys.stderr)
            return EXIT_WOULD_EXCEED
    elif a.cmd == 'settle':
        flags = {k: getattr(a, k) for k in ('input', 'cache_write', 'cache_read', 'output')}
        try:
            if a.actual_json:
                actual = read_actual_file(a.actual_json)
            elif any(v is not None for v in flags.values()):
                actual = {k: v or 0 for k, v in flags.items()}
            else:
                actual = None
        except ValueError as error:
            parser.error(str(error))
        status_, row = settle(a.lease, actual, a.session_id, a.outcome, a.result_file)
        if status_ == 'missing':
            print(f'agent-budget: no lease or ledger row for {a.lease}', file=sys.stderr)
            return EXIT_NO_LEASE
        emit(a, dict(row, status=status_), f'{status_}: {row["actual_total"]} new tokens ({row["outcome"]})')
    else:
        command = a.command[1:] if a.command[:1] == ['--'] else a.command
        if not command:
            parser.error('run requires -- CMD...')
        return run(a.provider, a.role, command, a.wait, a.label, tokens=a.tokens, model=a.model, task=a.task,
                   ticket=a.ticket, repo=a.repo, kind=a.kind, interactive=a.interactive,
                   session_out=a.session_out, result_file=a.result_file)
    return 0
