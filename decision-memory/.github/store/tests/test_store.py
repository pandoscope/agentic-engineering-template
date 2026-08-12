"""Tests for the budget, guard and replay layer.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

Stdlib `unittest`, no fixture repo: the git-facing adapters are thin
and the decisions they feed live in pure functions, which is what this
exercises. Run from the repo root:

    python .github/store/tests/test_store.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

STORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARDS_DIR = os.path.join(os.path.dirname(STORE_DIR), "guards")
sys.path.insert(0, STORE_DIR)
sys.path.insert(0, GUARDS_DIR)

import budget as store_budget  # noqa: E402
import config as store_config  # noqa: E402
import decision_validator  # noqa: E402
import extraction  # noqa: E402
import guards  # noqa: E402
import preferences_guard as guard  # noqa: E402
import render_preferences  # noqa: E402
import replay  # noqa: E402
import similarity  # noqa: E402


def make_record(record_id, chosen_slot, stream="cold", options=None):
    return {
        "v": 1,
        "type": "decision",
        "id": record_id,
        "date": "2026-07-15",
        "project": "factory",
        "question": "q?",
        "context": "ctx",
        "options": options
        or [
            {
                "slot": 1,
                "label": "a",
                "role": "prediction+recommendation",
                "rules_cited": [],
                "reasoning": "because the old rule said so",
            },
            {"slot": 2, "label": "b", "if_clause": "if x"},
        ],
        "prediction_stream": stream,
        "artifact_ref": None,
        "chosen_slot": chosen_slot,
        "chosen": "a",
        "rejections": [],
        "outcome": "hit",
    }


def make_prediction(record_id, slot, rules=()):
    return {"id": record_id, "predicted_slot": slot, "rules_cited": list(rules)}


def make_rule(
    text="a short rule.",
    section="process",
    confirmed=1,
    independent=0,
    last="2026-07-15",
):
    return {
        "section": section,
        "rule": text,
        "confirmed": confirmed,
        "independent": independent,
        "last": last,
    }


def make_preferences(*rules):
    return {"rules": list(rules)}


def source_text(*rules):
    return json.dumps(make_preferences(*rules))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        self.assertEqual(store_config.validate_config(dict(store_config.DEFAULTS)), [])

    def test_repo_config_loads(self):
        root = os.path.dirname(os.path.dirname(STORE_DIR))
        config = store_config.load_config(root)
        self.assertGreater(config["budget_tokens"], 0)

    def test_missing_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(store_config.load_config(tmp), dict(store_config.DEFAULTS))

    def test_budget_above_the_vendored_default_is_allowed(self):
        """The vendored constant is the DEFAULT, never a ceiling.

        One budget, one authority: the record guard reads this config,
        so a store raising its budget does not have to raise a template
        constant first.
        """
        config = dict(store_config.DEFAULTS)
        config["budget_tokens"] = config["budget_tokens"] * 2
        self.assertEqual(store_config.validate_config(config), [])

    def test_bad_values_are_rejected(self):
        config = dict(store_config.DEFAULTS)
        config.update(
            {
                "warn_at_percent": 0,
                "replay_window": -1,
                "carve_out_label": "",
                "min_gated_cases": 0,
            }
        )
        self.assertEqual(len(store_config.validate_config(config)), 4)

    def test_unknown_keys_are_tolerated(self):
        config = dict(store_config.DEFAULTS)
        config["_comment"] = "hi"
        self.assertEqual(store_config.validate_config(config), [])

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(
                os.path.join(tmp, store_config.CONFIG_FILENAME), "w", encoding="utf-8"
            ) as handle:
                handle.write("{nope")
            with self.assertRaises(store_config.ConfigError):
                store_config.load_config(tmp)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(store_config.DEFAULTS)
        self.config.update({"budget_tokens": 100, "warn_at_percent": 80})

    def test_levels(self):
        # estimate_tokens is len // 4, the vendored heuristic.
        self.assertEqual(
            store_budget.budget_status("x" * 4, self.config)["level"], "ok"
        )
        self.assertEqual(
            store_budget.budget_status("x" * 320, self.config)["level"], "warn"
        )
        self.assertEqual(
            store_budget.budget_status("x" * 404, self.config)["level"], "over"
        )

    def test_at_budget_exactly_is_not_over(self):
        status = store_budget.budget_status("x" * 400, self.config)
        self.assertEqual(status["tokens"], 100)
        self.assertEqual(status["percent"], 100.0)
        self.assertEqual(status["level"], "warn")

    def test_issue_body_mentions_the_skill_and_numbers(self):
        body = store_budget.issue_body(
            store_budget.budget_status("x" * 404, self.config), sha="abc123"
        )
        self.assertIn("compact-preferences", body)
        self.assertIn("over budget", body)
        self.assertIn("abc123", body)


class PreferenceSetTests(unittest.TestCase):
    """Schema and render of the preferences.json / preferences.txt pair."""

    def test_a_valid_set_has_no_errors(self):
        data = make_preferences(make_rule(), make_rule(text="another rule."))
        self.assertEqual(decision_validator.validate_preferences(data), [])

    def test_the_comment_key_is_tolerated(self):
        data = make_preferences(make_rule())
        data["_comment"] = "banner"
        self.assertEqual(decision_validator.validate_preferences(data), [])

    def test_top_level_shape_is_enforced(self):
        self.assertTrue(decision_validator.validate_preferences([]))
        self.assertTrue(decision_validator.validate_preferences({"rules": {}}))
        self.assertTrue(
            decision_validator.validate_preferences({"rules": [], "extra": 1})
        )

    def test_rule_keys_are_a_closed_set(self):
        incomplete = make_rule()
        incomplete.pop("last")
        extra = make_rule()
        extra["note"] = "x"
        for rule in (incomplete, extra):
            self.assertTrue(
                decision_validator.validate_preferences(make_preferences(rule)), rule
            )

    def test_rule_text_is_one_plain_line(self):
        for text in ("", "two\nlines", "tab\tseparated", " leading", "double  space"):
            errors = decision_validator.validate_preferences(
                make_preferences(make_rule(text=text))
            )
            self.assertTrue(errors, repr(text))

    def test_a_joined_qualifier_sentence_is_legal(self):
        """One rule may hold two sentences under one counter — "one line,
        one preference" counts preferences, not sentences."""
        text = (
            "Splits a check until each part is machine-checkable. "
            "Hands a model only what code cannot do, or cannot do efficiently."
        )
        self.assertEqual(
            decision_validator.validate_preferences(
                make_preferences(make_rule(text=text))
            ),
            [],
        )

    def test_counts_are_non_negative_integers(self):
        for key, value in (
            ("confirmed", -1),
            ("confirmed", "3"),
            ("confirmed", True),
            ("independent", 1.0),
        ):
            rule = make_rule()
            rule[key] = value
            self.assertTrue(
                decision_validator.validate_preferences(make_preferences(rule)),
                f"{key}={value!r}",
            )

    def test_independent_never_exceeds_confirmed(self):
        data = make_preferences(make_rule(confirmed=1, independent=2))
        self.assertTrue(decision_validator.validate_preferences(data))

    def test_last_is_a_date(self):
        data = make_preferences(make_rule(last="yesterday"))
        self.assertTrue(decision_validator.validate_preferences(data))

    def test_duplicate_rule_text_is_rejected(self):
        """Counter bumps match by rule text; two rules sharing it would
        make every bump ambiguous."""
        data = make_preferences(make_rule(), make_rule())
        self.assertTrue(decision_validator.validate_preferences(data))

    def test_sections_group_contiguously(self):
        data = make_preferences(
            make_rule(section="process"),
            make_rule(text="b.", section="infrastructure"),
            make_rule(text="c.", section="process"),
        )
        self.assertTrue(decision_validator.validate_preferences(data))

    def test_a_malformed_section_is_rejected(self):
        for section in ("Process", "two words", ""):
            data = make_preferences(make_rule(section=section))
            self.assertTrue(decision_validator.validate_preferences(data), section)

    def test_render_is_the_acked_shape(self):
        data = make_preferences(
            make_rule(
                text="Rejects a new dependency unless it removes a whole class of maintenance.",
                section="infrastructure",
                confirmed=6,
                independent=1,
            ),
            make_rule(
                text="Prefers machine checks over model checks wherever feasible.",
                confirmed=3,
                independent=1,
            ),
        )
        expected = (
            "confirmed\tindependent\trule\n"
            "# infrastructure\n"
            "6\t1\tRejects a new dependency unless it removes a whole class of maintenance.\n"
            "# process\n"
            "3\t1\tPrefers machine checks over model checks wherever feasible.\n"
        )
        self.assertEqual(decision_validator.render_preferences(data), expected)

    def test_an_empty_set_renders_the_header_alone(self):
        self.assertEqual(
            decision_validator.render_preferences({"rules": []}),
            "confirmed\tindependent\trule\n",
        )

    def test_the_date_stays_out_of_the_render(self):
        """`last` matters at update time, never in-session — it stays in
        the JSON and out of the injected surface."""
        rendered = decision_validator.render_preferences(make_preferences(make_rule()))
        self.assertNotIn("2026", rendered)

    def test_parse_preferences_reports_bad_json(self):
        data, errors = decision_validator.parse_preferences("{nope")
        self.assertIsNone(data)
        self.assertTrue(errors)

    def test_serialize_round_trips(self):
        data = make_preferences(make_rule())
        text = decision_validator.serialize_preferences(data)
        self.assertTrue(text.endswith("\n"))
        parsed, errors = decision_validator.parse_preferences(text)
        self.assertEqual(errors, [])
        self.assertEqual(parsed, data)


