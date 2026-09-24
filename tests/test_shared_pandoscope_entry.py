"""The shared Pandoscope glossary entry (skills#201): every stamped repo
carries it, and the pandoscope repo, whose slug is the product's name,
gets it in place of its seeded project entry."""

from __future__ import annotations

from pathlib import Path

from tests.render_support import render_answers


def test_every_project_carries_the_shared_pandoscope_entry(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    # The product entry ships to every stamped repo, beside the repo's
    # own seeded project entry (skills#201).
    dst_path = render_answers(tmp_path, base_answers, "any-project")
    glossary = dst_path / "docs" / "glossary"
    entry = (glossary / "pandoscope.md").read_text()
    assert entry.startswith("## Pandoscope\n")
    assert "command line" in entry
    assert (glossary / f"{base_answers['agentic_project_slug']}.md").exists()


def test_the_pandoscope_repo_gets_the_shared_entry_not_a_seed(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    # The pandoscope repo's slug is the product's name: its seeded
    # project entry would land on the shared entry's path. The seed is
    # skipped there, so the shared entry renders and keeps updating.
    answers = {
        **base_answers,
        "agentic_project_name": "Pandoscope",
        "agentic_project_slug": "pandoscope",
        "agentic_project_description": "SEEDED PROJECT DESCRIPTION",
    }
    dst_path = render_answers(tmp_path, answers, "pandoscope")
    entry = (dst_path / "docs" / "glossary" / "pandoscope.md").read_text()
    assert "command line" in entry
    assert "SEEDED PROJECT DESCRIPTION" not in entry
