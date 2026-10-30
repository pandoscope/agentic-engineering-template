"""Render and contract tests for the waybill subtemplate.

Selected by `agentic_subtemplate=waybill`, it stamps an org's ORDER repo:
the orders whose pull requests fire review sessions,
the dispositions that answer their findings,
and the check that validates every order before a session reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import copier

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
        "dispositions/README.md",
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
        "short-codes.json",
    }
)

# Pandoscope is named on purpose (the one-way rule, the order check's tool),
# and `my-org` is the README's invented example; any other owner is a leak.
ALLOWED_OWNERS = frozenset({"pandoscope", "my-org", "org"})


def _render_waybill(tmp_path: Path) -> Path:
    dst_path = tmp_path / "waybill"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "waybill"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally,
        # copier would render the latest RELEASE instead of this branch.
        vcs_ref="HEAD",
    )
    return dst_path


def _recopy(dst_path: Path) -> None:
    copier.run_recopy(
        dst_path=dst_path,
        answers_file=".copier-answers.agentic.yml",
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )


def test_waybill_render_produces_exactly_the_waybill_files(tmp_path: Path) -> None:
    dst_path = _render_waybill(tmp_path)
    rendered = {
        path.relative_to(dst_path).as_posix()
        for path in dst_path.rglob("*")
        if path.is_file()
    }
    assert rendered == WAYBILL_FILES


def test_the_answers_file_records_only_the_subtemplate(tmp_path: Path) -> None:
    """The waybill needs nothing from the org to work.
    Its texts speak of "this org", so no org name is stamped in and none can go stale."""
    dst_path = _render_waybill(tmp_path)
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()

    assert "agentic_subtemplate: waybill" in answers
    assert "agentic_repo_owner" not in answers
    assert "agentic_project_name" not in answers


def test_the_waybill_names_no_organisation(tmp_path: Path) -> None:
    """Every repository a stamped file names belongs to Pandoscope or to the invented example.
    A literal org would be wrong for every other org that stamps a waybill."""
    dst_path = _render_waybill(tmp_path)
    reference = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9-]*)/[A-Za-z0-9._-]+(?:#\d+|:)")
    for relpath in sorted(WAYBILL_FILES - {".copier-answers.agentic.yml"}):
        text = (dst_path / relpath).read_text()
        owners = {m.group(1) for m in reference.finditer(text)}
        assert owners <= ALLOWED_OWNERS, f"{relpath} names {owners - ALLOWED_OWNERS}"


def test_the_readme_states_the_order_contract(tmp_path: Path) -> None:
    """An order is a branch, a file and a pull request, and its name decides which Routine fires.
    The name ends in the repo's short code and the pull request's number,
    because every repo numbers its pull requests from 1."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "order/<name>" in readme
    assert "orders/<name>.yml" in readme
    assert "review-spec-fidelity-opus-<code><n>" in readme
    assert "short-codes.json" in readme
    assert "dispositions/<name>.yml" in readme
    assert "## One way only" in readme


def test_the_dispositions_readme_states_the_format(tmp_path: Path) -> None:
    """Findings are answered here, not in forge comments, one file per order."""
    text = (_render_waybill(tmp_path) / "dispositions" / "README.md").read_text()

    assert "reviewed_head:" in text
    assert (
        'status: open           # or "fixed <org/repo@sha>", or "no-commit: <why>"'
        in text
    )


def test_the_agent_rules_keep_the_waybill_one_way(tmp_path: Path) -> None:
    """Orders may pin Pandoscope repositories read-only.
    Nothing of the org's own work is written into a Pandoscope repository."""
    rules = (_render_waybill(tmp_path) / "AGENTS.md").read_text()

    assert "one way" in rules.lower()
    assert "token, secret, session transcript" in rules


def test_every_order_is_checked_before_a_session_reads_it(tmp_path: Path) -> None:
    check = (
        _render_waybill(tmp_path) / ".github" / "workflows" / "order-check.yml"
    ).read_text()

    assert 'paths: ["orders/*.yml"]' in check
    assert "order check" in check


def test_the_readme_is_vendored(tmp_path: Path) -> None:
    """What a waybill IS belongs to the template.
    An edit made in one org's waybill is replaced on the next update instead of forking the contract."""
    dst_path = _render_waybill(tmp_path)
    (dst_path / "README.md").write_text("# Edited in the waybill\n")

    _recopy(dst_path)

    assert (dst_path / "README.md").read_text() == (
        SUBTEMPLATE / "README.md"
    ).read_text()


def test_the_short_codes_are_seeded_once(tmp_path: Path) -> None:
    """`short-codes.json` is the org's own list.
    A recopy that clobbered it would silently rename every order the org places."""
    dst_path = _render_waybill(tmp_path)
    org_owned = '{"my-org/my-repo": "mr"}\n'
    (dst_path / "short-codes.json").write_text(org_owned)

    _recopy(dst_path)

    assert (dst_path / "short-codes.json").read_text() == org_owned