class PrefConfirmMathTests(unittest.TestCase):
    """Structural counter-bump validation on before/after JSON."""

    def test_a_clean_bump_passes(self):
        old = make_preferences(make_rule(confirmed=3))
        new = make_preferences(make_rule(confirmed=4, last="2026-07-20"))
        self.assertEqual(guards.validate_pref_confirm_change(old, new), [])

    def test_an_independent_bump_rides_along(self):
        old = make_preferences(make_rule(confirmed=3, independent=1))
        new = make_preferences(make_rule(confirmed=4, independent=2, last="2026-07-20"))
        self.assertEqual(guards.validate_pref_confirm_change(old, new), [])

    def test_rule_text_may_not_change(self):
        old = make_preferences(make_rule(text="old rule.", confirmed=3))
        new = make_preferences(make_rule(text="new rule.", confirmed=4))
        errors = guards.validate_pref_confirm_change(old, new)
        self.assertTrue(any("rule text changed" in e for e in errors))

    def test_counter_jumps_are_rejected(self):
        old = make_preferences(make_rule(confirmed=3))
        new = make_preferences(make_rule(confirmed=5))
        errors = guards.validate_pref_confirm_change(old, new)
        self.assertTrue(any("exactly 1" in e for e in errors))

    def test_independent_may_not_move_alone(self):
        old = make_preferences(make_rule(confirmed=3, independent=1))
        new = make_preferences(make_rule(confirmed=3, independent=2))
        self.assertTrue(guards.validate_pref_confirm_change(old, new))

    def test_independent_never_drops(self):
        """Lowering it under a mechanical subject would erase evidence as
        routine bookkeeping."""
        old = make_preferences(make_rule(confirmed=3, independent=2))
        new = make_preferences(make_rule(confirmed=4, independent=1))
        self.assertTrue(guards.validate_pref_confirm_change(old, new))

    def test_rule_count_may_not_change(self):
        old = make_preferences(make_rule())
        new = make_preferences(make_rule(), make_rule(text="smuggled in."))
        errors = guards.validate_pref_confirm_change(old, new)
        self.assertTrue(any("only update counters" in e for e in errors))

    def test_a_bump_that_changes_nothing_is_rejected(self):
        old = make_preferences(make_rule())
        self.assertTrue(guards.validate_pref_confirm_change(old, old))


class PreferencesChangeClassificationTests(unittest.TestCase):
    """One classifier feeds both the commit guard and the carve-out."""

    def test_pure_addition_including_insertion(self):
        a, b, c = (
            make_rule(text="a."),
            make_rule(text="b."),
            make_rule(text="c."),
        )
        kind, errors = guards.classify_preferences_change(
            source_text(a, c), source_text(a, b, c), "pref-promote: b"
        )
        self.assertEqual((kind, errors), ("addition", []))

    def test_file_creation_is_an_addition(self):
        kind, errors = guards.classify_preferences_change(
            None, source_text(make_rule()), "chore: migrate"
        )
        self.assertEqual((kind, errors), ("addition", []))

    def test_reordering_is_a_rewrite(self):
        a, b = make_rule(text="a."), make_rule(text="b.")
        kind, _ = guards.classify_preferences_change(
            source_text(a, b), source_text(b, a), "pref-compact: reorder"
        )
        self.assertEqual(kind, "rewrite")

    def test_a_valid_bump_under_the_confirm_subject_is_exempt(self):
        old = source_text(make_rule(confirmed=3))
        new = source_text(make_rule(confirmed=4, last="2026-07-20"))
        kind, errors = guards.classify_preferences_change(
            old, new, "pref-confirm: a short rule. (n=4)"
        )
        self.assertEqual((kind, errors), ("bump-exempt", []))

    def test_a_bad_bump_surfaces_the_math(self):
        old = source_text(make_rule(confirmed=3))
        new = source_text(make_rule(confirmed=9))
        kind, errors = guards.classify_preferences_change(
            old, new, "pref-confirm: a short rule. (n=9)"
        )
        self.assertEqual(kind, "rewrite")
        self.assertTrue(errors)

    def test_invalid_json_is_loud(self):
        kind, errors = guards.classify_preferences_change(
            "{nope", source_text(make_rule()), "chore: oops"
        )
        self.assertEqual(kind, "invalid")
        self.assertTrue(errors)

    def test_an_unchanged_set_is_none(self):
        text = source_text(make_rule())
        self.assertEqual(
            guards.classify_preferences_change(text, text, "chore: unrelated"),
            ("none", []),
        )


class MigrationTests(unittest.TestCase):
    LEGACY = (
        "# Active Preference Set\n"
        "\n"
        "Prose header the migration drops.\n"
        "\n"
        "## Infrastructure\n"
        "\n"
        "- Rejects a new dependency. [confirmed: 6, independent: 1, last: 2026-08-10]\n"
        "\n"
        "## Process\n"
        "\n"
        "- A rule that wraps\n"
        "  across lines. [confirmed: 0, independent: 0, last: 2026-08-11]\n"
    )

    def test_legacy_bullets_become_rules(self):
        rules, errors = render_preferences.parse_legacy(self.LEGACY)
        self.assertEqual(errors, [])
        self.assertEqual(rules[0]["section"], "infrastructure")
        self.assertEqual(rules[0]["confirmed"], 6)
        self.assertEqual(rules[0]["independent"], 1)
        self.assertEqual(rules[1]["rule"], "A rule that wraps across lines.")
        self.assertEqual(rules[1]["last"], "2026-08-11")

    def test_a_missing_suffix_fails(self):
        _, errors = render_preferences.parse_legacy("## S\n\n- bare rule\n")
        self.assertTrue(errors)

    def test_migrate_writes_the_pair_and_removes_the_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "preferences.md", self.LEGACY)
            self.assertEqual(render_preferences.main(["migrate", "--root", tmp]), 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, "preferences.md")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "preferences.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "preferences.txt")))
            self.assertEqual(guards.check_corpus(tmp), [])

    def test_migrate_refuses_a_second_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "preferences.md", self.LEGACY)
            self.assertEqual(render_preferences.main(["migrate", "--root", tmp]), 0)
            self._write(tmp, "preferences.md", self.LEGACY)
            self.assertNotEqual(render_preferences.main(["migrate", "--root", tmp]), 0)

    def test_check_detects_drift_and_render_repairs_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "preferences.md", self.LEGACY)
            render_preferences.main(["migrate", "--root", tmp])
            self._write(tmp, "preferences.txt", "confirmed\tindependent\trule\n")
            self.assertNotEqual(render_preferences.main(["check", "--root", tmp]), 0)
            self.assertEqual(render_preferences.main(["render", "--root", tmp]), 0)
            self.assertEqual(render_preferences.main(["check", "--root", tmp]), 0)

    @staticmethod
    def _write(root, name, text):
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(text)


class RuleLinesTests(unittest.TestCase):
    def test_the_rendered_format_parses(self):
        text = (
            "confirmed\tindependent\trule\n# process\n3\t1\tPrefers machine checks.\n"
        )
        self.assertEqual(similarity.rule_lines(text), ["Prefers machine checks."])

    def test_legacy_bullets_still_parse(self):
        """Records pin pre-migration commits; preferences_at serves those
        files verbatim, so both formats must stay readable."""
        text = "- old rule. [confirmed: 1, independent: 0, last: 2026-07-15]\n"
        self.assertEqual(similarity.rule_lines(text), ["old rule."])


class CarveOutTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(store_config.DEFAULTS)

    @staticmethod
    def commit(subject, old=None, new=None, sha="abcdef1234"):
        return {"sha": sha, "subject": subject, "old_source": old, "new_source": new}

    def test_pure_addition_needs_no_label(self):
        commits = [
            self.commit(
                "pref-promote: a new rule",
                old=source_text(make_rule()),
                new=source_text(make_rule(), make_rule(text="a new rule.")),
            )
        ]
        required, _ = guard.classify_pref_commits(commits)
        self.assertFalse(required)

    def test_valid_counter_bump_is_exempt(self):
        required, notes = guard.classify_pref_commits(
            [
                self.commit(
                    "pref-confirm: a short rule. (n=4)",
                    old=source_text(make_rule(confirmed=3)),
                    new=source_text(make_rule(confirmed=4, last="2026-07-20")),
                )
            ]
        )
        self.assertFalse(required)
        self.assertTrue(any("exempt" in note for note in notes))

    def test_counter_bump_that_rewrites_the_rule_needs_the_label(self):
        required, _ = guard.classify_pref_commits(
            [
                self.commit(
                    "pref-confirm: rule text (n=4)",
                    old=source_text(make_rule(text="old rule text.", confirmed=3)),
                    new=source_text(make_rule(text="new rule text.", confirmed=4)),
                )
            ]
        )
        self.assertTrue(required)

    def test_an_invalid_change_needs_the_label(self):
        required, notes = guard.classify_pref_commits(
            [self.commit("chore: oops", old="{nope", new=source_text(make_rule()))]
        )
        self.assertTrue(required)
        self.assertTrue(notes)

    def test_rewrite_needs_the_label(self):
        commits = [
            self.commit(
                "pref-promote: merged rule",
                old=source_text(make_rule(text="old rule.")),
                new=source_text(make_rule(text="merged rule.")),
            )
        ]
        errors, _ = guard.evaluate(
            commits=commits,
            labels=[],
            body="",
            head_preferences="short",
            preferences_touched=True,
            config=self.config,
        )
        self.assertTrue(any("without the" in e for e in errors))

    def test_labelled_rewrite_requires_a_replay_report(self):
        errors, _ = guard.evaluate(
            commits=[self._rewrite_commit()],
            labels=[self.config["carve_out_label"]],
            body="no report here",
            head_preferences="short",
            preferences_touched=True,
            config=self.config,
        )
        self.assertTrue(any("no replay report" in e for e in errors))

    def test_labelled_rewrite_passes_with_a_matching_passing_report(self):
        head = "compacted rules"
        report = {
            "gate": "pass",
            "candidate_preferences_sha256": guard.preferences_sha256(head),
        }
        body = f"{guard.REPLAY_MARKER}\n```json\n{json.dumps(report)}\n```\n"
        errors, _ = guard.evaluate(
            commits=[self._rewrite_commit()],
            labels=[self.config["carve_out_label"]],
            body=body,
            head_preferences=head,
            preferences_touched=True,
            config=self.config,
        )
        self.assertEqual(errors, [])

    def test_stale_report_is_rejected(self):
        report = {
            "gate": "pass",
            "candidate_preferences_sha256": guard.preferences_sha256("older text"),
        }
        body = f"{guard.REPLAY_MARKER}\n```json\n{json.dumps(report)}\n```\n"
        errors, _ = guard.check_replay_report(body, "current text")
        self.assertTrue(any("different preferences.txt" in e for e in errors))

    def test_failing_gate_is_rejected(self):
        head = "compacted"
        body = self._body("fail", head)
        errors, _ = guard.check_replay_report(body, head)
        self.assertTrue(any("gate is" in e for e in errors))

    def test_failing_gate_is_not_waivable(self):
        """The waiver covers absent evidence, never a measured regression."""
        head = "compacted"
        errors, _ = guard.check_replay_report(
            self._body("fail", head), head, waived=True, waiver_label="waiver"
        )
        self.assertTrue(any("gate is" in e for e in errors))

    def test_insufficient_evidence_is_rejected_without_the_waiver(self):
        head = "compacted"
        errors, _ = guard.check_replay_report(
            self._body(replay.GATE_INSUFFICIENT, head, gated_cases=3),
            head,
            waiver_label="waiver",
        )
        self.assertTrue(any("below the" in e for e in errors))

    def test_insufficient_evidence_passes_with_the_waiver(self):
        head = "compacted"
        errors, notes = guard.check_replay_report(
            self._body(replay.GATE_INSUFFICIENT, head, gated_cases=3),
            head,
            waived=True,
            waiver_label="waiver",
        )
        self.assertEqual(errors, [])
        self.assertTrue(any("human judgement" in note for note in notes))

    def test_waiver_label_reaches_the_report_check(self):
        """The label lives in config; evaluate() must thread it through."""
        head = "compacted"
        config = dict(self.config)
        errors, _ = guard.evaluate(
            commits=[self._rewrite_commit()],
            labels=[config["carve_out_label"], config["replay_waiver_label"]],
            body=self._body(replay.GATE_INSUFFICIENT, head, gated_cases=3),
            head_preferences=head,
            preferences_touched=True,
            config=config,
        )
        self.assertEqual(errors, [])

    @classmethod
    def _rewrite_commit(cls):
        return cls.commit(
            "pref-compact: merged rule",
            old=source_text(make_rule(text="old rule.")),
            new=source_text(make_rule(text="merged rule.")),
        )

    @staticmethod
    def _body(gate, head, gated_cases=9):
        report = {
            "gate": gate,
            "gated_cases": gated_cases,
            "min_gated_cases": 8,
            "candidate_preferences_sha256": guard.preferences_sha256(head),
        }
        return f"{guard.REPLAY_MARKER}\n```json\n{json.dumps(report)}\n```\n"


class BudgetGateTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(store_config.DEFAULTS)
        self.config.update({"budget_tokens": 10, "warn_at_percent": 80})

    def test_over_budget_blocks_a_pr_that_touches_the_file(self):
        errors, _ = guard.evaluate(
            commits=[],
            labels=[],
            body="",
            head_preferences="x" * 100,
            preferences_touched=True,
            config=self.config,
        )
        self.assertTrue(any("blocked until it is compacted" in e for e in errors))

    def test_over_budget_does_not_block_unrelated_prs(self):
        errors, notes = guard.evaluate(
            commits=[],
            labels=[],
            body="",
            head_preferences="x" * 100,
            preferences_touched=False,
            config=self.config,
        )
        self.assertEqual(errors, [])
        self.assertTrue(any("not blocked" in note for note in notes))


class ReplayTests(unittest.TestCase):
    def test_mask_strips_leaky_fields(self):
        case = replay.mask_record(make_record("20260715T143205Z-a", 1))
        self.assertNotIn("chosen_slot", case)
        self.assertNotIn("outcome", case)
        for option in case["options"]:
            self.assertNotIn("role", option)
            self.assertNotIn("rules_cited", option)
            self.assertNotIn("reasoning", option)
        self.assertEqual(case["options"][1]["if_clause"], "if x")

    def test_mask_can_keep_reasoning(self):
        case = replay.mask_record(
            make_record("20260715T143205Z-a", 1), include_reasoning=True
        )
        self.assertIn("reasoning", case["options"][0])

    def test_window_takes_the_most_recent(self):
        records = [make_record(f"2026071{i}T143205Z-a", 1) for i in range(5)]
        selected = replay.select_window(records, 2)
        self.assertEqual(
            [r["id"] for r in selected], [records[3]["id"], records[4]["id"]]
        )

    def test_scoring_splits_streams(self):
        records = [
            make_record("20260715T143205Z-a", 1),
            make_record("20260716T143205Z-b", 2),
        ]
        predictions, errors = replay.normalise_predictions(
            {
                "predictions": [
                    make_prediction("20260715T143205Z-a", 1, ["rule one"]),
                    make_prediction("20260716T143205Z-b", 1),
                ]
            }
        )
        self.assertEqual(errors, [])
        report, score_errors = replay.score(records, predictions, 20, "prefs")
        self.assertEqual(score_errors, [])
        self.assertEqual(
            report["streams"]["preference-driven"], {"n": 1, "hits": 1, "hit_rate": 1.0}
        )
        self.assertEqual(
            report["streams"]["cold"], {"n": 1, "hits": 0, "hit_rate": 0.0}
        )

    def test_stream_shift_is_reported(self):
        records = [make_record("20260715T143205Z-a", 1, stream="cold")]
        predictions, _ = replay.normalise_predictions(
            [make_prediction("20260715T143205Z-a", 1, ["a merged rule"])]
        )
        report, _ = replay.score(records, predictions, 20, "prefs")
        self.assertEqual(
            report["stream_shifts"],
            [
                {
                    "id": "20260715T143205Z-a",
                    "recorded": "cold",
                    "candidate": "preference-driven",
                }
            ],
        )

    def test_missing_and_extra_predictions_are_errors(self):
        records = [make_record("20260715T143205Z-a", 1)]
        predictions, _ = replay.normalise_predictions(
            [make_prediction("20260799T143205Z-z", 1)]
        )
        _, errors = replay.score(records, predictions, 20, "prefs")
        self.assertEqual(len(errors), 2)

    def test_bad_prediction_entries_are_rejected(self):
        _, errors = replay.normalise_predictions(
            [{"id": "x", "predicted_slot": "one"}, {"predicted_slot": 1}, "nope"]
        )
        self.assertEqual(len(errors), 3)

    def test_gate_passes_when_the_hit_rate_holds(self):
        baseline = self._report(pd=(4, 5), cold=(1, 5), sha="base")
        candidate = self._report(pd=(4, 5), cold=(0, 5), sha="cand")
        result = replay.gate(baseline, candidate)
        self.assertEqual(result["gate"], "pass")
        self.assertEqual(result["candidate_preferences_sha256"], "cand")

    def test_gate_fails_on_degradation(self):
        baseline = self._report(pd=(4, 5), cold=(1, 5), sha="base")
        candidate = self._report(pd=(2, 5), cold=(5, 5), sha="cand")
        result = replay.gate(baseline, candidate)
        self.assertEqual(result["gate"], "fail")
        self.assertTrue(any("degraded" in reason for reason in result["reasons"]))

    def test_gate_fails_when_the_candidate_drives_nothing(self):
        baseline = self._report(pd=(0, 3), cold=(1, 2), sha="base")
        candidate = self._report(pd=(0, 0), cold=(1, 5), sha="cand")
        result = replay.gate(baseline, candidate)
        self.assertEqual(result["gate"], "fail")

    def test_gate_fails_on_mismatched_windows(self):
        baseline = self._report(pd=(1, 1), cold=(0, 0), sha="base", ids=["a"])
        candidate = self._report(pd=(1, 1), cold=(0, 0), sha="cand", ids=["b"])
        self.assertEqual(replay.gate(baseline, candidate)["gate"], "fail")

    @staticmethod
    def _report(pd, cold, sha, ids=("a",)):
        def stream(hits_total):
            hits, total = hits_total
            return {
                "n": total,
                "hits": hits,
                "hit_rate": round(hits / total, 4) if total else None,
            }

        return {
            "window": 20,
            "scored": len(ids),
            "preferences_sha256": sha,
            "preferences_tokens": 100,
            "streams": {"preference-driven": stream(pd), "cold": stream(cold)},
            "stream_shifts": [],
            "cases": [{"id": case_id} for case_id in ids],
        }


