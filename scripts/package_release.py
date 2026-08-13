#!/usr/bin/env python3
"""Create a deterministic, credential-free .skill archive for distribution."""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "research-workspace-builder"
EXCLUDED_DIRS = {".git", "__pycache__", "dist", "node_modules"}
EXCLUDED_FILES = {".env", ".DS_Store"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        credential_file = path.name.startswith(".env.") and path.name != ".env.example"
        if (
            not path.is_file()
            or path.name in EXCLUDED_FILES
            or credential_file
            or path.suffix == ".pyc"
        ):
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def package(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{ARCHIVE_ROOT}.skill"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in included_files():
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", (2026, 8, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == ".py" else 0o644) << 16
            archive.writestr(info, path.read_bytes())
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(package(args.output_dir))


if __name__ == "__main__":
    main()
