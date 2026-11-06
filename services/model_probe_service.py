from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from services.config import DATA_DIR, config
from utils.helper import WEB_IMAGE_MODELS


def _checked_at_value(item: dict[str, object], path: Path) -> float:
    """Missing/invalid observation times must never become fresh on file copy."""
    value = str(item.get("checked_at_utc") or "").strip()
    if value:
        try:
            checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return checked.replace(tzinfo=checked.tzinfo or timezone.utc).timestamp()
        except ValueError:
            pass
    return 0.0


def model_check_is_usable(item: dict[str, object]) -> bool:
    """Return whether the latest saved probe is safe to advertise to callers."""
    model = str(item.get("model") or "").strip()
    status = str(item.get("status") or "").strip()
    if item.get("expired") or item.get("http_status") in {401, 403, 429}:
        return False
    values = item.get("actual_models")
    actual_models = {
        str(value).strip()
        for value in (values if isinstance(values, list) else [])
        if isinstance(value, str) and value.strip()
    }
    mode = str(item.get("mode") or "").strip()
    if mode == "text":
        return status == "responded" and bool(actual_models) and (model == "auto" or actual_models == {model})
    if mode in {"image", "web-image"}:
        # gpt-image-2 uses the legacy web route, which does not emit a model
        # slug even though the image result itself is valid.
        return status.startswith("image_generated") and (
            (model == "gpt-image-2" and mode == "web-image") or actual_models == {model}
        )
    return False


def read_model_checks(directory: Path | None = None, *, now: float | None = None) -> dict[str, object]:
    """Expose saved observations only; reading this endpoint never calls upstream."""
    directory = directory or DATA_DIR / "model-checks"
    latest: dict[str, dict[str, object]] = {}
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    ttl_seconds = config.model_check_ttl_hours * 3600
    if not directory.is_dir():
        return {"items": []}
    reports: list[tuple[float, Path, dict[str, object]]] = []
    for path in directory.glob("*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("mode") not in {"text", "image", "web-image"}:
            continue
        reports.append((_checked_at_value(report, path), path, report))

    for checked_ts, _, report in sorted(reports, key=lambda entry: (entry[0], entry[1].name), reverse=True):
        model = str(report.get("requested_model") or "").strip()
        if not model or model in latest:
            continue
        # Native image API probes do not describe the web-account route.
        if model in WEB_IMAGE_MODELS and report["mode"] != "web-image":
            continue
        evidence = report.get("model_evidence")
        evidence = evidence if isinstance(evidence, list) else []
        actual_models = sorted({
            item["model"] for item in evidence
            if isinstance(item, dict) and item.get("role") == "assistant"
            and str(item.get("path") or "").endswith(".model_slug")
            and isinstance(item.get("model"), str)
        })
        if not evidence:
            ids = report.get("assistant_model_ids")
            actual_models = sorted({value.strip() for value in ids if isinstance(value, str) and value.strip()}) if isinstance(ids, list) else []
        # Whitelist fields: never expose credentials, raw response bodies,
        # account identifiers, local paths or generated image contents.
        latest[model] = {
            "model": model,
            "mode": report["mode"],
            "status": str(report.get("status") or "unknown"),
            "actual_models": actual_models,
            "http_status": report.get("http_status"),
            "checked_at": report.get("checked_at_utc"),
            "elapsed_seconds": report.get("elapsed_seconds"),
            "account_plan": report.get("account_plan"),
            "expired": checked_ts <= 0 or checked_ts > now + 300 or now - checked_ts > ttl_seconds,
        }
        item = latest[model]
        item["usable"] = model_check_is_usable(item)
        item["availability"] = ("expired" if item["expired"] else "available" if item["usable"] else
                                "fallback" if item["actual_models"] and model not in item["actual_models"] else "unavailable")
    return {"items": [latest[model] for model in sorted(latest)], "ttl_hours": config.model_check_ttl_hours}


def read_usable_model_ids(directory: Path | None = None) -> set[str]:
    """Return only recent verified IDs; untested models stay in the raw catalog."""
    items = read_model_checks(directory).get("items")
    if not isinstance(items, list):
        return set()
    records = [item for item in items if isinstance(item, dict)]
    return {
        str(item.get("model") or "").strip()
        for item in records
        if model_check_is_usable(item) and str(item.get("model") or "").strip()
    }
