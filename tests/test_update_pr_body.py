"""The updater's join path keeps the PR's own body (#218).

Joining a stale `chore/template-update-*` PR used to rewrite its body
with the workflow's template. A PR a human opened carries the canonical
`ADVANCES ...` reference there, so the join turned the ticket gate red
on a PR that had passed it. The composer keeps that text verbatim and
appends the release notes under a marker it owns, replacing only the
section it wrote itself.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSER = ROOT / "template" / "scripts" / "ci" / "template-update-body.sh"
MARKER = "<!-- agentic-template-update -->"


def compose(tmp_path: Path, release: str, existing: str | None = None) -> str:
    release_file = tmp_path / "release.md"
    release_file.write_text(release)
    args = [str(release_file)]
    if existing is not None:
        existing_file = tmp_path / "existing.md"
        existing_file.write_text(existing)
        args.append(str(existing_file))
    proc = subprocess.run(
        ["bash", str(COMPOSER), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout


def test_a_fresh_pr_body_is_the_release_section(tmp_path: Path) -> None:
    out = compose(tmp_path, "Automated update v1 -> v2.\n")
    assert out == f"{MARKER}\n\nAutomated update v1 -> v2.\n"


def test_a_joined_body_survives_verbatim_above_the_release_notes(
    tmp_path: Path,
) -> None:
    existing = "Rebuilt by hand.\n\nADVANCES #216\n"
    out = compose(tmp_path, "Automated update v1 -> v2.\n", existing)

    assert out.startswith("Rebuilt by hand.\n\nADVANCES #216\n")
    assert "ADVANCES #216" in out.split(MARKER)[0]
    assert out.endswith("Automated update v1 -> v2.\n")


def test_a_second_join_replaces_only_its_own_section(tmp_path: Path) -> None:
    first = compose(tmp_path, "Automated update v1 -> v2.\n", "ADVANCES #216\n")
    second = compose(tmp_path, "Automated update v2 -> v3.\n", first)

    assert second.count(MARKER) == 1
    assert "v1 -> v2" not in second
    assert second == f"ADVANCES #216\n\n{MARKER}\n\nAutomated update v2 -> v3.\n"


def test_an_empty_existing_body_leaves_no_leading_blank_line(tmp_path: Path) -> None:
    assert compose(tmp_path, "notes\n", "") == f"{MARKER}\n\nnotes\n"
    assert compose(tmp_path, "notes\n", "\n\n") == f"{MARKER}\n\nnotes\n"


def test_the_join_path_feeds_the_existing_body_to_the_composer() -> None:
    """The composer only helps if the workflow reads the body it is
    about to replace. Pinned on the source the stores copy, so the
    check covers every stamped copy of the workflow.
    """
    workflow = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "template-update.yml.jinja"
    ).read_text()

    assert 'gh pr view "$EXISTING_PR" --json body' in workflow
    assert "scripts/ci/template-update-body.sh" in workflow
    # The join edit posts the composed body, not the release section.
    assert "--body-file /tmp/pr-body.md" in workflow
