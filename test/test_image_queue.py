from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.image_capacity import ImageCapacity, ImageCapacityError
from services.image_task_service import ImageTaskService
from test.test_image_task_service import OWNER, OTHER_OWNER, wait_for_task


def submit(service, task_id, owner=OWNER):
    return service.submit_generation(owner, client_task_id=task_id, prompt=task_id, model="gpt-image-2.6-sunburst", size=None)


def wait_idle(service):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = service.queue_status()
        if status["running"] == 0 and status["queued"] == 0:
            return
        time.sleep(0.01)
    raise AssertionError("workers did not finish")


class ImageQueueTests(unittest.TestCase):
    def test_per_user_concurrency_cannot_make_pending_queue_unbounded(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = Event()
            user = {"id": "limited-user", "role": "user"}
            def handler(payload):
                gate.wait(3)
                return {"data": [{"url": "http://fixture/image.png"}]}
            service = ImageTaskService(Path(directory) / "tasks.json", generation_handler=handler,
                                       queue_limits_getter=lambda: (4, 1, 30))
            try:
                with mock.patch("services.image_task_service.user_image_policy.concurrency", return_value=1):
                    submit(service, "first", user)
                    submit(service, "second", user)
                    with self.assertRaises(ImageCapacityError):
                        submit(service, "third", user)
                    self.assertEqual(service.queue_status()["queued"], 1)
                    self.assertEqual(service.queue_status()["running"], 1)
            finally:
                gate.set()
                wait_idle(service)

    def test_invalid_limits_rejected_without_config_change(self):
        from services.config import config
        previous = dict(config.data)
        for field, value in (("image_global_concurrency", 0), ("image_queue_capacity", -1),
                             ("image_queue_timeout_secs", 0), ("model_check_ttl_hours", "nan")):
            with self.assertRaises(ValueError):
                config.update({field: value})
        self.assertEqual(config.data, previous)

    def test_status_save_failure_rolls_back_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(Path(directory) / "tasks.json")
            with mock.patch("services.image_task_service.threading.Thread"):
                submit(service, "task")
            with mock.patch.object(service, "_save_locked", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    service._update_task("owner-1:task", status="success")
            self.assertEqual(service._tasks["owner-1:task"]["status"], "running")

    def test_bounded_fifo_duplicate_full_and_no_early_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            release, entered = Event(), Event()
            calls = []
            def handler(payload):
                calls.append(payload["prompt"])
                entered.set()
                release.wait(3)
                return {"data": [{"url": "http://test/image.png"}]}
            service = ImageTaskService(Path(directory) / "tasks.json", generation_handler=handler, queue_limits_getter=lambda: (1, 2, 30))
            try:
                submit(service, "first")
                self.assertTrue(entered.wait(2))
                self.assertEqual(submit(service, "second")["queue_position"], 1)
                self.assertEqual(submit(service, "third")["queue_position"], 2)
                self.assertEqual(submit(service, "second")["queue_position"], 1)
                with self.assertRaises(ImageCapacityError):
                    submit(service, "rejected")
                self.assertEqual(calls, ["first"])
                self.assertEqual(service.list_tasks(OWNER, ["rejected"])["missing_ids"], ["rejected"])
            finally:
                release.set()
                wait_idle(service)
            self.assertEqual(calls, ["first", "second", "third"])

    def test_failure_releases_worker_for_the_next_task(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = Event()
            def handler(payload):
                if payload["prompt"] == "bad":
                    gate.wait(2)
                    raise RuntimeError("fake failure")
                return {"data": [{"url": "http://test/image.png"}]}
            service = ImageTaskService(Path(directory) / "tasks.json", generation_handler=handler, queue_limits_getter=lambda: (1, 1, 30))
            submit(service, "bad")
            submit(service, "good")
            gate.set()
            wait_idle(service)
            self.assertEqual(service.list_tasks(OWNER, ["bad", "good"])["items"][1]["status"], "success")

    def test_cancellation_and_expiry_never_run_the_queued_job(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Event()
            calls = []
            def handler(payload):
                calls.append(payload["prompt"])
                release.wait(3)
                return {"data": [{"url": "http://test/image.png"}]}
            service = ImageTaskService(Path(directory) / "tasks.json", generation_handler=handler, queue_limits_getter=lambda: (1, 2, 30))
            try:
                submit(service, "first")
                submit(service, "cancel")
                submit(service, "expire")
                with self.assertRaises(ValueError):
                    service.cancel_queued(OTHER_OWNER, "cancel")
                self.assertEqual(service.cancel_queued(OWNER, "cancel")["status"], "error")
                with service._lock:
                    service._tasks["owner-1:expire"]["queued_ts"] -= 40
                expired = service.list_tasks(OWNER, ["expire"])["items"][0]
                self.assertIn("排队超时", expired["error"])
            finally:
                release.set()
                wait_idle(service)
            self.assertEqual(calls, ["first"])

    def test_no_queue_mode_and_failed_persistence_do_not_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(Path(directory) / "tasks.json", generation_handler=mock.Mock(), queue_limits_getter=lambda: (1, 0, 30))
            with mock.patch.object(service, "_save_locked", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    submit(service, "unsaved")
            self.assertEqual(service.list_tasks(OWNER, ["unsaved"])["missing_ids"], ["unsaved"])
            with mock.patch("services.image_task_service.threading.Thread"):
                submit(service, "first")
                with self.assertRaises(ImageCapacityError):
                    submit(service, "second")
            service.generation_handler.assert_not_called()

    def test_resume_shares_queue_and_is_idempotent_while_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{"id": "resume", "owner_id": "owner-1", "status": "error",
                "updated_at": "2099-01-01 00:00:00", "account_ref": "fake-ref", "conversation_id": "fake-conv", "recoverable": True}]}), encoding="utf-8")
            service = ImageTaskService(path, queue_limits_getter=lambda: (1, 1, 30))
            with mock.patch("services.image_task_service.threading.Thread") as threads:
                submit(service, "first")
                self.assertEqual(service.resume_poll(OWNER, "resume")["status"], "queued")
                self.assertEqual(service.resume_poll(OWNER, "resume")["queue_position"], 1)
                self.assertEqual(threads.return_value.start.call_count, 1)
                restored = ImageTaskService(path)
                self.assertTrue(restored.list_tasks(OWNER, ["resume"])["items"][0]["can_resume"])

    def test_api_full_returns_429_with_retry_header(self):
        import api.image_tasks as endpoints
        app = FastAPI()
        app.include_router(endpoints.create_router())
        with mock.patch.object(endpoints.image_task_service, "submit_generation", side_effect=ImageCapacityError("queue full")):
            response = TestClient(app).post("/api/image-tasks/generations", headers={"Authorization": "Bearer chatgpt2api"},
                                            json={"client_task_id": "one", "prompt": "test"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "5")


class ImageCapacityTests(unittest.TestCase):
    def test_shared_concurrency_is_bounded_and_returns_to_zero(self):
        capacity = ImageCapacity(lambda: (2, 20, 3))
        active, peak = 0, 0
        lock = Lock()
        def run():
            nonlocal active, peak
            with capacity.acquire():
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.01)
                with lock:
                    active -= 1
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: run(), range(16)))
        self.assertEqual(peak, 2)
        self.assertEqual(capacity.snapshot()["active"], 0)

    def test_overflow_timeout_and_exceptions_release_every_ticket(self):
        settings = [1, 0, 0.02]
        capacity = ImageCapacity(lambda: tuple(settings))
        with capacity.acquire():
            with self.assertRaises(ImageCapacityError):
                with capacity.acquire():
                    self.fail("full limiter admitted a request")
            settings[1] = 1
            with self.assertRaises(ImageCapacityError):
                with capacity.acquire():
                    self.fail("waiter should time out")
            self.assertEqual(capacity.snapshot()["waiting"], 0)
        with self.assertRaises(RuntimeError):
            with capacity.acquire():
                raise RuntimeError("fake upstream failure")
        with capacity.acquire():
            self.assertEqual(capacity.snapshot()["active"], 1)
        self.assertEqual(capacity.snapshot()["active"], 0)

    def test_rejected_generation_never_touches_an_account(self):
        from services.protocol import conversation
        capacity = ImageCapacity(lambda: (1, 0, 1))
        with capacity.acquire(), mock.patch.object(conversation, "image_capacity", capacity), mock.patch.object(conversation, "account_service") as accounts:
            with self.assertRaises(conversation.ImageGenerationError) as error:
                conversation._generate_single_image(conversation.ConversationRequest(prompt="test", model="gpt-image-2"), 1, 1)
        self.assertEqual(error.exception.status_code, 429)
        accounts.get_available_access_token.assert_not_called()
        accounts.mark_image_result.assert_not_called()
