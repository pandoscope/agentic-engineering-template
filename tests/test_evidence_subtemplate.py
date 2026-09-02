"""Render and contract tests for the evidence-memory subtemplate.

Selected by `agentic_subtemplate=evidence-memory`, it vendors what an
evidence store needs — the capture writer, the CI guard, and the store
docs — into a data repo, keyed by a minimal answers file.

The store holds detection records for bugs and features. It is memory,
not a tracker: every record names its forge ticket, and progress lives
there.
"""

from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path

import copier
import pytest

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent
SUBTEMPLATE = PROJECT_ROOT / "evidence-memory"

STORE_FILES = frozenset(
    {
        ".copier-answers.agentic.yml",
        ".github/guards/evidence_validator.py",
        ".github/guards/guards.py",
        ".github/guards/validator_core.py",
        ".github/labels.toml",
        ".github/workflows/guards.yml",
        ".github/workflows/ci-ok.yml",
        ".github/reference-keywords.json",
        "scripts/ci/check_gate.py",
        # The audit's script bridges render with the shared scripts/ci
        # payload; the audit WORKFLOW does not — stores render only
        # their own workflow set, and as private repos they are not
        # exposed (#189: the visibility gate would skip them anyway).
        "scripts/ci/trufflehog-detectors.sh",
        "scripts/ci/trufflehog-report.sh",
        "scripts/ci/secret-scanning-report.sh",
        ".github/workflows/template-update.yml",
        ".github/workflows/ticket-closed.yml",
        ".github/workflows/labels.yml",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "docs/conventions.md",
        "tools/capture.py",
        "tools/record_core.py",
    }
)

# The cores are copied into both store subtemplates because copier
# renders one _subdirectory at a time. Managed duplication, per
# AGENTS.md: declared here, and pinned identical by a test.
SHARED_CORES = (
    "tools/record_core.py",
    ".github/guards/validator_core.py",
    ".github/workflows/template-update.yml.jinja",
)

validator = load_module(
    "evidence_validator", SUBTEMPLATE / ".github" / "guards" / "evidence_validator.py"
)
capture = load_module("capture", SUBTEMPLATE / "tools" / "capture.py")

NOW = dt.datetime(2026, 7, 28, 16, 15, 0, tzinfo=dt.timezone.utc)


def draft() -> dict:
    """A draft record: the schema minus tool-minted fields, plus slug."""
    return {
        "slug": "drift-baseline-ignored",
        "symptom": "disambiguate --drift exits 0 with a stale baseline entry",
        "triage": "code-bug",
        "tier": 1,
        "rung": "capsule",
        "ticket": "https://github.com/pandoscope/disambiguate/issues/1",
        "environment": "disambiguate 0.3.0, python 3.13, repo@abc1234",
        "expected": "a baselined finding that no longer occurs is pruned",
        "observed": "the entry survives and the run still exits 0",
    }


def _render_store(tmp_path: Path) -> Path:
    dst_path = tmp_path / "evidence-memory"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "evidence-memory"},
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


def test_default_render_contains_no_evidence_tooling(render_project) -> None:
    """The writer is store tooling: it ships to evidence stores through
    this subtemplate, never to consumer repos."""
    dst_path = render_project()
    assert not (dst_path / "tools" / "capture.py").exists()
    assert not (dst_path / ".github" / "guards" / "evidence_validator.py").exists()


@pytest.mark.parametrize("relpath", SHARED_CORES)
def test_the_shared_cores_are_identical_across_subtemplates(relpath: str) -> None:
    """Managed duplication, not drift.

    Copier renders one _subdirectory at a time, so the evidence store
    cannot import the decision store's copy. Two copies is the current
    price; two DIFFERENT copies would be a defect, and the whole point
    of one contract core is that N stores cannot fork the schema.
    """
    decision_copy = (PROJECT_ROOT / "decision-memory" / relpath).read_bytes()
    evidence_copy = (SUBTEMPLATE / relpath).read_bytes()
    assert decision_copy == evidence_copy, (
        f"{relpath} has drifted between the store subtemplates — "
        "change it once and copy, or the stores fork the contract"
    )


# --------------------------- the contract ---------------------------


def test_a_valid_record_passes() -> None:
    record = capture.draft_to_record(draft(), NOW)
    assert validator.validate_record(record, filename_stem=record["id"]) == []


def test_the_envelope_carries_the_evidence_type() -> None:
    record = capture.draft_to_record(draft(), NOW)
    assert record["type"] == "evidence"
    assert record["id"] == "20260728T161500Z-drift-baseline-ignored"
    assert validator.validate_envelope({**record, "type": "decision"}) != []


