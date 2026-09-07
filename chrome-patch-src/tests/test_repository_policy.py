from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import stat
import subprocess
import sys
import unittest

from common import security_gate


ROOT = Path(__file__).resolve().parents[1]


class RepositoryPolicyTests(unittest.TestCase):
    def test_repository_sha256_manifest_is_complete_and_valid(self) -> None:
        excluded_directories = {".git", "__pycache__", ".pytest_cache", "run-logs", "test-output"}
        expected_files = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.name != "SHA256SUMS"
            and path.suffix != ".pyc"
            and not excluded_directories.intersection(path.relative_to(ROOT).parts)
        }
        manifest: dict[str, str] = {}
        for line in (ROOT / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertNotIn(relative, manifest)
            manifest[relative.removeprefix("./")] = digest
        self.assertEqual(set(manifest), expected_files)
        for relative, expected_digest in manifest.items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected_digest, relative)

    def test_security_scripts_are_executable(self) -> None:
        for relative in (
            "install.sh",
            "chromium/build_chromium.sh",
            "scripts/generate-sha256.sh",
            "scripts/ghcr-image.sh",
            "scripts/refresh-runtime-image.sh",
            "scripts/verify-runtime-profiles.py",
            "scripts/verify-container.sh",
            "scripts/view-results.sh",
        ):
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).stat().st_mode & stat.S_IXUSR)

    def test_source_lock_matches_runtime_and_dependencies(self) -> None:
        lock = json.loads((ROOT / "chromium" / "source.lock.json").read_text(encoding="utf-8"))
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(lock["chromium_version"], security_gate.APPROVED_VERSION)
        self.assertEqual(lock["sandbox_fix_commit"], security_gate.REQUIRED_FIX_COMMIT)
        self.assertEqual(
            lock["depot_tools_revision"], security_gate.APPROVED_DEPOT_TOOLS_REVISION
        )
        self.assertIn(f"ARG CHROMIUM_VERSION={lock['chromium_version']}", dockerfile)
        self.assertIn(f"ARG DEPOT_TOOLS_REVISION={lock['depot_tools_revision']}", dockerfile)
        self.assertIn(f"selenium=={lock['selenium_version']}", requirements)
        self.assertNotIn("webdriver-manager", requirements.lower())

    def test_pinned_ghcr_runtime_is_reproducible_and_secret_free(self) -> None:
        source_lock = json.loads(
            (ROOT / "chromium" / "source.lock.json").read_text(encoding="utf-8")
        )
        image_lock = json.loads(
            (ROOT / "chromium" / "runtime-image.lock.json").read_text(
                encoding="utf-8"
            )
        )
        script = (ROOT / "scripts" / "ghcr-image.sh").read_text(encoding="utf-8")
        refresh_script = (ROOT / "scripts" / "refresh-runtime-image.sh").read_text(
            encoding="utf-8"
        )
        refresh_dockerfile = (
            ROOT / "docker" / "Dockerfile.runtime-refresh"
        ).read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertEqual(image_lock["schema_version"], 1)
        self.assertEqual(image_lock["chromium_version"], source_lock["chromium_version"])
        self.assertEqual(image_lock["selenium_version"], source_lock["selenium_version"])
        self.assertRegex(image_lock["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            image_lock["immutable_reference"],
            f'{image_lock["image"]}@{image_lock["digest"]}',
        )
        self.assertTrue(image_lock["restore_requires_application_refresh"])
        self.assertIn(image_lock["image"], script)
        self.assertIn(image_lock["tag"], script)
        self.assertIn(image_lock["digest"], script)
        self.assertIn(image_lock["local_tag"], script)
        self.assertIn("set +x", script)
        self.assertIn("--password-stdin", script)
        self.assertIn("mktemp -d", script)
        self.assertIn("Docker daemon is not rootless", script)
        self.assertIn("write:packages", script)
        self.assertIn("read:packages", script)
        self.assertNotRegex(script, r"gh[pousr]_[A-Za-z0-9_]{20,}")
        self.assertIn("--restore-image", installer)
        self.assertIn("chrome-patch-image", installer)
        self.assertIn("refresh-runtime-image.sh", installer)
        self.assertIn('[[ "$MODE" == "skip-build" ]]', installer)
        self.assertIn("Verifying and refreshing the existing local image", installer)
        self.assertLess(
            installer.index('ghcr-image.sh" restore'),
            installer.index('$PROJECT_DIR/scripts/refresh-runtime-image.sh"'),
        )
        self.assertIn("Docker daemon is not rootless", refresh_script)
        self.assertIn("Dockerfile.runtime-refresh", refresh_script)
        self.assertIn("FROM ${BASE_IMAGE}", refresh_dockerfile)
        self.assertIn("cmp --silent /app/requirements.txt", refresh_dockerfile)
        self.assertIn("rm -rf /app", refresh_dockerfile)
        self.assertIn("COPY --chown=10001:10001 . /app", refresh_dockerfile)
        self.assertNotIn("pip install", refresh_dockerfile)
        self.assertNotIn("chromium-builder", refresh_dockerfile)
        self.assertNotIn("build_chromium.sh", refresh_dockerfile)

    def test_source_build_applies_and_tests_the_fix(self) -> None:
        script = (ROOT / "chromium" / "build_chromium.sh").read_text(encoding="utf-8")
        self.assertIn("git apply --check", script)
        self.assertIn("verify_source_fix.py", script)
        self.assertIn("sandbox_linux_unittests", script)
        self.assertIn("BrokerFilePermission", script)
        self.assertIn("DEPOT_TOOLS_REVISION", script)
        self.assertNotIn("git clone https://chromium.googlesource.com/chromium/tools/depot_tools", script)

    def test_runtime_browser_code_has_no_sandbox_bypass(self) -> None:
        browser = (ROOT / "common" / "browser.py").read_text(encoding="utf-8")
        probe = (ROOT / "common" / "sandbox_probe.py").read_text(encoding="utf-8")
        for switch in security_gate.FORBIDDEN_SWITCHES:
            self.assertNotIn(switch, browser)
        self.assertIn("--remote-debugging-pipe", browser)
        self.assertNotIn("ChromeDriverManager", browser)
        self.assertIn('"Layer 1 Sandbox": "Namespace"', probe)
        self.assertIn('WebDriverWait(driver, 15)', probe)
        self.assertIn('"Seccomp-BPF sandbox": "Yes"', probe)

    def test_dockerfile_does_not_install_debian_chromium(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"apt-get install[^\n]*\bchromium(?:-driver)?\b", dockerfile))
        self.assertIn("COPY --from=chromium-builder", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("ARG DEBIAN_IMAGE=debian:bookworm-slim", dockerfile)
        self.assertIn("ARG PYTHON_IMAGE=python:3.13.15-slim-bookworm", dockerfile)

    def test_compose_keeps_the_host_boundary_closed(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        for forbidden in (
            "privileged:",
            "cap_add:",
            "network_mode: host",
            "seccomp=unconfined",
            "/var/run/docker.sock",
            "/run/docker.sock",
            "CHROME_NO_SANDBOX",
        ):
            self.assertNotIn(forbidden, compose)
        for required in (
            "read_only: true",
            "cap_drop: [ALL]",
            "no-new-privileges:true",
            "network_mode: none",
            "internal: true",
            '"127.0.0.1:8501:8501"',
            "apparmor=chrome-patch-browser",
            "seccomp=./docker/chromium-seccomp.json",
        ):
            self.assertIn(required, compose)

    def test_runtime_profiles_are_narrow_and_self_validating(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify-runtime-profiles.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("exact Chromium namespaces", completed.stdout)
        apparmor = (ROOT / "docker" / "chrome-patch-browser.apparmor").read_text(
            encoding="utf-8"
        )
        self.assertIn("userns,", apparmor)
        self.assertIn("deny mount,", apparmor)
        self.assertNotIn("flags=(unconfined)", apparmor)

    def test_both_coursework_folders_are_wired_into_compose(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        for required in (
            "assignment8/get_books.py",
            "assignment8/owasp_top_10.py",
            "capstone/pipeline.py",
            "/results/assignment8",
            "/results/capstone",
        ):
            self.assertIn(required, compose)
        self.assertTrue((ROOT / "assignment8" / "fixtures" / "books.html").is_file())
        self.assertTrue((ROOT / "capstone" / "fixtures" / "weather.html").is_file())

    def test_installer_checks_integrity_before_docker(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        main_flow = installer.split("main() {", 1)[1]
        checksum_gate = main_flow.index("verify_repository_manifest --quiet")
        root_bootstrap = main_flow.index("root_bootstrap")
        self.assertLess(checksum_gate, root_bootstrap)
        self.assertIn('((EUID != 0))', installer)
        self.assertIn('DOCKER_HOST', installer)

    def test_installer_bootstraps_a_pinned_rootless_builder(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        for required in (
            "preflight_hardware",
            "MIN_BUILD_CPUS=16",
            "MIN_BUILD_MEMORY_MIB=30000",
            "MIN_BUILD_DISK_MIB=180000",
            "download.docker.com/linux/${DIST}/gpg",
            "docker-ce-rootless-extras",
            "dockerd-rootless-setuptool.sh install",
            "grep -qi rootless",
            "grep -qi seccomp",
            "install_rootlesskit_apparmor_profile",
            "/etc/apparmor.d/local/rootlesskit",
            "install_runtime_security_profiles",
            "verify-runtime-profiles.py",
            'readonly APP_DIR="/srv/chrome-patch"',
            "checkout --detach --force",
            "SOURCE_ARCHIVE_SHA256",
            "resolve_image_digest",
            '[[ ! -L "$APP_DIR" ]]',
            '[[ ! -L "$STATE_DIR" ]]',
            "chrome-patch-docker",
            "chrome-patch-compose",
            "chrome-patch-results",
            "chrome-patch-image",
        ):
            self.assertIn(required, installer)
        for forbidden in (
            "get.docker.com",
            "curl | sh",
            'usermod -aG docker',
            'usermod --append --groups docker',
            "cat > /etc/apparmor.d/usr.bin.rootlesskit",
        ):
            self.assertNotIn(forbidden, installer)

    def test_chromium_policy_blocks_local_file_and_download_access(self) -> None:
        policy = json.loads((ROOT / "docker" / "chromium-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["DownloadRestrictions"], 3)
        self.assertIn("file://*", policy["URLBlocklist"])
        self.assertEqual(policy["DefaultFileSystemReadGuardSetting"], 2)
        self.assertEqual(policy["DefaultFileSystemWriteGuardSetting"], 2)

    def test_proxy_allowlist_and_private_address_denial(self) -> None:
        squid = (ROOT / "docker" / "squid.conf").read_text(encoding="utf-8")
        for host in (
            ".durhamcounty.bibliocommons.com",
            ".owasp.org",
            ".timeanddate.com",
        ):
            self.assertIn(host, squid)
        self.assertLess(squid.index("http_access deny forbidden_destination"), squid.index("http_access allow approved_domains"))
        self.assertIn("169.254.0.0/16", squid)
        self.assertIn("http_access deny all", squid)


if __name__ == "__main__":
    unittest.main()
