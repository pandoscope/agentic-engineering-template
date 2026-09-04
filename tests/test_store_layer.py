"""The vendored store layer's self-test runs in this repo's suite too.

The same suites run inside a store, driven by preferences-guard.yml.
Running them here as well means a template-side edit cannot ship a
broken preference-set lifecycle to every store that updates.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
STORE_ROOT = PROJECT_ROOT / "decision-memory"
STORE_TESTS = STORE_ROOT / ".github" / "store" / "tests"


def test_vendored_store_layer_self_test_passes() -> None:
    """The store layer's own unittest suites must pass in the template.

    Runs as a subprocess, discovering from the store root exactly as
    preferences-guard.yml does: the suites bootstrap sys.path relative
    to their own location, which only holds outside this pytest process.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "--start-directory",
            str(STORE_TESTS.relative_to(STORE_ROOT)),
        ],
        cwd=STORE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
