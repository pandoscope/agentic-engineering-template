"""What the render reads from its environment rather than from an
answer — the forge and repo owner it detects, the store URL contract —
and the files copier seeds once and never stamps again.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import copier
import pytest
import yaml

from tests.conftest import load_module
from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers


def test_grilling_pinned_to_frankify_derivation(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """`grilling` pins the frankify-app/skills derivation, not upstream."""
    dst_path = render_answers(tmp_path, base_answers, "grilling-pin")

    lock = json.loads((dst_path / "skills-lock.json").read_text())
    grilling = lock["skills"]["grilling"]
    assert grilling["source"] == "frankify-app/skills"
    assert grilling["skillPath"] == "derived/grilling/SKILL.md"


def test_decision_memory_url_env_var_contract(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """DECISION_MEMORY_URL is an env-var-only contract: no copier question,
    no value in any committed artifact (.copier-answers* included); AGENTS.md
    documents the contract and doctor.sh checks the env var."""
    # Even if a consumer passes a URL as copier data, it must render nowhere.
    stray_url = "https://github.com/acme/decision-memory"
    answers = {**base_answers, "agentic_decision_memory_url": stray_url}
    dst_path = render_answers(tmp_path, answers, "decision-memory")

    for path in dst_path.rglob("*"):
        if path.is_file():
            assert stray_url not in path.read_text(), (
                f"DECISION_MEMORY_URL value leaked into {path}"
            )

    # Not an init-time answer: no question, so nothing recorded on update.
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        unexpect_strs=["decision_memory"],
    )
    check_file_contents(
        dst_path / "AGENTS.md",
        ["DECISION_MEMORY_URL", "skips recording"],
    )
    check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        ["DECISION_MEMORY_URL", 'git ls-remote "$DECISION_MEMORY_URL"'],
    )


def test_copier_has_no_decision_memory_question() -> None:
    """The template must never ask for the decision-memory URL at init time."""
    copier_yml = (PROJECT_ROOT / "copier.yml").read_text()
    assert "decision_memory" not in copier_yml


def _detect_forge():
    """The template's own probe, loaded from the extension it ships."""
    return load_module(
        "agentic_ext", PROJECT_ROOT / "extensions" / "agentic.py"
    ).detect_forge()


def _git_repo_with_origin(path: Path, url: str) -> None:
    """A throwaway repo, so `detect_forge()` sees the remote we choose."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=path, check=True)


@pytest.mark.parametrize(
    "origin_url",
    [
        # Detection succeeds — the case that dropped the answer.
        "https://github.com/acme/widget.git",
        # Detection fails — any non-github remote, including the local
        # proxy a sandboxed agent session actually runs behind.
        "http://127.0.0.1:41729/git/acme/widget",
    ],
)
def test_the_forge_answer_is_recorded_however_the_render_environment_looks(
    tmp_path: Path,
    base_answers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    origin_url: str,
) -> None:
    """The answers file is the record of how a repo was stamped, so a
    question whose `when` consults the ENVIRONMENT makes that record
    depend on where the render ran.

    `detect_forge()` shells out to `git remote get-url origin` in the
    process CWD. Gating the question on it meant the answer was written
    when detection failed and dropped when it succeeded — two checkouts
    of one commit producing different answers files. Detection belongs
    in the default, not in the gate.
    """
    cwd = tmp_path / "cwd"
    _git_repo_with_origin(cwd, origin_url)
    monkeypatch.chdir(cwd)

    # Assert the PRECONDITION, not a correlate of it. GitHub reachability
    # is not the question — a git config `insteadOf` rewrite can map every
    # github.com URL to a proxy, which leaves the network fine and the
    # probe permanently blind. A sandboxed agent session is exactly that.
    #
    # Skip rather than pass: a test that cannot reach its own condition
    # must not report the same colour as one that checked it.
    detected = _detect_forge()
    wanted = "github" if "github.com" in origin_url else None
    if detected != wanted:
        pytest.skip(
            f"environment cannot exercise this case: origin {origin_url!r} "
            f"resolves to detect_forge()={detected!r}, wanted {wanted!r}"
        )

    # Deliberately NOT supplied: an explicit value is recorded whatever
    # `when` says, which is exactly what hides the defect. A real update
    # supplies nothing — it reuses the answers file and re-evaluates the
    # gate, so a key recorded last time can silently vanish.
    answers_in = {k: v for k, v in base_answers.items() if k != "agentic_forge"}

    dst_path = render_answers(tmp_path, answers_in, "forge-recorded")
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()

    assert "agentic_forge: github" in answers


# Globals the Jinja extension injects. Anything here reads the machine
# the render happens on, not the answers.
ENVIRONMENT_PROBES = ("detect_forge", "resolve_repo_owner")


def test_no_question_gate_consults_the_environment() -> None:
    """A `when` clause decides whether an answer is RECORDED, so one that
    probes the machine makes the answers file depend on where the render
    ran — two checkouts of one commit, two different files.

    `agentic_forge` had exactly this: gated on `detect_forge()`, it was
    written when detection failed and dropped when it succeeded. The
    remedy is not specific to that question, so neither is this test.

    Probes belong in `default`, where they seed a first stamp and are
    then superseded by the recorded answer. `agentic_repo_owner` is the
    worked example: same probe, in the default, always recorded.
    """
    questions = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())

    offenders = [
        name
        for name, spec in questions.items()
        if isinstance(spec, dict)
        for probe in ENVIRONMENT_PROBES
        if probe in str(spec.get("when", ""))
    ]

    assert offenders == [], (
        f"{offenders}: `when` must depend only on other answers. Move the "
        "probe into `default` so the answer is asked, recorded, and "
        "reproducible."
    )


def test_architecture_and_project_term_are_seeded_once(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The architecture stub and the project's own glossary term are
    seeds, not stamps (#216).

    Both start as one-liners derived from the answers, and a real repo
    grows the full document in place. Stamping them again would reset
    that on every `copier update`, and the vendored-drift check would
    then judge a repo for owning its own architecture.
    """
    dst_path = render_answers(tmp_path, base_answers, "seeded")

    architecture = dst_path / "docs" / "architecture.md"
    term = dst_path / "docs" / "glossary" / f"{base_answers['agentic_project_slug']}.md"
    architecture.write_text("# The real architecture\n")
    term.write_text("## Snake Farm\n\nThe real definition.\n")

    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )

    assert architecture.read_text() == "# The real architecture\n"
    assert term.read_text() == "## Snake Farm\n\nThe real definition.\n"
