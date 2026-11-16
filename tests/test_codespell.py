"""The stamped `.codespellrc` and the codespell hook that reads it (#322).

The file is template-owned: one reviewed list that every stamped repo
carries, so a repo's lines can never drift from the render and no second
source can repeat a key. codespell rejects a repeated key outright
(exit 78) and silently ignores comma-less continuation lines, so each
list stays one key on one line.
"""

from __future__ import annotations

import configparser
import re
import shutil
import subprocess
from pathlib import Path

import copier
import pytest

from tests.render_support import render_answers


def _files(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` keyed by its relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _hook_rev(dst_path: Path) -> str:
    """The codespell version the stamped prek hook pins.

    Raises:
        AssertionError: the stamped config has no codespell repo entry.
    """
    config = (dst_path / ".pre-commit-config.yaml").read_text()
    match = re.search(r"codespell-project/codespell\n\s+rev: v(\S+)", config)
    assert match, "no codespell hook in the stamped .pre-commit-config.yaml"
    return match.group(1)


def test_each_list_is_one_key_on_one_line(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    dst_path = render_answers(tmp_path, base_answers, "codespell-keys")

    config = configparser.ConfigParser(strict=True, interpolation=None)
    config.read(dst_path / ".codespellrc")
    section = config["codespell"]
    assert section["ignore-words-list"] == "fount"  # codespell:ignore
    assert section["skip"] == "artifacts/naming/*"


def test_the_drift_check_leaves_a_fresh_stamp_clean(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The drift job's `copier recopy --overwrite` changes nothing on a
    stamp that has not been edited, `.codespellrc` included."""
    dst_path = render_answers(tmp_path, base_answers, "codespell-recopy")
    before = _files(dst_path)

    copier.run_recopy(
        dst_path=dst_path,
        answers_file=".copier-answers.agentic.yml",
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )

    assert _files(dst_path) == before


def test_the_pinned_codespell_honours_both_lists(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A listed word passes, the listed glob is skipped at any depth, and
    an unlisted typo outside the glob still fails, so codespell ran."""
    if shutil.which("uvx") is None:
        pytest.skip("the hook's codespell runs through uvx")
    dst_path = render_answers(tmp_path, base_answers, "codespell-run")
    naming = dst_path / "artifacts" / "naming" / "runs" / "001"
    naming.mkdir(parents=True)
    (naming / "round.md").write_text("recieve\n")  # codespell:ignore
    (dst_path / "listed.md").write_text("fount\n")  # codespell:ignore
    (dst_path / "control.md").write_text("recieve\n")  # codespell:ignore

    result = subprocess.run(
        [
            "uvx",
            f"codespell=={_hook_rev(dst_path)}",
            "listed.md",
            "artifacts/naming/runs/001/round.md",
            "control.md",
        ],
        cwd=dst_path,
        capture_output=True,
        text=True,
        check=False,
    )

    flagged = sorted({line.split(":")[0] for line in result.stdout.splitlines()})
    assert flagged == ["control.md"], result.stdout + result.stderr
