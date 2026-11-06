"""Run offline reliability checks with an isolated data directory and no real accounts."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ["CHATGPT2API_AUTH_KEY"] = "chatgpt2api"
    os.environ["STORAGE_BACKEND"] = "json"
    with tempfile.TemporaryDirectory(prefix="chatgpt2api-checks-") as directory:
        settings = importlib.import_module("services.config")
        settings.DATA_DIR = Path(directory)
        settings.BACKUP_STATE_FILE = settings.DATA_DIR / "backup_state.json"
        settings.CONFIG_FILE = settings.DATA_DIR / "config.json"
        settings.CONFIG_FILE.write_text(json.dumps({"auth-key": "chatgpt2api", "default_image_model": "gpt-image-2.6-sunburst", "log_levels": ["error"]}), encoding="utf-8")
        settings.config = settings.ConfigStore(settings.CONFIG_FILE)
        names = sys.argv[1:] or [
            "test.test_local_setup",
            "test.test_release_packaging",
            "test.test_image_reliability",
            "test.test_task_recovery",
            "test.test_json_storage_reliability",
            "test.test_image_task_service",
            "test.test_image_tasks_api",
            "test.test_image25_routing",
            "test.test_default_models",
            "test.test_account_image_capabilities",
            "test.test_text_model_routing",
            "test.test_multi_image_results",
            "test.test_v1_images_edits_api",
            "test.test_v1_images_edits_json",
            "test.test_image_base_url_api",
            "test.test_chat_completion_cache",
            "test.test_model_availability",
            "test.test_image_queue",
            "test.test_user_image_policy",
            "test.test_v1_models.ModelListTests.test_list_models_only_returns_image_models_backed_by_account_types",
            "test.test_v1_models.ModelListTests.test_list_models_does_not_return_codex_models_for_web_plus_accounts",
        ]
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
