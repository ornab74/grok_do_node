from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_source_fix", ROOT / "chromium" / "verify_source_fix.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceVerifierTests(unittest.TestCase):
    def _tree(self, source: str, tests: str) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        directory = root / "sandbox" / "linux" / "syscall_broker"
        directory.mkdir(parents=True)
        (directory / "broker_file_permission.cc").write_text(source, encoding="utf-8")
        (directory / "broker_file_permission_unittest.cc").write_text(tests, encoding="utf-8")
        return temporary, root

    def test_vulnerable_parent_only_validator_is_rejected(self) -> None:
        temporary, root = self._tree("ContainsParentReference(path)", '"/tmp/."')
        with temporary:
            self.assertTrue(MODULE.verify(root))

    def test_missing_broker_regression_suite_is_rejected(self) -> None:
        temporary, root = self._tree("ContainsParentOrSelfReference(path)", '"/tmp/.."')
        with temporary:
            self.assertIn(
                "BrokerFilePermission regression test suite is absent",
                MODULE.verify(root),
            )

    def test_parent_and_self_validator_with_regression_is_accepted(self) -> None:
        temporary, root = self._tree("ContainsParentOrSelfReference(path)", 'BrokerFilePermission "/."')
        with temporary:
            self.assertEqual(MODULE.verify(root), [])


if __name__ == "__main__":
    unittest.main()