@pytest.mark.parametrize("field", sorted(validator.REQUIRED_FIELDS))
def test_a_missing_required_field_fails(field: str) -> None:
    record = capture.draft_to_record(draft(), NOW)
    record.pop(field, None)
    assert any(e.startswith(f"{field}:") for e in validator.validate_record(record))


def test_a_multi_line_symptom_is_rejected() -> None:
    """The symptom is the grep-able fingerprint; dedup greps it across a
    local clone, so everything after the first line would be invisible."""
    record = capture.draft_to_record(
        {**draft(), "symptom": "first line\nsecond line"}, NOW
    )
    assert any(e.startswith("symptom:") for e in validator.validate_record(record))


@pytest.mark.parametrize(
    "triage", ["code-bug", "doc-bug", "expectation-bug", "feature"]
)
def test_the_triage_taxonomy_includes_feature(triage: str) -> None:
    """A feature-kata's capsule is a test failing because the capability
    is absent — TDD red — so bug-vs-feature is a triage value here, not
    a separate record kind."""
    record = capture.draft_to_record({**draft(), "triage": triage}, NOW)
    assert validator.validate_record(record) == []


def test_an_unknown_triage_is_rejected() -> None:
    record = capture.draft_to_record({**draft(), "triage": "wontfix"}, NOW)
    assert any(e.startswith("triage:") for e in validator.validate_record(record))


def test_a_tier_two_record_at_the_capsule_rung_must_carry_its_capsule() -> None:
    """Tier 2 exists BECAUSE the capsule cannot be public. One that
    reached the capsule rung with nothing stored means the capsule went
    somewhere else, which is the leak this tier prevents."""
    record = capture.draft_to_record({**draft(), "tier": 2, "rung": "capsule"}, NOW)
    assert any(e.startswith("capsule:") for e in validator.validate_record(record))

    with_capsule = capture.draft_to_record(
        {**draft(), "tier": 2, "rung": "capsule", "capsule": "$ repro.sh"}, NOW
    )
    assert validator.validate_record(with_capsule) == []


def test_a_tier_two_record_below_the_capsule_rung_needs_no_capsule() -> None:
    record = capture.draft_to_record({**draft(), "tier": 2, "rung": "ticket"}, NOW)
    assert validator.validate_record(record) == []


def test_a_record_must_name_its_forge_ticket() -> None:
    """The store is memory; the forge is the backlog. The link is what
    stops them becoming two trackers."""
    record = capture.draft_to_record({**draft(), "ticket": "see slack"}, NOW)
    assert any(e.startswith("ticket:") for e in validator.validate_record(record))


def test_links_may_not_point_forward() -> None:
    """Records are immutable, so an older record can never gain an edge
    to a newer one. IDs lead with a UTC timestamp, so a forward link is
    catchable from the ID alone."""
    newer = "20260729T000000Z-later-finding"
    record = capture.draft_to_record({**draft(), "same_symptom_as": newer}, NOW)
    errors = validator.validate_record(record)
    assert any("backward-only" in e for e in errors)


def test_a_backward_link_is_accepted() -> None:
    older = "20260727T000000Z-earlier-finding"
    record = capture.draft_to_record({**draft(), "regression_of": older}, NOW)
    assert validator.validate_record(record) == []


def test_the_store_does_not_borrow_decision_link_vocabulary() -> None:
    """related/supersedes/drill_down_of mean something else in the
    decision store; a shared spelling with a different meaning is worse
    than two spellings."""
    assert validator.LINK_FIELDS == ("same_symptom_as", "regression_of")
    for borrowed in ("related", "supersedes", "drill_down_of", "duplicate_of"):
        assert borrowed not in validator.LINK_FIELDS
        assert borrowed not in capture.FIELD_ORDER


def test_the_corpus_check_reports_a_link_outside_the_store() -> None:
    records = {"20260728T161500Z-a": {"same_symptom_as": "20260101T000000Z-gone"}}
    errors = validator.validate_corpus(records)
    assert len(errors) == 1
    assert "records/" in errors[0]


# ---------------------------- the writer ----------------------------


def test_the_writer_refuses_to_overwrite_a_record(tmp_path: Path) -> None:
    """The store is append-only, so an existing path means the ID
    collided and the caller has to mint again."""
    record = capture.draft_to_record(draft(), NOW)
    capture.write_record(record, tmp_path)
    with pytest.raises(SystemExit):
        capture.write_record(record, tmp_path)


