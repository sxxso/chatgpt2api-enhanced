"""Local-only browser fixture: generate a timeout, then recover a saved PNG.

No real accounts or upstream requests are used. The fixture stops after five
minutes (15 in queue mode), or POST /__fixture/stop. Login key: chatgpt2api.
"""
from __future__ import annotations

import importlib
import argparse
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", action="store_true", help="Hold the first image until POST /__fixture/release for queue UI checks")
    parser.add_argument("--users", action="store_true", help="Seed two local-only limited users and complete images immediately")
    args = parser.parse_args()
    os.environ["CHATGPT2API_AUTH_KEY"] = "chatgpt2api"
    os.environ["STORAGE_BACKEND"] = "json"
    with tempfile.TemporaryDirectory(prefix="chatgpt2api-ui-check-") as directory:
        settings = importlib.import_module("services.config")
        settings.DATA_DIR = Path(directory)
        settings.BACKUP_STATE_FILE = settings.DATA_DIR / "backup_state.json"
        settings.CONFIG_FILE = settings.DATA_DIR / "config.json"
        settings.CONFIG_FILE.write_text(json.dumps({"auth-key": "chatgpt2api", "default_image_model": "gpt-image-2.6-sunburst", "image_remove_conversation_always": False, "image_remove_conversation_after_result": False, "log_levels": ["error"]}), encoding="utf-8")
        settings.config = settings.ConfigStore(settings.CONFIG_FILE)
        settings.config.data.update({"image_global_concurrency": 1, "image_queue_capacity": 2})
        checks_dir = settings.DATA_DIR / "model-checks"
        checks_dir.mkdir()
        (checks_dir / "fixture.json").write_text(json.dumps({"mode": "web-image", "requested_model": "gpt-image-2.6-sunburst",
            "status": "image_generated", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "assistant_model_ids": ["gpt-image-2.6-sunburst"]}), encoding="utf-8")
        (settings.DATA_DIR / "accounts.json").write_text(json.dumps([{"access_token": "fixture-token", "user_id": "fixture-user", "status": "正常", "source_type": "web", "quota": 10}]), encoding="utf-8")
        if args.users:
            import hashlib
            (settings.DATA_DIR / "auth_keys.json").write_text(json.dumps([
                {"id": f"fixture-user-{name.lower()}", "name": f"fixture {name}", "role": "user", "enabled": True,
                 "key_hash": hashlib.sha256(f"fixture-only-user-{name}".encode()).hexdigest(), "daily_image_limit": 1,
                 "max_image_concurrency": 1, "allowed_image_models": ["gpt-image-2.6-sunburst"] if name == "A" else None}
                for name in ("A", "B")]), encoding="utf-8")
        from PIL import Image, ImageDraw
        from services.protocol import conversation
        import services.openai_backend_api as backend_module
        from services.account_service import account_service
        from services.model_service import model_catalog_service
        from services.openai_backend_api import ImagePollTimeoutError
        from api import create_app
        import uvicorn

        canvas = Image.new("RGB", (256, 256), "white")
        ImageDraw.Draw(canvas).ellipse((32, 32, 224, 224), fill="blue")
        output = BytesIO()
        canvas.save(output, format="PNG")
        observations = {"generation_attempts": 0, "resume_polls": 0}
        release = threading.Event()
        if args.users:
            release.set()

        class FixtureBackend:
            def __init__(self, access_token=""):
                if access_token != "fixture-token":
                    raise RuntimeError("Unexpected fixture account")

            def _poll_image_results(self, conversation_id, timeout):
                observations["resume_polls"] += 1
                time.sleep(2)
                return ["fixture-file"], []

            def resolve_conversation_image_urls(self, *args, **kwargs):
                return ["fixture-image"]

            def download_image_bytes(self, urls):
                return [output.getvalue()]

            def close(self):
                pass

        def interrupted_generation(backend, request, index=1, total=1):
            observations["generation_attempts"] += 1
            if args.queue:
                request.progress_callback("image_stream_resolve_start")
                if not release.wait(600):
                    raise RuntimeError("fixture wait expired")
                data = conversation.format_image_result([{"b64_json": base64.b64encode(output.getvalue()).decode("ascii")}],
                    request.prompt, request.response_format, request.base_url, int(time.time()))["data"]
                yield conversation.ImageOutput(kind="result", model=request.model, index=index, total=total, data=data)
                return
            yield conversation.ImageOutput(kind="progress", model=request.model, index=index, total=total, text="fixture progress", conversation_id="fixture-conversation")
            raise ImagePollTimeoutError("测试任务已超时，可继续等待原任务结果", "fixture-conversation")

        conversation.stream_image_outputs = interrupted_generation
        conversation.OpenAIBackendAPI = FixtureBackend
        backend_module.OpenAIBackendAPI = FixtureBackend
        account_service.get_available_access_token = lambda **kwargs: account_service._acquire_next_candidate_token()
        account_service.refresh_access_token = lambda token, **kwargs: token
        account_service.refresh_accounts = lambda *args, **kwargs: {"items": account_service.list_accounts(), "errors": []}
        model_catalog_service.list_models = lambda: {"object": "list", "data": []}
        app = create_app()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=3001, log_level="warning", access_log=False))

        @app.post("/__fixture/status")
        async def fixture_status():
            return {**observations, "accounts": [{"quota": a.get("quota"), "inflight": a.get("inflight", 0)} for a in account_service.list_accounts()]}

        @app.post("/__fixture/empty-models")
        async def empty_models():
            (checks_dir / "fixture.json").write_text("{}", encoding="utf-8")
            return {"empty": True}

        @app.post("/__fixture/stop")
        async def stop_fixture():
            server.should_exit = True
            release.set()
            return {"stopping": True}

        @app.post("/__fixture/release")
        async def release_fixture():
            release.set()
            return {"released": True}

        timer = threading.Timer(900 if args.queue else 300, lambda: setattr(server, "should_exit", True))
        timer.daemon = True
        timer.start()
        print("Isolated browser fixture: http://127.0.0.1:3001/image/ (no upstream calls)", flush=True)
        try:
            server.run()
        finally:
            release.set()
            timer.cancel()


if __name__ == "__main__":
    main()
