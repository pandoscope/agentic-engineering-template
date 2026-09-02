"""One glossary prune script for every repo the template stamps (#203, #210).

Consumers converge by deletion: the post-stamp task, the update workflow
and the drift job run `scripts/ci/prune_glossary.sh`, and what it removes
is committed. The template root runs the same script with
`--fail-on-removal`: its glossary is render output pinned by
`test_self_application.py` and kept reachable by README.md's index, so a
removal is a defect — named, restored, exit 1.

The stores vendor the script (copier renders one _subdirectory at a
time), pinned identical here like the shared workflows are.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "template" / "scripts" / "ci" / "prune_glossary.sh"
STORES = ("session-memory", "decision-memory", "evidence-memory")
ANSWERS = ".copier-answers.agentic.yml"


def _pin() -> str:
    """copier.yml's default for agentic_disambiguate_version."""
    in_question = False
    for line in (PROJECT_ROOT / "copier.yml").read_text().splitlines():
        if line.startswith("agentic_disambiguate_version:"):
            in_question = True
        elif in_question and line.strip().startswith("default:"):
            return line.split(":", 1)[1].strip().strip('"')
        elif in_question and line and not line[0].isspace():
            in_question = False
    raise AssertionError("copier.yml has no agentic_disambiguate_version default")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, *, answers: bool) -> Path:
    """A committed repo with the root's own linking documents and glossary.

    `answers=True` makes it a stamped consumer (pin from the answers
    file); `answers=False` makes it the template root (pin from
    copier.yml's default).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
        shutil.copy2(PROJECT_ROOT / name, repo / name)
    shutil.copytree(PROJECT_ROOT / "docs", repo / "docs")
    (repo / "scripts" / "ci").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "ci" / SCRIPT.name)
    if answers:
        (repo / ANSWERS).write_text(
            f'_commit: v0\nagentic_disambiguate_version: "{_pin()}"\n'
        )
    else:
        shutil.copy2(PROJECT_ROOT / "copier.yml", repo / "copier.yml")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _prune(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ci/prune_glossary.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _unlink_reinset(repo: Path) -> None:
    readme = repo / "README.md"
    lines = readme.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if "docs/glossary/reinset.md" not in line]
    assert len(kept) == len(lines) - 1, "fixture: README links reinset exactly once"
    readme.write_text("".join(kept), encoding="utf-8")


def _terms(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / "docs" / "glossary").glob("*.md"))


@pytest.fixture(autouse=True)
def _needs_uvx() -> None:
    if shutil.which("uvx") is None:
        pytest.skip("the prune runs disambiguate through uvx")


@pytest.mark.parametrize("store", STORES)
def test_store_copies_match_the_template_script(store: str) -> None:
    copy = PROJECT_ROOT / store / "scripts" / "ci" / SCRIPT.name
    assert copy.read_bytes() == SCRIPT.read_bytes(), (
        f"{copy} drifted from the template copy — change it once and copy"
    )


def test_a_consumer_prunes_unlinked_terms_and_reports_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path, answers=True)
    _unlink_reinset(repo)

    result = _prune(repo)

    assert result.returncode == 0, result.stderr
    assert "removed unlinked term(s)" in result.stdout
    assert "docs/glossary/reinset.md" in result.stdout
    assert "reinset.md" not in _terms(repo)


def test_the_root_as_committed_survives_its_own_prune(tmp_path: Path) -> None:
    repo = _repo(tmp_path, answers=False)
    terms = _terms(repo)

    result = _prune(repo, "--fail-on-removal")

    assert result.returncode == 0, result.stderr
    assert _terms(repo) == terms


def test_fail_on_removal_names_and_restores_the_term(tmp_path: Path) -> None:
    repo = _repo(tmp_path, answers=False)
    _unlink_reinset(repo)

    result = _prune(repo, "--fail-on-removal")

    assert result.returncode == 1
    assert "REMOVED TERMS" in result.stderr
    assert "docs/glossary/reinset.md" in result.stderr
    assert "(restored)" in result.stderr
    assert "reinset.md" in _terms(repo), "a tracked term the prune removed is put back"


def test_the_answers_file_pin_wins_over_a_copier_yml(tmp_path: Path) -> None:
    """A stamped repo that is itself a template reads its own stamp's pin."""
    repo = _repo(tmp_path, answers=True)
    (repo / "copier.yml").write_text(
        'agentic_disambiguate_version:\n  type: str\n  default: "0.0.0-not-a-release"\n'
    )

    result = _prune(repo)

    assert result.returncode == 0, result.stderr


def test_a_repo_without_a_glossary_has_nothing_to_prune(tmp_path: Path) -> None:
    """The stores ship the script and no glossary; the step must not error."""
    repo = _repo(tmp_path, answers=True)
    shutil.rmtree(repo / "docs" / "glossary")

    result = _prune(repo)

    assert result.returncode == 0, result.stderr
    assert "nothing to prune" in result.stdout


def test_a_missing_pin_is_its_own_loud_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path, answers=False)
    (repo / "copier.yml").write_text("agentic_project_name:\n  type: str\n")

    result = _prune(repo)

    assert result.returncode == 1
    assert "agentic_disambiguate_version" in result.stderr


def test_an_unknown_argument_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, answers=False)

    result = _prune(repo, "--dry-run")

    assert result.returncode == 2
    assert "unknown argument" in result.stderr
