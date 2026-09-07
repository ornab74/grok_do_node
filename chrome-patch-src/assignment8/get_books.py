from pathlib import Path
import json
import random
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pandas as pd
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from common.browser import create_driver

SEARCH_URL = (
    "https://durhamcounty.bibliocommons.com/v2/search"
    "?query=learning%20spanish&searchType=smart"
)

OUTPUT_DIR = Path("/results/assignment8")
CSV_PATH = OUTPUT_DIR / "get_books.csv"
JSON_PATH = OUTPUT_DIR / "get_books.json"

MAX_ATTEMPTS = 4
WAIT_SECONDS = 25
BACKOFF_SECONDS = 2
MIN_PAGE_DELAY = 2.5
MAX_PAGE_DELAY = 5.5

# Task 2 / Task 3: selectors taken from the inspected result DOM.
RESULT_SELECTOR = "li.row.cp-search-result-item"
TITLE_SELECTOR = "h3.cp-title span.title-content"
AUTHOR_LINK_SELECTOR = "span.cp-author-link a.author-link"
AUTHOR_LINK_FALLBACK_SELECTOR = "a.author-link"
AUTHOR_CONTAINER_SELECTOR = "span.cp-author-link"
FORMAT_YEAR_CONTAINER_SELECTOR = "div.cp-format-info"
FORMAT_YEAR_SELECTOR = "span.display-info-primary"

# The live BiblioCommons pager uses labels such as "Go to page 2", not a
# literal Next link. We discover those links from the page rather than assuming
# how many result pages exist.
PAGE_LINK_XPATH = (
    "//a["
    "starts-with(@aria-label, 'Go to page ') or "
    "contains(@href, 'page=')"
    "]"
)


def normalize_text(value):
    return " ".join((value or "").split())


def jitter_sleep(label="Pause"):
    delay = random.uniform(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
    print(f"{label}: {delay:.2f} seconds")
    time.sleep(delay)


def first_text(parent, selectors):
    for selector in selectors:
        for element in parent.find_elements(By.CSS_SELECTOR, selector):
            text = normalize_text(element.text)
            if text:
                return text
    return ""


def extract_authors(item):
    author_elements = item.find_elements(By.CSS_SELECTOR, AUTHOR_LINK_SELECTOR)
    if not author_elements:
        author_elements = item.find_elements(By.CSS_SELECTOR, AUTHOR_LINK_FALLBACK_SELECTOR)

    raw_authors = [element.text for element in author_elements]
    if not any(normalize_text(author) for author in raw_authors):
        author_containers = item.find_elements(By.CSS_SELECTOR, AUTHOR_CONTAINER_SELECTOR)
        raw_authors = [element.text for element in author_containers]

    authors = []
    seen = set()
    for raw_author in raw_authors:
        author = normalize_text(raw_author)
        author = re.sub(r"^by\s+", "", author, flags=re.IGNORECASE)
        author = author.strip(" ;\u2022")
        key = author.casefold()
        if author and key not in seen:
            authors.append(author)
            seen.add(key)

    return "; ".join(authors)


def extract_book(item):
    title = first_text(item, [TITLE_SELECTOR, "h3.cp-title a", "h3.cp-title"])
    author_text = extract_authors(item)

    format_year = ""
    format_containers = item.find_elements(By.CSS_SELECTOR, FORMAT_YEAR_CONTAINER_SELECTOR)
    if format_containers:
        format_year = first_text(
            format_containers[0],
            [FORMAT_YEAR_SELECTOR, "span.display-info"],
        )

    return {
        "Title": title,
        "Author": author_text,
        "Format-Year": format_year,
    }


def book_key(book):
    return (
        book["Title"].casefold(),
        book["Author"].casefold(),
        book["Format-Year"].casefold(),
    )


def load_url_with_retry(driver, url, description):
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"Loading {description} (attempt {attempt}/{MAX_ATTEMPTS})")
            driver.get(url)
            WebDriverWait(driver, WAIT_SECONDS).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, RESULT_SELECTOR))
            )
            return
        except (TimeoutException, WebDriverException) as error:
            last_error = error
            if attempt == MAX_ATTEMPTS:
                break

            delay = BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0.25, 1.25)
            print(f"{description} was not ready; retrying in {delay:.2f} seconds...")
            time.sleep(delay)

    raise RuntimeError(
        f"Unable to load {description} after {MAX_ATTEMPTS} attempts."
    ) from last_error