class CorpusReplayTests(unittest.TestCase):
    """The harness must cope with the real corpus, not just fixtures."""

    def test_cases_build_from_the_real_decisions(self):
        root = os.path.dirname(os.path.dirname(STORE_DIR))
        records = replay.load_records(root)
        if not records:
            # No corpus: this file also runs from the template that
            # vendors it, where decisions/ does not exist. The fixture
            # tests above still cover the harness; only this
            # real-corpus check needs a store to be meaningful.
            self.skipTest("no decisions/ corpus here — not a store checkout")
        cases = replay.build_cases(records, 20)
        self.assertEqual(cases["count"], min(20, len(records)))
        for case in cases["cases"]:
            self.assertIn("question", case)
            self.assertTrue(case["options"])


class SlotPermutationTests(unittest.TestCase):
    """Slot position must carry no signal — it used to carry most of it."""

    @staticmethod
    def _record(record_id, chosen_slot=1):
        options = [
            {"slot": 1, "label": "predicted", "role": "prediction", "rules_cited": []},
            {"slot": 2, "label": "recommended", "role": "recommendation"},
            {"slot": 3, "label": "wildcard", "role": "wildcard"},
        ]
        return make_record(record_id, chosen_slot, options=options)

    def test_presented_slots_are_renumbered_and_shuffled(self):
        """Some record in a spread must move, or nothing was permuted."""
        moved = False
        for index in range(12):
            record = self._record(f"202607{index:02d}T143205Z-a")
            case = replay.mask_record(record)
            self.assertEqual([o["slot"] for o in case["options"]], [1, 2, 3])
            if case["options"][0]["label"] != "predicted":
                moved = True
        self.assertTrue(moved)

    def test_the_permutation_is_stable_for_one_id(self):
        record = self._record("20260715T143205Z-a")
        first = [o["label"] for o in replay.mask_record(record)["options"]]
        second = [o["label"] for o in replay.mask_record(record)["options"]]
        self.assertEqual(first, second)

    def test_unmap_inverts_the_presentation_order(self):
        record = self._record("20260715T143205Z-a")
        case = replay.mask_record(record)
        for option in case["options"]:
            recorded = replay.unmap_slot(record, option["slot"])
            self.assertEqual(option["label"], self._label_of(record, recorded))

    def test_free_text_slot_beyond_the_options_maps_to_itself(self):
        """chosen_slot 4 against three options was never presented."""
        record = self._record("20260715T143205Z-a", chosen_slot=4)
        self.assertEqual(replay.unmap_slot(record, 4), 4)

    def test_scoring_un_maps_before_comparing(self):
        record = self._record("20260715T143205Z-a", chosen_slot=3)
        case = replay.mask_record(record)
        presented = next(
            option["slot"]
            for option in case["options"]
            if option["label"] == self._label_of(record, 3)
        )
        predictions, _ = replay.normalise_predictions(
            [make_prediction(record["id"], presented)]
        )
        report, errors = replay.score([record], predictions, 20, "prefs")
        self.assertEqual(errors, [])
        self.assertTrue(report["cases"][0]["hit"])
        self.assertEqual(report["cases"][0]["predicted_slot"], 3)
        self.assertEqual(report["cases"][0]["presented_slot"], presented)

    def test_cases_never_ship_the_mapping(self):
        """A shipped mapping hands the ordering signal straight back."""
        payload = replay.build_cases([self._record("20260715T143205Z-a")], 20)
        rendered = json.dumps(payload)
        self.assertNotIn("role", rendered)
        self.assertNotIn("slot_map", rendered)
        self.assertNotIn("recorded_slot", rendered)

    @staticmethod
    def _label_of(record, slot):
        return next(o["label"] for o in record["options"] if o["slot"] == slot)


class GateVerdictTests(unittest.TestCase):
    """Small-n must not read as validation."""

    def test_small_gated_stream_is_insufficient_evidence_not_pass(self):
        baseline = ReplayTests._report(pd=(3, 3), cold=(1, 5), sha="base")
        candidate = ReplayTests._report(pd=(3, 3), cold=(1, 5), sha="cand")
        result = replay.gate(baseline, candidate, min_gated_cases=8)
        self.assertEqual(result["gate"], replay.GATE_INSUFFICIENT)
        self.assertEqual(result["gated_cases"], 3)
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["notes"])

    def test_a_large_enough_stream_still_passes(self):
        baseline = ReplayTests._report(pd=(8, 9), cold=(1, 5), sha="base")
        candidate = ReplayTests._report(pd=(8, 9), cold=(1, 5), sha="cand")
        self.assertEqual(
            replay.gate(baseline, candidate, min_gated_cases=8)["gate"],
            replay.GATE_PASS,
        )

    def test_degradation_outranks_insufficient_evidence(self):
        """A regression visible at small n is still a regression."""
        baseline = ReplayTests._report(pd=(3, 3), cold=(1, 5), sha="base")
        candidate = ReplayTests._report(pd=(1, 3), cold=(1, 5), sha="cand")
        self.assertEqual(
            replay.gate(baseline, candidate, min_gated_cases=8)["gate"],
            replay.GATE_FAIL,
        )

    def test_default_threshold_of_zero_keeps_the_old_behaviour(self):
        baseline = ReplayTests._report(pd=(1, 1), cold=(0, 0), sha="base")
        candidate = ReplayTests._report(pd=(1, 1), cold=(0, 0), sha="cand")
        self.assertEqual(replay.gate(baseline, candidate)["gate"], replay.GATE_PASS)


