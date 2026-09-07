from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("/results/capstone"))
    args = parser.parse_args()
    files = sorted(path for path in args.directory.glob("*") if path.is_file())
    if not files:
        print("No capstone results yet. Run `make capstone` first.")
        return
    for path in files:
        print(f"{path.name}: {path.stat().st_size} bytes")
        if path.suffix in {".csv", ".json"}:
            print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]))
            print()


if __name__ == "__main__":
    main()
