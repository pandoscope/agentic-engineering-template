"""The glossary prune runs for real on the template root and removes nothing.

Consumers converge by deletion (#67, #203). This root's glossary is render
output pinned by `test_self_application.py` and kept reachable by the
Glossary index in README.md, so `scripts/dev/prune_glossary.sh` runs the
same pinned prune and treats a removal as a defect (#210): it names the
term, restores it, and exits non-zero.

The fixture is a copy of the root's own linking documents, so the test
also proves the root as committed survives its own prune.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "prune_glossary.sh"
# What reachability is computed over, plus the pin's source and the hook.
COPIED = ("README.md", "AGENTS.md", "CLAUDE.md", "copier.yml", "docs")


def _fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "root"
    repo.mkdir()
    for name in COPIED:
        src = PROJECT_ROOT / name
        if src.is_dir():
            shutil.copytree(src, repo / name)
        else:
            shutil.copy2(src, repo / name)
    (repo / "scripts" / "dev").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "dev" / SCRIPT.name)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    return repo


def _prune(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/dev/prune_glossary.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _needs_uvx() -> None:
    if shutil.which("uvx") is None:
        pytest.skip("the prune runs disambiguate through uvx")


def test_the_root_as_committed_survives_its_own_prune(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    terms = sorted(p.name for p in (repo / "docs" / "glossary").glob("*.md"))

    result = _prune(repo)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (repo / "docs" / "glossary").glob("*.md")) == terms


def test_a_dropped_readme_link_fails_naming_and_restoring_the_term(
    tmp_path: Path,
) -> None:
    repo = _fixture(tmp_path)
    readme = repo / "README.md"
    lines = readme.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if "docs/glossary/reinset.md" not in line]
    assert len(kept) == len(lines) - 1, "fixture: README links reinset exactly once"
    readme.write_text("".join(kept), encoding="utf-8")

    result = _prune(repo)

    assert result.returncode == 1
    assert "REMOVED TERMS" in result.stderr
    assert "docs/glossary/reinset.md" in result.stderr
    assert "(restored)" in result.stderr
    assert (repo / "docs" / "glossary" / "reinset.md").is_file(), (
        "a tracked term the prune removed is put back"
    )


def test_a_missing_pin_is_its_own_loud_failure(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    (repo / "copier.yml").write_text("agentic_project_name:\n  type: str\n")

    result = _prune(repo)

    assert result.returncode == 1
    assert "agentic_disambiguate_version" in result.stderr
