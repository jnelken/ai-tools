#!/usr/bin/env python3
"""Record the exact owner of in-progress work without replacing another session's notes."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path


def primary_checkout(path):
    """The primary checkout that owns `path`. IN_PROGRESS.md lives only there, never in a linked worktree."""
    root = Path(path).resolve(strict=True)
    dotgit = root / '.git'
    if dotgit.is_dir() and not dotgit.is_symlink():
        return root
    if dotgit.is_file() and not dotgit.is_symlink():
        # A linked worktree's .git is a file: "gitdir: <primary>/.git/worktrees/<name>".
        text = dotgit.read_text(encoding='utf8').strip()
        if not text.startswith('gitdir:'):
            raise ValueError('unrecognised .git file')
        gitdir = Path(text[len('gitdir:'):].strip())
        if not gitdir.is_absolute():
            gitdir = root / gitdir
        common = gitdir.resolve().parent.parent
        if common.name == '.git' and common.is_dir():
            return common.parent
    raise ValueError('workspace must be inside a Git checkout')


def punch_in(workspace, ticket, session, model, provider):
    # Record ownership in the primary checkout even when handed a linked worktree.
    root = primary_checkout(workspace)
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


def release(workspace, session):
    """Remove one finished session's punch-in block from the primary checkout's IN_PROGRESS.md.

    Other sessions' blocks and handoff notes stay. Returns True when a block was removed."""
    root = primary_checkout(workspace)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', session):
        raise ValueError('invalid session ID')
    # A block may sit in either file (a repo can have both); punch_in writes only the first one.
    candidates = [root / '.claude' / 'IN_PROGRESS.md', root / 'IN_PROGRESS.md']
    lock = Path(tempfile.gettempdir()) / ('agent-punch-in-' + hashlib.sha256(os.fsencode(root)).hexdigest() + '.lock')
    marker = f'<!-- agent-work:{session} -->'
    end = f'<!-- /agent-work:{session} -->'
    removed = False
    with lock.open('a') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX)
        for target in candidates:
            if target.is_symlink() or not target.is_file():
                continue
            original = target.read_text(encoding='utf8')
            start = original.find(marker)
            if start < 0:
                continue
            stop = original.find(end, start + len(marker))
            if stop < 0:
                raise ValueError('incomplete existing session marker')
            updated = original[:start].rstrip() + '\n' + original[stop + len(end):].lstrip('\n')
            if not updated.endswith('\n'):
                updated += '\n'
            fd, name = tempfile.mkstemp(prefix='.punch-in-', dir=target.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf8') as out:
                    out.write(updated)
                os.chmod(name, target.stat().st_mode & 0o777)
                os.replace(name, target)
            finally:
                if os.path.exists(name): os.unlink(name)
            removed = True
    return removed


def sweep(code_dir, queue):
    """Release punch-in blocks whose session belongs to a work item that reached `done`.

    `code_dir` holds each repo's primary checkout; `queue` is the work-queue SQLite file, opened
    read-only. Returns the (repo, session) pairs it released."""
    released = []
    if not code_dir or not Path(queue).exists():
        return released
    with closing(sqlite3.connect(f'file:{queue}?mode=ro', uri=True, timeout=5)) as con:
        rows = con.execute('select w.repo, s.binding from sessions s join work w on w.id = s.work_id '
                           "where w.state = 'done'").fetchall()
    for repo, binding in rows:
        try:
            session = json.loads(binding).get('session_id')
            checkout = Path(code_dir) / repo
            if not isinstance(session, str) or not session or not checkout.is_dir():
                continue
            if release(checkout, session):
                released.append((repo, session))
        except (ValueError, OSError, TypeError, AttributeError):
            continue  # one unreadable row or checkout must not stop the batch
    for repo, session in released:
        print(f'released {session} in {repo}')
    return released


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd')
    p.add_argument('--workspace')
    p.add_argument('--ticket', default='none')
    p.add_argument('--session')
    p.add_argument('--model')
    p.add_argument('--provider')
    r = sub.add_parser('release')
    r.add_argument('--workspace', required=True)
    r.add_argument('--session', required=True)
    s = sub.add_parser('sweep')
    s.add_argument('--code-dir', required=True)
    s.add_argument('--queue', required=True)
    a = p.parse_args()
    if a.cmd == 'release':
        print(release(a.workspace, a.session))
    elif a.cmd == 'sweep':
        sweep(a.code_dir, a.queue)
    else:
        if not (a.workspace and a.session and a.model and a.provider):
            p.error('--workspace, --session, --model and --provider are required')
        print(punch_in(a.workspace, a.ticket, a.session, a.model, a.provider))


if __name__ == '__main__':
    main()
