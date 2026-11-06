from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.init_local_config import initialize


class LocalSetupTests(unittest.TestCase):
    def prepare(self, root: Path):
        (root / "config.example.json").write_text(json.dumps({"auth-key": "", "default_image_model": "fixture-model"}), encoding="utf-8")

    def test_unique_key_and_valid_utf8_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            self.assertTrue(initialize(root))
            value = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(value["auth-key"]), 40)
            self.assertEqual(value["default_image_model"], "fixture-model")
            other = root / "another"
            other.mkdir()
            self.prepare(other)
            initialize(other)
            self.assertNotEqual(value["auth-key"], json.loads((other / "config.json").read_text())["auth-key"])

    def test_existing_settings_and_key_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            initialize(root)
            path = root / "config.json"
            before = path.read_bytes()
            self.assertFalse(initialize(root))
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_existing_file_and_directory_are_preserved(self):
        for content in ("{", "[]", '{"auth-key":""}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.prepare(root)
                path = root / "config.json"
                path.write_text(content)
                with self.assertRaises(ValueError):
                    initialize(root)
                self.assertEqual(path.read_text(), content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            (root / "config.json").mkdir()
            with self.assertRaises(ValueError):
                initialize(root)
            self.assertTrue((root / "config.json").is_dir())


class ProbeExitStatusTests(unittest.TestCase):
    def test_image_probe_requires_result_and_matching_assistant_model(self):
        from scripts.probe_pool_models import probe_succeeded
        report = {"mode": "web-image", "requested_model": "fixture-model", "status": "image_generated_model_unverified"}
        self.assertFalse(probe_succeeded(report))
        report["model_evidence"] = [{"role": "user", "path": "$.model_slug", "model": "fixture-model"}]
        self.assertFalse(probe_succeeded(report))
        report["model_evidence"] = [{"role": "assistant", "path": "$.model_slug", "model": "other-model"}]
        self.assertFalse(probe_succeeded(report))
        report["model_evidence"][0]["model"] = "fixture-model"
        self.assertTrue(probe_succeeded(report))
        report["status"] = "error"
        self.assertFalse(probe_succeeded(report))
