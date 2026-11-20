#!/usr/bin/env python3
"""Measure instruction overhead and write a monthly snapshot plus a measurement table.

Deterministic and report-only: it reads instruction files and runs trivial `claude -p`
probes, and writes only under --root (snapshots/ and reports/). Token counts for files
are estimates (chars/4); the probes report real API usage, which also captures what
local files cannot show (connector tool names, plugin skills, hook-injected context).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / '.claude'
AI_TOOLS = Path(os.environ.get('AI_TOOLS_HOME', HOME / '.ai-tools'))
CLAUDE_BIN = os.environ.get('CLAUDE_BIN', str(HOME / '.local/bin/claude'))
DEFAULT_TARGETS = ['~/code/ai-tools', '~/code/api', '~/code/woodrow', '~/code/folio-platform']


def tok(text):
    return round(len(text) / 4)


def read(path):
    try:
        return Path(path).read_text(errors='replace')
    except OSError:
        return ''


def frontmatter(text):
    m = re.match(r'^---\n(.*?)\n---', text, re.S)
    out, key = {}, None
    for line in (m.group(1).split('\n') if m else []):
        km = re.match(r'^([A-Za-z_-]+):\s*(.*)$', line)
        if km:
            key = km.group(1)
            out[key] = km.group(2)
        elif key:
            out[key] += ' ' + line.strip()
    return out


def resolved_profile(target):
    cmd = [sys.executable, str(AI_TOOLS / 'bin/agent-profile'), 'resolve',
           '--harness', 'claude', '--target', str(target), '--text']
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 - snapshot must survive a bad target
        return {'error': str(e)[:200]}
    text = p.stdout
    parts = re.split(r'^# Source: (.+)$', text, flags=re.M)
    fragments = {parts[i]: tok(parts[i + 1]) for i in range(1, len(parts) - 1, 2)}
    repo_files = {n: tok(read(Path(target) / n)) for n in ('CLAUDE.md', 'AGENTS.md') if (Path(target) / n).exists()}
    return {'profile_tokens': tok(text), 'fragments': fragments, 'repo_files': repo_files}


def skills_layer():
    rows = []
    for f in sorted((CLAUDE_DIR / 'skills').glob('*/SKILL.md')):
        fm = frontmatter(read(f))
        name = fm.get('name') or f.parent.name
        desc = fm.get('description', '')
        rows.append({'name': name, 'description_chars': len(desc), 'tokens': tok(f'{name}: {desc}')})
    rows.sort(key=lambda r: -r['tokens'])
    return {'count': len(rows), 'total_tokens': sum(r['tokens'] for r in rows), 'top': rows[:8]}


def memory_layer():
    rows = []
    for f in sorted((CLAUDE_DIR / 'projects').glob('*/memory/MEMORY.md')):
        text = read(f)
        rows.append({'project': f.parent.parent.name, 'lines': text.count('\n'), 'tokens': tok(text)})
    rows.sort(key=lambda r: -r['tokens'])
    return {'count': len(rows), 'top': rows[:5]}


def settings_layer():
    try:
        s = json.loads(read(CLAUDE_DIR / 'settings.json') or '{}')
    except json.JSONDecodeError:
        return {'error': 'settings.json unparsable'}
    hooks = s.get('hooks', {})
    return {
        'mcp_servers': len(s.get('mcpServers', {})),
        'enabled_plugins': sum(1 for v in s.get('enabledPlugins', {}).values() if v),
        'hook_commands': {ev: sum(len(g.get('hooks', [])) for g in groups) for ev, groups in hooks.items()},
    }


def probe(cwd, model):
    Path(cwd).mkdir(parents=True, exist_ok=True)
    cmd = [CLAUDE_BIN, '-p', 'Reply with only the word OK.', '--output-format', 'json', '--model', model]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=240)
        data = json.loads(p.stdout)
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)[:200]}
    if isinstance(data, list):
        data = next((d for d in reversed(data) if d.get('type') == 'result'), {})
    usage = data.get('usage') or {}
    total = sum(usage.get(k) or 0 for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
    return {'total_input_tokens': total, 'num_turns': data.get('num_turns'),
            'cost_usd': data.get('total_cost_usd'),
            'usage': {k: v for k, v in usage.items() if isinstance(v, int)}}


def delta(cur, prev):
    if cur is None or prev is None:
        return '—'
    d = cur - prev
    return f'{d:+d}' if d else '0'


def get(d, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|']
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def render(snap, prev):
    L = [f"# Instruction overhead measurements — {snap['month']}", '',
         'File tokens are estimates (chars/4). Probe tokens are API-reported (input + cache); '
         'a probe with more than one turn is inflated by the extra turns.', '']
    rows = []
    for label, p in snap['probes'].items():
        pp = get(prev, 'probes', label, 'total_input_tokens')
        rows.append([label, p.get('total_input_tokens', p.get('error')), delta(p.get('total_input_tokens'), pp), p.get('num_turns')])
    L += ['## End-to-end probe: what a fresh session really loads', '',
          table(['cwd', 'input tokens', 'Δ vs last', 'turns'], rows), '']
    rows = []
    for t, v in snap['targets'].items():
        pv = get(prev, 'targets', t, 'profile_tokens')
        top = ', '.join(f'{k} {n}' for k, n in sorted(v.get('fragments', {}).items(), key=lambda kv: -kv[1])[:3])
        rows.append([t, v.get('profile_tokens', v.get('error')), delta(v.get('profile_tokens'), pv), top])
    L += ['## Resolved profile per target', '', table(['target', 'tokens', 'Δ vs last', 'largest fragments'], rows), '']
    sk = snap['skills']
    L += ['## Skill listing (name + description in every session)', '',
          f"{sk['count']} skills, ~{sk['total_tokens']} tokens (Δ {delta(sk['total_tokens'], get(prev, 'skills', 'total_tokens'))}). Largest:", '',
          table(['skill', 'tokens'], [[r['name'], r['tokens']] for r in sk['top']]), '']
    L += ['## Global CLAUDE.md', '',
          f"~{snap['global_claude_md_tokens']} tokens (Δ {delta(snap['global_claude_md_tokens'], prev.get('global_claude_md_tokens') if prev else None)})", '']
    mem = snap['memory']
    L += [f"## Memory indexes ({mem['count']} projects; only the matching project's loads)", '',
          table(['project', 'lines', 'tokens'], [[r['project'], r['lines'], r['tokens']] for r in mem['top']]), '']
    L += ['## Settings', '', '```json', json.dumps(snap['settings'], indent=2), '```', '']
    return '\n'.join(L)


def measure(args, root):
    targets = [Path(t).expanduser() for t in
               (os.environ.get('INSTRUCTION_AUDIT_TARGETS', ':'.join(DEFAULT_TARGETS))).split(':')]
    targets = [t for t in targets if t.is_dir()]
    probe_cwds = {'neutral (no repo)': root / 'probe-cwd'}
    for t in os.environ.get('INSTRUCTION_AUDIT_PROBE_TARGETS', '~/code/ai-tools').split(':'):
        t = Path(t).expanduser()
        if t.is_dir():
            probe_cwds[str(t)] = t

    snap = {
        'month': args.month, 'estimator': 'chars/4', 'model': args.model,
        'global_claude_md_tokens': tok(read(CLAUDE_DIR / 'CLAUDE.md')),
        'targets': {str(t): resolved_profile(t) for t in targets},
        'skills': skills_layer(), 'memory': memory_layer(), 'settings': settings_layer(),
        'output_styles': {f.name: tok(read(f)) for f in sorted((CLAUDE_DIR / 'output-styles').glob('*.md'))},
        'probes': {} if args.no_probe else {label: probe(cwd, args.model) for label, cwd in probe_cwds.items()},
    }
    earlier = sorted(p for p in (root / 'snapshots').glob('*.json') if p.stem < args.month)
    prev = json.loads(earlier[-1].read_text()) if earlier else None
    (root / 'snapshots' / f'{args.month}.json').write_text(json.dumps(snap, indent=2) + '\n')
    out = root / 'reports' / f'{args.month}.measurements.md'
    out.write_text(render(snap, prev))
    print(f'measurements: {out}')
    print(f"snapshot: {root / 'snapshots' / f'{args.month}.json'}")
    print(f"previous snapshot: {earlier[-1] if earlier else 'none (this is the baseline)'}")


def save(args, root):
    analysis = sys.stdin.read().strip()
    meas = root / 'reports' / f'{args.month}.measurements.md'
    if not analysis:
        sys.exit('save: empty analysis on stdin; no report written')
    if not meas.exists():
        sys.exit(f'save: {meas} missing; run `measure` first')
    out = root / 'reports' / f'{args.month}.md'
    out.write_text(meas.read_text().rstrip() + '\n\n# Analysis\n\n' + analysis + '\n')
    print(f'report: {out}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, fn, help_ in (('measure', measure, 'snapshot the layers and write the measurement table'),
                            ('save', save, 'write the month report: measurements + analysis read from stdin')):
        p = sub.add_parser(name, help=help_)
        p.add_argument('--root', default=str(CLAUDE_DIR / 'automations/instruction-audit'),
                       help='runtime dir (snapshots/ and reports/ are created in it)')
        p.add_argument('--month', default=time.strftime('%Y-%m'), help='YYYY-MM (default: this month)')
        if name == 'measure':
            p.add_argument('--model', default='claude-sonnet-5-5')
            p.add_argument('--no-probe', action='store_true')
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    root = Path(args.root)
    (root / 'snapshots').mkdir(parents=True, exist_ok=True)
    (root / 'reports').mkdir(parents=True, exist_ok=True)
    args.fn(args, root)


if __name__ == '__main__':
    main()
