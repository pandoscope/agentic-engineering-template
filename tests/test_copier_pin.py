"""The drift check runs a pinned copier (#324).

An unpinned `uvx copier` resolves the newest release on every run, so a
copier release can change the drift verdict while neither the repo nor
the template changed. The drift check installs from the stamped
`scripts/ci/copier-requirements.txt`. Its pins mirror `uv.lock`, the
authoritative side: the template's own tests run under that copier.

The update job stays unpinned on purpose. Its pin would be the one of
the version being left, and a release that needs a newer copier could
then never be reached.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers

PIN_FILE = "scripts/ci/copier-requirements.txt"
PINNED_UVX = f"uvx --with-requirements {PIN_FILE} copier"


def _locked_version(package: str) -> str:
    """The version `uv.lock` resolves for ``package``.

    Raises:
        AssertionError: the lock has no single entry for ``package``.
    """
    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text())
    versions = [
        entry["version"] for entry in lock["package"] if entry["name"] == package
    ]
    assert len(versions) == 1, f"one uv.lock entry expected for {package}: {versions}"
    return versions[0]


def test_pin_file_mirrors_the_lock(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    dst_path = render_answers(tmp_path, base_answers, "copier-pin")

    pins = (dst_path / PIN_FILE).read_text().split()
    expected = [
        f"copier=={_locked_version('copier')}",
        f"copier-template-extensions=={_locked_version('copier-template-extensions')}",
    ]
    assert pins == expected, (
        "uv.lock moved: set the defaults of agentic_copier_version and "
        f"agentic_copier_template_extensions_version in copier.yml to {expected}"
    )
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        unexpect_strs=["agentic_copier_version"],
    )


def test_only_the_drift_check_is_pinned(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    dst_path = render_answers(tmp_path, base_answers, "copier-pin-workflows")
    workflows = dst_path / ".github" / "workflows"

    check_file_contents(workflows / "lint.yml", [f"{PINNED_UVX} recopy"])
    check_file_contents(
        workflows / "template-update.yml",
        ["uvx --with copier-template-extensions copier update"],
        unexpect_strs=[PIN_FILE],
    )


def test_no_pin_file_without_the_workflows(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Only the GitHub workflows run copier; a Forgejo stamp ships none."""
    answers = {
        **base_answers,
        "agentic_forge": "forgejo",
        "agentic_forgejo_host": "git.example.com",
    }
    dst_path = render_answers(tmp_path, answers, "copier-pin-forgejo")

    assert not (dst_path / PIN_FILE).exists()