class ExtractionTests(unittest.TestCase):
    """Scope is the PR's records; evidence is the whole corpus."""

    @staticmethod
    def _corpus(n=4):
        return [make_record(f"2026071{i}T14320{i}Z-case", 1) for i in range(n)]

    def test_scope_is_what_came_after_the_watermark(self):
        records = self._corpus()
        batch = extraction.build_batch(records, {records[2]["id"], records[3]["id"]})
        self.assertEqual(batch["count"], 2)
        self.assertEqual(batch["scope"], sorted([records[2]["id"], records[3]["id"]]))

    def test_history_carries_everything_outside_the_scope(self):
        """Cross-session repetition is evidence the pass must still see."""
        records = self._corpus()
        batch = extraction.build_batch(records, {records[3]["id"]})
        self.assertEqual(batch["history_count"], 3)
        self.assertEqual(
            [entry["id"] for entry in batch["history"]],
            [record["id"] for record in records[:3]],
        )

    def test_an_empty_scope_still_reports_the_corpus(self):
        records = self._corpus()
        batch = extraction.build_batch(records, set())
        self.assertEqual(batch["count"], 0)
        self.assertEqual(batch["history_count"], 4)

    def test_queues_rank_corrections_above_misses(self):
        correction = make_record("20260715T143205Z-a", 1)
        correction.update({"correction": True, "outcome": "miss"})
        self.assertEqual(extraction.queue_for(correction), extraction.QUEUE_CORRECTIONS)

    def test_queue_for_each_outcome(self):
        cases = {
            "miss": extraction.QUEUE_MISSES,
            "refined": extraction.QUEUE_REFINEMENTS,
            "near-tie": extraction.QUEUE_REFINEMENTS,
            "hit": extraction.QUEUE_CONFIRMATIONS,
        }
        for outcome, queue in cases.items():
            record = make_record("20260715T143205Z-a", 1)
            record["outcome"] = outcome
            self.assertEqual(extraction.queue_for(record), queue)

    def test_rule_driven_acceptance_is_flagged(self):
        """A rule that cited itself into the chosen slot proves nothing."""
        record = make_record("20260715T143205Z-a", 1)
        record["options"][0]["rules_cited"] = ["some rule"]
        self.assertTrue(extraction.is_rule_driven_acceptance(record))

    def test_a_cited_rule_that_lost_is_not_flagged(self):
        record = make_record("20260715T143205Z-a", 2)
        record["options"][0]["rules_cited"] = ["some rule"]
        self.assertFalse(extraction.is_rule_driven_acceptance(record))

    def test_uncited_prediction_is_not_flagged(self):
        self.assertFalse(
            extraction.is_rule_driven_acceptance(make_record("20260715T143205Z-a", 1))
        )

    def test_summaries_keep_the_output_side(self):
        """Unlike replay, extraction reads what the decider actually did."""
        summary = extraction.summarise(make_record("20260715T143205Z-a", 2))
        self.assertEqual(summary["chosen_slot"], 2)
        self.assertIn("operative_reason", summary)


