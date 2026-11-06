"""Create a private local config once; never replace an existing config."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def initialize(root: Path = ROOT) -> bool:
    destination = root / "config.json"
    if destination.exists():
        if not destination.is_file():
            raise ValueError("config.json must be a file. The existing path was preserved.")
        existing = json.loads(destination.read_text(encoding="utf-8-sig"))
        if not isinstance(existing, dict) or not str(existing.get("auth-key") or "").strip():
            raise ValueError("Existing config.json has no admin key. Set auth-key in that file; it was preserved.")
        return False
    template = json.loads((root / "config.example.json").read_text(encoding="utf-8-sig"))
    template["auth-key"] = secrets.token_urlsafe(32)
    # Exclusive creation prevents concurrent installers from replacing each other's key.
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(template, output, ensure_ascii=False, indent=2)
        output.write("\n")
    if os.name != "nt":
        destination.chmod(0o600)
    return True


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        created = initialize()
    except (OSError, ValueError) as exc:
        print(f"Configuration setup failed: {exc}")
        return 1
    print("Created config.json with a unique admin key." if created else "Existing config.json was preserved.")
    print("Open config.json locally and copy auth-key to log in. Do not upload this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
