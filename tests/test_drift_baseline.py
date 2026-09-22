"""scripts/ci/drift_baseline.sh seeds a repo's drift baseline once (#269).

The drift hook fails on every finding a repo carries the day it
arrives. The seed grandfathers them: the script writes
`.drift-baseline.json` when the file is missing and leaves an existing
one alone, so only a committed `--write-baseline` ever shrinks it. The
copier task and the template-update workflow both run it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.render_support import PROJECT_ROOT
from tests.test_prune_glossary import _git, _pin

SCRIPT = PROJECT_ROOT / "template" / "scripts" / "ci" / "drift_baseline.sh"
BASELINE = ".drift-baseline.json"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("README.md", "AGENTS.md"):
        shutil.copy2(PROJECT_ROOT / name, repo / name)
    shutil.copytree(PROJECT_ROOT / "docs", repo / "docs")
    (repo / "scripts" / "ci").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "ci" / SCRIPT.name)
    (repo / "scripts" / "ci" / "disambiguate-version").write_text(f"{_pin()}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _seed(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ci/drift_baseline.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _needs_uvx() -> None:
    if shutil.which("uvx") is None:
        pytest.skip("the seed runs disambiguate through uvx")


@pytest.mark.xfail(strict=True, reason="red: no seed script yet (#269)")
def test_a_missing_baseline_is_written_from_the_current_findings(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    result = _seed(repo)

    assert result.returncode == 0, result.stderr
    baseline = json.loads((repo / BASELINE).read_text())
    assert baseline["version"] == 1
    assert isinstance(baseline["findings"], list)


@pytest.mark.xfail(strict=True, reason="red: no seed script yet (#269)")
def test_an_existing_baseline_is_left_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sentinel = '{"version": 1, "findings": ["README.md:unlinked-term:sentinel"]}\n'
    (repo / BASELINE).write_text(sentinel)

    result = _seed(repo)

    assert result.returncode == 0, result.stderr
    assert (repo / BASELINE).read_text() == sentinel
