from __future__ import annotations

import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService

from common.security_gate import APPROVED_VERSION, SecurityGateError, assert_no_forbidden_switches


BASE_ARGUMENTS = (
    "--headless=new",
    "--remote-debugging-pipe",
    "--disable-background-networking",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-gpu",
    "--disable-quic",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--window-size=1920,1080",
)


def browser_arguments(proxy: str = "") -> list[str]:
    arguments = list(BASE_ARGUMENTS)
    if proxy:
        if proxy != "http://egress-proxy:3128":
            raise SecurityGateError("only the private allowlisting proxy is permitted")
        arguments.extend((f"--proxy-server={proxy}", "--proxy-bypass-list=<-loopback>"))
    assert_no_forbidden_switches(arguments)
    return arguments


def create_driver(user_agent: str = "CTD-secure-scraper/2.0") -> webdriver.Chrome:
    binary = Path(os.environ.get("CHROMIUM_BINARY", "/opt/chromium/chrome"))
    driver_path = Path(os.environ.get("CHROMEDRIVER_PATH", "/opt/chromium/chromedriver"))
    if binary != Path("/opt/chromium/chrome") or driver_path != Path("/opt/chromium/chromedriver"):
        raise SecurityGateError("browser and driver paths are immutable in the hardened image")

    options = webdriver.ChromeOptions()
    options.binary_location = str(binary)
    for argument in browser_arguments(os.environ.get("SCRAPER_PROXY", "").strip()):
        options.add_argument(argument)
    options.add_argument(f"--user-agent={user_agent}")
    options.add_experimental_option(
        "prefs",
        {
            "download_restrictions": 3,
            "profile.default_content_setting_values.automatic_downloads": 2,
            "profile.default_content_setting_values.clipboard": 2,
            "profile.default_content_setting_values.geolocation": 2,
            "profile.default_content_setting_values.images": 2,
            "profile.default_content_setting_values.media_stream": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.sensors": 2,
        },
    )
    options.set_capability("acceptInsecureCerts", False)
    service = ChromeService(executable_path=str(driver_path), log_output=os.devnull)
    driver = webdriver.Chrome(service=service, options=options)
    if driver.capabilities.get("browserVersion") != APPROVED_VERSION:
        driver.quit()
        raise SecurityGateError("the running browser does not match the approved version")
    driver.set_page_load_timeout(35)
    return driver
