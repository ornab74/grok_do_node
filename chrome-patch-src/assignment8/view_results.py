from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("/results/assignment8"))
    args = parser.parse_args()
    files = sorted(path for path in args.directory.glob("*") if path.is_file())
    if not files:
        print("No Assignment 8 results yet. Run `make assignment8` first.")
        return
    for path in files:
        print(f"\n=== {path.name} ===")
        print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]))


if __name__ == "__main__":
    main()
