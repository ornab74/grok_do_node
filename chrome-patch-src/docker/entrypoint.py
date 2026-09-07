from __future__ import annotations

import os
import sys
from collections.abc import Iterable

from common.security_gate import run_static_gate


SANDBOX_PROBE_COMMAND = ("python", "-m", "common.sandbox_probe")
NON_BROWSER_COMMANDS = frozenset(
    {
        ("python", "assignment8/view_results.py"),
        ("python", "capstone/view_results.py"),
        ("python", "capstone/query_weather.py", "summary"),
        (
            "streamlit",
            "run",
            "capstone/dashboard.py",
            "--server.address=0.0.0.0",
            "--server.port=8501",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ),
    }
)


def requires_prelaunch_browser_probe(command: Iterable[str]) -> bool:
    normalized = tuple(command)
    return normalized != SANDBOX_PROBE_COMMAND and normalized not in NON_BROWSER_COMMANDS


def main() -> int:
    command = sys.argv[1:] or list(SANDBOX_PROBE_COMMAND)
    try:
        run_static_gate(command)
        if requires_prelaunch_browser_probe(command):
            from common.sandbox_probe import run_probe

            run_probe()
    except Exception as exc:
        print(f"SECURITY GATE REFUSED TO START: {exc}", file=sys.stderr)
        return 126
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
