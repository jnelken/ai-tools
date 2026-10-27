"""Best-effort, account-wide quota snapshots; never invokes a model or refreshes OAuth."""
import argparse
import datetime
import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from contextlib import contextmanager


@contextmanager
def locked(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def state_dir():
    return Path.home() / '.claude' / 'state'


def token():
    # Respect the CLI's explicit authentication override. Never print credentials.
    if os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'):
        return os.environ['CLAUDE_CODE_OAUTH_TOKEN']
    if os.environ.get('ANTHROPIC_API_KEY'):
        return None
    # macOS CLI authentication lives in Keychain; the old file can remain expired.
    if sys.platform == 'darwin' and not os.environ.get('CLAUDE_CONFIG_DIR'):
        try:
            result = subprocess.run(['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                value = json.loads(result.stdout).get('claudeAiOauth', {}).get('accessToken')
                if value:
                    return value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    config = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    try:
        data = json.loads((config / '.credentials.json').read_text()).get('claudeAiOauth', {})
        if data.get('expiresAt', float('inf')) / 1000 > time.time():
            return data.get('accessToken')
    except (OSError, ValueError):
        pass
    return None


def windows(data):
    result = {}
    for name, value in data.items():
        if name.startswith(('five_hour', 'seven_day')) and isinstance(value, dict):
            pct = value.get('utilization', value.get('used_percentage'))
            if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100:
                result[name] = {'used_percentage': pct, 'resets_at': value.get('resets_at', value.get('reset_epoch'))}
    return result


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def probe(credential):
    if not credential:
        return None, 'no_subscription_credentials'
    request = urllib.request.Request('https://api.anthropic.com/api/oauth/usage', headers={
        'Authorization': 'Bearer ' + credential, 'anthropic-beta': 'oauth-2025-04-20',
        'User-Agent': 'ai-tools-quota-snapshot/1',
    })
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = windows(json.load(response))
        return (data, None) if data else (None, 'missing_quota_windows')
    except urllib.error.HTTPError as error:
        return None, 'http_' + str(error.code)
    except Exception:
        return None, 'probe_failed'  # Never log a response body, headers, token or exception string.


def quota():
    state = state_dir()
    cache_path = state / 'quota-snapshot-cache.json'
    with locked(state / 'quota-probe.lock'):
        now = time.time()
        credential = token()
        identity = hashlib.sha256((credential or 'no-subscription').encode()).hexdigest()
        cache = load(cache_path)
        if cache.get('identity') != identity:
            cache = {'identity': identity}
        # Parallel session boundaries share a bounded-age observation; every boundary still logs.
        if now - cache.get('attempted_at', 0) >= 5:
            value, error = probe(credential)
            cache['attempted_at'] = now
            cache['error'] = error
            if value:
                cache.update(observed_at=time.time(), windows=value, source='oauth_usage')
            fd = os.open(cache_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(cache, handle)
        if not cache.get('windows'):
            cached = load(state / 'claude-usage.json')
            value = windows(cached)
            if credential and value and isinstance(cached.get('timestamp'), (int, float)):
                cache.update(windows=value, observed_at=cached['timestamp'], source='statusline_cache')
        observed = cache.get('observed_at')
        return {
            'source': cache.get('source', 'unavailable'), 'observed_at': observed,
            'age_seconds': max(0, round(time.time() - observed, 3)) if observed else None,
            'status': ('unavailable' if not cache.get('windows') else
                       'stale' if cache.get('error') or cache.get('source') != 'oauth_usage' or now - observed > 5 else 'fresh'),
            'windows': cache.get('windows', {}), 'error': cache.get('error'),
        }


def snapshot(event, **context):
    try:
        record = dict(timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      event=event, pid=os.getpid(), cwd=os.getcwd(),
                      profile=os.environ.get('AGENT_PROFILE'), **context, quota=quota())
        state = state_dir()
        with locked(state / 'quota-log.lock'):
            fd = os.open(state / 'quota-snapshots.jsonl', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as handle:
                handle.write(json.dumps(record, separators=(',', ':')) + '\n')
    except Exception:
        print('claude-quota: snapshot could not be recorded', file=sys.stderr)


def run(command, label=None, env=None):
    launch_id = str(uuid.uuid4())
    context = {'launch_id': launch_id, 'label': label, 'session_id': None}
    snapshot('start', **context)
    child = None
    received = []
    previous = {}

    def forward(signum, _frame):
        received.append(signum)
        if child is not None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward)
    code = 127
    try:
        child_env = dict(os.environ if env is None else env, CLAUDE_QUOTA_MANAGED=launch_id)
        child = subprocess.Popen(command, env=child_env)
        if received:
            child.send_signal(received[-1])
        code = child.wait()
    except OSError:
        print('claude-session: unable to start CLI', file=sys.stderr)
    finally:
        # Keep forwarding while the end probe runs; do not turn a timeout into a missing end row.
        snapshot('finish', **context, exit_code=code, signal=received[-1] if received else None)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return code if code >= 0 else 128 - code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['hook', 'run'])
    parser.add_argument('--label')
    argv = sys.argv[1:]
    split = argv.index('--') if '--' in argv else len(argv)
    args = parser.parse_args(argv[:split])
    args.command = argv[split + 1:]
    if args.mode == 'hook':
        if os.environ.get('CLAUDE_QUOTA_MANAGED'):
            return 0
        data = json.load(sys.stdin)
        event = {'SessionStart': 'start', 'SessionEnd': 'finish'}.get(data.get('hook_event_name'))
        if event:
            snapshot(event, session_id=data.get('session_id'), launch_id=None,
                     label='interactive', reason=data.get('reason'), transcript_path=data.get('transcript_path'))
        return 0
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('run requires -- CLI [arguments]')
    return run(command, args.label)


if __name__ == '__main__':
    sys.exit(main())
