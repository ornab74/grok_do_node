from __future__ import annotations

import csv
import json
from pathlib import Path
import sys


root = Path(sys.argv[1])
books = list(csv.DictReader((root / "get_books.csv").open(encoding="utf-8")))
book_json = json.loads((root / "get_books.json").read_text(encoding="utf-8"))
risks = list(csv.DictReader((root / "owasp_top_10.csv").open(encoding="utf-8")))
assert len(books) == 3
assert books == book_json
assert len(risks) == 10
assert {row["title"][:3] for row in risks} == {f"A{i:02d}" for i in range(1, 11)}
print("Output verification passed: 3 books, 10 unique OWASP risks.")
