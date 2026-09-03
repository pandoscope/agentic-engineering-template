"""Tests for the recorder's contract core (tools/record.py).

The contract core is pure (no IO) and is the dojo lift-target:
mint_id, mint_envelope, draft_to_record, serialize_record. Validation
itself is NOT tested here — it lives in the vendored validator (see
test_decision_validator.py); these tests only cross-check that minted
records satisfy it.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent

record_tool = load_module(
    "record_tool", PROJECT_ROOT / "decision-memory" / "tools" / "record.py"
)
dv = load_module(
    "decision_validator",
    PROJECT_ROOT / "decision-memory" / ".github" / "guards" / "decision_validator.py",
)

NOW = dt.datetime(2026, 7, 21, 14, 32, 5, tzinfo=dt.timezone.utc)


def draft() -> dict:
    """A draft record: the schema minus tool-minted fields, plus slug."""
    return {
        "slug": "agent-access",
        "project": "factory",
        "question": "How do agent environments access the preference repo?",
        "context": "session-local facts informing the options",
        "options": [
            {
                "slot": 1,
                "label": "read-only deploy key",
                "role": "prediction+recommendation",
                "rules_cited": [],
                "reasoning": "integrity via least privilege",
            },
            {"slot": 2, "label": "full-access PAT", "if_clause": "if speed"},
        ],
        "prediction_stream": "cold",
        "artifact_ref": None,
        "chosen_slot": 1,
        "chosen": "read-only deploy key",
        "correction": False,
        "rejections": [
            {
                "option": "full-access PAT",
                "reason": "blast radius",
                "status": "presumed-false",
                "reason_source": "inferred",
                "reason_class": "TBD",
            }
        ],
        "outcome": "hit",
    }


def test_mint_id_format() -> None:
    assert record_tool.mint_id("agent-access", NOW) == "20260721T143205Z-agent-access"


@pytest.mark.parametrize(
    "bad_slug",
    ["Agent-Access", "agent_access", "agent access", "", "a" * 41],
)
def test_mint_id_rejects_bad_slugs(bad_slug: str) -> None:
    with pytest.raises(ValueError):
        record_tool.mint_id(bad_slug, NOW)


def test_mint_envelope() -> None:
    envelope = record_tool.mint_envelope("agent-access", NOW)
    assert envelope == {
        "v": 1,
        "type": "decision",
        "id": "20260721T143205Z-agent-access",
    }


def test_draft_to_record_mints_and_validates() -> None:
    record = record_tool.draft_to_record(
        draft(),
        NOW,
        session="session_01ABC",
        preference_commit="0" * 40,
    )
    assert record["id"] == "20260721T143205Z-agent-access"
    assert record["date"] == "2026-07-21"
    assert record["session"] == "session_01ABC"
    assert record["preference_set"] == {"commit": "0" * 40}
    assert "slug" not in record
    assert dv.validate_record(record, filename_stem=record["id"]) == []


def test_draft_values_win_over_minted_defaults() -> None:
    d = draft()
    d["session"] = "session_from_chat"
    d["preference_set"] = {"commit": "f" * 40}
    record = record_tool.draft_to_record(
        d, NOW, session="ignored", preference_commit="0" * 40
    )
    assert record["session"] == "session_from_chat"
    assert record["preference_set"] == {"commit": "f" * 40}


def test_draft_without_slug_is_an_error() -> None:
    d = draft()
    del d["slug"]
    with pytest.raises(ValueError):
        record_tool.draft_to_record(d, NOW)


def test_record_field_order_groups_input_before_output() -> None:
    record = record_tool.draft_to_record(draft(), NOW)
    keys = list(record.keys())
    assert keys[:3] == ["v", "type", "id"]
    assert keys.index("question") < keys.index("chosen_slot")
    assert keys.index("prediction_stream") < keys.index("outcome")


def test_unknown_draft_fields_are_preserved() -> None:
    d = draft()
    d["some_future_field"] = {"nested": True}
    record = record_tool.draft_to_record(d, NOW)
    assert record["some_future_field"] == {"nested": True}


def test_serialize_record_round_trips() -> None:
    record = record_tool.draft_to_record(draft(), NOW)
    text = record_tool.serialize_record(record)
    assert text.endswith("\n")
    assert json.loads(text) == record
    # Serialization must preserve the constructed field order.
    assert text.index('"question"') < text.index('"chosen_slot"')


def test_repo_url_tail_matches_across_url_forms() -> None:
    tail = record_tool.repo_url_tail
    assert tail("https://github.com/acme/decision-memory.git") == (
        "acme/decision-memory"
    )
    assert tail("git@github.com:acme/decision-memory.git") == ("acme/decision-memory")
    # Managed environments rewrite remotes through a local proxy.
    assert tail("http://local_proxy@127.0.0.1:41729/git/acme/decision-memory") == (
        "acme/decision-memory"
    )
    assert tail("https://github.com/acme/OTHER") != tail(
        "https://github.com/acme/decision-memory"
    )


def test_resolve_batch_supersedes_maps_slugs_to_minted_ids() -> None:
    drafts = [draft(), draft()]
    drafts[0]["slug"] = "old-ruling"
    drafts[1]["slug"] = "new-ruling"
    drafts[1]["supersedes_slug"] = "old-ruling"
    resolved = record_tool.resolve_batch_refs(drafts, NOW)
    assert resolved[1]["supersedes"] == record_tool.mint_id("old-ruling", NOW)
    assert "supersedes_slug" not in resolved[1]
    # Order-independent: a draft may supersede a LATER one too.
    drafts[0]["supersedes_slug"] = "new-ruling"
    resolved = record_tool.resolve_batch_refs(drafts, NOW)
    assert resolved[0]["supersedes"] == record_tool.mint_id("new-ruling", NOW)


def test_resolve_batch_supersedes_rejects_unknown_slug() -> None:
    d = draft()
    d["supersedes_slug"] = "not-in-this-batch"
    with pytest.raises(ValueError):
        record_tool.resolve_batch_refs([d], NOW)


def test_resolve_batch_supersedes_rejects_conflicting_fields() -> None:
    drafts = [draft(), draft()]
    drafts[0]["slug"] = "old-ruling"
    drafts[1]["supersedes_slug"] = "old-ruling"
    drafts[1]["supersedes"] = "20260101T000000Z-existing"
    with pytest.raises(ValueError):
        record_tool.resolve_batch_refs(drafts, NOW)


def test_session_hit_rates_bucket_refined_separately() -> None:
    records = [
        {"prediction_stream": "cold", "outcome": "hit"},
        {"prediction_stream": "cold", "outcome": "refined"},
        {"prediction_stream": "cold", "outcome": "miss"},
        {"prediction_stream": "preference-driven", "outcome": "refined"},
    ]
    streams = record_tool.session_hit_rates(records)
    assert streams["cold"] == {"hit": 1, "miss": 1, "near-tie": 0, "refined": 1}
    assert streams["preference-driven"]["refined"] == 1


def test_resolve_batch_refs_handles_drill_down_and_related() -> None:
    drafts = [draft(), draft(), draft()]
    drafts[0]["slug"] = "parent-ruling"
    drafts[1]["slug"] = "sibling-ruling"
    drafts[2]["slug"] = "follow-up"
    drafts[2]["drill_down_of_slug"] = "parent-ruling"
    drafts[2]["related_slugs"] = ["sibling-ruling"]
    resolved = record_tool.resolve_batch_refs(drafts, NOW)
    assert resolved[2]["drill_down_of"] == record_tool.mint_id("parent-ruling", NOW)
    assert resolved[2]["related"] == [record_tool.mint_id("sibling-ruling", NOW)]
    assert "drill_down_of_slug" not in resolved[2]
    assert "related_slugs" not in resolved[2]

    # related_slugs merges with pre-existing repo-ID references.
    drafts[2]["related"] = ["20260101T000000Z-existing"]
    resolved = record_tool.resolve_batch_refs(drafts, NOW)
    assert resolved[2]["related"] == [
        "20260101T000000Z-existing",
        record_tool.mint_id("sibling-ruling", NOW),
    ]


def test_resolve_batch_refs_rejects_unknown_drill_down_slug() -> None:
    d = draft()
    d["drill_down_of_slug"] = "not-in-batch"
    with pytest.raises(ValueError):
        record_tool.resolve_batch_refs([d], NOW)


def test_recorder_operates_on_the_checkout_it_lives_in(tmp_path: Path) -> None:
    """The store checkout is the tool's own home.

    The recorder ships inside a store, so `which checkout?` has exactly
    one answer: the one this file sits in.
    """
    store = tmp_path / "decision-memory"
    (store / "tools").mkdir(parents=True)
    (store / ".git").mkdir()
    (store / "tools" / "record.py").write_text(
        (PROJECT_ROOT / "decision-memory" / "tools" / "record.py").read_text()
    )
    relocated = load_module("relocated_record", store / "tools" / "record.py")
    assert relocated.store_root() == store


def _record(outcome: str, cited: list[str], disconfirmed=None, correction=None) -> dict:
    record = {
        "options": [
            {"slot": 1, "role": "prediction", "rules_cited": cited},
            {"slot": 2, "rules_cited": ["other rule."]},
        ],
        "chosen_slot": 1 if outcome == "hit" else 2,
        "outcome": outcome,
        "prediction_stream": "preference-driven",
    }
    if disconfirmed is not None:
        record["rules_disconfirmed"] = disconfirmed
    if correction is not None:
        record["correction"] = correction
    return record


def test_a_disconfirmed_rule_earns_no_confirmation() -> None:
    """AET#227: a rule the decider set aside is neither win nor loss.

    submit bumped "Makes absence observable" from 1 to 2 on a record
    that listed it under rules_disconfirmed (decision-memory PR #25).
    """
    record = _record("hit", ["rule a.", "rule b."], disconfirmed=["rule b."])
    confirmations, skipped = record_tool.confirmations_for(record)
    assert confirmations == [("rule a.", False)]
    assert skipped == [("rule b.", "disconfirmed")]


def test_a_correction_earns_no_automatic_confirmation() -> None:
    """A record whose reason was replaced confirms nothing by itself."""
    record = _record("hit", ["rule a."], correction=True)
    confirmations, skipped = record_tool.confirmations_for(record)
    assert confirmations == []
    assert skipped == [("rule a.", "correction")]


def test_an_independent_confirmation_still_skips_a_disconfirmed_rule() -> None:
    record = _record("miss", ["rule a."], disconfirmed=["other rule."])
    confirmations, skipped = record_tool.confirmations_for(record)
    assert confirmations == []
    assert skipped == [("other rule.", "disconfirmed")]
