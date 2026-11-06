from __future__ import annotations

import tempfile
import json
import time
from datetime import datetime, timezone
import unittest
from pathlib import Path
from unittest import mock

from services.model_probe_service import model_check_is_usable, read_model_checks, read_usable_model_ids
from services.protocol import openai_v1_models


class ModelAvailabilityTests(unittest.TestCase):
    def write_probe(self, root, name, model="gpt-6", *, age=0, mode="text", status="responded", **extra):
        payload = {"mode": mode, "requested_model": model, "status": status,
                   "checked_at_utc": datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat(),
                   "assistant_model_ids": [model], **extra}
        (root / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_expired_success_does_not_keep_model_in_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_probe(root, "old.json", age=8 * 86400)
            self.assertEqual(read_usable_model_ids(root), set())
            self.assertEqual(read_model_checks(root)["items"][0]["availability"], "expired")

    def test_all_reports_considered_and_no_data_means_no_verified_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(read_usable_model_ids(root), set())
            self.write_probe(root, "a-verified.json")
            for index in range(205):
                self.write_probe(root, f"z-{index}.json", model="failing", status="error")
            self.assertEqual(read_usable_model_ids(root), {"gpt-6"})

    def test_native_rejection_cannot_override_the_web_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_probe(root, "web.json", "gpt-image-2.6-sunburst", age=60, mode="web-image", status="image_generated")
            self.write_probe(root, "native.json", "gpt-image-2.6-sunburst", mode="image", status="rejected", http_status=403)
            self.assertEqual(read_usable_model_ids(root), {"gpt-image-2.6-sunburst"})

    def test_echoed_request_is_not_model_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_probe(root, "echo.json", model_evidence=[
                {"role": "system", "path": "$.metadata.model_slug", "model": "gpt-6"}], returned_model_ids=["gpt-6"])
            self.assertEqual(read_usable_model_ids(root), set())

    def test_corrupt_missing_date_and_future_reports_are_not_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.json").write_text("{", encoding="utf-8")
            self.write_probe(root, "missing.json", checked_at_utc=None)
            self.write_probe(root, "future.json", "other-model", age=-3600)
            self.assertEqual(read_usable_model_ids(root), set())

    def test_saved_success_does_not_create_models_with_no_current_route(self):
        with (mock.patch.object(openai_v1_models.model_catalog_service, "list_models", return_value={"data": []}),
              mock.patch.object(openai_v1_models.account_service, "list_accounts", return_value=[]),
              mock.patch.object(openai_v1_models, "read_usable_model_ids", return_value={"orphan-model", "gpt-image-2.6-sunburst"})):
            self.assertEqual(openai_v1_models.list_models()["data"], [])

    def test_latest_report_wins_by_checked_at_not_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z-old.json").write_text(
                '{"mode":"text","requested_model":"gpt-6","status":"responded","checked_at_utc":"2026-11-05T00:00:00+00:00","assistant_model_ids":["gpt-6"]}',
                encoding="utf-8",
            )
            (root / "a-new.json").write_text(
                '{"mode":"text","requested_model":"gpt-6","status":"error","http_status":403,"checked_at_utc":"2026-11-06T00:00:00+00:00"}',
                encoding="utf-8",
            )

            result = read_model_checks(root)
            usable = read_usable_model_ids(root)

        self.assertEqual(result["items"][0]["status"], "error")
        self.assertEqual(usable, set())

    def test_text_and_image_matching_rules(self) -> None:
        self.assertTrue(model_check_is_usable({"mode": "text", "model": "gpt-6", "status": "responded", "actual_models": ["gpt-6"]}))
        self.assertFalse(model_check_is_usable({"mode": "text", "model": "gpt-6", "status": "responded_with_different_model", "actual_models": ["gpt-5-6"]}))
        self.assertTrue(model_check_is_usable({"mode": "web-image", "model": "gpt-image-2.6-sunburst", "status": "image_generated_model_unverified", "actual_models": ["gpt-image-2.6-sunburst"]}))
        self.assertTrue(model_check_is_usable({"mode": "web-image", "model": "gpt-image-2", "status": "image_generated_legacy_route", "actual_models": []}))
        self.assertFalse(model_check_is_usable({"mode": "web-image", "model": "gpt-image-2.6-sunburst", "status": "error", "actual_models": []}))
        self.assertFalse(model_check_is_usable({"mode": "text", "model": "gpt-6", "status": "responded", "actual_models": ["gpt-6", "other"]}))

    def test_default_model_change_rejects_unverified_without_altering_config(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import system
        from services.config import config
        app = FastAPI()
        app.include_router(system.create_router("test"))
        previous = config.default_image_model
        with mock.patch("services.model_probe_service.read_usable_model_ids", return_value={"gpt-6"}):
            response = TestClient(app).post("/api/settings", json={"default_image_model": "unknown"},
                headers={"Authorization": "Bearer chatgpt2api"})
            self.assertEqual(response.status_code, 400)
            response = TestClient(app).post("/api/settings", json={"default_image_model": "gpt-6"},
                headers={"Authorization": "Bearer chatgpt2api"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(config.default_image_model, previous)

    def test_raw_catalog_requires_admin(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import system
        app = FastAPI()
        app.include_router(system.create_router("test"))
        with mock.patch("services.auth_service.auth_service.authenticate", return_value={"id": "user", "role": "user"}):
            response = TestClient(app).get("/api/models/raw", headers={"Authorization": "Bearer test-user"})
        self.assertEqual(response.status_code, 403)

    def test_public_catalog_filters_rejected_and_fallback_models(self) -> None:
        raw = {
            "object": "list",
            "data": [
                {"id": "gpt-6", "object": "model"},
                {"id": "gpt-6-thinking", "object": "model"},
                {"id": "gpt-image-2.6-sunburst", "object": "model"},
            ],
        }
        with (
            mock.patch.object(openai_v1_models.model_catalog_service, "list_models", return_value=raw),
            mock.patch.object(openai_v1_models.account_service, "list_accounts", return_value=[]),
            mock.patch.object(openai_v1_models, "read_usable_model_ids", return_value={"gpt-6", "gpt-image-2.6-sunburst"}),
        ):
            result = openai_v1_models.list_models()

        self.assertEqual(
            [item["id"] for item in result["data"]],
            ["gpt-6", "gpt-image-2.6-sunburst"],
        )


if __name__ == "__main__":
    unittest.main()
