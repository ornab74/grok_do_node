from __future__ import annotations

import unittest

from docker.entrypoint import requires_prelaunch_browser_probe


class EntrypointCommandPolicyTests(unittest.TestCase):
    def test_browser_jobs_require_a_prelaunch_probe(self) -> None:
        for command in (
            ["python", "assignment8/get_books.py", "--fixture"],
            ["python", "capstone/pipeline.py", "--live"],
            ["/bin/sh", "-ec", "python -m pytest"],
        ):
            with self.subTest(command=command):
                self.assertTrue(requires_prelaunch_browser_probe(command))

    def test_probe_command_does_not_recursively_probe(self) -> None:
        self.assertFalse(
            requires_prelaunch_browser_probe(
                ["python", "-m", "common.sandbox_probe"]
            )
        )

    def test_exact_non_browser_commands_skip_the_dynamic_probe(self) -> None:
        for command in (
            ["python", "assignment8/view_results.py"],
            ["python", "capstone/view_results.py"],
            ["python", "capstone/query_weather.py", "summary"],
            [
                "streamlit",
                "run",
                "capstone/dashboard.py",
                "--server.address=0.0.0.0",
                "--server.port=8501",
                "--server.headless=true",
                "--browser.gatherUsageStats=false",
            ],
        ):
            with self.subTest(command=command):
                self.assertFalse(requires_prelaunch_browser_probe(command))

    def test_allowlist_rejects_extra_arguments_and_similar_paths(self) -> None:
        for command in (
            ["python", "assignment8/view_results.py", "--extra"],
            ["python", "assignment8/view_results.py.bak"],
            ["python", "capstone/query_weather.py", "../../etc/passwd"],
            ["/bin/sh", "-c", "python assignment8/view_results.py"],
        ):
            with self.subTest(command=command):
                self.assertTrue(requires_prelaunch_browser_probe(command))


if __name__ == "__main__":
    unittest.main()
