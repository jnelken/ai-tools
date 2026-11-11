#!/usr/bin/env python3
"""Mechanically bind a provider session to one saved assignment and checkout.

No model calls and no global session lookup. A failed compatibility check means
the caller must use the saved artifacts instead of resuming an unrelated chat.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import workqueue


def auth_fingerprint(provider, env=None):
    env = os.environ if env is None else env
    if provider == 'claude':
        directory = Path(env.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser()
        auth = directory / '.credentials.json'
        variables = ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN')
    elif provider == 'codex':
        directory = Path(env.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
        auth = directory / 'auth.json'
        variables = ('OPENAI_API_KEY', 'CODEX_API_KEY')
    else:
        return None
    digest = hashlib.sha256()
    digest.update(str(directory.resolve()).encode())
    for name in variables:
        digest.update(name.encode() + b'\0' + env.get(name, '').encode() + b'\0')
    if provider == 'claude' and not any(env.get(name) for name in variables):
        # Claude commonly stores credentials in the macOS keychain. The CLI's
        # login status plus its account/organization IDs provide a stable local
        # identity without copying or logging the keychain credential.
        config = (directory / '.claude.json' if env.get('CLAUDE_CONFIG_DIR')
                  else Path.home() / '.claude.json')
        try:
            account = json.loads(config.read_text()).get('oauthAccount') or {}
            status = subprocess.run([env.get('ADVANCE_ROADMAP_CLAUDE_BIN', 'claude'),
                                     'auth', 'status', '--json'], capture_output=True,
                                    text=True, timeout=10, env=env)
            info = json.loads(status.stdout)
            if (status.returncode == 0 and info.get('loggedIn') and account.get('accountUuid')
                    and account.get('organizationUuid')):
                digest.update(json.dumps([account['accountUuid'], account['organizationUuid'],
                                          info.get('authMethod'), info.get('apiProvider')]).encode())
                return digest.hexdigest()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    try:
        digest.update(auth.read_bytes())
    except FileNotFoundError:
        # Providers may authenticate via a keychain; without a verifiable
        # identity do not assume a different invocation uses the same account.
        if not any(env.get(name) for name in variables):
            return None
    except OSError:
        return None
    return digest.hexdigest()


def context(request, provider, workspace, env=None):
    plan = request.get('orchestrator') or {}
    wid = workqueue.identity(plan)
    path = Path(workspace).resolve()
    branch = workqueue.git(path, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    head = workqueue.git(path, 'rev-parse', 'HEAD')
    auth = auth_fingerprint(provider, env)
    if not all((wid, branch, head, auth)) or branch != plan.get('branch'):
        return None
    return {'work_id': wid, 'provider': provider, 'workspace': str(path),
            'branch': branch, 'head': head, 'auth': auth,
            'requirements': workqueue.requirements_fingerprint(plan)}


def binding(request, provider, workspace, session_id, env=None):
    ctx = context(request, provider, workspace, env)
    if not ctx or not isinstance(session_id, str) or not session_id.strip():
        return None
    return {**ctx, 'session_id': session_id.strip(), 'attempt': request.get('stamp')}


def compatible(saved, request, provider, workspace, env=None):
    ctx = context(request, provider, workspace, env)
    if not isinstance(saved, dict) or ctx is None:
        return False
    return bool(saved.get('session_id')) and all(saved.get(k) == v for k, v in ctx.items())


def select(store, request, provider, workspace, env=None):
    if request.get('mode') != 'resume':
        return {'mode': 'fresh', 'reason': 'new assignment'}
    wid = workqueue.identity(request.get('orchestrator') or {})
    saved = store.session(wid, provider) if wid else None
    if not saved:
        return {'mode': 'fallback', 'reason': 'no owned session for provider'}
    if not compatible(saved, request, provider, workspace, env):
        return {'mode': 'fallback', 'reason': 'session identity, auth, requirements, workspace, branch or HEAD changed'}
    return {'mode': 'native', 'session_id': saved['session_id'], 'resumed_from': saved.get('attempt')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    with workqueue.Store(args.root) as store:
        decision = select(store, workqueue.read(args.request, {}), args.provider, args.workspace)
    decision['selection_seconds'] = round(time.monotonic() - started, 3)
    print(json.dumps(decision))


if __name__ == '__main__':
    main()
