#!/usr/bin/env python3
"""Read-only baseline for the roadmap scheduler design; emits aggregate JSON only."""
import argparse
import collections
import json
from pathlib import Path


def analyze(root, since, until):
    rows = [json.loads(line) for line in (root / 'runs.jsonl').read_text().splitlines() if line.strip()]
    rows = [r for r in rows if since <= r['stamp'][:8] < until]
    by_stamp = {r['stamp']: r for r in rows}
    roles = collections.Counter()
    no_work_planning = 0
    for row in rows:
        for call in (row.get('tokens') or {}).get('calls', []):
            roles[call['role']] += call.get('total', 0)
            if row['outcome'] == 'blocked-no-item' and call['role'] == 'orchestrator':
                no_work_planning += call.get('total', 0)
    mismatches = collections.Counter()
    phases = collections.Counter()
    assignments = collections.Counter()
    for path in sorted((root / 'batches').glob('*/batch.json')):
        if not since <= path.parent.name[:10].replace('-', '') < until:
            continue
        batch = json.loads(path.read_text())
        phases[batch['phase']] += 1
        for item in batch.get('queue', []):
            row = by_stamp.get(item.get('stamp'))
            if not row:
                continue
            if item['repo'] != row.get('repo'):
                mismatches[row['outcome']] += 1
            if row['outcome'] == 'blocked-no-item':
                assignments[item['repo']] += 1
    return {
        'period': {'since_inclusive': since, 'until_exclusive': until},
        'records': len(rows), 'unique_stamps': len(by_stamp),
        'outcomes': dict(collections.Counter(r['outcome'] for r in rows)),
        'token_coverage_records': sum(bool(r.get('tokens')) for r in rows),
        'new_tokens_by_role': dict(roles),
        'no_work_planner_new_tokens': no_work_planning,
        'batch_phases': dict(phases),
        'record_repo_mismatches_by_outcome': dict(mismatches),
        'assigned_repos_with_no_work': len(assignments),
        'no_work_repetition_histogram': dict(collections.Counter(assignments.values())),
        'limitations': [
            'Finalized run records only; no fabricated rows for missing or still-active runs.',
            'New tokens = uncached input + cache write + output; cache reads excluded.',
            'Provider quota percentages and dollars are not interchangeable with token totals.',
            'Use batch assignment when run record repo attribution disagrees.',
            'Historical scheduler versions and recent fixes confound comparisons.',
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / '.claude/automations/advance-roadmap')
    parser.add_argument('--since', default='20261103')
    parser.add_argument('--until', default='20261110')
    args = parser.parse_args()
    print(json.dumps(analyze(args.root, args.since, args.until), indent=2))
