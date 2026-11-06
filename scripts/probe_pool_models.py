"""Probe the current account pool without changing accounts or provider settings.

Catalog mode is read-only. Text/image modes each send one real upstream request.
Reports contain no credentials or account identifiers.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import ConversationRequest, iter_conversation_payloads, stream_image_outputs
from utils.helper import BASE_IMAGE_MODELS
from utils.log import logger


MODEL_KEYS = {"model", "model_slug", "model_name", "default_model_slug", "effective_model", "image_model", "image_gen_model"}


def probe_succeeded(report: dict[str, Any]) -> bool:
    mode = report.get("mode")
    if mode == "catalog":
        return report.get("status") == "catalog_read"
    if mode == "web-image-prepare":
        return bool(report.get("has_conduit_token"))
    evidence = report.get("model_evidence") or []
    actual = {item["model"] for item in evidence if isinstance(item, dict)
              and item.get("role") == "assistant" and str(item.get("path", "")).endswith(".model_slug")}
    if not evidence:
        actual = set(report.get("assistant_model_ids") or [])
    requested = report.get("requested_model")
    if mode == "text":
        return report.get("status") == "responded" and bool(actual) and (requested == "auto" or actual == {requested})
    return str(report.get("status", "")).startswith("image_generated") and (
        (mode == "web-image" and requested == "gpt-image-2") or actual == {requested})


def model_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MODEL_KEYS and isinstance(item, str):
                result.add(item)
            elif key == "p" and isinstance(item, str) and item.rsplit("/", 1)[-1] in MODEL_KEYS:
                if isinstance(value.get("v"), str):
                    result.add(value["v"])
            result.update(model_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.update(model_ids(item))
    return result


def model_evidence(value: Any, path: str = "$", role: str = "") -> list[dict[str, str]]:
    evidence = []
    if isinstance(value, dict):
        if isinstance(value.get("author"), dict):
            role = str(value["author"].get("role") or role)
        for key, item in value.items():
            if key in MODEL_KEYS and isinstance(item, str):
                evidence.append({"path": f"{path}.{key}", "model": item, "role": role})
            evidence.extend(model_evidence(item, f"{path}.{key}", role))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            evidence.extend(model_evidence(item, f"{path}[{index}]", role))
    return evidence


def safe_text(value: Any, token: str) -> str:
    text = str(value).replace(token, "[redacted]")
    text = re.sub(r"eyJ[A-Za-z0-9_.-]{30,}", "[redacted]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"(?:user-|acct_)[A-Za-z0-9_-]+", "[account]", text)
    return text[:1200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["catalog", "text", "image", "web-image-prepare", "web-image"])
    parser.add_argument("--model", default="auto")
    parser.add_argument("--account-index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    accounts = json.loads((ROOT / "data/accounts.json").read_text(encoding="utf-8"))
    eligible = [a for a in accounts if a.get("access_token") and a.get("status") not in {"异常", "禁用"}]
    if not eligible:
        parser.error("No eligible account is available")
    account = eligible[args.account_index % len(eligible)]
    token = account["access_token"]
    output_dir = ROOT / "data/model-checks"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", args.model)
    report_file = output_dir / f"{stamp}-{args.mode}-{name}.json"
    report: dict[str, Any] = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "requested_model": args.model,
        "account_plan": account.get("type"),
        "eligible_account_count": len(eligible),
    }
    started = time.monotonic()
    backend = OpenAIBackendAPI(access_token=token)
    logger._logger.disabled = True
    # Keep each probe bounded; do not retry or switch providers/accounts.
    for method in ("get", "post"):
        original = getattr(backend.session, method)
        def bounded(*call_args, _original=original, **kwargs):
            kwargs["timeout"] = min(float(kwargs.get("timeout") or args.timeout), args.timeout)
            return _original(*call_args, **kwargs)
        setattr(backend.session, method, bounded)
    try:
        if args.mode == "catalog":
            catalog = backend.list_models()
            report["advertised_models"] = [item["id"] for item in catalog.get("data", [])]
            init = backend._get_conversation_init()
            report["init_fields"] = sorted(init)
            report["init_model_ids"] = sorted(model_ids(init))
            report["image_capability_hints"] = sorted(set(re.findall(
                r'[A-Za-z0-9_./-]*(?:image|sunburst|flare|picture)[A-Za-z0-9_./-]*',
                json.dumps(init), re.I,
            )))[:80]
            report["status"] = "catalog_read"
        elif args.mode == "text":
            seen_models: set[str] = set()
            evidence: list[dict[str, str]] = []
            upstream_errors: list[str] = []
            def payloads():
                for payload in backend.stream_conversation(
                    messages=[{"role": "user", "content": f"Connectivity check {uuid.uuid4().hex[:8]}. Reply only with OK."}],
                    model=args.model,
                ):
                    try:
                        event = json.loads(payload)
                    except (ValueError, TypeError):
                        event = None
                    seen_models.update(model_ids(event))
                    evidence.extend(model_evidence(event))
                    if isinstance(event, dict) and event.get("error"):
                        upstream_errors.append(safe_text(event["error"], token))
                    yield payload
            parts = [event.get("delta", "") for event in iter_conversation_payloads(payloads())
                     if event.get("type") == "conversation.delta"]
            text = "".join(parts).strip()
            report["response_text"] = safe_text(text, token)[:200]
            report["returned_model_ids"] = sorted(seen_models)
            report["model_evidence"] = list({(item["path"], item["model"], item["role"]): item for item in evidence}.values())
            assistant_models = sorted({item["model"] for item in evidence
                                       if item["role"] == "assistant" and item["path"].endswith(".model_slug")})
            report["assistant_model_ids"] = assistant_models
            report["upstream_errors"] = upstream_errors
            report["status"] = "responded" if text and not upstream_errors else "empty_or_failed"
            if text and args.model != "auto" and assistant_models and args.model not in assistant_models:
                report["status"] = "responded_with_different_model"
        elif args.mode == "web-image-prepare":
            # Test the explicit identifier at the website prepare endpoint only.
            # Do not let the existing unknown-model fallback replace it with auto.
            backend._bootstrap()
            requirements = backend._get_chat_requirements()
            backend._image_model_settings = lambda _model: (args.model, "")
            conduit = backend._prepare_image_conversation(
                "一个蓝色圆形，纯白背景，无文字。", requirements, args.model,
            )
            report["has_conduit_token"] = bool(conduit)
            report["status"] = "prepare_accepted_model_unverified"
        elif args.mode == "web-image":
            # Probe the literal website model ID in this process only. Do not
            # add an unverified model to the application's public model list.
            BASE_IMAGE_MODELS.add(args.model)
            backend._image_model_settings = lambda _model: (args.model, "")
            evidence = []
            original_stream = backend.stream_conversation
            def observed_stream(**kwargs):
                for payload in original_stream(**kwargs):
                    try:
                        evidence.extend(model_evidence(json.loads(payload)))
                    except (ValueError, TypeError):
                        pass
                    yield payload
            backend.stream_conversation = observed_stream
            request = ConversationRequest(
                prompt="生成一张图片：一个蓝色圆形，纯白背景，简洁平面图形，无文字。",
                model=args.model, response_format="url", base_url="http://127.0.0.1:3000",
            )
            outputs = list(stream_image_outputs(backend, request))
            report["output_kinds"] = [output.kind for output in outputs]
            report["model_evidence"] = list({(item["path"], item["model"], item["role"]): item for item in evidence}.values())
            report["returned_model_ids"] = sorted({item["model"] for item in evidence})
            report["image_urls"] = [item["url"] for output in outputs for item in (output.data or []) if item.get("url")]
            report["messages"] = [safe_text(output.text, token) for output in outputs if output.kind == "message"]
            report["status"] = "image_generated_model_unverified" if report["image_urls"] else "no_image"
        else:
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
            auth_claims = claims.get("https://api.openai.com/auth", {})
            account_id = auth_claims.get("chatgpt_account_id") or account.get("account_id")
            headers = {
                "Authorization": f"Bearer {token}", "Content-Type": "application/json",
                "Accept": "application/json", "User-Agent": "ChatGPT2API/model-probe (Windows)",
                "originator": "chatgpt2api", "x-client-request-id": str(uuid.uuid4()),
                "x-codex-image-turn-id": str(uuid.uuid4()),
            }
            if account_id:
                headers["ChatGPT-Account-ID"] = account_id
            response = backend.session.post(
                backend.base_url + "/backend-api/codex/images/generations",
                headers=headers,
                json={"model": args.model, "prompt": "一个蓝色圆形，纯白背景，简洁平面图形，无文字。",
                      "n": 1, "quality": "low", "size": "1024x1024", "background": "opaque"},
                timeout=args.timeout,
            )
            report["http_status"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text[:500]}
            report["response_fields"] = sorted(body) if isinstance(body, dict) else []
            report["returned_model_ids"] = sorted(model_ids(body))
            if response.status_code >= 400 or not body.get("data"):
                report["status"] = "rejected"
                report["error"] = safe_text(body.get("error", body), token)
            else:
                image_bytes = base64.b64decode(body["data"][0]["b64_json"])
                with Image.open(BytesIO(image_bytes)) as generated:
                    report["actual_size"] = list(generated.size)
                    report["image_format"] = generated.format
                    generated.verify()
                image_file = report_file.with_suffix(".png")
                image_file.write_bytes(image_bytes)
                report["image_file"] = str(image_file)
                report["reported_size"] = body.get("size")
                report["reported_quality"] = body.get("quality")
                report["embedded_model_strings"] = sorted(set(
                    match.decode("ascii") for match in re.findall(rb"gpt-image(?:[-_.a-zA-Z0-9]+)?", image_bytes)
                ))[:20]
                report["status"] = "image_generated_model_unverified"
                if args.model in report["returned_model_ids"]:
                    report["status"] = "image_generated_model_reported"
    except Exception as exc:
        report["status"] = "error"
        report["error_type"] = type(exc).__name__
        report["error"] = safe_text(exc, token)
        if hasattr(exc, "status_code"):
            report["http_status"] = exc.status_code
    finally:
        backend.close()
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key not in {"model_evidence", "init_fields"}}
    print(json.dumps({**summary, "report_file": str(report_file)}, ensure_ascii=False, indent=2))
    return 0 if probe_succeeded(report) else 2


if __name__ == "__main__":
    raise SystemExit(main())
