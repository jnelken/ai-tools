import concurrent.futures
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import reviewlease


class ReviewLeaseTest(unittest.TestCase):
    def test_one_reserved_review_per_provider_even_concurrently(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review-spend.json'
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(lambda _: reviewlease.claim(path, 'codex', ['codex']), range(4)))
            self.assertEqual(outcomes.count(True), 1)
            self.assertFalse(reviewlease.claim(path, 'claude', ['codex']))
            self.assertTrue(reviewlease.claim(path, 'claude', ['claude']))


if __name__ == '__main__':
    unittest.main()
