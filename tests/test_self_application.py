"""The repo root must match what `template/` renders.

This repo is the template AND uses itself as a template, so root files
with a `template/` counterpart are render output. That is also the only
way the shared glossary terms become lintable: they live under
`template/`, which is never a glossary root, and only become real terms
once stamped into this repo's own `docs/glossary/`.

A stale stamp therefore means the glossary being linted is not the
glossary being shipped, which is why this is a gate rather than a
convention.
"""

from __future__ import annotations

from pathlib import Path
import re

import copier
import yaml

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent

# The divergence list is the route guard's, not a second copy: the state
# check here and the provenance check there exempt the same paths, and
# two lists would drift into disagreeing about what "diverges".
DELIBERATE_DIVERGENCE = load_module(
    "self_application", PROJECT_ROOT / "scripts" / "dev" / "self_application.py"
).DELIBERATE_DIVERGENCE


def _skip_if_exists_paths() -> set[str]:
    """Paths copier seeds once and never overwrites.

    These can never match after seeding, so they are excluded
    structurally rather than listed as a choice. Parsed here with a real
    YAML reader; `test_self_application_route` pins the route guard's
    dependency-free reading of the same block against this one.
    """
    config = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())
    paths: set[str] = set()
    for entry in config.get("_skip_if_exists", []):
        # Entries are jinja-guarded per subtemplate, e.g.
        # "{% if ... %}docs/conventions.md{% endif %}" — drop the tags and
        # keep what they guard. An answer interpolation inside the path
        # ("docs/glossary/{{ agentic_project_slug }}.md") stays verbatim:
        # only a render resolves it, and the guard leaves it alone too.
        literal = re.sub(r"\{%.*?%\}", "", entry).strip()
        if literal:
            paths.add(literal)
    return paths


def test_repo_root_matches_the_rendered_template(tmp_path: Path) -> None:
    render = tmp_path / "self"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=render,
        data={
            "agentic_project_name": "Agentic Engineering Template",
            "agentic_project_description": (
                "Copier template for agentic engineering scaffolding"
            ),
            "agentic_project_slug": "agentic-engineering-template",
            "agentic_repo_owner": "frankify-app",
            "agentic_merge_approvers": "pando-genet",
        },
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        vcs_ref="HEAD",
    )

    excluded = set(DELIBERATE_DIVERGENCE) | _skip_if_exists_paths()
    stale: list[str] = []
    missing: list[str] = []

    for rendered in sorted(render.rglob("*")):
        if not rendered.is_file():
            continue
        relative = rendered.relative_to(render).as_posix()
        if relative in excluded:
            continue
        root_file = PROJECT_ROOT / relative
        if not root_file.is_file():
            missing.append(relative)
        elif root_file.read_bytes() != rendered.read_bytes():
            stale.append(relative)

    assert not stale, (
        f"Root files differ from the template render: {stale}. "
        "Re-run the self-application step (docs/conventions.md)."
    )
    assert not missing, (
        f"Rendered files absent from the repo root: {missing}. "
        "Adopt them, or add them to DELIBERATE_DIVERGENCE with a reason."
    )
