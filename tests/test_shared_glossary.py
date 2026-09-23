"""The shared glossary terms stamped into every generated repo.

Cross-repo vocabulary has one canonical home — `template/docs/glossary/`
— and is distributed by the ordinary scaffold render, so a definition
change rides the same review path as any other template change.

Nothing under `template/` is ever a glossary root (disambiguate walks up
and finds the repo-root `docs/glossary/` first), so the canonical files
are structurally unprunable. Every stamped *copy* carries the auto-prune
marker and can be removed by a repo that never links it.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import copier
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SOURCE_DIR = PROJECT_ROOT / "template" / "docs" / "glossary"

SHARED_TERMS = frozenset(
    {
        "decision-memory",
        "reinset",
        "decision-record",
        "grilling",
        "org",
        "pando",
        "preference-set",
        "principal",
        "pando-worker",
        "evidence-memory",
        "org-genome",
        "memory-repo",
        "session-memory",
        "record-contract",
        "agent-session",
        "template-stamp",
        "pandoscope-template",
    }
)

AUTO_PRUNE = "<!-- d10e: auto-prune -->"
LINK_RE = re.compile(r"\]\((?!https?:)([a-z0-9-]+)\.md\)")


def _render(tmp_path: Path, answers: dict[str, str], dst_name: str) -> Path:
    dst_path = tmp_path / dst_name
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally, copier would
        # otherwise render the latest RELEASE instead of this branch.
        vcs_ref="HEAD",
    )
    return dst_path


def test_shared_terms_are_stamped_into_generated_repos(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    dst_path = _render(tmp_path, base_answers, "consumer")

    stamped = {
        path.stem
        for path in (dst_path / "docs" / "glossary").glob("*.md")
        if path.stem in SHARED_TERMS
    }

    assert stamped == set(SHARED_TERMS)


def test_stamped_terms_carry_the_auto_prune_marker(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A consumer's copy consents to removal when it goes unlinked.

    Copier copies plain `.md` verbatim, so the marker has to be in the
    source file — there is no render-time injection to rely on.
    """
    dst_path = _render(tmp_path, base_answers, "marker")

    for term in SHARED_TERMS:
        body = (dst_path / "docs" / "glossary" / f"{term}.md").read_text()
        assert AUTO_PRUNE in body, f"{term}: stamped copy must consent to pruning"


def test_project_seed_term_does_not_link_the_shared_terms(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The seed term must not make shared terms reachable in consumers.

    Linking them from a stamped document would make every term reachable
    in every repo, so none would ever be orphaned and `prune` would have
    nothing to remove — defeating the whole mechanism.
    """
    dst_path = _render(tmp_path, base_answers, "seed")
    slug = base_answers["agentic_project_slug"]

    seed = (dst_path / "docs" / "glossary" / f"{slug}.md").read_text()

    assert not (set(LINK_RE.findall(seed)) & SHARED_TERMS)


def test_shared_terms_are_link_closed_and_acyclic() -> None:
    """`disambiguate --lint` fails a consumer on a dangling ref or a cycle."""
    edges: dict[str, set[str]] = {}
    for term in SHARED_TERMS:
        targets = set(LINK_RE.findall((SOURCE_DIR / f"{term}.md").read_text()))
        assert targets <= SHARED_TERMS, f"{term} links outside the set: {targets}"
        edges[term] = targets

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, trail: tuple[str, ...]) -> None:
        assert node not in visiting, f"cycle: {' -> '.join((*trail, node))}"
        if node in done:
            return
        visiting.add(node)
        for target in sorted(edges[node]):
            walk(target, (*trail, node))
        visiting.discard(node)
        done.add(node)

    for term in sorted(SHARED_TERMS):
        walk(term, ())


def test_shared_terms_are_repo_neutral() -> None:
    """Entries must read correctly in every consumer.

    `factory` is meta-owned vocabulary that is deliberately NOT promoted,
    so a shared entry must not reference it.
    """
    for term in SHARED_TERMS:
        body = (SOURCE_DIR / f"{term}.md").read_text()
        assert body.startswith("## "), f"{term}: entry must open with its H2 name"
        assert "factory" not in body.lower(), f"{term}: references un-promoted term"
        assert "this repo" not in body.lower(), f"{term}: repo-specific framing"


# Host tools the post-render _tasks invoke.
PRUNE_REQUIRED_TOOLS = ("git", "npx", "uvx", "prek")


def test_a_stamped_repo_keeps_only_the_shared_terms_it_links(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The stamp converges the glossary instead of accumulating it.

    A consumer receives every shared term and keeps the ones it links, so
    `disambiguate --lint` passes on a repo that did nothing wrong. Here
    the README links none of them, so the whole consenting branch goes
    and the repo's own seed term — which never consented — stays.

    Runs the real task list: the convergence has to happen during the
    stamp, or every consumer carries the same cleanup by hand.
    """
    missing = [tool for tool in PRUNE_REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"post-stamp prune needs host tools on PATH: {missing}")

    dst_path = tmp_path / "consumer"
    dst_path.mkdir()
    subprocess.run(["git", "init"], cwd=dst_path, check=True, capture_output=True)
    (dst_path / "README.md").write_text("# Consumer\n", encoding="utf-8")

    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        vcs_ref="HEAD",
    )

    glossary = dst_path / "docs" / "glossary"
    survivors = {path.stem for path in glossary.glob("*.md")}

    assert not (survivors & SHARED_TERMS), "unlinked shared terms must be pruned"
    assert base_answers["agentic_project_slug"] in survivors, (
        "the repo's own seed term never consented and must survive"
    )