def collect_current_page(driver):
    """
    Collect lazy-rendered cards on the current results page.

    The live page initially exposes only a small batch of cards even though the
    pager reports 20 results per page. Scrolling the last visible result into
    view is more reliable than scrolling the window by a fixed number of pixels.
    """
    page_books = {}
    unchanged_rounds = 0
    last_dom_count = 0

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    for _ in range(40):
        items = driver.find_elements(By.CSS_SELECTOR, RESULT_SELECTOR)

        for item in items:
            try:
                book = extract_book(item)
            except StaleElementReferenceException:
                continue

            if book["Title"]:
                page_books.setdefault(book_key(book), book)

        dom_count = len(items)
        print(
            f"Current page: {dom_count} result elements in DOM; "
            f"{len(page_books)} unique books collected"
        )

        if dom_count > last_dom_count:
            unchanged_rounds = 0
            last_dom_count = dom_count
        else:
            unchanged_rounds += 1

        if items:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'end'});",
                    items[-1],
                )
            except StaleElementReferenceException:
                continue

        # Give the site's lazy renderer time to append the next card batch.
        time.sleep(1.5)

        # Once 20 are materialized we have the normal full page. On the final
        # page there may be fewer, so several unchanged rounds ends collection.
        if len(page_books) >= 20:
            break
        if unchanged_rounds >= 5:
            break

    return list(page_books.values())


def page_number_from_href(href):
    if not href:
        return None
    query = parse_qs(urlsplit(href).query)
    try:
        return int(query.get("page", [None])[0])
    except (TypeError, ValueError):
        return None


def discover_page_urls(driver):
    """Discover numbered pagination URLs currently advertised by the page."""
    pages = {}

    for link in driver.find_elements(By.XPATH, PAGE_LINK_XPATH):
        href = link.get_attribute("href")
        page_number = page_number_from_href(href)
        if href and page_number and page_number >= 2:
            pages[page_number] = href

    return pages


def build_page_url(page_number):
    """Preserve the assigned search query while changing only its page number."""
    parts = urlsplit(SEARCH_URL)
    query = parse_qs(parts.query)
    query["page"] = [str(page_number)]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment)
    )


def collect_all_pages(driver):
    """Optional extra-credit: collect every advertised search-results page."""
    all_books = {}
    page_number = 1

    while True:
        print(f"\nCollecting Durham results page {page_number}")
        page_books = collect_current_page(driver)

        for book in page_books:
            all_books.setdefault(book_key(book), book)

        print(
            f"Page {page_number}: {len(page_books)} results; "
            f"total unique: {len(all_books)}"
        )

        advertised_pages = discover_page_urls(driver)
        next_page = page_number + 1

        # Prefer the URL actually advertised by the site's pagination. If the
        # pager only shows a window of page numbers, construct the immediately
        # following URL using the same query shape and verify it by loading it.
        next_url = advertised_pages.get(next_page)
        if next_url is None and any(number > page_number for number in advertised_pages):
            next_url = build_page_url(next_page)

        if next_url is None:
            print("No later numbered page advertised; pagination complete.")
            break

        jitter_sleep(f"Ethical delay before requesting Durham page {next_page}")

        try:
            load_url_with_retry(
                driver,
                next_url,
                f"Durham results page {next_page}",
            )
        except RuntimeError:
            print(f"Page {next_page} could not be loaded; stopping pagination.")
            break

        page_number = next_page

    return list(all_books.values())


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    driver = create_driver()

    try:
        load_url_with_retry(driver, SEARCH_URL, "Durham search")

        # Task 3: required empty results list, then append one dict per result.
        results = []
        for book in collect_all_pages(driver):
            results.append(book)

        print(f"\nFound {len(results)} total unique search results")

        # Task 3: create and print DataFrame.
        df = pd.DataFrame(results, columns=["Title", "Author", "Format-Year"])
        print(df)

        # Task 4: write CSV and JSON.
        df.to_csv(CSV_PATH, index=False)
        with JSON_PATH.open("w", encoding="utf-8") as json_file:
            json.dump(results, json_file, indent=2, ensure_ascii=False)

        print(f"Wrote {CSV_PATH}")
        print(f"Wrote {JSON_PATH}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
