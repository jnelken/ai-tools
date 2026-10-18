"""Regression tests for lib/personal_code_dir.py (DEV-222).

Run from repo root: python3 -m unittest discover -s lib -p 'test_*.py'
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_code_dir as pcd  # noqa: E402


class CodeDirResolution(unittest.TestCase):
    def setUp(self):
        # Clear overrides so each case starts from discovery.
        self._saved = {
            var: os.environ.pop(var)
            for var in ("PERSONAL_CODE_DIR", "JLIN_CODE_DIR", "HYGIENE_CODE_DIR")
            if var in os.environ
        }

    def tearDown(self):
        for var in ("PERSONAL_CODE_DIR", "JLIN_CODE_DIR", "HYGIENE_CODE_DIR"):
            os.environ.pop(var, None)
        os.environ.update(self._saved)

    def _dirs(self, existing):
        existing = {os.path.normpath(p) for p in existing}

        def isdir(path):
            return os.path.normpath(path) in existing

        return isdir

    def test_windows_prefers_d_code_when_dropbox_also_exists(self):
        dropbox = os.path.join(pcd.HOME, "Dropbox/code")
        with mock.patch.object(pcd, "_is_windows", return_value=True), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([r"D:\code", dropbox])):
            self.assertEqual(pcd.code_dir(), r"D:\code")

    def test_windows_falls_back_to_home_code_alias(self):
        home_code = os.path.join(pcd.HOME, "code")
        dropbox = os.path.join(pcd.HOME, "Dropbox/code")
        with mock.patch.object(pcd, "_is_windows", return_value=True), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([home_code, dropbox])):
            self.assertEqual(pcd.code_dir(), home_code)

    def test_windows_missing_personal_root_falls_through_to_dropbox(self):
        dropbox = os.path.join(pcd.HOME, "Dropbox/code")
        with mock.patch.object(pcd, "_is_windows", return_value=True), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([dropbox])):
            self.assertEqual(pcd.code_dir(), dropbox)

    def test_windows_returns_none_when_nothing_exists(self):
        with mock.patch.object(pcd, "_is_windows", return_value=True), \
             mock.patch.object(os.path, "isdir", return_value=False):
            self.assertIsNone(pcd.code_dir())

    def test_mac_ignores_home_code_even_when_present(self):
        """~/code on the work Mac holds Concentro checkouts — never the personal root."""
        home_code = os.path.join(pcd.HOME, "code")
        dropbox = os.path.join(pcd.HOME, "Dropbox/code")
        with mock.patch.object(pcd, "_is_windows", return_value=False), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([home_code, dropbox])):
            self.assertEqual(pcd.code_dir(), dropbox)

    def test_mac_dropbox_cloudstorage_fallback(self):
        cloud = os.path.join(pcd.HOME, "Library/CloudStorage/Dropbox/code")
        with mock.patch.object(pcd, "_is_windows", return_value=False), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([cloud])):
            self.assertEqual(pcd.code_dir(), cloud)

    def test_mac_returns_none_without_dropbox(self):
        home_code = os.path.join(pcd.HOME, "code")
        with mock.patch.object(pcd, "_is_windows", return_value=False), \
             mock.patch.object(os.path, "isdir", side_effect=self._dirs([home_code])):
            self.assertIsNone(pcd.code_dir())

    def test_personal_code_dir_override_wins(self):
        os.environ["PERSONAL_CODE_DIR"] = "~/forced-personal"
        with mock.patch.object(pcd, "_is_windows", return_value=True), \
             mock.patch.object(os.path, "isdir", return_value=True):
            self.assertEqual(
                pcd.code_dir(),
                os.path.abspath(os.path.expanduser("~/forced-personal")),
            )

    def test_legacy_env_wins_after_personal(self):
        os.environ["JLIN_CODE_DIR"] = "~/legacy-jlin"
        with mock.patch.object(pcd, "_is_windows", return_value=False), \
             mock.patch.object(os.path, "isdir", return_value=True):
            self.assertEqual(
                pcd.code_dir(legacy_env="JLIN_CODE_DIR"),
                os.path.abspath(os.path.expanduser("~/legacy-jlin")),
            )

    def test_personal_beats_legacy(self):
        os.environ["PERSONAL_CODE_DIR"] = "~/forced-personal"
        os.environ["JLIN_CODE_DIR"] = "~/legacy-jlin"
        with mock.patch.object(os.path, "isdir", return_value=True):
            self.assertEqual(
                pcd.code_dir(legacy_env="JLIN_CODE_DIR"),
                os.path.abspath(os.path.expanduser("~/forced-personal")),
            )

    def test_candidates_never_include_home_code_off_windows(self):
        with mock.patch.object(pcd, "_is_windows", return_value=False):
            candidates = pcd._code_dir_candidates()
        self.assertNotIn(os.path.join(pcd.HOME, "code"), candidates)
        self.assertNotIn(r"D:\code", candidates)

    def test_candidates_put_windows_roots_before_dropbox(self):
        with mock.patch.object(pcd, "_is_windows", return_value=True):
            candidates = pcd._code_dir_candidates()
        self.assertEqual(candidates[0], r"D:\code")
        self.assertEqual(candidates[1], os.path.join(pcd.HOME, "code"))
        self.assertLess(
            candidates.index(r"D:\code"),
            candidates.index(os.path.join(pcd.HOME, "Dropbox/code")),
        )


class FindRepoOwnership(unittest.TestCase):
    def test_named_repo_under_code_dir_needs_no_owner_check(self):
        root = "/tmp/personal-code"
        with mock.patch.object(pcd, "code_dir", return_value=root), \
             mock.patch.object(os.path, "isdir", return_value=True):
            self.assertEqual(pcd.find_repo("minecraft"), os.path.join(root, "minecraft"))

    def test_extra_parent_requires_jnelken_origin(self):
        with mock.patch.object(pcd, "code_dir", return_value=None), \
             mock.patch.object(pcd, "is_personal_checkout", side_effect=lambda path: path.endswith("good")):
            self.assertEqual(
                pcd.find_repo("good"),
                os.path.join(pcd.HOME, "code", "good"),
            )
            self.assertIsNone(pcd.find_repo("bad"))


if __name__ == "__main__":
    unittest.main()
