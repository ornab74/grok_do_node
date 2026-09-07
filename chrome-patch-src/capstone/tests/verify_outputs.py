from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3
import sys


root = Path(sys.argv[1])
raw = list(csv.DictReader((root / "weather_raw.csv").open(encoding="utf-8")))
clean = list(csv.DictReader((root / "weather_clean.csv").open(encoding="utf-8")))
stats = json.loads((root / "cleaning_stats.json").read_text(encoding="utf-8"))
with sqlite3.connect(root / "weather.db") as connection:
    db_count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
assert len(raw) == 9
assert len(clean) == 9
assert stats == {"raw_rows": 9, "clean_rows": 9, "removed_rows": 0}
assert db_count == 9
print("Output verification passed: 9 fixture observations in CSV and SQLite.")
