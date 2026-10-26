"""doctor.sh runs the repo's own checks from scripts/doctor.local.sh
when that file exists (#273), the same seam session-start.local.sh
gives SessionStart."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.render_support import render_answers


@pytest.fixture
def doctor(tmp_path: Path, base_answers: dict[str, str]) -> Path:
    dst_path = render_answers(tmp_path, base_answers, "doctor-local")
    return dst_path / "scripts" / "doctor.sh"


def run(doctor: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    # Stub host tools, so the result does not depend on this machine.
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for tool in ("git", "npx", "uvx", "gh", "prek"):
        stub = stubs / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DECISION_MEMORY_URL"}
    env["PATH"] = f"{stubs}:/usr/bin:/bin"
    return subprocess.run(
        ["bash", str(doctor)], capture_output=True, text=True, env=env
    )


def test_without_local_checks_doctor_passes(doctor: Path, tmp_path: Path) -> None:
    result = run(doctor, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "doctor.local.sh" not in result.stdout


def test_passing_local_checks_count_as_ok(doctor: Path, tmp_path: Path) -> None:
    (doctor.parent / "doctor.local.sh").write_text("echo 'gateway reachable'\n")
    result = run(doctor, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gateway reachable" in result.stdout
    assert "✓ doctor.local.sh" in result.stdout


def test_failing_local_checks_fail_doctor(doctor: Path, tmp_path: Path) -> None:
    (doctor.parent / "doctor.local.sh").write_text("echo 'gateway down'\nexit 3\n")
    result = run(doctor, tmp_path)
    assert result.returncode == 1
    assert "✗ doctor.local.sh" in result.stdout
