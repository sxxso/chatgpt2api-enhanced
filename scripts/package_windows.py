"""Build a Windows ZIP from committed source plus the freshly exported web panel."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_release import forbidden, missing_required_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "releases")
    args = parser.parse_args()
    if subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT).returncode:
        parser.error("Commit reviewed source changes before creating the release ZIP.")
    panel = ROOT / "web" / "out"
    if not (panel / "login" / "index.html").is_file():
        parser.error("Build the panel first with npm ci and npm run build in web/.")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    entries = [(name, ROOT / name) for name in tracked if name]
    entries += [("web_dist/" + path.relative_to(panel).as_posix(), path) for path in panel.rglob("*") if path.is_file()]
    missing = missing_required_files({name for name, _ in entries}, windows=True)
    if missing:
        parser.error("Required release files are missing: " + ", ".join(missing))
    for name, path in entries:
        if forbidden(name, archive=True) or path.is_symlink():
            parser.error(f"Private or linked release path: {name}")
    args.output.mkdir(parents=True, exist_ok=True)
    version = (ROOT / "VERSION").read_text().strip()
    destination = args.output / f"ChatGPT2API-Enhanced-v{version}-Windows.zip"
    if destination.exists():
        parser.error("Release ZIP exists; choose a new output directory rather than replacing it.")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for name, path in entries:
            package.write(path, "chatgpt2api-enhanced/" + name)
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_release.py"), "--archive", str(destination)], cwd=ROOT)
    if result.returncode:
        print("Release check failed; do not upload this ZIP.")
        return result.returncode
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS.txt").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    print(f"Created {destination.name}: {len(entries)} files, {destination.stat().st_size} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
