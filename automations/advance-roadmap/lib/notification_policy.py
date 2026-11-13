#!/usr/bin/env python3
"""Identify quiet continuous checkpoints; unknown outcomes remain visible."""
import json
import sys
from pathlib import Path


def routine_checkpoint(row, result, review=None):
    if row.get('outcome') == 'skipped-unchanged':
        return True
    reason = (((row.get('incremental') or {}).get('lifecycle') or {}).get('reason') or '')
    review = review or {}
    quota_review = review.get('verdict') == 'unavailable' and (review.get('gated') or review.get('limited'))
    return (row.get('outcome') == 'yielded' and not result.get('needs_input')
            and not quota_review
            and reason not in ('quota-ceiling', 'quota-target', 'usage-unavailable',
                               'watchdog', 'guard-failed', 'external-signal'))


def main(runs, stamp, directory):
    try:
        rows = [json.loads(line) for line in Path(runs).read_text().splitlines() if line.strip()]
        row = next(row for row in reversed(rows) if row.get('stamp') == stamp)
        result_path = Path(directory) / 'worker-result.json'
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        review_path = Path(directory) / 'reviews.jsonl'
        reviews = [json.loads(line) for line in review_path.read_text().splitlines() if line.strip()] if review_path.exists() else []
        return 0 if routine_checkpoint(row, result, reviews[-1] if reviews else None) else 1
    except (OSError, ValueError, StopIteration, TypeError):
        return 1


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
