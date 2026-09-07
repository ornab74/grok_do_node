from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

import pandas as pd


RAW_COLUMNS = [
    "city",
    "observed_local",
    "condition",
    "temperature_raw",
    "source_url",
    "scraped_at_utc",
]


@dataclass(slots=True)
class WeatherRecord:
    city: str
    observed_local: str
    condition: str
    temperature_raw: str
    source_url: str
    scraped_at_utc: str


def _condition_text(cell) -> str:
    from selenium.webdriver.common.by import By

    text = " ".join(cell.text.split())
    if text:
        return text
    images = cell.find_elements(By.CSS_SELECTOR, "img[title], img[alt]")
    if not images:
        return "Unknown"
    return (images[0].get_attribute("title") or images[0].get_attribute("alt") or "Unknown").strip()


def extract_weather(driver, source_url: str, scraped_at_utc: str | None = None) -> list[WeatherRecord]:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
    table = None
    for selector in ("#wt-tb", "table.zebra", "table"):
        matches = driver.find_elements(By.CSS_SELECTOR, selector)
        if matches:
            table = matches[0]
            break
    if table is None:
        raise RuntimeError("weather table was not found")

    stamp = scraped_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[WeatherRecord] = []
    seen: set[tuple[str, str]] = set()
    for row in table.find_elements(By.CSS_SELECTOR, "tr"):
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        for start in range(0, len(cells) - 3, 4):
            city_cell, time_cell, condition_cell, temp_cell = cells[start : start + 4]
            city = " ".join(city_cell.text.split())
            observed = " ".join(time_cell.text.split())
            temperature = " ".join(temp_cell.text.split())
            anchors = city_cell.find_elements(By.CSS_SELECTOR, "a[href]")
            href = anchors[0].get_attribute("href") if anchors else source_url
            if not city or not temperature:
                continue
            key = (city, observed)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                WeatherRecord(
                    city=city,
                    observed_local=observed or "Unknown",
                    condition=_condition_text(condition_cell),
                    temperature_raw=temperature,
                    source_url=href or source_url,
                    scraped_at_utc=stamp,
                )
            )
    return records


def write_raw_csv(records: list[WeatherRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


def _temperature_c(raw: object) -> float | None:
    text = str(raw).replace("\u00a0", " ")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    if (match.group(2) or "C").upper() == "F":
        value = (value - 32.0) * 5.0 / 9.0
    return round(value, 1)


def clean_weather_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    missing = set(RAW_COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"raw data is missing columns: {sorted(missing)}")
    before = len(raw)
    clean = raw.copy()
    clean["city"] = clean["city"].astype("string").str.replace("*", "", regex=False).str.strip()
    clean["condition"] = clean["condition"].astype("string").str.strip().fillna("Unknown")
    clean["observed_local"] = clean["observed_local"].astype("string").str.strip()
    clean["temperature_c"] = clean["temperature_raw"].map(_temperature_c)
    clean = clean.dropna(subset=["city", "temperature_c"])
    clean = clean[clean["city"].str.len() > 0]
    clean = clean.drop_duplicates(subset=["city", "observed_local", "temperature_c"], keep="first")
    clean["temperature_f"] = (clean["temperature_c"] * 9.0 / 5.0 + 32.0).round(1)
    clean["temperature_band"] = pd.cut(
        clean["temperature_c"],
        bins=[-float("inf"), 10, 20, 30, float("inf")],
        labels=["cold", "mild", "warm", "hot"],
        right=False,
    ).astype("string")
    clean = clean.sort_values(["temperature_c", "city"], ascending=[False, True]).reset_index(drop=True)
    stats = {"raw_rows": before, "clean_rows": len(clean), "removed_rows": before - len(clean)}
    return clean, stats


def clean_csv(raw_path: Path, clean_path: Path, stats_path: Path) -> pd.DataFrame:
    raw = pd.read_csv(raw_path)
    clean, stats = clean_weather_frame(raw)
    clean.to_csv(clean_path, index=False)
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return clean


def load_database(clean: pd.DataFrame, database: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "city",
        "observed_local",
        "condition",
        "temperature_c",
        "temperature_f",
        "temperature_band",
        "source_url",
        "scraped_at_utc",
    ]
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE IF EXISTS observations")
        connection.execute(
            """
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                observed_local TEXT NOT NULL,
                condition TEXT NOT NULL,
                temperature_c REAL NOT NULL,
                temperature_f REAL NOT NULL,
                temperature_band TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX idx_observations_temp ON observations(temperature_c)")
        connection.execute("CREATE INDEX idx_observations_condition ON observations(condition)")
        connection.executemany(
            """
            INSERT INTO observations (
                city, observed_local, condition, temperature_c, temperature_f,
                temperature_band, source_url, scraped_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            clean[columns].itertuples(index=False, name=None),
        )
        count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    return int(count)
