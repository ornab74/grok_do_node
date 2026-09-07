#!/usr/bin/env python3
"""Create a minimal, normalized Chromium runtime directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import stat


REQUIRED_FILES = (
    "chrome",
    "chromedriver",
    "icudtl.dat",
    "resources.pak",
)
OPTIONAL_FILES = (
    "chrome_100_percent.pak",
    "chrome_200_percent.pak",
    "chrome_crashpad_handler",
    "libEGL.so",
    "libGLESv2.so",
    "libvk_swiftshader.so",
    "snapshot_blob.bin",
    "v8_context_snapshot.bin",
    "vk_swiftshader_icd.json",
)
REQUIRED_DIRECTORIES = ("locales",)
OPTIONAL_DIRECTORIES = (
    "resources",
    "MEIPreload",
    "PrivacySandboxAttestationsPreloaded",
    "hyphen-data",
)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--fix-commit", required=True)
    parser.add_argument("--fix-state", choices=("applied", "already-present"), required=True)
    parser.add_argument("--depot-tools-revision", required=True)
    args = parser.parse_args()

    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        source = args.output / name
        if not source.is_file():
            raise SystemExit(f"required Chromium build output is missing: {source}")
        copy_file(source, destination / name)
    for name in OPTIONAL_FILES:
        source = args.output / name
        if source.is_file():
            copy_file(source, destination / name)
    for name in REQUIRED_DIRECTORIES:
        source = args.output / name
        if not source.is_dir():
            raise SystemExit(f"required Chromium build output is missing: {source}")
        shutil.copytree(source, destination / name, symlinks=True, dirs_exist_ok=True)
    for name in OPTIONAL_DIRECTORIES:
        source = args.output / name
        if source.is_dir():
            shutil.copytree(source, destination / name, symlinks=True, dirs_exist_ok=True)
    if not any((destination / name).is_file() for name in ("v8_context_snapshot.bin", "snapshot_blob.bin")):
        raise SystemExit("required V8 snapshot build output is missing")

    # The SUID helper is deliberately not packaged. This image requires the
    # unprivileged user-namespace sandbox and verifies it at runtime.
    for executable in (destination / "chrome", destination / "chromedriver"):
        executable.chmod(0o755)
    for path in destination.rglob("*"):
        if path.is_file() and path.name not in {"chrome", "chromedriver", "chrome_crashpad_handler"}:
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o022)

    metadata = {
        "chromium_version": args.version,
        "source_revision": args.source_revision,
        "sandbox_fix_commit": args.fix_commit,
        "sandbox_fix_state": args.fix_state,
        "depot_tools_revision": args.depot_tools_revision,
        "platform": f"linux-{platform.machine()}",
        "suid_sandbox_packaged": False,
    }
    (destination / "SOURCE_METADATA.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    files = sorted(
        path for path in destination.rglob("*") if path.is_file() and path.name != "manifest.sha256"
    )
    manifest = "".join(f"{sha256(path)}  {path.relative_to(destination)}\n" for path in files)
    (destination / "manifest.sha256").write_text(manifest, encoding="utf-8")
    print(f"Packaged {len(files)} Chromium runtime files in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
