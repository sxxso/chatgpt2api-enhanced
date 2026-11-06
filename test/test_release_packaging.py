from __future__ import annotations

import unittest

from scripts.check_release import REQUIRED_FILES, forbidden, missing_required_files


class ReleasePackagingTests(unittest.TestCase):
    def test_missing_entry_point_is_rejected(self):
        files = set(REQUIRED_FILES)
        files.remove("main.py")
        self.assertEqual(missing_required_files(files), ["main.py"])

    def test_windows_package_requires_panel(self):
        files = set(REQUIRED_FILES)
        self.assertEqual(len(missing_required_files(files, windows=True)), 3)
        files.update({"web_dist/login/index.html", "web_dist/image/index.html", "web_dist/settings/index.html"})
        self.assertEqual(missing_required_files(files, windows=True), [])

    def test_runtime_data_and_traversal_are_rejected(self):
        for name in ("config.json", "data/accounts.json", ".env", "../outside", "web/node_modules/next/package.json"):
            with self.subTest(name=name):
                self.assertTrue(forbidden(name, archive=True))
        self.assertTrue(forbidden("web_dist/image/index.html"))
        self.assertFalse(forbidden("web_dist/image/index.html", archive=True))
        self.assertFalse(forbidden("config.example.json"))
