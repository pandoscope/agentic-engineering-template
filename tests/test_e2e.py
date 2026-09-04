"""End-to-end Copier render tests — full template output in isolated temp dirs."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import copier
import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent

# Exact file set the template must produce (no orphans, no omissions).
COMMON_FILES = frozenset(
    {
        ".claude/settings.json",
        ".codespellrc",
        ".copier-answers.agentic.yml",
        ".editorconfig",
        ".markdownlint-cli2.yaml",
        ".yamllint.yaml",
        "AGENTS.md",
        "CLAUDE.md",
        "commitlint.config.mjs",
        "docs/architecture.md",
        "docs/conventions.md",
        "docs/glossary/.markdownlint-cli2.yaml",
        "docs/glossary/snake-farm.md",
        "scripts/agent-shims/gh",
        "scripts/agent-shims/tea",
        "scripts/doctor.sh",
        "scripts/lib/doctor-install.sh",
        # Shared glossary terms, stamped into every generated repo (#52).
        "docs/glossary/decision-memory.md",
        "docs/glossary/reinset.md",
        "docs/glossary/decision-record.md",
        "docs/glossary/pando-cell.md",
        "docs/glossary/evidence-memory.md",
        "docs/glossary/org-genome.md",
        "docs/glossary/grilling.md",
        "docs/glossary/memory-repo.md",
        "docs/glossary/org.md",
        "docs/glossary/pando.md",
        "docs/glossary/principal.md",
        "docs/glossary/session-memory.md",
        "docs/glossary/preference-set.md",
        "docs/glossary/record-contract.md",
        "docs/glossary/agent-session.md",
        "docs/glossary/template-stamp.md",
        "docs/glossary/pandoscope-template.md",
        "scripts/check-branch-name.sh",
        "scripts/check-linear-history.sh",
        "scripts/check_file_length.py",
        "scripts/enable-agent-shims.sh",
        "skills-lock.json",
        # The uniform gate's judge (#137) renders on every forge; only
        # its GitHub workflow vehicle is forge-conditional.
        "scripts/ci/check_gate.py",
        "scripts/ci/gate_aggregate.py",
        "scripts/ci/gate_api.py",
        "scripts/ci/gate_approval.py",
        "scripts/ci/gate_leaks.py",
        "scripts/ci/gate_payload.py",
        "scripts/ci/gate_rerun.py",
        "scripts/ci/gate_reviews.py",
        "scripts/ci/gate_ticket.py",
        # The glossary prune (#67, #203, #210): task, update workflow,
        # drift job and the template root all run this one script.
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
        # The daily self-audit's denylist bridges (#189): value-silent
        # bash on both ends of the trufflehog scan.
        "scripts/ci/trufflehog-detectors.sh",
        "scripts/ci/trufflehog-report.sh",
        # The forge-native net's reporter (#189, layer 4).
        "scripts/ci/secret-scanning-report.sh",
    }
)

# Shipped only on the GitHub forge (the updater authenticates via a GitHub
# App and drives `gh`; Forgejo repos get no .github directory).
GITHUB_ONLY_FILES = frozenset(
    {
        ".github/workflows/template-update.yml",
        ".github/labels.toml",
        ".github/workflows/add-to-project.yml",
        ".github/workflows/labels.yml",
        ".github/workflows/lint.yml",
        # Inert without a `.pandoscope-sessions` marker (paths filter);
        # the marker is a per-repo opt-in the template never stamps.
        ".github/workflows/notify-sessions-manifest.yml",
        ".github/workflows/ticket-closed.yml",
        # The uniform CI gate (#137): the one required context and the
        # keyword file its checks read.
        ".github/workflows/ci-ok.yml",
        # The daily per-repo self-audit (#189, layer 3).
        ".github/workflows/trufflehog-audit.yml",
        # The daily forge-native secret-scanning check (#189, layer 4).
        ".github/workflows/secret-scanning.yml",
        # The event-driven denylist scan of issue/comment payloads (#208).
        ".github/workflows/payload-scan.yml",
        ".github/reference-keywords.json",
        # The merge-approval gate (#187): the approver allowlist its
        # check reads.
        ".github/merge-approvers.json",
        # The stale-red janitor (#190): re-runs superseded red gate
        # runs in place so they stop blocking merge.
        ".github/workflows/gate-rerun.yml",
    }
)

EXPECTED_WITH_PREK = (
    COMMON_FILES
    | GITHUB_ONLY_FILES
    | {
        ".pre-commit-config.yaml",
        # Seeded once (#216): a home for the repo's own hooks that the
        # template never stamps again and the drift check never judges.
        ".pre-commit-config.local.yaml",
        # Seeded once (#239): the repo's own record of which overruns it
        # tolerates and the ticket closing each one.
        ".file-length-allowlist",
        "scripts/ensure-prek.sh",
    }
)
EXPECTED_WITHOUT_PREK = COMMON_FILES | GITHUB_ONLY_FILES


def _relative_file_tree(root: Path) -> frozenset[str]:
    return frozenset(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def _assert_tree(root: Path, expected: frozenset[str]) -> None:
    actual = _relative_file_tree(root)
    extra = actual - expected
    missing = expected - actual
    assert not extra, f"Unexpected files rendered: {sorted(extra)}"
    assert not missing, f"Expected files missing: {sorted(missing)}"


def test_e2e_copy_defaults_renders_expected_tree(
    render_project: Callable[..., Path],
) -> None:
    """``copier copy --defaults`` produces exactly the owned file set (prek on)."""
    dst_path = render_project()

    _assert_tree(dst_path, EXPECTED_WITH_PREK)

    agents = (dst_path / "AGENTS.md").read_text()
    assert "# Snake Farm — Agent Guidelines" in agents
    assert "https://github.com/actions-user/snake-farm" in agents
    assert "`prek` must pass on every commit" in agents

    precommit = (dst_path / ".pre-commit-config.yaml").read_text()
    assert "commitlint" in precommit
    assert "check-json" in precommit
    assert "markdownlint-cli2" in precommit
    assert "codespell" in precommit
    assert "disambiguate==" in precommit
    assert "commitizen" not in precommit

    answers = (dst_path / ".copier-answers.agentic.yml").read_text()
    assert "agentic_project_slug: snake-farm" in answers
    assert "agentic_precommit: prek" in answers

    doctor = dst_path / "scripts" / "doctor.sh"
    assert doctor.stat().st_mode & 0o111, "doctor.sh must be executable after render"


def test_e2e_copy_precommit_none_renders_expected_tree(
    render_project: Callable[..., Path],
) -> None:
    """``agentic_precommit=none`` omits ``.pre-commit-config.yaml`` entirely."""
    dst_path = render_project(agentic_precommit="none")

    _assert_tree(dst_path, EXPECTED_WITHOUT_PREK)
    assert not (dst_path / ".pre-commit-config.yaml").exists()

    agents = (dst_path / "AGENTS.md").read_text()
    assert "`prek` must pass on every commit" not in agents

    doctor = (dst_path / "scripts" / "doctor.sh").read_text()
    assert "REQUIRED_TOOLS+=(prek)" not in doctor


@pytest.mark.parametrize(
    ("overrides", "slug", "repo_url"),
    [
        (
            {
                "agentic_project_name": "My Cool App",
                "agentic_project_slug": "my-cool-app",
            },
            "my-cool-app",
            "https://github.com/actions-user/my-cool-app",
        ),
        (
            {
                "agentic_forge": "forgejo",
                "agentic_forgejo_host": "git.example.com",
            },
            "snake-farm",
            "https://git.example.com/actions-user/snake-farm",
        ),
    ],
)
def test_e2e_copy_variants_render_clean_tree(
    render_project: Callable[..., Path],
    overrides: dict[str, str],
    slug: str,
    repo_url: str,
) -> None:
    """Variant answers still render the same owned file set with substituted values."""
    dst_path = render_project(**overrides)

    base = EXPECTED_WITH_PREK
    if overrides.get("agentic_forge") == "forgejo":
        base = base - GITHUB_ONLY_FILES
    expected = {path.replace("snake-farm", slug) for path in base}
    _assert_tree(dst_path, frozenset(expected))

    assert repo_url in (dst_path / "AGENTS.md").read_text()
    assert (dst_path / "docs" / "glossary" / f"{slug}.md").exists()


def test_e2e_copy_docs_kind_omits_code_artifacts(
    render_project: Callable[..., Path],
) -> None:
    """``agentic_project_kind=docs`` drops the architecture stub, nothing else."""
    dst_path = render_project(agentic_project_kind="docs")

    _assert_tree(dst_path, frozenset(EXPECTED_WITH_PREK - {"docs/architecture.md"}))


def test_e2e_copy_non_english_omits_codespell(
    render_project: Callable[..., Path],
) -> None:
    """``agentic_language=de`` drops codespell config and hook, keeps the rest."""
    dst_path = render_project(agentic_language="de")

    _assert_tree(dst_path, frozenset(EXPECTED_WITH_PREK - {".codespellrc"}))

    precommit = (dst_path / ".pre-commit-config.yaml").read_text()
    assert "codespell" not in precommit
    assert "disambiguate==" in precommit


# Host tools the post-render _tasks invoke. Missing any → skip the smoke test
# rather than fail, so the suite stays green on machines without the toolchain.
SMOKE_REQUIRED_TOOLS = ("git", "npx", "uvx", "prek")


def test_reporting_tasks_cannot_roll_back_a_render() -> None:
    """A task that reports rather than gates must not abort the run.

    Copier rolls the whole render back on a non-zero task, so
    `scripts/doctor.sh` exiting on a missing host tool loses the stamp
    entirely. `gh` is deliberately absent from remote agent sessions —
    the repo CLAUDE.md files say to use forge MCP tools instead — which
    makes the abort the normal case there rather than an edge one.

    Asserted on the task string because that is where the tolerance
    lives: doctor's own exit code stays meaningful when invoked directly,
    so CI and humans still get a hard signal.
    """
    tasks = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())["_tasks"]
    reporting = [task for task in tasks if "doctor.sh" in task]

    assert reporting, "the doctor report task must still be in _tasks"
    for task in reporting:
        assert "|| true" in task, f"doctor task can abort a render: {task}"


def test_e2e_prek_install_registers_git_hooks(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Post-render tasks run ``prek install`` so commits trigger prek hooks.

    Without this, a rendered repo has ``.pre-commit-config.yaml`` but no
    ``.git/hooks/pre-commit`` script, so prek never runs on commit. The task
    only fires inside a git work tree, so we init one before rendering.
    """
    missing = [tool for tool in SMOKE_REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"prek hook test needs host tools on PATH: {missing}")

    dst_path = tmp_path / "hooks"
    dst_path.mkdir()
    subprocess.run(["git", "init"], cwd=dst_path, check=True, capture_output=True)

    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        vcs_ref="HEAD",
    )

    hook = dst_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists(), "prek install must register a pre-commit git hook"


