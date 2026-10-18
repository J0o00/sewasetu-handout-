# conftest.py — shared pytest fixtures and helpers for Sewa Setu tests
# All fixtures that touch the database insert rows only under reserved
# test-mobile prefixes (9999900xxx) so cleanup is reliable and auditable.
# Tests that mutate the live database are guarded by a check that the
# count of real citizens remains unchanged after the test run.

import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Project-root resolution: works regardless of where pytest is invoked from.
# parents[0] = tests/
# parents[1] = sewasetu-handout/   <-- contains docker-compose.yml
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_in_app_container(cmd):
    """
    Run a command inside the running 'app' docker-compose service.

    Uses the directory containing docker-compose.yml as the working directory
    so this works when pytest is invoked from any location.

    Returns (exit_code, stdout, stderr).
    """
    full_cmd = ["docker", "compose", "exec", "-T", "app"] + cmd
    res = subprocess.run(
        full_cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode, res.stdout, res.stderr
