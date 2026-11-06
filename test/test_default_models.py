from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import api.ai as ai_module
import api.image_tasks as image_tasks_module
from services.config import config
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import anthropic_v1_messages, openai_search, openai_v1_chat_complete, openai_v1_response, web_search_tool
from services.protocol.chat_completion_cache import chat_completion_cache


class DefaultModelTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(config.data, {"default_text_model": "gpt-6", "default_search_model": "gpt-6", "default_image_model": "gpt-image-2.6-sunburst"})
        patcher.start()
        self.addCleanup(patcher.stop)
        chat_completion_cache.clear()
        self.addCleanup(chat_completion_cache.clear)

    def test_missing_and_blank_models_use_defaults(self):
        for value in (None, "", "  "):
            self.assertEqual(config.resolve_text_model(value), "gpt-6")
            self.assertEqual(config.resolve_search_model(value), "gpt-6")
            self.assertEqual(config.resolve_image_model(value), "gpt-image-2.6-sunburst")

    def test_explicit_models_and_auto_override_defaults(self):
        self.assertEqual(config.resolve_text_model("gpt-6-mini"), "gpt-6-mini")
        self.assertEqual(config.resolve_search_model("auto"), "auto")
        self.assertEqual(config.resolve_image_model("gpt-image-2.6-flare"), "gpt-image-2.6-flare")

    def test_image_api_and_task_defaults_preserve_explicit_overrides(self):
        def echo_model(payload):
            return {"model": payload["model"]}

        app = FastAPI()
        app.include_router(ai_module.create_router())
        app.include_router(image_tasks_module.create_router())
        client = TestClient(app)
        with (
            mock.patch.object(ai_module, "require_identity", return_value={"id": "test", "role": "admin"}),
            mock.patch.object(image_tasks_module, "require_identity", return_value={"id": "test", "role": "admin"}),
            mock.patch.object(ai_module, "filter_or_log", new=mock.AsyncMock()),
            mock.patch.object(image_tasks_module, "filter_or_log", new=mock.AsyncMock()),
            mock.patch.object(ai_module, "LoggedCall") as logged,
            mock.patch.object(image_tasks_module, "LoggedCall"),
            mock.patch.object(ai_module.openai_v1_image_generations, "handle", side_effect=echo_model),
            mock.patch.object(ai_module.openai_v1_image_edit, "handle", side_effect=echo_model),
            mock.patch.object(image_tasks_module.image_task_service, "submit_generation", side_effect=lambda _identity, **kwargs: {"model": kwargs["model"]}),
        ):
            logged.return_value.run = mock.AsyncMock(side_effect=lambda handler, payload: handler(payload))
            for override in (None, "gpt-image-2.6-flare"):
                expected = override or "gpt-image-2.6-sunburst"
                fields = {"model": override} if override else {}
                for endpoint, payload in (
                    ("/v1/images/generations", {"prompt": "cat", **fields}),
                    ("/v1/images/edits", {"prompt": "cat", "image": "data:image/png;base64,aW1hZ2U=", **fields}),
                    ("/api/image-tasks/generations", {"prompt": "cat", "client_task_id": "test", **fields}),
                ):
                    with self.subTest(endpoint=endpoint, model=expected):
                        response = client.post(endpoint, json=payload)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["model"], expected)

    def test_image_chat_and_responses_use_configured_default(self):
        self.assertEqual(openai_v1_chat_complete.chat_image_args({"prompt": "cat", "modalities": ["image"]})[0], "gpt-image-2.6-sunburst")
        with (
            mock.patch.object(openai_v1_response, "stream_image_outputs_with_pool", return_value=iter([])) as generate,
            mock.patch.object(openai_v1_response, "stream_image_response", return_value=iter([])),
        ):
            list(openai_v1_response.response_events({"input": "cat", "tools": [{"type": "image_generation"}]}))
        self.assertEqual(generate.call_args.args[0].model, "gpt-image-2.6-sunburst")

    def test_chat_and_responses_use_configured_default(self):
        self.assertEqual(openai_v1_chat_complete.text_chat_parts({"messages": [{"role": "user", "content": "hello"}]})[0], "gpt-6")
        self.assertEqual(openai_v1_response.text_response_parts({"input": "hello"})[0], "gpt-6")

    def test_messages_selects_an_account_for_the_default_model(self):
        with mock.patch.object(anthropic_v1_messages.account_service, "get_text_access_token", return_value="test-token") as selector, mock.patch.object(anthropic_v1_messages, "OpenAIBackendAPI"):
            request = anthropic_v1_messages.message_request({"messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual(request.model, "gpt-6")
        selector.assert_called_once_with(model="gpt-6")

    def test_default_change_does_not_reuse_previous_model_cache(self):
        body = {"messages": [{"role": "user", "content": "default cache isolation"}]}
        with mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()) as selector, mock.patch.object(openai_v1_chat_complete, "collect_text", return_value="OK"):
            first = openai_v1_chat_complete.handle(body)
            config.data["default_text_model"] = "gpt-6-mini"
            second = openai_v1_chat_complete.handle(body)
        self.assertEqual(first["model"], "gpt-6")
        self.assertEqual(second["model"], "gpt-6-mini")
        self.assertEqual(selector.call_args_list, [mock.call("gpt-6"), mock.call("gpt-6-mini")])

    def test_search_selects_and_passes_configured_model(self):
        with mock.patch.object(web_search_tool.account_service, "get_text_access_token", return_value="test-token") as selector, mock.patch.object(web_search_tool.account_service, "get_account", return_value={}), mock.patch.object(web_search_tool.account_service, "mark_text_used"), mock.patch.object(web_search_tool, "OpenAIBackendAPI") as factory:
            factory.return_value.search.return_value = {"answer": "result"}
            openai_search.handle({"prompt": "query"})
        selector.assert_called_once_with(model="gpt-6")
        factory.return_value.search.assert_called_once_with("query", model="gpt-6")
        factory.return_value.close.assert_called_once()

    def test_search_alias_uses_configured_website_model(self):
        with mock.patch.object(web_search_tool.account_service, "get_text_access_token", return_value="test-token") as selector, mock.patch.object(web_search_tool.account_service, "get_account", return_value={}), mock.patch.object(web_search_tool.account_service, "mark_text_used"), mock.patch.object(web_search_tool, "OpenAIBackendAPI") as factory:
            factory.return_value.search.return_value = {"answer": "result"}
            web_search_tool.run_web_search("query", model="gpt-5-search-api-2026-06-01")
        selector.assert_called_once_with(model="gpt-6")

    def test_search_compatibility_handlers_forward_model(self):
        result = {"answer": "result", "sources": []}
        with mock.patch.object(openai_v1_chat_complete, "run_web_search", return_value=result) as search:
            openai_v1_chat_complete.handle({"messages": [{"role": "user", "content": "query"}], "tools": [{"type": "web_search"}]})
        search.assert_called_once_with("query", model="gpt-6")
        with mock.patch.object(openai_v1_response, "run_web_search", return_value=result) as search:
            openai_v1_response.handle({"input": "query", "tools": [{"type": "web_search"}]})
        search.assert_called_once_with("query", model="gpt-6")

    def test_backend_search_uses_default_in_prepare_and_generation(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.session = mock.Mock()
        backend.access_token = "test-token"
        backend._prepare_search_conversation = mock.Mock(return_value="conduit")
        backend._bootstrap = mock.Mock()
        backend._run_search_conversation = mock.Mock(return_value="conversation")
        backend._wait_search_result = mock.Mock(return_value={"answer": "result"})
        result = backend.search("query")
        backend._prepare_search_conversation.assert_called_once_with("query", "gpt-6")
        backend._run_search_conversation.assert_called_once_with("query", "conduit", "gpt-6")
        self.assertEqual(result["requested_model"], "gpt-6")

    def test_search_reports_observed_model_without_inventing_one(self):
        backend = object.__new__(OpenAIBackendAPI)
        backend.session = mock.Mock()
        message = {"author": {"role": "assistant"}, "metadata": {"model_slug": "gpt-6"}, "content": {"content_type": "text", "parts": ["answer"]}}
        result = backend._extract_search_result("test", {"mapping": {"m": {"message": message}}})
        self.assertEqual(result["model"], "gpt-6")
        message["metadata"] = {}
        self.assertEqual(backend._extract_search_result("test", {"mapping": {"m": {"message": message}}})["model"], "")


if __name__ == "__main__":
    unittest.main()
