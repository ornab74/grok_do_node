from __future__ import annotations

import json
import sys

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from common.browser import create_driver
from common.security_gate import SecurityGateError


REQUIRED_STATUS = {
    "Layer 1 Sandbox": "Namespace",
    "PID namespaces": "Yes",
    "Network namespaces": "Yes",
    "Seccomp-BPF sandbox": "Yes",
}


def _read_status_table(driver: object) -> tuple[dict[str, str], str]:
    def populated(current: object) -> bool:
        rows = current.find_elements(By.CSS_SELECTOR, "#sandbox-status tr")
        evaluation = current.find_element(By.ID, "evaluation").text.strip()
        return len(rows) >= len(REQUIRED_STATUS) and bool(evaluation)

    WebDriverWait(driver, 15).until(populated)
    observed: dict[str, str] = {}
    for row in driver.find_elements(By.CSS_SELECTOR, "#sandbox-status tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) == 2:
            observed[cells[0].text.strip()] = cells[1].text.strip()
    evaluation = driver.find_element(By.ID, "evaluation").text.strip()
    return observed, evaluation


def run_probe() -> dict[str, object]:
    driver = create_driver("CTD-sandbox-self-test/2.0")
    try:
        driver.get("chrome://sandbox/")
        observed, evaluation = _read_status_table(driver)
    finally:
        driver.quit()

    failed = {
        name: {"expected": expected, "observed": observed.get(name, "missing")}
        for name, expected in REQUIRED_STATUS.items()
        if observed.get(name) != expected
    }
    if evaluation != "You are adequately sandboxed.":
        failed["overall evaluation"] = {
            "expected": "You are adequately sandboxed.",
            "observed": evaluation or "missing",
        }
    if failed:
        raise SecurityGateError(
            "Chromium sandbox self-test failed: " + json.dumps(failed, sort_keys=True)
        )
    result = {
        "sandbox": "verified",
        "required_checks": REQUIRED_STATUS,
        "evaluation": evaluation,
    }
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    try:
        run_probe()
    except Exception as exc:
        print(f"SANDBOX PROBE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
