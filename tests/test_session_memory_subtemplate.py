"""Render and contract tests for the session-memory subtemplate.

Selected by `agentic_subtemplate=session-memory`, it stamps a session
STORE — a data repo of append-only thread events — with the store's
agent rules and the workflow that reports its ticket closes to the
ledger.

Deliberately thin next to the other store subtemplates: the recorder
that writes this store ships with the thread-ledger skill, so the
schema has exactly one home and this subtemplate vendors no copy of
it.
"""

from __future__ import annotations

from pathlib import Path

import copier
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SUBTEMPLATE = PROJECT_ROOT / "session-memory"

STORE_FILES = frozenset(
    {
        ".copier-answers.agentic.yml",
        ".github/workflows/close-loop.yml",
        ".github/workflows/ledger-guard.yml",
        ".github/workflows/render-ledger.yml",
        ".github/workflows/template-update.yml",
        ".github/workflows/ticket-closed.yml",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "repo-codes.json",
        # The glossary prune every stamped repo runs (#203); a store has
        # no glossary, so here it reports that and exits 0.
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
    }
)


def _render_store(tmp_path: Path) -> Path:
    dst_path = tmp_path / "session-memory"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "session-memory"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally, copier would
        # otherwise render the latest RELEASE instead of this branch.
        vcs_ref="HEAD",
    )
    return dst_path


def test_store_render_produces_exactly_the_store_files(tmp_path: Path) -> None:
    dst_path = _render_store(tmp_path)
    rendered = {
        path.relative_to(dst_path).as_posix()
        for path in dst_path.rglob("*")
        if path.is_file()
    }
    assert rendered == STORE_FILES


def test_the_answers_file_records_only_the_subtemplate(tmp_path: Path) -> None:
    """Project-scaffold questions are skipped for a store, so the
    answers file stays minimal — a store that recorded a project name
    would claim to be scaffolded from a template it never rendered."""
    dst_path = _render_store(tmp_path)
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()

    assert "agentic_subtemplate: session-memory" in answers
    assert "agentic_project_name" not in answers
    assert "agentic_project_kind" not in answers


def test_the_readme_is_vendored(tmp_path: Path) -> None:
    """The store is data, not the source of its own documentation: what
    a session store IS belongs to the template, so an edit made in the
    store is replaced on the next update rather than forking N
    descriptions of one contract."""
    dst_path = _render_store(tmp_path)
    (dst_path / "README.md").write_text("# Edited in the store\n")

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


def test_the_shortcode_map_is_seeded_once(tmp_path: Path) -> None:
    """`repo-codes.json` is the store's data, not the template's: the
    org's own shortcode entries live there, and a recopy that clobbered
    them would silently rename every ticket in the rendered view."""
    dst_path = _render_store(tmp_path)
    store_owned = '{"my-org/my-repo": "MR"}\n'
    (dst_path / "repo-codes.json").write_text(store_owned)

    copier.run_recopy(
        dst_path=dst_path,
        answers_file=".copier-answers.agentic.yml",
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )

    assert (dst_path / "repo-codes.json").read_text() == store_owned


def test_the_store_names_no_organisation(tmp_path: Path) -> None:
    """The rendered store must carry no org's names: the sender resolves
    its destination from a variable at run time, so a stamped file that
    named one would be wrong for every other org and stale for this
    one."""
    dst_path = _render_store(tmp_path)
    for relpath in sorted(STORE_FILES):
        text = (dst_path / relpath).read_text()
        assert "pandoscope" not in text, f"{relpath} names an organisation"


def test_default_render_carries_no_store_rules(tmp_path: Path) -> None:
    """The store's agent rules ship to session stores through this
    subtemplate, never to consumer repos — a consumer told its
    `ledger/` is append-only has been handed rules for a repo it is
    not."""
    dst_path = tmp_path / "consumer"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={
            "agentic_project_name": "Consumer",
            "agentic_project_description": "A consumer repo",
            "agentic_project_slug": "consumer",
            "agentic_repo_owner": "example",
            "agentic_merge_approvers": "example",
        },
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        vcs_ref="HEAD",
    )
    assert (dst_path / "AGENTS.md").read_text() != (
        SUBTEMPLATE / "AGENTS.md"
    ).read_text()
    assert "`ledger/` is append-only" not in (dst_path / "AGENTS.md").read_text()


# Copier renders one _subdirectory at a time, so a store cannot import
# the consumer copy of a shared workflow. Managed duplication, per
# AGENTS.md: declared here, and pinned identical by the test below.
SHARED_WITH_CONSUMERS = (
    ".github/workflows/template-update.yml.jinja",
    ".github/workflows/ticket-closed.yml.jinja",
)

CONSUMER_GITHUB = (
    PROJECT_ROOT / "template" / "{% if agentic_forge == 'github' %}.github{% endif %}"
)


@pytest.mark.parametrize("relpath", SHARED_WITH_CONSUMERS)
def test_shared_workflows_match_the_ones_consumers_get(relpath: str) -> None:
    """Four copies that DIFFER would mean the stores report and update
    by different rules than every other repo, which is exactly the
    drift the pinning exists to stop."""
    consumer = (CONSUMER_GITHUB / relpath.removeprefix(".github/")).read_bytes()
    for subtemplate in ("session-memory", "decision-memory", "evidence-memory"):
        store = (PROJECT_ROOT / subtemplate / relpath).read_bytes()
        assert store == consumer, (
            f"{relpath} has drifted in the {subtemplate} subtemplate — "
            "change it once and copy, or the repos fork the contract"
        )


def test_every_store_reports_its_ticket_closes() -> None:
    """A store mints tickets that ledger threads reference, so a store
    without the sender is a hole in the loop: its closes reach the
    ledger only when somebody runs the sweep by hand."""
    for subtemplate in ("session-memory", "decision-memory", "evidence-memory"):
        sender = (
            PROJECT_ROOT / subtemplate / ".github/workflows/ticket-closed.yml.jinja"
        )
        assert sender.is_file(), f"{subtemplate} ships no ticket-close sender"
