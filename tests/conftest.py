from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import copier
import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def load_module(name: str, path: Path) -> ModuleType:
    """Import a module from an explicit file path.

    The guard scripts and tools are plain files, not packages; every
    test module loads them through this single helper.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def base_answers() -> dict[str, str]:
    return {
        "agentic_project_name": "Snake Farm",
        "agentic_project_description": "A sample Snake farming project.",
        "agentic_project_slug": "snake-farm",
        "agentic_precommit": "prek",
        "agentic_forge": "github",
        "agentic_repo_owner": "actions-user",
        "agentic_merge_approvers": "actions-user",
    }


@pytest.fixture
def render_project(tmp_path: Path, base_answers: dict[str, str]):
    """Render the Copier template into an isolated directory under ``tmp_path``."""

    def _render(
        *,
        dst_name: str | None = None,
        **overrides: str,
    ) -> Path:
        answers = {**base_answers, **overrides}
        slug = answers.get("agentic_project_slug", "project")
        dst_path = tmp_path / (dst_name or slug)
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

    return _render
