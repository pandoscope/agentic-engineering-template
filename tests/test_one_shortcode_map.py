"""The org has one shortcode map, and only the waybill subtemplate seeds it.

pandoscope/pandoscope#61: the shortcodes land in every order's name and
every reference in a reply, so a second map drifts from the first.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest
import yaml

from tests.render_support import PROJECT_ROOT, render_answers

# Every file name a shortcode map has carried.
MAP_NAMES = frozenset({"shortcodes.json", "repo-codes.json"})
SUBTEMPLATES = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())[
    "agentic_subtemplate"
]["choices"]


def _maps(root: Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name in MAP_NAMES and ".git" not in path.relative_to(root).parts
    }


@pytest.mark.parametrize(
    "subtemplate",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True))
        if name == "session-memory"
        else name
        for name in SUBTEMPLATES
    ],
)
def test_only_the_waybill_seeds_a_shortcode_map(
    tmp_path: Path, subtemplate: str, base_answers: dict[str, str]
) -> None:
    answers = {**base_answers, "agentic_subtemplate": subtemplate}
    root = render_answers(tmp_path, answers, subtemplate)
    expected = {"shortcodes.json"} if subtemplate == "waybill" else set()
    assert _maps(root) == expected


@pytest.mark.xfail(strict=True)
def test_the_waybill_seeds_the_map_in_the_format_the_ledger_reads(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    """Shortcode to repository under `repos`, empty until the org adds its own;
    the README says that a name takes the lowercase form."""
    answers = {**base_answers, "agentic_subtemplate": "waybill"}
    root = render_answers(tmp_path, answers, "waybill")
    seed = json.loads((root / "shortcodes.json").read_text())
    assert seed["repos"] == {}
    assert set(seed) == {"_comment", "repos"}
    readme = (root / "README.md").read_text()
    assert "lowercase form" in readme
    assert "the one shortcode map" in readme
