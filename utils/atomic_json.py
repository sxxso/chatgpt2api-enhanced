"""Atomic JSON snapshots with one previous valid version kept beside the file."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


class JSONDataError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise JSONDataError(f"Cannot read valid JSON from {path.name}; the original file was preserved. Check its .bak backup.") from exc


def _replace_bytes(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, value: Any, *, backup: bool = True) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A corrupt snapshot must never become an empty replacement or poison
        # the last valid backup. Recovery is an explicit operation.
        read_json(path)
        if backup:
            _replace_bytes(path.with_suffix(path.suffix + ".bak"), path.read_bytes())
    _replace_bytes(path, encoded)
