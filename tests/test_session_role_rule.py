"""The second level of pandoscope/meta#195: the session role rule.

A session whose setup failed has no hooks of the environment, so the
instruction file that every repository checks in carries the rule: a
session without the composed role line in its context does no work and
tells the principal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers

RULE = [
    "## Session Role",
    "open with the line `# Role: <role>`",
    "holds no such line does no work",
    "tells the principal that the session has no role",
    "`# No role`",
]


def test_claude_md_states_the_session_role_rule(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    dst_path = render_answers(tmp_path, base_answers, "session-role")
    check_file_contents(dst_path / "CLAUDE.md", RULE)


@pytest.mark.parametrize(
    "store", ["decision-memory", "evidence-memory", "session-memory"]
)
def test_every_store_claude_md_states_the_session_role_rule(store: str) -> None:
    """The store subtemplates stamp CLAUDE.md verbatim."""
    text = (PROJECT_ROOT / store / "CLAUDE.md").read_text()
    for line in RULE:
        assert line in text, f"{store}/CLAUDE.md lacks {line!r}"
