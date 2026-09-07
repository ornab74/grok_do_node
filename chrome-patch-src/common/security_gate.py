from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping


APPROVED_VERSION = "151.0.7922.169"
REQUIRED_FIX_COMMIT = "4298968d02fa7a24dccc65b03071af84c5418c38"
APPROVED_DEPOT_TOOLS_REVISION = "547d7e12fe104305c7de797d5a2b4155914ad362"
CHROMIUM_ROOT = Path("/opt/chromium")
FORBIDDEN_SWITCHES = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-seccomp-filter-sandbox",
    "--disable-namespace-sandbox",
    "--disable-web-security",
    "--ignore-certificate-errors",
    "--allow-running-insecure-content",
    "--allow-file-access-from-files",
    "--disable-site-isolation-trials",
    "--no-zygote",
    "--single-process",
    "--remote-debugging-address",
    "--remote-debugging-port",
    "--remote-allow-origins",
)
FORBIDDEN_ENVIRONMENT = (
    "CHROME_NO_SANDBOX",
    "ALLOW_DRIVER_DOWNLOAD",
    "SE_MANAGER_PATH",
    "SELENIUM_MANAGER_PATH",
    "SELENIUM_REMOTE_URL",
)
FLAG_ENVIRONMENT = (
    "CHROME_FLAGS",
    "CHROMIUM_FLAGS",
    "CHROME_OPTS",
    "CHROMIUM_OPTS",
)


class SecurityGateError(RuntimeError):
    """Raised when the browser or container does not match the approved boundary."""


def version_from_output(output: str) -> str:
    match = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", output)
    if not match:
        raise SecurityGateError(f"could not parse a four-part version from: {output!r}")
    return match.group(1)


def assert_no_forbidden_switches(values: Iterable[str]) -> None:
    joined = "\n".join(values).lower()
    for switch in FORBIDDEN_SWITCHES:
        if switch.lower() in joined:
            raise SecurityGateError(f"forbidden Chromium switch requested: {switch}")


def assert_environment_safe(environment: Mapping[str, str]) -> None:
    for name in FORBIDDEN_ENVIRONMENT:
        if environment.get(name, "").strip():
            raise SecurityGateError(f"forbidden browser override is set: {name}")
    assert_no_forbidden_switches(environment.get(name, "") for name in FLAG_ENVIRONMENT)
    proxy = environment.get("SCRAPER_PROXY", "").strip()
    if proxy and proxy != "http://egress-proxy:3128":
        raise SecurityGateError("SCRAPER_PROXY must use the private allowlisting proxy")
    expected_proxy = "http://egress-proxy:3128" if proxy else ""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = environment.get(name, "").strip()
        if value and value != expected_proxy:
            raise SecurityGateError(f"{name} does not match the approved egress proxy")


def _read_metadata(root: Path) -> dict[str, object]:
    try:
        return json.loads((root / "SOURCE_METADATA.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityGateError(f"invalid Chromium source metadata: {exc}") from exc


def assert_metadata(metadata: Mapping[str, object]) -> None:
    if metadata.get("chromium_version") != APPROVED_VERSION:
        raise SecurityGateError("Chromium source version is not the exact approved version")
    if metadata.get("sandbox_fix_commit") != REQUIRED_FIX_COMMIT:
        raise SecurityGateError("required Linux broker fix commit is not recorded")
    if metadata.get("sandbox_fix_state") not in {"applied", "already-present"}:
        raise SecurityGateError("Linux broker fix was not proven present during the build")
    if metadata.get("depot_tools_revision") != APPROVED_DEPOT_TOOLS_REVISION:
        raise SecurityGateError("Chromium build toolchain revision is not approved")
    if metadata.get("suid_sandbox_packaged") is not False:
        raise SecurityGateError("unexpected SUID sandbox helper in runtime package")
    if metadata.get("platform") != "linux-x86_64" or os.uname().machine != "x86_64":
        raise SecurityGateError("the approved browser build is linux-amd64 only")
    revision = str(metadata.get("source_revision", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SecurityGateError("invalid Chromium source revision in metadata")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_manifest(root: Path) -> None:
    manifest_path = root / "manifest.sha256"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SecurityGateError(f"Chromium hash manifest is unavailable: {exc}") from exc
    if not lines:
        raise SecurityGateError("Chromium hash manifest is empty")
    checked: set[str] = set()
    for line in lines:
        try:
            expected, relative = line.split("  ", 1)
        except ValueError as exc:
            raise SecurityGateError("malformed Chromium hash manifest") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise SecurityGateError("malformed digest in Chromium hash manifest")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise SecurityGateError("hash manifest escapes the Chromium root") from exc
        if not candidate.is_file() or _sha256(candidate) != expected:
            raise SecurityGateError(f"Chromium runtime file failed integrity check: {relative}")
        checked.add(relative)
    if not {"chrome", "chromedriver", "SOURCE_METADATA.json"}.issubset(checked):
        raise SecurityGateError("hash manifest does not cover required browser files")


def _run_version(binary: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecurityGateError(f"could not read version from {binary}: {exc}") from exc
    return version_from_output(result.stdout or result.stderr)


def assert_browser_pair(root: Path) -> None:
    chrome = root / "chrome"
    driver = root / "chromedriver"
    for path in (chrome, driver):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SecurityGateError(f"required executable is missing: {path}")
        if path.stat().st_mode & 0o022:
            raise SecurityGateError(f"browser executable is group/world writable: {path}")
    chrome_version = _run_version(chrome)
    driver_version = _run_version(driver)
    if chrome_version != APPROVED_VERSION or driver_version != APPROVED_VERSION:
        raise SecurityGateError(
            f"browser/driver must both be {APPROVED_VERSION}; got {chrome_version}/{driver_version}"
        )


def _proc_status() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key] = value.strip()
    return values


def assert_container_boundary() -> None:
    if os.geteuid() == 0:
        raise SecurityGateError("the scraper must never run as root")
    status = _proc_status()
    for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        if int(status.get(field, "1"), 16) != 0:
            raise SecurityGateError(f"Linux capabilities were not fully dropped: {field}")
    if status.get("NoNewPrivs") != "1":
        raise SecurityGateError("no-new-privileges is not active")
    if status.get("Seccomp") != "2":
        raise SecurityGateError("the outer container seccomp filter is not active")
    root_mount = None
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[4] == "/":
            root_mount = fields[5].split(",")
            break
    if root_mount is None or "ro" not in root_mount:
        raise SecurityGateError("the container root filesystem is not read-only")
    forbidden_paths = (
        Path("/var/run/docker.sock"),
        Path("/run/docker.sock"),
        Path("/dev/dri"),
        Path("/host"),
    )
    for path in forbidden_paths:
        if path.exists():
            raise SecurityGateError(f"forbidden host/device path is exposed: {path}")


def run_static_gate(
    command: Iterable[str],
    *,
    root: Path = CHROMIUM_ROOT,
    require_container: bool = True,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    environment = environment if environment is not None else os.environ
    assert_environment_safe(environment)
    assert_no_forbidden_switches(command)
    metadata = _read_metadata(root)
    assert_metadata(metadata)
    assert_manifest(root)
    if require_container:
        assert_container_boundary()
    # Execute only after source metadata and file hashes have succeeded.
    assert_browser_pair(root)
    return metadata
