"""Compare observed continuation costs without extra model calls or attribution guesses."""
import math


def percentile(values, quantile=.8):
    values = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    return values[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def compare(records):
    groups = {mode: [] for mode in ('fresh', 'native', 'fallback')}
    cohorts = {}
    for record in records:
        incremental = record.get('incremental') or {}
        mode = (incremental.get('continuation') or {}).get('mode')
        if mode not in groups:
            continue
        worker_calls = [c for c in ((record.get('tokens') or {}).get('calls') or [])
                        if c.get('role') == 'worker']
        lifecycle = incremental.get('lifecycle') or {}
        sample = {'tokens': sum(numeric(c.get('total')) for c in worker_calls) if worker_calls else None,
            'cache_read': sum(numeric(c.get('cache_read')) for c in worker_calls) if worker_calls else None,
            'active_seconds': incremental.get('active_seconds'),
            'milestone_seconds': incremental.get('milestone_observed_by_seconds'),
            'first_model_input': lifecycle.get('first_model_input'),
            'compaction_events': lifecycle.get('compaction_events'),
            'checkpoint_generation_tokens': lifecycle.get('checkpoint_generation_tokens'),
            'reconstruction_tokens': lifecycle.get('reconstruction_tokens')}
        groups[mode].append(sample)
        admission = incremental.get('admission') or {}
        key = tuple(admission.get(field) for field in ('worker', 'model', 'effort', 'phase'))
        if all(key):
            cohorts.setdefault(key, {'native': [], 'fallback': []})
            if mode in ('native', 'fallback'):
                cohorts[key][mode].append(sample)
    output = {}
    for mode, samples in groups.items():
        output[mode] = {'samples': len(samples), 'worker_tokens_p80': percentile([r['tokens'] for r in samples]),
            'cache_read_p80': percentile([r['cache_read'] for r in samples]),
            'active_seconds_p80': percentile([r['active_seconds'] for r in samples]),
            'milestone_observed_by_seconds_p80': percentile([r['milestone_seconds'] for r in samples]),
            'compaction_events_observed': sum(r['compaction_events'] or 0 for r in samples),
            'first_model_input_samples': sum(r['first_model_input'] is not None for r in samples),
            'checkpoint_generation_attributed_samples': sum(r['checkpoint_generation_tokens'] is not None for r in samples),
            'reconstruction_attributed_samples': sum(r['reconstruction_tokens'] is not None for r in samples)}
    output['minimum_samples_met'] = min(output['native']['samples'], output['fallback']['samples']) >= 5
    output['matched_cohorts'] = []
    for key, modes in cohorts.items():
        if not modes['native'] or not modes['fallback']:
            continue
        row = dict(zip(('provider', 'model', 'effort', 'phase'), key))
        for mode, samples in modes.items():
            row[mode] = {'samples': len(samples), 'worker_tokens_p80': percentile([s['tokens'] for s in samples]),
                         'cache_read_p80': percentile([s['cache_read'] for s in samples]),
                         'active_seconds_p80': percentile([s['active_seconds'] for s in samples]),
                         'milestone_observed_by_seconds_p80': percentile([s['milestone_seconds'] for s in samples])}
        output['matched_cohorts'].append(row)
    output['limitations'] = ('Whole-worker tokens and elapsed time include checkpoint/reconstruction work; '
        'provider streams do not separately attribute all checkpoint, compaction or reconstruction tokens. '
        'Milestone times are observed-by upper bounds, and phase/model mix can confound comparisons.')
    return output
