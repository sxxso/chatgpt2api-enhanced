from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from services.account_service import AccountService
from services.openai_backend_api import ImagePollTimeoutError
from services.protocol import conversation
from services.storage.json_storage import JSONStorageBackend


class ImageReliabilityTests(unittest.TestCase):
    def test_progress_only_failure_does_not_release_another_requests_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            path.write_text(json.dumps([{"access_token": "fixture-token", "status": "正常", "quota": 5}]), encoding="utf-8")
            accounts = AccountService(JSONStorageBackend(path))
            accounts._image_inflight["fixture-token"] = 2
            output = conversation.ImageOutput(kind="progress", model="gpt-image-2.6-sunburst", index=1, total=1, conversation_id="fixture-conversation")
            with (
                mock.patch.object(conversation, "account_service", accounts),
                mock.patch.object(accounts, "get_available_access_token", return_value="fixture-token"),
                mock.patch.object(conversation, "OpenAIBackendAPI"),
                mock.patch.object(conversation, "stream_image_outputs", return_value=iter([output])),
                mock.patch.object(conversation, "_remove_image_conversation_later"),
            ):
                with self.assertRaises(conversation.ImageGenerationError):
                    conversation._generate_single_image(conversation.ConversationRequest(model="gpt-image-2.6-sunburst"), 1, 1)
            self.assertEqual(accounts._image_inflight["fixture-token"], 1)
            self.assertEqual(accounts.get_account("fixture-token")["fail"], 1)

    def test_timeout_with_existing_conversation_does_not_start_another_generation(self):
        account = mock.Mock()
        account.get_available_access_token.return_value = "fixture-token"
        account.get_account.return_value = {"user_id": "fixture-user"}
        account.image_account_ref.return_value = "fixture-account-ref"
        updates = []
        request = conversation.ConversationRequest(model="gpt-image-2.6-sunburst")
        request.context_callback = updates.append
        with (
            mock.patch.object(conversation, "account_service", account),
            mock.patch.object(conversation, "OpenAIBackendAPI"),
            mock.patch.object(conversation, "stream_image_outputs", side_effect=ImagePollTimeoutError("图片轮询超时", "fixture-conversation")) as generate,
            mock.patch.object(conversation, "_remove_image_conversation_later"),
        ):
            with self.assertRaises(ImagePollTimeoutError):
                conversation._generate_single_image(request, 1, 1)
        self.assertEqual(generate.call_count, 1)
        account.mark_image_result.assert_called_once_with("fixture-token", False)
        self.assertTrue(any(item.get("conversation_id") == "fixture-conversation" for item in updates))


if __name__ == "__main__":
    unittest.main()
