from __future__ import annotations

from services.protocol.web_search_tool import run_web_search


def handle(body: dict[str, object]) -> dict[str, object]:
    return run_web_search(str(body["prompt"]), model=body.get("model"))
