"""Render and contract tests for the waybill subtemplate.

Selected by `agentic_subtemplate=waybill`, it stamps an org's ORDER
repo: the orders whose pull requests fire review sessions, the
dispositions that answer their findings, and the check that validates
every order before a session reads it.
"""

from __future__ import annotations

from pathlib import Path

import copier
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SUBTEMPLATE = PROJECT_ROOT / "waybill"

WAYBILL_FILES = frozenset(
    {
        ".copier-answers.agentic.yml",
        ".github/workflows/order-check.yml",
        ".github/workflows/template-update.yml",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
    }
)


def _render_waybill(tmp_path: Path) -> Path:
    dst_path = tmp_path / "waybill"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "waybill"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally, copier would
        # otherwise render the latest RELEASE instead of this branch.
        vcs_ref="HEAD",
    )
    return dst_path


@pytest.mark.xfail(strict=True, reason="#307: the waybill subtemplate is a stub")
def test_waybill_render_produces_exactly_the_waybill_files(tmp_path: Path) -> None:
    dst_path = _render_waybill(tmp_path)
    rendered = {
        path.relative_to(dst_path).as_posix()
        for path in dst_path.rglob("*")
        if path.is_file()
    }
    assert rendered == WAYBILL_FILES


def test_the_answers_file_records_only_the_subtemplate(tmp_path: Path) -> None:
    """The waybill needs nothing from the org to work: its texts speak of
    "this org", so no org name is stamped in and none can go stale."""
    dst_path = _render_waybill(tmp_path)
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()

    assert "agentic_subtemplate: waybill" in answers
    assert "agentic_repo_owner" not in answers
    assert "agentic_project_name" not in answers


@pytest.mark.xfail(strict=True, reason="#307: the waybill subtemplate is a stub")
def test_the_readme_states_the_order_contract(tmp_path: Path) -> None:
    """An order is a branch, a file and a pull request; the name decides
    which Routine fires, and it ends in the repo's short code and the
    pull request's number, because every repo numbers its pull requests
    from 1. Findings are answered here, not in forge comments."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "order/<name>" in readme
    assert "orders/<name>.yml" in readme
    assert "short code" in readme
    assert "review-spec-fidelity-opus-<code><n>" in readme
    assert "dispositions/<order id>.yml" in readme
    assert "## One way only" in readme


@pytest.mark.xfail(strict=True, reason="#307: the waybill subtemplate is a stub")
def test_the_agent_rules_keep_the_waybill_one_way(tmp_path: Path) -> None:
    """Orders may pin Pandoscope repositories read-only, but nothing of the
    org's own work is written into a Pandoscope repository."""
    rules = (_render_waybill(tmp_path) / "AGENTS.md").read_text()

    assert "one way" in rules.lower()
    assert "Never write a token, a secret or a session transcript here." in rules


@pytest.mark.xfail(strict=True, reason="#307: the waybill subtemplate is a stub")
def test_every_order_is_checked_before_a_session_reads_it(tmp_path: Path) -> None:
    check = (
        _render_waybill(tmp_path) / ".github" / "workflows" / "order-check.yml"
    ).read_text()

    assert 'paths: ["orders/*.yml"]' in check
    assert "order check" in check


@pytest.mark.xfail(strict=True, reason="#307: the waybill subtemplate is a stub")
def test_the_readme_is_vendored(tmp_path: Path) -> None:
    """What a waybill IS belongs to the template: an edit made in one org's
    waybill is replaced on the next update instead of forking the contract."""
    dst_path = _render_waybill(tmp_path)
    (dst_path / "README.md").write_text("# Edited in the waybill\n")

    copier.run_recopy(
        dst_path=dst_path,
        answers_file=".copier-answers.agentic.yml",
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )

    assert (dst_path / "README.md").read_text() == (
        SUBTEMPLATE / "README.md"
    ).read_text()
