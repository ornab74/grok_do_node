from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.robotparser import RobotFileParser


USER_AGENT = "CTD-secure-scraper/2.0 (+educational; low-rate)"


class ScrapingPolicyError(RuntimeError):
    """Raised when a live target fails the local safety policy."""


def _is_public(address: str) -> bool:
    return bool(ipaddress.ip_address(address).is_global)


def validate_https_target(url: str, allowed_hosts: set[str]) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https":
        raise ScrapingPolicyError("live scraping requires HTTPS")
    if parsed.username or parsed.password:
        raise ScrapingPolicyError("credentials in target URLs are forbidden")
    if parsed.port not in (None, 443):
        raise ScrapingPolicyError("only HTTPS port 443 is allowed")
    if host not in allowed_hosts:
        raise ScrapingPolicyError(f"host is not allowlisted: {host or '<missing>'}")
    if not os.getenv("SCRAPER_PROXY"):
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ScrapingPolicyError(f"DNS lookup failed for {host}: {exc}") from exc
        addresses = {answer[4][0] for answer in answers}
        if not addresses or any(not _is_public(address) for address in addresses):
            raise ScrapingPolicyError("target DNS resolved to non-public address space")
    return url


def assert_robots_allowed(url: str, allowed_hosts: set[str]) -> None:
    validate_https_target(url, allowed_hosts)
    parsed = urlsplit(url)
    robots_url = f"https://{parsed.hostname}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with build_opener().open(request, timeout=15) as response:
            body = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        raise ScrapingPolicyError(f"robots.txt could not be verified: {exc}") from exc
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.splitlines())
    if not parser.can_fetch(USER_AGENT, url):
        raise ScrapingPolicyError(f"robots.txt does not permit this URL: {url}")


def check_redirect_target(current_url: str, allowed_hosts: set[str]) -> None:
    validate_https_target(current_url, allowed_hosts)
