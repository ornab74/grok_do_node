from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3


QUERIES = {
    "summary": """
        SELECT COUNT(*) AS cities,
               ROUND(MIN(temperature_c), 1) AS min_c,
               ROUND(AVG(temperature_c), 1) AS avg_c,
               ROUND(MAX(temperature_c), 1) AS max_c
        FROM observations
    """,
    "hottest": """
        SELECT city, temperature_c, condition, observed_local
        FROM observations ORDER BY temperature_c DESC, city LIMIT ?
    """,
    "coldest": """
        SELECT city, temperature_c, condition, observed_local
        FROM observations ORDER BY temperature_c ASC, city LIMIT ?
    """,
    "conditions": """
        SELECT condition, COUNT(*) AS cities, ROUND(AVG(temperature_c), 1) AS avg_c
        FROM observations GROUP BY condition ORDER BY cities DESC, condition LIMIT ?
    """,
}


def print_rows(cursor: sqlite3.Cursor) -> None:
    names = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    widths = [len(name) for name in names]
    for row in rows:
        widths = [max(width, len(str(value))) for width, value in zip(widths, row)]
    print(" | ".join(name.ljust(width) for name, width in zip(names, widths)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(width) for value, width in zip(row, widths)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run safe predefined weather queries.")
    parser.add_argument("--database", type=Path, default=Path("/results/capstone/weather.db"))
    parser.add_argument("query", choices=sorted(QUERIES))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    limit = min(max(args.limit, 1), 100)
    uri = f"file:{args.database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        sql = QUERIES[args.query]
        cursor = connection.execute(sql, () if args.query == "summary" else (limit,))
        print_rows(cursor)


if __name__ == "__main__":
    main()
