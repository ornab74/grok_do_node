from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from common import security_gate


class SecurityGateTests(unittest.TestCase):
    def test_version_parser_requires_four_parts(self) -> None:
        self.assertEqual(
            security_gate.version_from_output("Chromium 151.0.7922.169"),
            security_gate.APPROVED_VERSION,
        )
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.version_from_output("Chromium unknown")

    def test_forbidden_switches_are_rejected(self) -> None:
        for switch in security_gate.FORBIDDEN_SWITCHES:
            with self.subTest(switch=switch):
                with self.assertRaises(security_gate.SecurityGateError):
                    security_gate.assert_no_forbidden_switches(["chromium", switch])

    def test_unsafe_environment_is_rejected(self) -> None:
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.assert_environment_safe({"CHROME_NO_SANDBOX": "1"})
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.assert_environment_safe({"CHROMIUM_FLAGS": "--no-sandbox"})
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.assert_environment_safe({"SCRAPER_PROXY": "http://attacker:3128"})
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.assert_environment_safe({"HTTPS_PROXY": "http://attacker:3128"})
        security_gate.assert_environment_safe(
            {
                "SCRAPER_PROXY": "http://egress-proxy:3128",
                "HTTP_PROXY": "http://egress-proxy:3128",
                "HTTPS_PROXY": "http://egress-proxy:3128",
            }
        )

    def test_metadata_requires_exact_version_and_fix(self) -> None:
        metadata = {
            "chromium_version": security_gate.APPROVED_VERSION,
            "source_revision": "a" * 40,
            "sandbox_fix_commit": security_gate.REQUIRED_FIX_COMMIT,
            "sandbox_fix_state": "applied",
            "depot_tools_revision": security_gate.APPROVED_DEPOT_TOOLS_REVISION,
            "platform": "linux-x86_64",
            "suid_sandbox_packaged": False,
        }
        security_gate.assert_metadata(metadata)
        metadata["chromium_version"] = "151.0.7922.168"
        with self.assertRaises(security_gate.SecurityGateError):
            security_gate.assert_metadata(metadata)

    def test_hash_manifest_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("chrome", "chromedriver", "SOURCE_METADATA.json"):
                (root / name).write_bytes(name.encode())
            lines = []
            for name in ("chrome", "chromedriver", "SOURCE_METADATA.json"):
                digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
                lines.append(f"{digest}  {name}\n")
            (root / "manifest.sha256").write_text("".join(lines), encoding="utf-8")
            security_gate.assert_manifest(root)
            (root / "chrome").write_bytes(b"changed")
            with self.assertRaises(security_gate.SecurityGateError):
                security_gate.assert_manifest(root)


if __name__ == "__main__":
    unittest.main()
