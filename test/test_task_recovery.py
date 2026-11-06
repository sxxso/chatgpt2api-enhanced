from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from services.account_service import AccountService
from services.openai_backend_api import ImagePollTimeoutError
from services.image_task_service import ImageTaskService
from services.protocol import conversation
from services.storage.json_storage import JSONStorageBackend
from test.test_image_task_service import OWNER, OTHER_OWNER, wait_for_task


class TaskRecoveryTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("services.image_task_service.log_service")
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_task_file(self, root, status="error"):
        path = root / "tasks.json"
        path.write_text(json.dumps({"tasks": [{"id": "fixture-task", "owner_id": OWNER["id"], "status": status, "mode": "generate", "model": "gpt-image-2.6-sunburst", "account_ref": "fixture-ref", "conversation_id": "fixture-conversation", "prompt": "blue circle", "base_url": "http://local.test", "response_format": "url", "error": "图片轮询超时", "created_at": "2099-01-01 00:00:00", "updated_at": "2099-01-01 00:00:00"}]}), encoding="utf-8")
        return path

    def test_timeout_context_survives_restart_and_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(self.make_task_file(Path(directory), "running"))
            task = service.list_tasks(OWNER, ["fixture-task"])["items"][0]
            self.assertEqual(task["conversation_id"], "fixture-conversation")
            self.assertTrue(task["can_resume"])
            self.assertNotIn("account_ref", task)
            self.assertNotIn("prompt", task)

    def test_original_account_is_used_for_polling_and_no_generation_is_submitted(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(self.make_task_file(Path(directory)))
            with (
                mock.patch("services.image_task_service.account_service", create=True) as accounts,
                mock.patch("services.openai_backend_api.OpenAIBackendAPI") as factory,
                mock.patch.object(conversation, "format_image_result", return_value={"data": [{"url": "http://local.test/images/fixture.png"}]}) as format_result,
                mock.patch.object(service, "generation_handler") as generate,
            ):
                accounts.get_image_account_token.return_value = "rotated-fixture-token"
                factory.return_value._poll_image_results.return_value = (["fixture-file"], [])
                factory.return_value.resolve_conversation_image_urls.return_value = ["https://image.test/fixture"]
                factory.return_value.download_image_bytes.return_value = [b"fixture-image"]
                service.resume_poll(OWNER, "fixture-task", 10)
                task = wait_for_task(service, OWNER, "fixture-task", "success")
                accounts.get_image_account_token.assert_called_once_with("fixture-ref")
                factory.assert_called_once_with(access_token="rotated-fixture-token")
                self.assertEqual(format_result.call_args.args[3], "http://local.test")
                generate.assert_not_called()
                self.assertFalse(task["can_resume"])

    def test_foreign_owner_cannot_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(self.make_task_file(Path(directory)))
            with self.assertRaises(ValueError):
                service.resume_poll(OTHER_OWNER, "fixture-task", 10)

    def test_duplicate_resume_does_not_start_a_second_poll_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(self.make_task_file(Path(directory)))
            with mock.patch("services.image_task_service.threading.Thread") as thread:
                service.resume_poll(OWNER, "fixture-task", 10)
                service.resume_poll(OWNER, "fixture-task", 10)
                self.assertEqual(thread.return_value.start.call_count, 1)

    def test_legacy_task_without_an_account_cannot_switch_to_a_random_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_task_file(Path(directory))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["tasks"][0].pop("account_ref")
            path.write_text(json.dumps(data), encoding="utf-8")
            service = ImageTaskService(path)
            with mock.patch("services.openai_backend_api.OpenAIBackendAPI") as factory:
                with self.assertRaises(ValueError):
                    service.resume_poll(OWNER, "fixture-task", 10)
                factory.assert_not_called()

    def test_resumed_result_is_accounted_once_without_releasing_active_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            path.write_text(json.dumps([{"access_token": "fixture-token", "status": "正常", "quota": 5}]), encoding="utf-8")
            accounts = AccountService(JSONStorageBackend(path))
            accounts._image_inflight["fixture-token"] = 2
            accounts.mark_image_result("fixture-token", True, release_slot=False, result_id="fixture-completion")
            accounts.mark_image_result("fixture-token", True, release_slot=False, result_id="fixture-completion")
            self.assertEqual(accounts._image_inflight["fixture-token"], 2)
            self.assertEqual(accounts.get_account("fixture-token")["success"], 1)
            self.assertEqual(accounts.get_account("fixture-token")["quota"], 4)
            restored = AccountService(JSONStorageBackend(path))
            restored.mark_image_result("fixture-token", True, release_slot=False, result_id="fixture-completion")
            self.assertEqual(restored.get_account("fixture-token")["quota"], 4)

    def test_context_is_saved_before_a_timeout_and_contains_no_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            def generate(payload):
                payload["context_callback"]({"account_ref": "fixture-ref", "conversation_id": "fixture-conversation"})
                raise ImagePollTimeoutError("图片轮询超时", "fixture-conversation")
            service = ImageTaskService(path, generation_handler=generate)
            service.submit_generation(OWNER, client_task_id="fixture-task", prompt="blue circle", model="gpt-image-2.6-sunburst", size=None)
            wait_for_task(service, OWNER, "fixture-task", "error")
            restored = ImageTaskService(path)
            self.assertTrue(restored.list_tasks(OWNER, ["fixture-task"])["items"][0]["can_resume"])
            self.assertNotIn("access_token", path.read_text(encoding="utf-8"))

    def test_account_reference_survives_access_token_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            path.write_text(json.dumps([{"access_token": "old-token", "user_id": "fixture-user", "source_type": "web", "status": "正常", "quota": 2}]), encoding="utf-8")
            accounts = AccountService(JSONStorageBackend(path))
            reference = accounts.image_account_ref("old-token")
            account = accounts.get_account("old-token")
            account["access_token"] = "new-token"
            path.write_text(json.dumps([account]), encoding="utf-8")
            restored = AccountService(JSONStorageBackend(path))
            with mock.patch.object(restored, "refresh_access_token", side_effect=lambda token, **kwargs: token):
                self.assertEqual(restored.get_image_account_token(reference), "new-token")


if __name__ == "__main__":
    unittest.main()
