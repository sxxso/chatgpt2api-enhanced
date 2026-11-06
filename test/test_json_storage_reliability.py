from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from services.storage.json_storage import JSONStorageBackend
from services.config import ConfigStore


class JSONStorageReliabilityTests(unittest.TestCase):
    def test_corrupt_accounts_are_not_loaded_as_an_empty_pool_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            original = '[{"quota":'
            path.write_text(original, encoding="utf-8")
            store = JSONStorageBackend(path)
            with self.assertRaises(RuntimeError):
                store.load_accounts()
            self.assertEqual(store.health_check()["status"], "unhealthy")
            with self.assertRaises(RuntimeError):
                store.save_accounts([])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_wrong_account_shape_and_bad_auth_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "accounts.json"
            path.write_text('{"items": []}', encoding="utf-8")
            keys = root / "auth_keys.json"
            keys.write_text('{"items":', encoding="utf-8")
            store = JSONStorageBackend(path)
            with self.assertRaises(RuntimeError):
                store.load_accounts()
            with self.assertRaises(RuntimeError):
                store.load_auth_keys()
            with self.assertRaises(RuntimeError):
                store.save_auth_keys([])

    def test_atomic_save_keeps_the_previous_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            store = JSONStorageBackend(path)
            first = [{"access_token": "fixture", "quota": 2}]
            store.save_accounts(first)
            store.save_accounts([{"access_token": "fixture", "quota": 1}])
            backup = path.with_suffix(path.suffix + ".bak")
            self.assertTrue(backup.exists())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), first)
            self.assertEqual(store.load_accounts()[0]["quota"], 1)

    def test_failed_atomic_replace_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            store = JSONStorageBackend(path)
            store.save_accounts([{"access_token": "fixture", "quota": 2}])
            original = path.read_bytes()
            replace = os.replace
            def fail_primary(source, destination):
                if Path(destination) == path:
                    raise OSError("fixture write failure")
                return replace(source, destination)
            with mock.patch("os.replace", side_effect=fail_primary):
                with self.assertRaises(OSError):
                    store.save_accounts([])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.with_suffix(".json.bak").read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corruption_does_not_replace_the_last_good_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            store = JSONStorageBackend(path)
            store.save_accounts([{"quota": 2}])
            store.save_accounts([{"quota": 1}])
            backup = path.with_suffix(".json.bak")
            previous = backup.read_bytes()
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                store.save_accounts([])
            self.assertEqual(backup.read_bytes(), previous)

    def test_auth_key_legacy_list_and_object_formats_remain_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            store = JSONStorageBackend(path)
            for value in ([{"id": "fixture"}], {"items": [{"id": "fixture"}]}):
                store.auth_keys_path.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(store.load_auth_keys(), [{"id": "fixture"}])


    def test_failed_settings_save_preserves_file_and_in_memory_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"auth-key": "fixture", "default_image_model": "gpt-image-2.6-flare"}), encoding="utf-8")
            settings = ConfigStore(path)
            original = path.read_bytes()
            with mock.patch("os.replace", side_effect=OSError("fixture write failure")):
                with self.assertRaises(OSError):
                    settings.update({"default_image_model": "gpt-image-2.6-sunburst"})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(settings.default_image_model, "gpt-image-2.6-flare")


if __name__ == "__main__":
    unittest.main()
