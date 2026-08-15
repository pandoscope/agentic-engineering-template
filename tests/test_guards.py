"""Tests for the pure functions of the vendored CI guard (guards.py)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent
GUARDS_PATH = PROJECT_ROOT / "decision-memory" / ".github" / "guards" / "guards.py"

guards = load_module("guards", GUARDS_PATH)


def test_commit_subjects_of_the_repo_types_pass() -> None:
    for subject in (
        "decision(factory): repo hosting — private GitHub over self-hosted",
        "pref-proposal: prefers CI-enforced integrity over access control",
        "pref-promote: rejects new infrastructure dependencies",
        "pref-confirm: rejects new infrastructure dependencies (n=4)",
        "chore: initialize repository",
        "chore(ci): tighten guards",
    ):
        assert guards.check_commit_subject(subject) is None, subject


def test_foreign_commit_subjects_fail() -> None:
    for subject in (
        "feat: add a feature",
        "decision: missing project scope",
        "decision(factory): no separator between slug and chosen",
        "pref-confirm: missing counter suffix",
        "update stuff",
    ):
        assert guards.check_commit_subject(subject) is not None, subject


def _rule(
    text="Rejects new deps.", confirmed=3, independent=0, last="2026-07-15", doc=None
):
    return {
        "rule": text,
        "confirmed": confirmed,
        "independent": independent,
        "last": last,
        "doc": doc,
    }


def _prefs(*rules):
    return {"rules": list(rules)}


def test_pref_confirm_counter_math_accepts_single_bump() -> None:
    old = _prefs(_rule(confirmed=3))
    new = _prefs(_rule(confirmed=4, last="2026-07-21"))
    assert guards.validate_pref_confirm_change(old, new) == []


def test_pref_confirm_counter_math_rejects_bad_increment() -> None:
    old = _prefs(_rule(confirmed=3))
    new = _prefs(_rule(confirmed=5, last="2026-07-21"))
    errors = guards.validate_pref_confirm_change(old, new)
    assert any("increment" in e for e in errors)


def test_pref_confirm_counter_math_rejects_text_change() -> None:
    old = _prefs(_rule(text="Rejects new deps.", confirmed=3))
    new = _prefs(_rule(text="Accepts new deps.", confirmed=4, last="2026-07-21"))
    errors = guards.validate_pref_confirm_change(old, new)
    assert any("rule text" in e for e in errors)


def test_pref_confirm_counter_math_rejects_rule_removal() -> None:
    errors = guards.validate_pref_confirm_change(_prefs(_rule()), _prefs())
    assert errors


# --- the preference-set schema ----------------------------------------


def test_an_unknown_rule_key_is_rejected() -> None:
    rule = _rule()
    rule["src"] = "x"
    errors = guards.decision_validator.validate_preferences(_prefs(rule))
    assert errors and any("src" in e for e in errors)


def test_a_rule_missing_its_counters_is_rejected() -> None:
    # The closed key set is what keeps a hand-added rule from entering
    # without counters and reading as one nobody has ever confirmed.
    rule = _rule()
    del rule["confirmed"]
    assert guards.decision_validator.validate_preferences(_prefs(rule))


def test_the_render_never_wraps_a_rule() -> None:
    # One rule, one physical line: text carrying a newline (or a tab,
    # which the TSV render could not escape) fails the schema instead
    # of corrupting the render.
    for text in ("A rule that runs onto\na second line.", "a\tb"):
        assert guards.decision_validator.validate_preferences(
            _prefs(_rule(text=text))
        ), repr(text)


# --- the independent counter -----------------------------------------


def test_a_bump_may_raise_independent_by_one() -> None:
    old = _prefs(_rule(confirmed=3, independent=1))
    new = _prefs(_rule(confirmed=4, independent=2, last="2026-07-21"))
    assert guards.validate_pref_confirm_change(old, new) == []


def test_a_bump_may_not_lower_independent() -> None:
    old = _prefs(_rule(confirmed=3, independent=2))
    new = _prefs(_rule(confirmed=4, independent=1, last="2026-07-21"))
    errors = guards.validate_pref_confirm_change(old, new)
    assert errors and "independent" in errors[0]


def test_independent_may_not_exceed_confirmed() -> None:
    # Independent confirmations are a subset of all of them, so a rule
    # claiming more of the subset than the whole is incoherent
    # regardless of how it got there.
    errors = guards.decision_validator.validate_preferences(
        _prefs(_rule(confirmed=2, independent=3))
    )
    assert errors and any("independent" in e for e in errors)


# --- the predictions corpus ------------------------------------------
# An autonomous run's own choices: same schema, same append-only
# guarantee, outside the preference pipeline entirely.


def test_predictions_are_append_only_too() -> None:
    assert guards.APPEND_ONLY_DIRS == ("decisions/", "predictions/")


def test_the_prediction_commit_type_is_accepted() -> None:
    assert guards.check_commit_subject("prediction(factory): a-slug — chosen") is None


def test_a_prediction_subject_still_needs_its_scope_and_chosen() -> None:
    assert guards.check_commit_subject("prediction: no scope") is not None
    assert guards.check_commit_subject("prediction(factory): no separator") is not None


def test_both_corpora_validate_and_share_the_link_graph(tmp_path) -> None:
    import json as _json

    record = {
        "v": 1,
        "type": "decision",
        "id": "20260730T000000Z-a",
        "date": "2026-07-30",
        "project": "factory",
        "question": "q",
        "options": [
            {"slot": 1, "label": "x", "role": "prediction", "rules_cited": []},
        ],
        "prediction_stream": "cold",
        "artifact_ref": None,
        "chosen_slot": 1,
        "chosen": "x",
        "rejections": [],
        "outcome": "hit",
    }
    (tmp_path / "predictions").mkdir()
    # A prediction whose `related` points nowhere must be caught, which
    # only happens if predictions enter the same link graph.
    dangling = dict(record, id="20260730T000001Z-b", related=["20260730T000009Z-ghost"])
    (tmp_path / "predictions" / f"{dangling['id']}.json").write_text(
        _json.dumps(dangling)
    )
    (tmp_path / "store.config.json").write_text('{"budget_tokens": 2000}')
    errors = guards.check_corpus(str(tmp_path))
    assert any("ghost" in error for error in errors), errors
