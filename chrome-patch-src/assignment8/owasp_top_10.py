from pathlib import Path
import csv
import random
import re
import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from common.browser import create_driver

# Task 6: start with the exact page named in the assignment.
ASSIGNED_OWASP_URL = "https://owasp.org/www-project-top-ten/"

OUTPUT_DIR = Path("/results/assignment8")
OUTPUT_PATH = OUTPUT_DIR / "owasp_top_10.csv"
FIELDNAMES = ["Vulnerability Title", "href"]

MAX_ATTEMPTS = 4
WAIT_SECONDS = 25
BACKOFF_SECONDS = 2
MIN_JITTER = 2.5
MAX_JITTER = 5.5

# The assigned project page currently links to the released 2025 Top Ten.
# This XPath finds that release link without hard-coding the ten risks.
RELEASE_LINK_XPATH = (
    "//a["
    "contains(@href, '/Top10/2025/') and "
    "contains(normalize-space(.), 'OWASP Top Ten 2025')"
    "]"
)

# The lesson demonstrates XPath as DOM navigation: start from a useful element,
# move to a parent, then navigate to the related sibling/container. On the
# release page we first locate a heading containing the Top 10 section marker.
TOP_TEN_HEADING_XPATH = (
    "//*[self::h1 or self::h2 or self::h3]"
    "[contains(normalize-space(.), 'OWASP Top 10') or "
    "contains(normalize-space(.), 'OWASP Top Ten')]"
)

# Validation happens after structural DOM navigation. This is deliberately not
# used to locate ten hard-coded links with XPath.
TITLE_PATTERN = re.compile(r"^A(0[1-9]|10):2025\b")


def normalize_text(value):
    return " ".join((value or "").split())


def jitter_sleep(label, base=0.0):
    delay = base + random.uniform(MIN_JITTER, MAX_JITTER)
    print(f"{label}: {delay:.2f} seconds")
    time.sleep(delay)


def rank_from_title(title):
    match = TITLE_PATTERN.match(title)
    if not match:
        return None
    return int(match.group(1))


def load_with_retry(driver, url, description):
    """Navigate with bounded exponential backoff and randomized jitter."""
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"Loading {description} (attempt {attempt}/{MAX_ATTEMPTS})")
            driver.get(url)
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda browser: browser.find_element(By.TAG_NAME, "body")
            )
            jitter_sleep("Render delay")
            return
        except (TimeoutException, WebDriverException) as error:
            last_error = error
            if attempt == MAX_ATTEMPTS:
                break

            base = BACKOFF_SECONDS * (2 ** (attempt - 1))
            jitter_sleep(
                f"{description} was not ready; backoff before retry",
                base=base,
            )

    raise RuntimeError(
        f"Unable to load {description} after {MAX_ATTEMPTS} attempts."
    ) from last_error


def find_release_url(driver):
    """Find the current release link from the exact assigned project page."""
    links = WebDriverWait(driver, WAIT_SECONDS).until(
        lambda browser: browser.find_elements(By.XPATH, RELEASE_LINK_XPATH) or False
    )

    for link in links:
        href = link.get_attribute("href")
        if href:
            return href

    raise RuntimeError("The OWASP project page did not expose a release href.")


def candidate_section_containers(driver):
    """
    Use the same XPath parent/sibling idea demonstrated in the lesson.

    Starting from a Top Ten heading, examine its parent and nearby siblings as
    candidate containers. The risk links are then found normally inside those
    containers rather than being hard-coded into one giant XPath expression.
    """
    headings = driver.find_elements(By.XPATH, TOP_TEN_HEADING_XPATH)
    containers = []

    for heading in headings:
        # Lesson pattern: go up to the parent.
        parent = heading.find_element(By.XPATH, "..")
        containers.append(parent)

        # Lesson pattern: navigate to following sibling containers.
        siblings = parent.find_elements(By.XPATH, "following-sibling::*")
        containers.extend(siblings[:4])

        # Some OWASP layouts put the heading and list under a larger wrapper.
        try:
            grandparent = parent.find_element(By.XPATH, "..")
            containers.append(grandparent)
        except WebDriverException:
            pass

    # If the heading structure changes, main remains a structural fallback,
    # rather than an XPath that already contains A01 through A10.
    containers.extend(driver.find_elements(By.CSS_SELECTOR, "main"))
    return containers


def collect_top_ten_from_dom(driver):
    """Collect A01-A10 after XPath has navigated us to likely DOM sections."""
    by_rank = {}

    for container in candidate_section_containers(driver):
        # As in the lesson, once the correct section is reached, collect links
        # from inside that section with a simple selector.
        for link in container.find_elements(By.CSS_SELECTOR, "a"):
            title = normalize_text(link.text)
            href = link.get_attribute("href")
            rank = rank_from_title(title)

            if rank is None or not href or rank in by_rank:
                continue

            by_rank[rank] = {
                "Vulnerability Title": title,
                "href": href,
            }

        if len(by_rank) == 10:
            break

    return [by_rank[rank] for rank in range(1, 11) if rank in by_rank]


def wait_for_top_ten(driver):
    """Wait until structural DOM navigation yields all ten vulnerability links."""
    def all_ten_loaded(browser):
        results = collect_top_ten_from_dom(browser)
        return results if len(results) == 10 else False

    return WebDriverWait(driver, WAIT_SECONDS).until(all_ten_loaded)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    driver = create_driver()

    try:
        # Task 6: Selenium reads the exact page supplied by the assignment.
        load_with_retry(
            driver,
            ASSIGNED_OWASP_URL,
            "the assigned OWASP Top Ten page",
        )

        release_url = find_release_url(driver)
        print(f"Discovered release URL from assigned page: {release_url}")

        # Polite pause before navigating to the linked release page.
        jitter_sleep("Ethical delay before following OWASP release link")

        load_with_retry(
            driver,
            release_url,
            "the OWASP Top Ten 2025 release page",
        )

        # Task 6: XPath parent/sibling navigation identifies the relevant DOM
        # area, then links in that section are collected into dictionaries.
        try:
            results = wait_for_top_ten(driver)
        except TimeoutException as error:
            raise RuntimeError(
                "The OWASP release page loaded, but structural XPath navigation "
                "did not expose all 10 vulnerability links."
            ) from error

        print(results)

        # Task 6: write the list of dictionaries to CSV.
        with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(results)

        print(f"Wrote {OUTPUT_PATH}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