class ExtractionWatermarkTests(unittest.TestCase):
    """The watermark is a commit's position, derived not stored."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "decisions"))
        self._git("init", "-q")
        self._git("config", "user.email", "t@e.st")
        self._git("config", "user.name", "test")
        self._commit("chore: initialize repository", allow_empty=True)

    def _git(self, *args):
        subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True)

    def _commit(self, subject, allow_empty=False):
        self._git("add", "-A")
        args = ["commit", "-qm", subject]
        if allow_empty:
            args.insert(1, "--allow-empty")
        self._git(*args)
        return subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _add_record(self, suffix):
        record = make_record(f"202607{suffix}T143205Z-case", 1)
        path = os.path.join(self.root, "decisions", f"{record['id']}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        self._commit(f"decision(f): case-{suffix} — a")
        return record["id"]

    def test_a_shallow_clone_is_refused_rather_than_guessed_at(self):
        """A truncated history cannot distinguish "no pass ever ran" from
        "the pass is older than the boundary" — and the answers differ by
        the whole corpus.

        Observed on the live store: a shallow clone reported 106 records
        and no watermark, when five passes had run and the real scope was
        19. Committing that pass would have re-proposed promoted rules and
        double-bumped counters, and the merge gate would have allowed it —
        it checks that a pass commit EXISTS, never that its scope was real.

        The grilling skill tells sessions to shallow-clone the store, so
        this is the normal state, not an exotic one.
        """
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        later = self._add_record("02")

        # Re-clone shallowly: the pass commit falls outside the boundary.
        shallow = os.path.join(self.tmp.name, "shallow")
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{self.root}", shallow],
            check=True,
            capture_output=True,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", shallow, "rev-parse", "--is-shallow-repository"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "true",
            "fixture must actually be shallow, or this test proves nothing",
        )

        with self.assertRaises(SystemExit) as caught:
            extraction.scope_ids(shallow)
        self.assertIn("shallow", str(caught.exception).lower())
        self.assertIn("unshallow", str(caught.exception))
        del later

    def test_no_pass_ever_means_the_whole_corpus(self):
        """Bootstrap is not a mode — it is what an empty history means."""
        first = self._add_record("01")
        second = self._add_record("02")
        scope, watermark = extraction.scope_ids(self.root)
        self.assertIsNone(watermark)
        self.assertEqual(scope, {first, second})

    def test_a_pass_moves_the_watermark(self):
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        scope, watermark = extraction.scope_ids(self.root)
        self.assertIsNotNone(watermark)
        self.assertEqual(scope, set())

    def test_records_after_a_pass_are_the_next_scope(self):
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        later = self._add_record("02")
        scope, _ = extraction.scope_ids(self.root)
        self.assertEqual(scope, {later})

    def test_an_empty_pass_still_moves_the_watermark(self):
        """A pass that found nothing produces no proposal and no bump.

        Keying the watermark on those would stall it every time
        extraction legitimately had nothing to say.
        """
        self._add_record("01")
        sha = self._commit(
            f"{extraction.EXTRACTION_PREFIX} 1 record, nothing to promote",
            allow_empty=True,
        )
        self.assertEqual(extraction.last_extraction_commit("HEAD", self.root), sha)
        self.assertEqual(extraction.scope_ids(self.root)[0], set())

    def test_a_pref_confirm_commit_is_not_a_watermark(self):
        """`submit` emits those, so they would move it for a pass that
        never ran — a false watermark skips records instead of
        re-reading them."""
        self._add_record("01")
        self._commit("pref-confirm: some rule (n=2)", allow_empty=True)
        self.assertIsNone(extraction.last_extraction_commit("HEAD", self.root))

    def test_a_body_mention_is_not_a_watermark(self):
        self._add_record("01")
        self._git(
            "commit",
            "--allow-empty",
            "-qm",
            "chore: talk about it",
            "-m",
            "we should pref-extract: something eventually",
        )
        self.assertIsNone(extraction.last_extraction_commit("HEAD", self.root))

    def test_the_newest_pass_wins(self):
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} first", allow_empty=True)
        second = self._add_record("02")
        newest = self._commit(
            f"{extraction.EXTRACTION_PREFIX} second", allow_empty=True
        )
        self.assertEqual(extraction.last_extraction_commit("HEAD", self.root), newest)
        self.assertEqual(extraction.added_since(newest, self.root), set())
        self.assertIn(second, extraction.added_since(None, self.root))

    def test_a_missed_pass_is_recovered_by_the_next_one(self):
        """The self-healing property: a session that merged without a
        pass is not lost, the next pass simply reaches further back."""
        missed = self._add_record("01")
        # no pass here — this is the session that slipped through
        later = self._add_record("02")
        scope, watermark = extraction.scope_ids(self.root)
        self.assertIsNone(watermark)
        self.assertEqual(scope, {missed, later})


class ExtractionGateTests(ExtractionWatermarkTests):
    """A missed pass is recoverable, but nothing would prompt recovery."""

    def setUp(self):
        super().setUp()
        self.base = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_a_branch_adding_no_records_needs_no_pass(self):
        self._commit("chore: unrelated", allow_empty=True)
        self.assertEqual(extraction.check_pass(self.base, self.root), [])

    def test_a_branch_adding_records_without_a_pass_fails(self):
        self._add_record("01")
        errors = extraction.check_pass(self.base, self.root)
        self.assertTrue(any("contains no pref-extract:" in e for e in errors))

    def test_a_branch_closing_with_a_pass_succeeds(self):
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        self.assertEqual(extraction.check_pass(self.base, self.root), [])

    def test_a_record_added_after_the_pass_fails(self):
        """Extraction is the last step; a later record is one it never saw."""
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        self._add_record("02")
        errors = extraction.check_pass(self.base, self.root)
        self.assertTrue(any("added after the" in e for e in errors))

    def test_the_guard_surfaces_the_failure(self):
        self._add_record("01")
        errors, _ = guard.evaluate(
            commits=[],
            labels=[],
            body="",
            head_preferences="short",
            preferences_touched=False,
            config=dict(store_config.DEFAULTS),
            extraction_errors=extraction.check_pass(self.base, self.root),
        )
        self.assertTrue(any("contains no pref-extract:" in e for e in errors))

    def test_the_guard_passes_a_closed_branch(self):
        self._add_record("01")
        self._commit(f"{extraction.EXTRACTION_PREFIX} 1 record", allow_empty=True)
        errors, notes = guard.evaluate(
            commits=[],
            labels=[],
            body="",
            head_preferences="short",
            preferences_touched=False,
            config=dict(store_config.DEFAULTS),
            extraction_errors=extraction.check_pass(self.base, self.root),
            extraction_note="extraction pass abc123def closes this PR's 1 record(s)",
        )
        self.assertEqual(errors, [])
        self.assertTrue(any("extraction pass" in note for note in notes))


class SimilarityGateTests(unittest.TestCase):
    """Everything this gate catches is only fixable before ingestion."""

    @staticmethod
    def _draft(record_id, question, chosen, **overrides):
        """A record shaped like a real one.

        The chosen option's label carries the ANSWER, not the question —
        conflating them makes every pair on one question look like it
        reached the same answer.
        """
        record = make_record(record_id, 1)
        record["question"] = question
        record["chosen"] = chosen
        record["options"] = [{"slot": 1, "label": chosen}]
        record.update(overrides)
        return record

    def test_unrelated_records_do_not_cluster(self):
        """A false cluster costs a glance; the threshold must still hold."""
        left = self._draft("20260715T143205Z-a", "How do agents reach the store?", "x")
        right = self._draft(
            "20260716T143205Z-b", "Which wrapping style for prose files?", "y"
        )
        self.assertLess(similarity.similarity(left, right), 0.35)

    def test_reworded_same_ruling_is_a_duplicate(self):
        """Two extraction runs over one session, different wording."""
        commit = {"commit": "abc123"}
        left = self._draft(
            "20260715T143205Z-a",
            "How are rejection reasons recorded on a silent pick?",
            "declared provenance enum",
            preference_set=commit,
        )
        right = self._draft(
            "20260715T143206Z-b",
            "How are rejection reasons recorded when the decider picks silently?",
            "declared provenance enum",
            preference_set=commit,
        )
        self.assertGreaterEqual(similarity.similarity(left, right), 0.35)
        self.assertEqual(similarity.classify(left, right), similarity.DUPLICATE)

    def test_distinct_provenance_is_a_re_decision(self):
        left = self._draft(
            "20260715T143205Z-a",
            "Where do glossary terms live?",
            "vendored",
            preference_set={"commit": "aaa"},
        )
        right = self._draft(
            "20270101T120000Z-b",
            "Where do glossary terms live?",
            "thin stubs",
            preference_set={"commit": "bbb"},
        )
        self.assertEqual(similarity.classify(left, right), similarity.RE_DECISION)

    def test_an_existing_link_outranks_every_other_verdict(self):
        left = self._draft(
            "20260715T143205Z-a",
            "Where do glossary terms live?",
            "vendored",
            preference_set={"commit": "aaa"},
        )
        right = self._draft(
            "20270101T120000Z-b",
            "Where do glossary terms live?",
            "thin stubs",
            preference_set={"commit": "bbb"},
            supersedes="20260715T143205Z-a",
        )
        self.assertEqual(similarity.classify(left, right), similarity.LINKED)

    def test_missing_provenance_with_different_answers_is_uncertain(self):
        """Chat drafts carry null provenance by design — absence is not
        evidence of distinctness, so this needs a human."""
        left = self._draft("20260715T143205Z-a", "Same question?", "one", session=None)
        right = self._draft("20260715T143206Z-b", "Same question?", "two", session=None)
        self.assertEqual(similarity.classify(left, right), similarity.UNCERTAIN)

    def test_artifact_corroboration_lifts_a_borderline_pair(self):
        ref = {"repo": "r", "path": "docs/conventions.md", "commit": None}
        left = self._draft("20260715T143205Z-a", "How is wrapping handled?", "sembr")
        right = self._draft("20260716T143205Z-b", "How is wrapping decided?", "sembr")
        bare = similarity.similarity(left, right)
        left["artifact_ref"] = dict(ref)
        right["artifact_ref"] = dict(ref)
        self.assertGreater(similarity.similarity(left, right), bare)

    def test_a_differing_artifact_does_not_corroborate(self):
        left = self._draft("20260715T143205Z-a", "q", "x")
        right = self._draft("20260716T143205Z-b", "q", "x")
        left["artifact_ref"] = {"repo": "r", "path": "a.md"}
        right["artifact_ref"] = {"repo": "r", "path": "b.md"}
        self.assertFalse(similarity.artifact_corroborates(left, right))

    def test_a_reworded_answer_still_reads_as_agreement(self):
        """Two extractions of one ruling reword freely; exact equality
        would call every reworded duplicate a different answer."""
        left = self._draft(
            "20260715T143205Z-a",
            "Which artifacts enter first?",
            "start only with skills that have mechanical oracles",
        )
        right = self._draft(
            "20260715T143206Z-b",
            "Which skills enter first?",
            "start only with skills that have mechanical oracles",
        )
        right["chosen"] = "mechanical-oracle artifacts first; the logger is the seed"
        self.assertTrue(similarity.answers_agree(left, right))

    def test_the_chosen_slot_number_is_never_compared(self):
        """Two runs ordering the options differently give one ruling
        different slot numbers — seen in real data as slot 1 vs slot 3."""
        answer = "both: write-time best-effort, compaction authoritative"
        left = self._draft(
            "20260715T143205Z-a", "Where does enforcement happen?", answer
        )
        right = self._draft(
            "20260715T143206Z-b", "Where does enforcement happen?", answer
        )
        right["chosen_slot"] = 3
        right["options"] = [
            {"slot": 1, "label": "at write time"},
            {"slot": 2, "label": "at compaction"},
            {"slot": 3, "label": answer},
        ]
        self.assertTrue(similarity.answers_agree(left, right))

    def test_a_label_is_never_compared_against_prose(self):
        """The cross-match must not fire.

        Here left's PROSE equals right's LABEL, and nothing else
        matches. Comparing across kinds would score 1.0 and call two
        unrelated answers agreement; like-with-like scores 0 twice.
        """
        left = self._draft("20260715T143205Z-a", "Same question?", "beta")
        left["options"] = [{"slot": 1, "label": "alpha"}]
        right = self._draft("20260715T143206Z-b", "Same question?", "gamma")
        right["options"] = [{"slot": 1, "label": "beta"}]
        self.assertFalse(similarity.answers_agree(left, right))

    def test_pairs_rank_worst_first(self):
        commit = {"commit": "abc"}
        drafts = [
            self._draft(
                "20260715T143205Z-a", "How is X decided?", "same", preference_set=commit
            ),
            self._draft(
                "20260715T143206Z-b",
                "How is X decided again?",
                "same",
                preference_set=commit,
            ),
            self._draft(
                "20260715T143207Z-c",
                "How is X decided?",
                "other",
                preference_set={"commit": "zzz"},
            ),
        ]
        pairs = similarity.find_pairs(drafts, [])
        self.assertTrue(pairs)
        verdicts = [pair["verdict"] for pair in pairs]
        self.assertEqual(verdicts[0], similarity.DUPLICATE)

    def test_diffs_name_the_fields_a_human_must_adjudicate(self):
        left = self._draft("20260715T143205Z-a", "q", "one")
        right = self._draft("20260715T143206Z-b", "q", "two")
        diffs = similarity.field_diffs(left, right)
        self.assertEqual(diffs["chosen"], ["one", "two"])
        self.assertNotIn("question", diffs)

    def test_false_cold_flags_a_matching_active_rule(self):
        preferences = (
            "# Active Preference Set\n\n"
            "- Rejects new infrastructure dependencies unless they remove an "
            "entire class of maintenance. [confirmed: 3, independent: 0, last: 2026-07-15]\n"
        )
        record = self._draft(
            "20260715T143205Z-a",
            "Do we add a new infrastructure dependency to remove a maintenance class?",
            "no",
            prediction_stream="cold",
        )
        matches = similarity.false_cold_candidates(record, preferences)
        self.assertTrue(matches)
        self.assertIn("infrastructure", matches[0]["shared_terms"])

    def test_false_cold_survives_a_long_record(self):
        """The measure must not be size-biased.

        Rules run ~8 tokens and records 20-40, so dividing by the union
        capped the score below any useful threshold — a 7-token rule
        against a 42-token record maxed out at 0.167 and could not fire
        on a rule quoted verbatim.
        """
        preferences = (
            "- Rejects new infrastructure dependencies unless they remove an "
            "entire class of maintenance. [confirmed: 3, independent: 0, last: 2026-07-15]\n"
        )
        record = self._draft(
            "20260715T143205Z-a",
            "Do we add a new infrastructure dependency, and does it remove an "
            "entire class of maintenance, given the registry, the release "
            "machinery, the pinning story, the mirror, the audit trail, the "
            "rotation schedule and the vendoring alternative?",
            "no, vendor it instead of taking the dependency",
            prediction_stream="cold",
        )
        self.assertGreater(len(similarity.record_tokens(record)), 20)
        self.assertTrue(similarity.false_cold_candidates(record, preferences))

    def test_a_preference_driven_record_is_never_false_cold(self):
        record = self._draft("20260715T143205Z-a", "infrastructure dependency?", "no")
        record["prediction_stream"] = "preference-driven"
        self.assertEqual(
            similarity.false_cold_candidates(record, "- infrastructure"), []
        )

    def test_ref_tiers(self):
        self.assertEqual(
            similarity.ref_tier(
                {"artifact_ref": {"repo": "r", "path": "p", "commit": "c"}}
            ),
            similarity.REF_COMPLETE,
        )
        self.assertEqual(
            similarity.ref_tier({"artifact_ref": {"repo": "r", "path": "p"}}),
            similarity.REF_PARTIAL,
        )
        self.assertEqual(
            similarity.ref_tier({"artifact_ref": None}), similarity.REF_NULL
        )

    def test_report_is_read_only_and_complete(self):
        drafts = [self._draft("20260715T143205Z-a", "q", "x")]
        with tempfile.TemporaryDirectory() as tmp:
            report = similarity.build_report(drafts, [], tmp)
        for key in ("pairs", "verdict_counts", "false_cold", "artifact_ref_tiers"):
            self.assertIn(key, report)
        self.assertEqual(report["candidates"], 1)


# Token lengths measured on the corpus this gate was calibrated
# against: 77 real drafts plus 17 ingested records. Fixtures must reach
# the TOP of this range. Every size-bias defect found so far was
# invisible for exactly one reason — hand-written fixtures were shorter
# than reality, so a measure that degraded with length never got the
# chance to degrade.
REAL_TOKEN_LENGTHS = {"min": 6, "median": 22, "max": 54}


class MeasureInvariantTests(unittest.TestCase):
    """Properties every scoring measure must hold, at any input size.

    These are katas, in the sense docs/glossary/kata.md gives the word:
    promoted from real failures, and kept afterwards as protection.

    They are deliberately NOT more example fixtures. The defects they
    replicate were all invisible to hand-written examples, because the
    examples were written by whoever held the same misconception as the
    code. A property checked across the real size range catches what
    another example of the same shape cannot.
    """

    RULE = (
        "Rejects new infrastructure dependencies unless they remove an "
        "entire class of maintenance"
    )
    # Distinct filler tokens, so padding a record adds length without
    # accidentally adding overlap with the rule.
    FILLER = (
        "registry mirror rotation pinning audit vendoring machinery cadence "
        "quorum ledger beacon satchel lantern harbour thicket parapet "
        "cobbler tundra zephyr marmot cistern bellows drover kestrel "
        "pylon gantry furrow bramble sundial oxbow quarry trellis "
        "wicket flotsam gable ravine spindle tarpaulin vellum wharf "
        "alcove burlap cinder dovetail ember fathom girder hinterland "
        "inkwell jetsam kiln lintel"
    ).split()

    def _record_quoting_the_rule(self, filler_tokens: int) -> dict:
        """A cold record that quotes the rule verbatim, padded to length."""
        padding = " ".join(self.FILLER[:filler_tokens])
        record = make_record("20260715T143205Z-quote", 1, stream="cold")
        record["question"] = f"{self.RULE}? {padding}"
        record["options"] = [{"slot": 1, "label": "no"}]
        return record

    def test_a_verbatim_rule_is_flagged_at_every_real_record_length(self):
        """KATA: the false-cold measure must not be size-biased.

        Replicates the defect where `false_cold_candidates` used
        jaccard. Jaccard divides by the union, so a 7-token rule
        against a 42-token record maxed out at 0.167 against a 0.18
        threshold — the check could not fire on ANY input, including a
        record that quoted the rule word for word.

        Asserting one example would not have caught it. Asserting the
        property across the real length range does.
        """
        preferences = (
            f"- {self.RULE}. [confirmed: 3, independent: 0, last: 2026-07-15]\n"
        )
        for filler in (0, 10, 20, 30, 40):
            with self.subTest(filler=filler):
                record = self._record_quoting_the_rule(filler)
                matches = similarity.false_cold_candidates(record, preferences)
                self.assertTrue(
                    matches,
                    f"a verbatim rule went unflagged in a "
                    f"{len(similarity.record_tokens(record))}-token record",
                )

    def test_the_fixtures_reach_the_real_corpus_maximum(self):
        """KATA: fixtures shorter than reality hide size-dependent bugs.

        The guard on the guard. If the longest record these invariants
        can build is shorter than the longest real record, the
        invariant above is checking a range the store has already left
        behind, and would pass while production silently failed.
        """
        longest = self._record_quoting_the_rule(len(self.FILLER))
        self.assertGreaterEqual(
            len(similarity.record_tokens(longest)),
            REAL_TOKEN_LENGTHS["max"],
            "fixtures no longer span the real corpus — re-measure "
            "REAL_TOKEN_LENGTHS and lengthen FILLER",
        )

    def test_a_verbatim_rule_still_scores_full_coverage_when_padded(self):
        """Coverage is size-invariant; that is the whole reason for it.

        Pinning the property directly, not just its consequence at one
        threshold: a threshold change must not be able to silently
        satisfy the test above while the measure regresses.
        """
        preferences = (
            f"- {self.RULE}. [confirmed: 3, independent: 0, last: 2026-07-15]\n"
        )
        scores = set()
        for filler in (0, 20, 40):
            record = self._record_quoting_the_rule(filler)
            matches = similarity.false_cold_candidates(record, preferences)
            scores.add(matches[0]["rule_coverage"])
        self.assertEqual(
            len(scores), 1, f"coverage moved with record length: {sorted(scores)}"
        )

    def test_every_reported_entity_has_a_name(self):
        """KATA: drafts are keyed by `slug` and have no `id` yet.

        Replicates the defect where the gate read `id` only and printed
        `None` for every draft — on data it was built specifically to
        check, before ingestion mints any id.
        """
        drafts = [
            {
                "slug": "20260715T143205Z-a",
                "question": "How do agents reach the store?",
                "options": [{"slot": 1, "label": "through the recorder"}],
                "chosen_slot": 1,
                "chosen": "through the recorder",
                "prediction_stream": "cold",
            },
            {
                "slug": "20260715T143206Z-b",
                "question": "How do agents reach the store?",
                "options": [{"slot": 1, "label": "through the recorder"}],
                "chosen_slot": 1,
                "chosen": "through the recorder",
                "prediction_stream": "cold",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = similarity.build_report(drafts, [], tmp)
        self.assertTrue(report["pairs"], "the two identical drafts should pair")
        for pair in report["pairs"]:
            self.assertIsNotNone(pair["left"])
            self.assertIsNotNone(pair["right"])
        for entry in report["artifact_ref_tiers"]:
            self.assertIsNotNone(entry["id"])


class CalibrationTests(unittest.TestCase):
    """Thresholds are claims about a corpus, so they expire."""

    def test_thresholds_are_store_tunable(self):
        """The whole point of moving them out of the vendored module.

        A store that cannot act on a recalibration without editing
        vendored code has to choose between a stale threshold and a
        merge conflict on every `copier update`.
        """
        for key in store_config.CALIBRATED:
            self.assertIn(key, store_config.DEFAULTS)

    def test_a_config_override_actually_reaches_the_measure(self):
        left = {
            "slug": "a",
            "question": "How do agents reach the store?",
            "options": [{"slot": 1, "label": "recorder"}],
        }
        right = {
            "slug": "b",
            "question": "How do humans reach the store?",
            "options": [{"slot": 1, "label": "browser"}],
        }
        # Both channels, because the gate surfaces on similarity OR
        # containment: raising one alone leaves the other free to
        # surface the pair, which would make this test pass for a
        # reason that has nothing to do with the override working.
        loose = similarity.tuning(
            {"similarity_threshold": 0.01, "containment_threshold": 0.01}
        )
        strict = similarity.tuning(
            {"similarity_threshold": 0.99, "containment_threshold": 0.99}
        )
        self.assertTrue(similarity.find_pairs([left, right], [], None, loose))
        self.assertFalse(similarity.find_pairs([left, right], [], None, strict))

    def test_an_unmeasured_threshold_says_so(self):
        """Never-measured is not a mild version of outgrown."""
        stale = store_config.stale_calibrations(dict(store_config.DEFAULTS), 5)
        self.assertEqual(len(stale), len(store_config.CALIBRATED))
        self.assertTrue(all(e["reason"] == "never measured" for e in stale))

    def test_a_fresh_stamp_is_not_stale(self):
        config = dict(store_config.DEFAULTS)
        config["calibration"] = {
            name: {"corpus_size": 100, "separation": 0.2, "measured": "2026-07-27"}
            for name in store_config.CALIBRATED
        }
        self.assertEqual(store_config.stale_calibrations(config, 120), [])

    def test_a_stamp_the_corpus_outgrew_is_stale(self):
        config = dict(store_config.DEFAULTS)
        config["calibration"] = {
            name: {"corpus_size": 50, "measured": "2026-07-27"}
            for name in store_config.CALIBRATED
        }
        stale = store_config.stale_calibrations(config, 100)
        self.assertEqual(len(stale), len(store_config.CALIBRATED))
        self.assertIn("50 -> 100", stale[0]["reason"])

    def test_the_growth_factor_is_configurable(self):
        config = dict(store_config.DEFAULTS)
        config["calibration_growth_factor"] = 10.0
        config["calibration"] = {
            name: {"corpus_size": 50} for name in store_config.CALIBRATED
        }
        self.assertEqual(store_config.stale_calibrations(config, 100), [])

    def test_a_threshold_outside_zero_to_one_is_rejected(self):
        config = dict(store_config.DEFAULTS)
        config["similarity_threshold"] = 1.4
        errors = store_config.validate_config(config)
        self.assertTrue(any("similarity_threshold" in e for e in errors))

    def test_a_boolean_is_not_a_threshold(self):
        config = dict(store_config.DEFAULTS)
        config["artifact_boost"] = True
        errors = store_config.validate_config(config)
        self.assertTrue(any("artifact_boost" in e for e in errors))

    def test_an_unreadable_stamp_is_an_error_not_a_shrug(self):
        """A stamp that proves nothing while looking like evidence is
        worse than no stamp at all."""
        config = dict(store_config.DEFAULTS)
        config["calibration"] = {"similarity_threshold": {"corpus_size": "lots"}}
        errors = store_config.validate_config(config)
        self.assertTrue(any("corpus_size" in e for e in errors))

    def test_a_stamp_for_an_unknown_constant_is_rejected(self):
        config = dict(store_config.DEFAULTS)
        config["calibration"] = {"budget_tokens": {"corpus_size": 10}}
        errors = store_config.validate_config(config)
        self.assertTrue(any("not a calibrated constant" in e for e in errors))

    def test_the_documented_out_flag_exists(self):
        """The skills document `--out`; a flag only in prose is a broken
        command in a file that reads like instructions."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "gate.json")
            self.assertEqual(similarity.main(["--root", tmp, "--out", out]), 0)
            with open(out, encoding="utf-8") as handle:
                self.assertIn("pairs", json.load(handle))

    def test_the_gate_report_surfaces_staleness(self):
        drafts = [
            {
                "slug": "20260715T143205Z-a",
                "question": "q?",
                "options": [{"slot": 1, "label": "x"}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = similarity.build_report(drafts, [], tmp)
        self.assertTrue(report["stale_calibrations"])
        self.assertIn("recalibrate-thresholds", similarity.render(report))


class CommitTypeTests(unittest.TestCase):
    """The commit lint is the grammar half of docs/conventions.md."""

    def test_the_repos_types_are_accepted(self):
        subjects = [
            "decision(factory): agent-access — collaborators over deploy keys",
            "pref-proposal: prefers the simplest solution",
            "pref-promote: rejects new infrastructure dependencies",
            "pref-confirm: rejects new infrastructure dependencies (n=4)",
            "pref-compact: compact active set — 7 rules -> 4",
            "pref-drift: infrastructure rule mispredicts solution shape",
            "chore: extraction marker -> 20260715T143205Z-a",
        ]
        for subject in subjects:
            self.assertIsNone(guards.check_commit_subject(subject), subject)

    def test_an_unknown_type_is_rejected(self):
        self.assertIsNotNone(guards.check_commit_subject("feat: add a thing"))

    def test_compaction_may_remove_preference_lines(self):
        self.assertIn("pref-compact:", guards.PREF_EDIT_TYPES)

    def test_drift_may_not_remove_preference_lines(self):
        """Drift proposes; it never rewrites the active set."""
        self.assertNotIn("pref-drift:", guards.PREF_EDIT_TYPES)


class FixtureStoreTests(unittest.TestCase):
    """A throwaway store, so the guard is exercised without a real one.

    The real-corpus checks skip in the template that vendors these
    files — no `decisions/` exists there. This builds one, which is
    what makes a template-side edit unable to ship a broken guard.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "decisions"))
        self.records = [
            make_record(f"2026071{i}T14320{i}Z-fixture-case", 1) for i in range(3)
        ]
        for record in self.records:
            self._write_record(record)
        self._write_preferences(make_preferences(make_rule()))
        self._write("store.config.json", json.dumps({"budget_tokens": 2000}))

    def _write_record(self, record):
        path = os.path.join(self.root, "decisions", f"{record['id']}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle)

    def _write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def _write_preferences(self, data):
        self._write("preferences.json", decision_validator.serialize_preferences(data))
        self._write("preferences.txt", decision_validator.render_preferences(data))

    def test_the_fixture_corpus_is_clean(self):
        self.assertEqual(guards.check_corpus(self.root), [])

    def test_the_budget_comes_from_the_repo_local_config(self):
        """Lower the store's budget and the vendored guard must fail."""
        self._write("store.config.json", json.dumps({"budget_tokens": 1}))
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("exceeds the 1 budget" in e for e in errors))

    def test_a_budget_above_the_vendored_default_is_enforced_as_given(self):
        """The render lands over the vendored 2000-token default but under
        the store's own 4000 — the store's number wins."""
        rules = [
            make_rule(text=f"rule number {index} " + "x" * 40 + ".")
            for index in range(200)
        ]
        self._write_preferences(make_preferences(*rules))
        self._write("store.config.json", json.dumps({"budget_tokens": 4000}))
        self.assertEqual(guards.check_corpus(self.root), [])

    def test_mirror_drift_fails_the_corpus_check(self):
        self._write("preferences.txt", "confirmed\tindependent\trule\n")
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("not the render" in e for e in errors))

    def test_a_missing_render_fails_the_corpus_check(self):
        os.remove(os.path.join(self.root, "preferences.txt"))
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("preferences.txt: missing" in e for e in errors))

    def test_a_pre_migration_store_fails_with_the_instruction(self):
        os.remove(os.path.join(self.root, "preferences.json"))
        os.remove(os.path.join(self.root, "preferences.txt"))
        self._write("preferences.md", "- old rule. [confirmed: 1]\n")
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("migrate" in e for e in errors))

    def test_a_leftover_legacy_file_fails(self):
        self._write("preferences.md", "- old rule. [confirmed: 1]\n")
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("legacy" in e for e in errors))

    def test_preferences_at_reads_the_pinned_format(self):
        """A record's pinned commit may predate the format split; the
        pinned file is served whichever shape it has."""
        for args in (
            ("init", "-q"),
            ("config", "user.email", "t@e.st"),
            ("config", "user.name", "test"),
        ):
            subprocess.run(["git", "-C", self.root, *args], check=True)
        legacy = "- old rule. [confirmed: 1, independent: 0, last: 2026-07-15]\n"
        self._write("preferences.md", legacy)
        os.remove(os.path.join(self.root, "preferences.json"))
        os.remove(os.path.join(self.root, "preferences.txt"))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "chore: legacy"], check=True
        )
        old_sha = self._head_sha()
        os.remove(os.path.join(self.root, "preferences.md"))
        self._write_preferences(make_preferences(make_rule()))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "chore: migrated"], check=True
        )
        new_sha = self._head_sha()
        self.assertEqual(similarity.preferences_at(old_sha, self.root), legacy)
        self.assertIn("a short rule.", similarity.preferences_at(new_sha, self.root))

    def _head_sha(self):
        return subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_scope_comes_from_the_diff(self):
        """The batch boundary is git's answer, not a file's."""
        subprocess.run(["git", "-C", self.root, "init", "-q"], check=True)
        for args in (
            ("config", "user.email", "t@e.st"),
            ("config", "user.name", "test"),
            ("add", "-A"),
            ("commit", "-qm", "chore: base"),
        ):
            subprocess.run(["git", "-C", self.root, *args], check=True)
        base = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        added = make_record("20260799T143209Z-fixture-new", 1)
        self._write_record(added)
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "decision(f): new — a"],
            check=True,
        )

        self.assertEqual(extraction.added_since(base, self.root), {added["id"]})
        batch = extraction.build_batch(replay.load_records(self.root), {added["id"]})
        self.assertEqual(batch["count"], 1)
        self.assertEqual(batch["history_count"], 3)

    def test_replay_builds_cases_from_the_fixture_corpus(self):
        cases = replay.build_cases(replay.load_records(self.root), 20)
        self.assertEqual(cases["count"], 3)
        self.assertEqual(cases["slot_order"], "permuted")

    def test_a_broken_config_fails_the_corpus_check_loudly(self):
        self._write("store.config.json", "{nope")
        self.assertTrue(guards.check_corpus(self.root))


if __name__ == "__main__":
    unittest.main()
