from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.config import config
from services.model_probe_service import read_model_checks
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import is_supported_image_model


class Image25RoutingTests(unittest.TestCase):
    def test_prepare_and_generation_preserve_explicit_image_model(self):
        for model in (
            "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
            "gpt-image-2.6-flare", "gpt-image-2.6-sunburst",
        ):
            with self.subTest(model=model):
                self.assertTrue(is_supported_image_model(model))
                backend = object.__new__(OpenAIBackendAPI)
                backend.base_url = "https://chatgpt.com"
                backend.session = mock.Mock()
                backend._image_headers = mock.Mock(return_value={})
                response = backend.session.post.return_value
                response.status_code = 200
                response.json.return_value = {"conduit_token": "test-conduit"}
                with mock.patch.dict(config.data, {"default_upstream_model_name": "legacy-chat-model"}):
                    backend._prepare_image_conversation("test", None, model)
                    self.assertEqual(backend.session.post.call_args.kwargs["json"]["model"], model)
                    backend._start_image_generation("test", None, "test-conduit", model, [])
                    self.assertEqual(backend.session.post.call_args.kwargs["json"]["model"], model)

    def test_legacy_model_still_uses_configured_chat_model(self):
        backend = object.__new__(OpenAIBackendAPI)
        with mock.patch.dict(config.data, {"default_upstream_model_name": "legacy-chat-model"}):
            self.assertEqual(backend._image_model_settings("gpt-image-2")[0], "legacy-chat-model")
        self.assertFalse(is_supported_image_model("gpt-image-2.5-nonexistent"))
        self.assertFalse(is_supported_image_model("gpt-image-2.6-nonexistent"))

    def test_checks_use_latest_observation_and_hide_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = {"mode": "image", "requested_model": "gpt-image-2.5-flare", "status": "rejected", "error": "private"}
            newer = {"mode": "web-image", "requested_model": "gpt-image-2.5-flare", "status": "image_generated_model_unverified", "image_file": "private-path", "access_token": "private-token"}
            (root / "20260101.json").write_text(json.dumps(older), encoding="utf-8")
            (root / "20260102.json").write_text(json.dumps(newer), encoding="utf-8")
            (root / "20260103.json").write_text("{bad json", encoding="utf-8")
            items = read_model_checks(root)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mode"], "web-image")
        self.assertNotIn("private", json.dumps(items))


if __name__ == "__main__":
    unittest.main()
