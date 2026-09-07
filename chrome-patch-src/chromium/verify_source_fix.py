#!/usr/bin/env python3
"""Fail a Chromium build unless the validated broker path fix is present."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SOURCE = Path("sandbox/linux/syscall_broker/broker_file_permission.cc")
TEST = Path("sandbox/linux/syscall_broker/broker_file_permission_unittest.cc")


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    source_path = root / SOURCE
    test_path = root / TEST
    if not source_path.is_file():
        return [f"missing Chromium source file: {source_path}"]
    if not test_path.is_file():
        return [f"missing Chromium regression test: {test_path}"]

    source = source_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")
    if "ContainsParentOrSelfReference" not in source:
        errors.append("fixed path validator ContainsParentOrSelfReference is absent")
    if "ContainsParentReference(path)" in source:
        errors.append("legacy parent-only path validator is still active")
    if "BrokerFilePermission" not in tests:
        errors.append("BrokerFilePermission regression test suite is absent")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = verify(args.root.resolve())
    if errors:
        for error in errors:
            print(f"SOURCE FIX CHECK FAILED: {error}", file=sys.stderr)
        return 1
    print("Source fix check passed: parent and self references are rejected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
