from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from selenium.webdriver.common.by import By

from common.browser import create_driver
from common.fixture_server import serve_fixture
from common.scraping_security import check_redirect_target
from weather_pipeline import clean_csv, extract_weather, load_database, write_raw_csv


BASE_DIR = Path(__file__).resolve().parent
WEATHER_URL = "https://www.timeanddate.com/weather/"
ALLOWED_HOSTS = {"www.timeanddate.com"}
USER_AGENT = "CTD-weather-capstone/2.0 educational-low-rate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape, clean, and store world weather data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fixture", action="store_true", help="use the bundled fixture (default)")
    mode.add_argument("--live", action="store_true", help="make one robots-checked live request")
    parser.add_argument("--output-dir", type=Path, default=Path("/results/capstone"))
    return parser.parse_args()


def assert_robots_allowed_with_browser(url: str) -> None:
    """Verify robots.txt through the same hardened browser/proxy path as the scrape."""
    robots_url = urljoin(url, "/robots.txt")
    driver = create_driver(USER_AGENT)
    try:
        driver.get(robots_url)
        check_redirect_target(driver.current_url, ALLOWED_HOSTS)
        body = driver.find_element(By.TAG_NAME, "body").text
        if not body.strip():
            raise RuntimeError("robots.txt loaded but returned no readable content")

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(body.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            raise RuntimeError(f"robots.txt does not permit scraping {url}")

        print(f"robots.txt permits the weather scrape: {url}")
    finally:
        driver.quit()


def scrape(target: str, live: bool):
    driver = create_driver(USER_AGENT)
    try:
        driver.get(target)
        if live:
            check_redirect_target(driver.current_url, ALLOWED_HOSTS)
        return extract_weather(
            driver,
            source_url=WEATHER_URL,
            scraped_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    finally:
        driver.quit()


def main() -> None:
    args = parse_args()
    live = bool(args.live)
    if live:
        assert_robots_allowed_with_browser(WEATHER_URL)
        records = scrape(WEATHER_URL, True)
    else:
        with serve_fixture(BASE_DIR / "fixtures" / "weather.html") as target:
            records = scrape(target, False)
    if not records:
        raise RuntimeError("the weather scraper returned no records")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "weather_raw.csv"
    clean_path = args.output_dir / "weather_clean.csv"
    stats_path = args.output_dir / "cleaning_stats.json"
    database = args.output_dir / "weather.db"
    write_raw_csv(records, raw_path)
    clean = clean_csv(raw_path, clean_path, stats_path)
    count = load_database(clean, database)
    print(f"Wrote {len(records)} raw rows, {len(clean)} clean rows, and {count} database rows.")
    print(f"Results directory: {args.output_dir}")


if __name__ == "__main__":
    main()
