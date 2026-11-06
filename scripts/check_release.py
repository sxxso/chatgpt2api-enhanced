"""Check publishable files without printing secret values or making network calls."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", ".next", "out", "web_dist", "__pycache__", "data", "data-gym-cache", ".local", ".codex", "artifacts", "releases"}
REQUIRED_FILES = {
    "main.py", "api/__init__.py", "api/app.py", "services/config.py",
    "utils/atomic_json.py", "pyproject.toml", "uv.lock", "LICENSE", "NOTICE",
    "VERSION", "README.md", "config.example.json", "web/package.json",
    "web/package-lock.json", "scripts/setup-windows.ps1", "scripts/run-local.ps1",
    "scripts/init_local_config.py", "scripts/verify-default-model.ps1",
    "docs/小白使用指南.md", "首次安装.cmd", "启动.cmd", "停止.cmd",
}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9_]{24,}"),
    "fine-grained-token": re.compile(rb"github_pat_[A-Za-z0-9_]{30,}"),
    "api-key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{30,}"),
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{16,}"),
    "personal-workspace": re.compile(rb"(?i)[A-Z]:[\\/](?:Users[\\/][^\\/\r\n]+|Aava-xiangmu[\\/])"),
}


def forbidden(name: str, *, archive: bool = False) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name):
        return True
    if path.name == "config.json" or path.name.startswith("config.json."):
        return True
    if path.name.startswith(".env") and path.name != ".env.example":
        return True
    blocked = SKIP_DIRS - ({"web_dist"} if archive else set())
    return any(part in blocked or part.startswith(("chatgpt2api-checks-", "chatgpt2api-ui-check-")) for part in path.parts)


def missing_required_files(names: set[str], *, windows: bool = False) -> list[str]:
    required = set(REQUIRED_FILES)
    if windows:
        required.update({"web_dist/login/index.html", "web_dist/image/index.html", "web_dist/settings/index.html"})
    return sorted(required - names)


def source_files():
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True)
        for name in sorted(set(result.stdout.decode("utf-8").split("\0")) - {""}):
            yield name, (ROOT / name).read_bytes()
    else:
        # Standalone source exports have no history yet. Omit local runtime only.
        for directory, dirs, files in os.walk(ROOT, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
            for name in sorted(files):
                path = Path(directory) / name
                relative = path.relative_to(ROOT).as_posix()
                if relative == "config.json" or relative.endswith((".bak", ".zip", ".pyc", ".tsbuildinfo")):
                    continue
                yield relative, path.read_bytes()


def archive_files(path: Path):
    with zipfile.ZipFile(path) as package:
        for info in package.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if not parts or parts[0] != "chatgpt2api-enhanced":
                raise ValueError("Unexpected archive root; nothing was extracted.")
            yield "/".join(parts[1:]), package.read(info)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Scan a Windows/source release ZIP")
    args = parser.parse_args()
    findings = []
    count = 0
    names: set[str] = set()
    for name, content in archive_files(args.archive) if args.archive else source_files():
        count += 1
        names.add(name)
        if forbidden(name, archive=bool(args.archive)):
            findings.append((name, "private-path"))
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                # Deliberately fake repeated-x examples are not credentials.
                if label.endswith("token") and set(match.group().split(b"_", 1)[-1]) == {ord("x")}:
                    continue
                findings.append((name, label))
                break
    findings.extend((name, "missing-required-file") for name in missing_required_files(
        names, windows=bool(args.archive and args.archive.name.endswith("-Windows.zip"))))
    if findings:
        for name, label in findings:
            print(f"BLOCKED: {name}: {label} (value hidden)")
        return 1
    print(f"Release boundary check passed: {count} files; no private paths or recognized secret patterns.")
    print("This check does not prove the absence of every possible secret; review the staged file list too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
