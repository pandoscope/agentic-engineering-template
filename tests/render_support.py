"""Rendering helpers the generated-project suites share.

Every one of them renders the template into a tmp directory and reads
the result, so the render call and the file assertion live here rather
than in whichever suite happened to need them first.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import copier

PROJECT_ROOT = Path(__file__).parent.parent


def check_file_contents(
    file_path: Path,
    expected_strs: Sequence[str] = (),
    unexpect_strs: Sequence[str] = (),
) -> None:
    assert file_path.exists(), f"Expected file missing: {file_path}"
    file_content = file_path.read_text()
    for content in expected_strs:
        assert content in file_content, f"Expected {content!r} in {file_path}"
    for content in unexpect_strs:
        assert content not in file_content, f"Unexpected {content!r} in {file_path}"


def render_answers(tmp_path: Path, answers: dict[str, str], dst_name: str) -> Path:
    dst_path = tmp_path / dst_name
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally, copier would
        # otherwise render the latest RELEASE instead of this branch
        # (CI checkouts have no tags and already fall back to HEAD).
        vcs_ref="HEAD",
    )
    return dst_path