def test_e2e_smoke_full_render_runs_tasks(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Full ``copier copy`` (post-render tasks included) yields a clean tree.

    Replaces the manual ``copier copy . /tmp/smoke --defaults --trust`` smoke
    check from the spec: it exercises the real generation path end to end,
    including the ``_tasks`` step that the other e2e tests skip.
    """
    missing = [tool for tool in SMOKE_REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"smoke test needs host tools on PATH: {missing}")

    dst_path = tmp_path / "smoke"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        vcs_ref="HEAD",
    )

    # Post-render tasks add files (e.g. .agents/skills/), so the tree is a
    # superset of the owned set rather than an exact match.
    rendered = _relative_file_tree(dst_path)
    missing = EXPECTED_WITH_PREK - rendered
    assert not missing, f"Smoke render missing owned files: {sorted(missing)}"
    assert (dst_path / ".agents" / "skills").is_dir(), (
        "post-render skills install must populate .agents/skills/"
    )


def test_stamped_files_survive_a_consumers_own_hooks_unchanged(tmp_path, base_answers):
    """Measured on pandoscope/disambiguate#82 (#216): the drift check
    flagged three template-side defects every consumer trips on —
    doctor.sh rendering with a doubled final newline (end-of-file-fixer
    trims it), the glossary markdownlint config lacking MD049 off (the
    avoided-term grammar's literal `_Avoid_:` prefix collides with it),
    and a stale suppression in pandoscope-template.md."""
    dst_path = tmp_path / "stamp-hooks"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        vcs_ref="HEAD",
    )
    doctor = (dst_path / "scripts" / "doctor.sh").read_bytes()
    assert doctor.endswith(b"\n") and not doctor.endswith(b"\n\n")
    lint = (dst_path / "docs" / "glossary" / ".markdownlint-cli2.yaml").read_text()
    assert "MD049: false" in lint
    term = (dst_path / "docs" / "glossary" / "pandoscope-template.md").read_text()
    assert "ignore[unlinked-term]" not in term
