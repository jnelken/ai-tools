#!/usr/bin/env python3
"""Record the exact owner of in-progress work without replacing another session's notes."""
import argparse
import fcntl
import hashlib
import os
import re
import tempfile
from pathlib import Path


def punch_in(workspace, ticket, session, model, provider):
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or not (root / '.git').exists():
        raise ValueError('workspace must be a Git checkout root')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', session):
        raise ValueError('invalid session ID')
    def clean(value):
        return ' '.join(str(value or 'unknown').split()).replace('|', '/').replace('`', '')[:200]
    # Follow the same precedence as pick-up. Never write through an existing symlink.
    claude = root / '.claude' / 'IN_PROGRESS.md'
    target = claude if claude.exists() or claude.is_symlink() else root / 'IN_PROGRESS.md'
    if target.is_symlink():
        raise ValueError('IN_PROGRESS.md is a symlink')
    lock = Path(tempfile.gettempdir()) / ('agent-punch-in-' + hashlib.sha256(os.fsencode(root)).hexdigest() + '.lock')
    marker = f'<!-- agent-work:{session} -->'
    end = f'<!-- /agent-work:{session} -->'
    entry = (f'{marker}\n- [ ] Ticket: {clean(ticket)} | Session: {session} | '
             f'Provider: {clean(provider)} | Model: {clean(model)}\n{end}')
    with lock.open('a') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX)
        original = target.read_text(encoding='utf8') if target.exists() else '# In Progress\n'
        start = original.find(marker)
        if start >= 0:
            stop = original.find(end, start + len(marker))
            if stop < 0:
                raise ValueError('incomplete existing session marker')
            updated = original[:start] + entry + original[stop + len(end):]
        else:
            updated = original.rstrip() + '\n\n' + entry + '\n'
        if updated != original:
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
            fd, name = tempfile.mkstemp(prefix='.punch-in-', dir=root)
            try:
                with os.fdopen(fd, 'w', encoding='utf8') as out:
                    out.write(updated)
                os.chmod(name, mode)
                os.replace(name, target)
            finally:
                if os.path.exists(name): os.unlink(name)
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', required=True)
    p.add_argument('--ticket', default='none')
    p.add_argument('--session', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--provider', required=True)
    a = p.parse_args()
    print(punch_in(a.workspace, a.ticket, a.session, a.model, a.provider))


if __name__ == '__main__':
    main()
