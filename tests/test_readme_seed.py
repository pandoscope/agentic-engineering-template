"""A stamp into an empty repo seeds a README that reaches every shared term (#256).

The consumer stamp that produced meta#150 rendered into a fresh git repo:
no README existed, the post-render prune had no root to walk from, and it
removed every vendored glossary term. The README is seeded once from the
project name and description, never overwritten later (the conventions
file's rule), and carries a glossary index linking every vendored term so
the prune's roots reach them whether or not the prune counts mentions
(pandoscope/disambiguate#84).
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers
from tests.test_shared_glossary import SHARED_TERMS

PRUNE = PROJECT_ROOT / "template" / "scripts" / "ci" / "prune_glossary.sh"


def _terms(repo: Path) -> set[str]:
    return {p.stem for p in (repo / "docs" / "glossary").glob("*.md")}


def test_an_empty_target_gets_a_readme_with_name_description_and_index(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    dst = render_answers(tmp_path, base_answers, "empty")

    readme = dst / "README.md"
    check_file_contents(
        readme,
        ["# Snake Farm", "A sample Snake farming project.", "## Glossary"],
    )
    text = readme.read_text(encoding="utf-8")
    for term in sorted(SHARED_TERMS | {"snake-farm"}):
        assert f"(docs/glossary/{term}.md)" in text, f"index misses {term}"


def test_an_existing_readme_is_left_untouched(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    dst = tmp_path / "owned"
    dst.mkdir()
    own = "# The consumer's own README\n\nHand-written, never stamped over.\n"
    (dst / "README.md").write_text(own, encoding="utf-8")

    render_answers(tmp_path, base_answers, "owned")

    assert (dst / "README.md").read_text(encoding="utf-8") == own


def test_a_stamp_into_an_empty_git_repo_keeps_every_term_through_the_prune(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    if shutil.which("uvx") is None:
        pytest.skip("the prune runs disambiguate through uvx")
    dst = tmp_path / "fresh"
    dst.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=dst, check=True)

    render_answers(tmp_path, base_answers, "fresh")
    subprocess.run([*git, "add", "-A"], cwd=dst, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "chore: stamp"], cwd=dst, check=True)
    before = _terms(dst)
    assert SHARED_TERMS <= before, "fixture: the stamp delivers every shared term"

    result = subprocess.run(
        ["bash", str(dst / "scripts" / "ci" / "prune_glossary.sh")],
        cwd=dst,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert _terms(dst) == before, f"prune removed {sorted(before - _terms(dst))}"
