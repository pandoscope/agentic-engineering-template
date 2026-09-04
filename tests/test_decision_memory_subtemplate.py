"""Render tests for the decision-memory subtemplate.

Selected by `agentic_subtemplate=decision-memory`, it vendors
everything a store needs — the recorder, the CI guards, the
preference-set lifecycle and the store docs — into a data repo, keyed
by a minimal answers file.
"""

from __future__ import annotations

from pathlib import Path

import copier

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent

STORE_FILES = frozenset(
    {
        ".agents/skills/adjudicate-drafts/SKILL.md",
        ".agents/skills/compact-preferences/SKILL.md",
        ".agents/skills/extract-preferences/SKILL.md",
        ".agents/skills/recalibrate-thresholds/SKILL.md",
        ".copier-answers.agentic.yml",
        ".github/guards/decision_validator.py",
        ".github/guards/guards.py",
        ".github/guards/validator_core.py",
        ".github/store/README.md",
        ".github/store/budget.py",
        ".github/store/config.py",
        ".github/store/extraction.py",
        ".github/store/preferences_guard.py",
        ".github/store/render_preferences.py",
        ".github/store/replay.py",
        ".github/store/similarity.py",
        ".github/store/tests/store_support.py",
        ".github/store/tests/test_config.py",
        ".github/store/tests/test_extraction.py",
        ".github/store/tests/test_guards.py",
        ".github/store/tests/test_preferences.py",
        ".github/store/tests/test_replay.py",
        ".github/store/tests/test_similarity.py",
        ".github/workflows/preferences-budget.yml",
        ".github/workflows/preferences-guard.yml",
        ".github/workflows/guards.yml",
        ".github/workflows/ci-ok.yml",
        ".github/reference-keywords.json",
        "scripts/ci/check_gate.py",
        "scripts/ci/gate_aggregate.py",
        "scripts/ci/gate_api.py",
        "scripts/ci/gate_approval.py",
        "scripts/ci/gate_leaks.py",
        "scripts/ci/gate_payload.py",
        "scripts/ci/gate_rerun.py",
        "scripts/ci/gate_reviews.py",
        "scripts/ci/gate_ticket.py",
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
        # The audit's script bridges render with the shared scripts/ci
        # payload; the audit WORKFLOW does not — stores render only
        # their own workflow set, and as private repos they are not
        # exposed (#189: the visibility gate would skip them anyway).
        "scripts/ci/trufflehog-detectors.sh",
        "scripts/ci/trufflehog-report.sh",
        "scripts/ci/secret-scanning-report.sh",
        ".github/workflows/template-update.yml",
        ".github/workflows/ticket-closed.yml",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "docs/conventions.md",
        "docs/extraction-prompt.md",
        "preferences.json",
        "preferences.txt",
        "store.config.json",
        "tools/record.py",
        "tools/record_core.py",
    }
)


def _render_store(tmp_path: Path) -> Path:
    dst_path = tmp_path / "decision-memory"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "decision-memory"},
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
        str(p.relative_to(dst_path)) for p in dst_path.rglob("*") if p.is_file()
    }
    assert rendered == STORE_FILES


def test_vendored_validator_is_byte_identical_to_source(
    tmp_path: Path,
) -> None:
    dst_path = _render_store(tmp_path)
    source_dir = PROJECT_ROOT / "decision-memory" / ".github" / "guards"
    for name in ("decision_validator.py", "guards.py"):
        vendored = (dst_path / ".github" / "guards" / name).read_text()
        assert vendored == (source_dir / name).read_text()


def test_guard_answers_file_is_minimal(tmp_path: Path) -> None:
    """Project-scaffold questions are skipped, so the data repo records
    only the subtemplate choice — it stays consumer-ignorant."""
    dst_path = _render_store(tmp_path)
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()
    assert "agentic_subtemplate: decision-memory" in answers
    for key in (
        "agentic_project_name",
        "agentic_tracker_cli",
        "agentic_precommit",
    ):
        assert key not in answers


def test_store_docs_are_vendored_and_preferences_seeded(
    tmp_path: Path,
) -> None:
    """Docs travel with the schema (vendored, byte-identical); the
    preference set is seeded once and never overwritten on update."""
    dst_path = _render_store(tmp_path)
    source = PROJECT_ROOT / "decision-memory" / "docs" / "conventions.md"
    assert (dst_path / "docs" / "conventions.md").read_text() == source.read_text()

    source = dst_path / "preferences.json"
    rendered = dst_path / "preferences.txt"
    assert "Seeded once" in source.read_text()
    assert rendered.read_text() == ""
    # Owned by the store: a local edit to either half of the pair must
    # survive a re-render.
    source.write_text('{"rules": []}\n')
    rendered.write_text("my rule.\n")
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "decision-memory"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )
    assert source.read_text() == '{"rules": []}\n'
    assert "my rule." in rendered.read_text()


def test_store_render_bridges_claude_skills_to_the_canonical_dir(
    tmp_path: Path,
) -> None:
    """Skills live in `.agents/skills/`; `.claude/` links to them.

    Same bridge the main template renders, so an agent discovers the
    store's skills the way it discovers any other repo's.
    """
    dst_path = _render_store(tmp_path)
    bridge = dst_path / ".claude" / "skills"
    assert bridge.is_symlink(), ".claude/skills must stay a symlink, not a copy"
    assert bridge.readlink().as_posix() == "../.agents/skills"
    assert (bridge / "compact-preferences" / "SKILL.md").is_file()


def test_store_config_survives_a_re_render(tmp_path: Path) -> None:
    """The knobs are the store's to tune, so `copier update` must never
    revert a human's budget back to the template's seed."""
    dst_path = _render_store(tmp_path)
    config = dst_path / "store.config.json"
    tuned = '{"budget_tokens": 1500, "replay_window": 40}\n'
    config.write_text(tuned)
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "decision-memory"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )
    assert config.read_text() == tuned


def test_store_render_ships_both_lifecycle_skills(tmp_path: Path) -> None:
    """Extraction grows the set, compaction shrinks it — a store that
    got only one half would have no way to run the other."""
    dst_path = _render_store(tmp_path)
    skills = dst_path / ".agents" / "skills"
    assert (skills / "extract-preferences" / "SKILL.md").is_file()
    assert (skills / "compact-preferences" / "SKILL.md").is_file()


def test_default_render_contains_no_guard_files(
    render_project,
) -> None:
    dst_path = render_project()
    assert not (dst_path / ".github" / "guards").exists()
    assert not (dst_path / ".github" / "workflows" / "guards.yml").exists()


def test_default_render_contains_no_recorder(
    render_project,
) -> None:
    """The recorder is store tooling: it ships to decision-memory
    stores through the decision-memory subtemplate, never to consumer repos."""
    dst_path = render_project()
    assert not (dst_path / "tools" / "record.py").exists()


def test_a_predictions_only_pr_needs_no_extraction_pass(tmp_path) -> None:
    """The extraction gate keys on decisions/, so an autonomous run's
    records never demand a pass they have no business in."""
    extraction = load_module(
        "extraction",
        PROJECT_ROOT / "decision-memory" / ".github" / "store" / "extraction.py",
    )
    assert extraction.DECISIONS_DIR == "decisions"
    source = (
        PROJECT_ROOT / "decision-memory" / ".github" / "store" / "extraction.py"
    ).read_text()
    assert "predictions" not in source, (
        "extraction must not know about predictions/ at all — the "
        "exclusion is structural, not a filter someone must remember"
    )