def test_the_writer_lays_the_record_out_symptom_first(tmp_path: Path) -> None:
    record = capture.draft_to_record(draft(), NOW)
    path = capture.write_record(record, tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    keys = list(written)
    assert keys[:5] == ["v", "type", "id", "date", "symptom"]


def test_the_writer_requires_a_slug() -> None:
    payload = draft()
    del payload["slug"]
    with pytest.raises(ValueError):
        capture.draft_to_record(payload, NOW)


def test_the_writer_keeps_unknown_draft_fields(tmp_path: Path) -> None:
    record = capture.draft_to_record({**draft(), "future_field": "kept"}, NOW)
    assert record["future_field"] == "kept"


# ------------------ the tier-2 ticket-filing window ------------------


def test_a_tier_two_record_may_mint_before_its_ticket_exists() -> None:
    """The tier-2 ticket is filed only after a human approves the
    capsule, so the record necessarily predates it. That intermediate
    state is a valid RECORD; the guard is what refuses to merge it."""
    record = capture.draft_to_record(
        {**draft(), "tier": 2, "ticket": None, "capsule": "$ repro.sh"}, NOW
    )
    assert validator.validate_record(record) == []


def test_a_tier_one_record_may_not_mint_without_its_ticket() -> None:
    """Tier 1 has no approval step to wait for — its capsule is public,
    so the ticket is filed at detection."""
    record = capture.draft_to_record({**draft(), "tier": 1, "ticket": None}, NOW)
    assert any(e.startswith("ticket:") for e in validator.validate_record(record))


def test_the_ticket_must_still_be_a_url_when_present() -> None:
    for tier in (1, 2):
        record = capture.draft_to_record(
            {**draft(), "tier": tier, "ticket": "see slack"}, NOW
        )
        assert any(e.startswith("ticket:") for e in validator.validate_record(record))


def test_the_ticket_key_must_be_present_even_when_null() -> None:
    """Null is a declaration that the ticket is pending; a missing key
    is an omission. The contract distinguishes them."""
    record = capture.draft_to_record(
        {**draft(), "tier": 2, "capsule": "$ repro.sh"}, NOW
    )
    del record["ticket"]
    assert validator.validate_record(record) == ["ticket: required field missing"]


def test_a_null_ticket_blocks_the_merge_gate() -> None:
    """Valid as a record, not mergeable as a corpus: every record in the
    store names its forge ticket, or the store and the backlog drift."""
    record = capture.draft_to_record({**draft(), "tier": 2, "ticket": None}, NOW)
    errors = validator.check_tickets_filed({record["id"]: record})
    assert len(errors) == 1
    assert "ticket" in errors[0]

    filed = {**record, "ticket": "https://github.com/pandoscope/ghx/issues/1"}
    assert validator.check_tickets_filed({filed["id"]: filed}) == []


# ------------------------ the tier-2 gate's labels ------------------------


def test_the_store_ships_the_labels_its_tier_two_gate_needs(
    tmp_path: Path,
) -> None:
    """#103's two-lane gate is label-based, so the labels have to exist
    in the store before the gate can be mechanical rather than
    conventional. The project template's taxonomy does not reach here:
    a store renders its own file set and stays consumer-ignorant.
    """
    dst_path = _render_store(tmp_path)

    config = (dst_path / ".github" / "labels.toml").read_text()
    # The lane marker: a tier-2 record waits for a human, and this is how
    # that wait is visible without opening every PR.
    assert "needs-human-review" in config
    # The tier itself, so the gate can select on it.
    assert "tier-2" in config

    workflow = (dst_path / ".github" / "workflows" / "labels.yml").read_text()
    assert ".github/labels.toml" in workflow
    assert "LABELS_TOKEN" in workflow


def test_the_store_takes_no_triage_or_phase_labels(tmp_path: Path) -> None:
    """Triage lives on the forge TICKET, not on the record's PR — the
    store is memory, not a tracker. Shipping the ticket taxonomy here
    would invite classifying in two places, which is how they diverge.
    """
    dst_path = _render_store(tmp_path)
    # Parse rather than substring-match: the file explains in prose which
    # labels it deliberately omits, and naming them there is the point.
    defined = tomllib.loads((dst_path / ".github" / "labels.toml").read_text())

    assert set(defined) == {"needs-human-review", "tier-2"}


def test_the_store_updater_matches_the_one_consumers_get() -> None:
    """A store vendors record_core and validator_core, so a fix to
    either reaches it only through `copier update`. Without an updater
    the store silently runs a stale validator — the gap meta#30 names.

    Byte-identical to the consumer workflow on purpose: three copies
    that DIFFER would mean the stores update by different rules than
    everything else, which is the drift the pinning exists to stop.
    """
    consumer = (
        PROJECT_ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "template-update.yml.jinja"
    ).read_bytes()
    store = (
        SUBTEMPLATE / ".github" / "workflows" / "template-update.yml.jinja"
    ).read_bytes()
    assert store == consumer
