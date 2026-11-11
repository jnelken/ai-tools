from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import continuation_metrics


class ContinuationMetricsTest(unittest.TestCase):
    def test_compares_like_phases_and_marks_unattributed_costs(self):
        def record(mode, tokens, phase):
            return {'tokens': {'calls': [{'role': 'worker', 'total': tokens, 'cache_read': 50}]},
                    'incremental': {'continuation': {'mode': mode}, 'active_seconds': 20,
                                    'milestone_observed_by_seconds': 18,
                                    'admission': {'worker': 'claude', 'model': 'm', 'effort': 'high', 'phase': phase},
                                    'lifecycle': {'compaction_events': 1}}}
        result = continuation_metrics.compare([record('native', 100, 'implementing'),
                                               record('fallback', 200, 'implementing'),
                                               record('fallback', 999, 'reviewing')])
        (cohort,) = result['matched_cohorts']
        self.assertEqual(cohort['native']['worker_tokens_p80'], 100)
        self.assertEqual(cohort['fallback']['worker_tokens_p80'], 200)
        self.assertEqual(result['native']['checkpoint_generation_attributed_samples'], 0)
        self.assertFalse(result['minimum_samples_met'])
        self.assertIn('do not separately attribute', result['limitations'])


if __name__ == '__main__':
    unittest.main()
