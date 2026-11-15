import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import dirty_snapshot


class DirtySnapshotTests(unittest.TestCase):
    def test_content_changes_are_detected_even_when_status_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            git('init', '-q')
            (root / 'new file').write_text('first')
            before = dirty_snapshot.fingerprint(root)
            (root / 'new file').write_text('second')
            self.assertNotEqual(before, dirty_snapshot.fingerprint(root))


if __name__ == '__main__':
    unittest.main()
