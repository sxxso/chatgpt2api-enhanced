from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest import mock

from services.auth_service import AuthService
from services.user_image_policy import UserImagePolicy, UserImagePolicyError


MODEL = "gpt-image-2.6-sunburst"


class KeyStorage:
    def __init__(self):
        self.items = []
        self.fail = False

    def load_auth_keys(self):
        return deepcopy(self.items)

    def save_auth_keys(self, items):
        if self.fail:
            raise OSError("disk full")
        self.items = deepcopy(items)


class UserImagePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = KeyStorage()
        self.auth = AuthService(self.storage)
        self.identity, self.key = self.auth.create_key(role="user", daily_image_limit=2, max_image_concurrency=1)
        self.day = "2026-11-06"
        self.path = Path(self.directory.name) / "usage.json"
        self.policy = UserImagePolicy(self.path, self.auth.image_policy, lambda: self.day)

    def test_quota_success_failure_and_restart_deduplication(self):
        with self.policy.acquire(self.identity, MODEL, "one") as complete:
            complete()
        with self.assertRaises(RuntimeError):
            with self.policy.acquire(self.identity, MODEL, "failed"):
                raise RuntimeError("upstream failed")
        with self.policy.acquire(self.identity, MODEL, "two") as complete:
            complete()
        with self.assertRaises(UserImagePolicyError):
            with self.policy.acquire(self.identity, MODEL, "third"):
                self.fail("quota bypassed")
        reloaded = UserImagePolicy(self.path, self.auth.image_policy, lambda: self.day)
        with reloaded.acquire(self.identity, MODEL, "two") as complete:
            complete()
        self.assertEqual(reloaded.usage(self.identity["id"]), {"day": self.day, "used": 2, "active": 0})

    def test_concurrency_and_other_user_independent(self):
        other, _ = self.auth.create_key(role="user", max_image_concurrency=1)
        with self.policy.acquire(self.identity, MODEL, "one"):
            with self.assertRaises(UserImagePolicyError):
                with self.policy.acquire(self.identity, MODEL, "two"):
                    self.fail("concurrency bypassed")
            with self.policy.acquire(other, MODEL, "other") as complete:
                complete()
        self.assertEqual(self.policy.usage(self.identity["id"])["active"], 0)

    def test_day_rollover_keeps_result_deduplication(self):
        with self.policy.acquire(self.identity, MODEL, "one") as complete:
            complete()
        self.day = "2026-11-07"
        self.assertEqual(self.policy.usage(self.identity["id"])["used"], 0)
        with self.policy.acquire(self.identity, MODEL, "one") as complete:
            complete()
        self.assertEqual(self.policy.usage(self.identity["id"])["used"], 0)

    def test_live_policy_changes_apply_to_queued_identity(self):
        self.auth.update_key(self.identity["id"], {"allowed_image_models": []})
        with self.assertRaises(UserImagePolicyError):
            self.policy.check(self.identity, MODEL)
        self.auth.update_key(self.identity["id"], {"allowed_image_models": None, "enabled": False})
        with self.assertRaises(UserImagePolicyError):
            self.policy.check(self.identity, MODEL)
        self.auth.update_key(self.identity["id"], {"enabled": True, "expires_at": "2000-01-01T00:00:00Z"})
        self.assertIsNone(self.auth.authenticate(self.key))
        with self.assertRaises(UserImagePolicyError):
            self.policy.check(self.identity, MODEL)
        self.auth.update_key(self.identity["id"], {"expires_at": None})
        self.assertIsNotNone(self.auth.authenticate(self.key))
        self.auth.delete_key(self.identity["id"])
        with self.assertRaises(UserImagePolicyError):
            self.policy.check(self.identity, MODEL)

    def test_invalid_policy_and_failed_key_writes_do_not_change_memory(self):
        for update in ({"daily_image_limit": -1}, {"max_image_concurrency": True},
                       {"allowed_image_models": ["unknown"]}, {"expires_at": "2026-11-07"}):
            with self.assertRaises(ValueError):
                self.auth.update_key(self.identity["id"], update)
        self.storage.fail = True
        for operation in (lambda: self.auth.create_key(role="user"),
                          lambda: self.auth.update_key(self.identity["id"], {"key": "replacement", "enabled": False}),
                          lambda: self.auth.delete_key(self.identity["id"])):
            with self.assertRaises(OSError):
                operation()
            self.assertIsNotNone(self.auth.authenticate(self.key))
        self.assertEqual(len(self.auth._items), 1)

    def test_write_failure_does_not_corrupt_usage_or_leak_slot(self):
        with mock.patch("services.user_image_policy.write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                with self.policy.acquire(self.identity, MODEL, "one") as complete:
                    complete()
        self.assertEqual(self.policy.usage(self.identity["id"])["active"], 0)
        self.assertEqual(self.policy.usage(self.identity["id"])["used"], 0)

    def test_parallel_requests_cannot_overdraw_reserved_quota(self):
        self.auth.update_key(self.identity["id"], {"max_image_concurrency": 32})
        def attempt(index):
            try:
                with self.policy.acquire(self.identity, MODEL, str(index)) as complete:
                    complete()
                return True
            except UserImagePolicyError:
                return False
        with ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(sum(pool.map(attempt, range(32))), 2)

    def test_common_generator_blocks_before_upstream_and_preserves_admin(self):
        from services.protocol import conversation
        self.auth.update_key(self.identity["id"], {"allowed_image_models": []})
        request = conversation.ConversationRequest(model=MODEL, image_identity=self.identity)
        with mock.patch("services.user_image_policy.user_image_policy", self.policy), mock.patch.object(conversation, "_generate_single_image_with_capacity") as upstream:
            with self.assertRaises(conversation.ImageGenerationError) as raised:
                conversation._generate_single_image(request, 1, 1)
            self.assertEqual(raised.exception.status_code, 403)
            upstream.assert_not_called()
            request.n = 2
            with self.assertRaises(conversation.ImageGenerationError) as multi_error:
                list(conversation.stream_image_outputs_with_pool(request))
            self.assertEqual(multi_error.exception.status_code, 403)
            request.image_identity = {"id": "admin", "role": "admin"}
            conversation._generate_single_image(request, 1, 1)
            upstream.assert_called_once()

    def test_protocols_propagate_authenticated_identity(self):
        from services.protocol import openai_v1_image_generations, openai_v1_image_edit, openai_v1_chat_complete, openai_v1_response
        body = {"model": MODEL, "prompt": "test", "messages": [{"role": "user", "content": "test"}], "input": "test",
                "images": [(b"fixture", "reference.png", "image/png")], "tools": [{"type": "image_generation"}],
                "_image_identity": self.identity, "_image_result_id": "original"}
        for module, call in ((openai_v1_image_generations, lambda: openai_v1_image_generations.handle(body)),
                             (openai_v1_image_edit, lambda: openai_v1_image_edit.handle(body)),
                             (openai_v1_chat_complete, lambda: openai_v1_chat_complete.image_chat_response(body)),
                             (openai_v1_chat_complete, lambda: list(openai_v1_chat_complete.image_chat_events(body))),
                             (openai_v1_response, lambda: list(openai_v1_response.response_events(body)))):
            seen = []
            def capture(request):
                seen.append(request)
                from services.protocol.conversation import ImageOutput
                return iter([ImageOutput(kind="result", model=MODEL, index=1, total=1, data=[{"b64_json": "aGVsbG8="}])])
            with mock.patch.object(module, "stream_image_outputs_with_pool", side_effect=capture):
                call()
            self.assertTrue(seen, module.__name__)
            self.assertEqual(seen[0].image_identity, self.identity)
            self.assertEqual(seen[0].image_result_id, "original")

    def test_http_forbidden_models_and_identity_spoof_cannot_bypass_limits(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import ai, support
        from api.errors import install_exception_handlers
        from services.protocol import conversation
        app = FastAPI()
        app.include_router(ai.create_router())
        install_exception_handlers(app)
        client = TestClient(app)
        headers = {"Authorization": "Bearer " + self.key}
        self.auth.update_key(self.identity["id"], {"allowed_image_models": [], "daily_image_limit": 1})
        with mock.patch.object(support, "auth_service", self.auth), mock.patch("services.user_image_policy.user_image_policy", self.policy), mock.patch.object(conversation, "_generate_single_image_with_capacity") as upstream:
            routes = [("/v1/images/generations", {"prompt": "test", "model": MODEL}),
                      ("/v1/images/edits", {"prompt": "test", "model": MODEL, "image": "data:image/png;base64,aGVsbG8="}),
                      ("/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "test"}]}),
                      ("/v1/responses", {"model": MODEL, "input": "test", "tools": [{"type": "image_generation"}]})]
            for path, body in routes:
                for stream in (False, True):
                    response = client.post(path, headers=headers, json={**body, "stream": stream})
                    self.assertEqual(response.status_code, 403, (path, response.text))
            upstream.assert_not_called()
            self.auth.update_key(self.identity["id"], {"allowed_image_models": None})
            upstream.return_value = [conversation.ImageOutput(kind="result", model=MODEL, index=1, total=1, data=[{"b64_json": "aGVsbG8="}])]
            body = {"model": MODEL, "messages": [{"role": "user", "content": "test"}],
                    "_image_identity": {"id": "admin", "role": "admin"}, "_image_result_id": "fake"}
            response = client.post("/v1/chat/completions", headers=headers, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post("/v1/chat/completions", headers=headers, json=body)
            self.assertEqual(response.status_code, 429, response.text)
            self.assertEqual(upstream.call_count, 1)

    def test_background_resumes_use_same_usage_id_as_generation(self):
        import hashlib
        from services.image_task_service import ImageTaskService
        from services.protocol import conversation
        from test.test_image_task_service import wait_for_task
        def handler(payload):
            request = conversation.ConversationRequest(model=MODEL, image_identity=payload["_image_identity"], image_result_id=payload["_image_result_id"])
            conversation._generate_single_image(request, 1, 1)
            # Model success was accounted but saving the task result was interrupted.
            payload["context_callback"]({"account_ref": "original-account", "conversation_id": "original-conversation"})
            raise TimeoutError("poll timeout")
        service = ImageTaskService(Path(self.directory.name) / "tasks.json", generation_handler=handler)
        from PIL import Image
        from io import BytesIO
        output = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
        backend = mock.Mock()
        backend._poll_image_results.return_value = (["file"], [])
        backend.resolve_conversation_image_urls.return_value = ["fixture"]
        backend.download_image_bytes.return_value = [output.getvalue()]
        with mock.patch("services.user_image_policy.user_image_policy", self.policy), mock.patch("services.image_task_service.user_image_policy", self.policy), mock.patch.object(conversation, "_generate_single_image_with_capacity", return_value=[conversation.ImageOutput(kind="result", model=MODEL, index=1, total=1, data=[{"url": "fixture"}])]):
            service.submit_generation(self.identity, client_task_id="one", prompt="test", model=MODEL, size=None)
            wait_for_task(service, self.identity, "one", "error")
            self.assertEqual(self.policy.usage(self.identity["id"])["used"], 1)
            with mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=backend), mock.patch("services.image_task_service.account_service"):
                service.resume_poll(self.identity, "one")
                wait_for_task(service, self.identity, "one", "success")
            self.assertEqual(self.policy.usage(self.identity["id"])["used"], 1)
        self.assertIn(hashlib.sha256(f"{self.identity['id']}:one".encode()).hexdigest() + ":1", self.policy._users[self.identity["id"]]["results"])

    def test_immediate_resume_waits_for_failed_worker_to_release_slot(self):
        from threading import Event
        from services.image_task_service import ImageTaskService
        from test.test_image_task_service import wait_for_task
        from test.test_image_queue import wait_idle
        entered, finish, resumed = Event(), Event(), Event()
        def handler(payload):
            payload["context_callback"]({"account_ref": "original", "conversation_id": "conversation"})
            raise TimeoutError("poll timeout")
        service = ImageTaskService(Path(self.directory.name) / "tasks.json", generation_handler=handler)
        def log(*args, **kwargs):
            entered.set()
            finish.wait(3)
        with mock.patch.object(service, "_log_call", side_effect=log), mock.patch.object(service, "_run_resume_poll_limited", side_effect=lambda *args: resumed.set()):
            admin = {"id": "admin", "role": "admin"}
            service.submit_generation(admin, client_task_id="race", prompt="test", model=MODEL, size=None)
            self.assertTrue(entered.wait(2))
            wait_for_task(service, admin, "race", "error")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(service.resume_poll, admin, "race")
                finish.set()
                self.assertIn(future.result(3)["status"], ("queued", "running"))
            self.assertTrue(resumed.wait(2))
            wait_idle(service)

    def test_empty_resume_download_does_not_consume_user_quota(self):
        from services.image_task_service import ImageTaskService
        from test.test_task_recovery import TaskRecoveryTests
        from test.test_image_queue import wait_idle
        from test.test_image_task_service import OWNER, wait_for_task
        path = TaskRecoveryTests().make_task_file(Path(self.directory.name))
        service = ImageTaskService(path)
        backend = mock.Mock()
        backend._poll_image_results.return_value = (["file"], [])
        backend.resolve_conversation_image_urls.return_value = ["fixture"]
        backend.download_image_bytes.return_value = []
        completed = mock.Mock()
        with mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=backend), mock.patch("services.image_task_service.account_service") as accounts:
            service._run_resume_poll(f"{OWNER['id']}:fixture-task", "fixture-conversation", 30, OWNER, "generate", MODEL, on_success=completed)
        completed.assert_not_called()
        accounts.mark_image_result.assert_not_called()
        self.assertIn("图片结果为空", service.list_tasks(OWNER, ["fixture-task"])["items"][0]["error"])
