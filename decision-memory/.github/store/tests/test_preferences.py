"""The preferences.json / preferences.txt pair: its schema, its render,
the counter math, and the classifier both commit guards read.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`. Run the whole
layer from the repo root:

    python -m unittest discover --start-directory .github/store/tests
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from store_support import (
    decision_validator,
    guards,
    make_preferences,
    make_rule,
    render_preferences,
    similarity,
    source_text,
)


class PreferenceDocTests(unittest.TestCase):
    """The doc field: required-but-nullable promotion-doc provenance.

    Every rule entry carries `doc` — a null is the explicit "no
    promotion doc exists" declaration, a missing key is a defect, and
    absence therefore stays observable. The render never emits it: the
    injected surface stays a plain rule list.
    """

    def test_doc_null_is_a_valid_declared_absence(self):
        data = make_preferences(make_rule(doc=None))
        self.assertEqual(guards.decision_validator.validate_preferences(data), [])

    def test_doc_url_is_valid(self):
        data = make_preferences(make_rule(doc="https://example.com/proposals/x.md"))
        self.assertEqual(guards.decision_validator.validate_preferences(data), [])

    def test_a_rule_without_doc_is_rejected(self):
        rule = make_rule()
        del rule["doc"]
        errors = guards.decision_validator.validate_preferences(make_preferences(rule))
        self.assertTrue(errors and any("keys must be exactly" in e for e in errors))

    def test_doc_must_be_null_or_http_url(self):
        for bad in ("proposals/x.md", "", True, 7):
            with self.subTest(doc=bad):
                errors = guards.decision_validator.validate_preferences(
                    make_preferences(make_rule(doc=bad))
                )
                self.assertTrue(any("doc" in e for e in errors))

    def test_render_never_emits_the_doc(self):
        data = make_preferences(make_rule(doc="https://example.com/proposals/x.md"))
        rendered = guards.decision_validator.render_preferences(data)
        self.assertNotIn("example.com", rendered)
        self.assertIn(make_rule()["rule"], rendered)


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

    def test_render_is_the_acked_shape(self):
        """A plain ordered list — the order is the priority order."""
        data = make_preferences(
            make_rule(
                text="Rejects a new dependency unless it removes a whole class of maintenance.",
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
            "Rejects a new dependency unless it removes a whole class of maintenance.\n"
            "Prefers machine checks over model checks wherever feasible.\n"
        )
        self.assertEqual(decision_validator.render_preferences(data), expected)

    def test_an_empty_set_renders_an_empty_file(self):
        self.assertEqual(decision_validator.render_preferences({"rules": []}), "")

    def test_the_bookkeeping_stays_out_of_the_render(self):
        """Counters and dates matter at update time, never in-session —
        numbers in the render would invite the reader to discount young
        rules, and a promoted rule is equally binding at zero."""
        rendered = decision_validator.render_preferences(
            make_preferences(make_rule(confirmed=7, independent=2))
        )
        self.assertEqual(rendered, "a short rule.\n")

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
            None, source_text(make_rule()), "chore: seed"
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

    @staticmethod
    def _pre_doc(**kwargs):
        rule = make_rule(**kwargs)
        del rule["doc"]
        return rule

    def test_doc_backfill_migration_is_accepted(self):
        # A store crossing the release that made `doc` required: every
        # rule gains doc: null by hand, nothing else moves. The old side
        # cannot parse under this schema, yet the change is accepted.
        old = json.dumps(
            {"rules": [self._pre_doc(text="a."), self._pre_doc(text="b.")]}
        )
        new = source_text(
            make_rule(text="a.", doc=None), make_rule(text="b.", doc=None)
        )
        kind, errors = guards.classify_preferences_change(
            old, new, "chore: update agentic template"
        )
        self.assertEqual((kind, errors), ("migration", []))

    def test_doc_backfill_of_a_partially_migrated_set(self):
        # One rule already carries doc; the other is backfilled. Still a
        # migration as long as the already-migrated rule is untouched.
        old = json.dumps(
            {"rules": [make_rule(text="a.", doc=None), self._pre_doc(text="b.")]}
        )
        new = source_text(
            make_rule(text="a.", doc=None), make_rule(text="b.", doc=None)
        )
        kind, errors = guards.classify_preferences_change(old, new, "chore: finish it")
        self.assertEqual((kind, errors), ("migration", []))

    def test_doc_backfill_rejects_a_smuggled_counter_change(self):
        # Adding doc AND bumping a counter is not a pure backfill, and the
        # old side does not parse — so a rewrite hiding behind the schema
        # bump stays a hard failure rather than sliding through.
        old = json.dumps({"rules": [self._pre_doc(confirmed=3)]})
        new = source_text(make_rule(confirmed=4, doc=None))
        kind, _ = guards.classify_preferences_change(old, new, "chore: sneaky")
        self.assertEqual(kind, "invalid")

    def test_doc_backfill_rejects_inventing_provenance(self):
        # A backfill declares absence (null); minting a doc URL for a
        # legacy rule is a claim, not a migration.
        old = json.dumps({"rules": [self._pre_doc()]})
        new = source_text(make_rule(doc="https://example.com/x.md"))
        kind, _ = guards.classify_preferences_change(old, new, "chore: invent")
        self.assertEqual(kind, "invalid")

    def test_doc_backfill_rejects_an_added_rule(self):
        old = json.dumps({"rules": [self._pre_doc(text="a.")]})
        new = source_text(
            make_rule(text="a.", doc=None), make_rule(text="b.", doc=None)
        )
        kind, _ = guards.classify_preferences_change(old, new, "chore: plus one")
        self.assertEqual(kind, "invalid")


class RenderCliTests(unittest.TestCase):
    def test_render_writes_the_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_preferences(make_rule())
            self._write(
                tmp,
                "preferences.json",
                decision_validator.serialize_preferences(data),
            )
            self.assertEqual(render_preferences.main(["render", "--root", tmp]), 0)
            with open(os.path.join(tmp, "preferences.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), decision_validator.render_preferences(data))
            self.assertEqual(guards.check_corpus(tmp), [])

    def test_check_detects_drift_and_render_repairs_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_preferences(make_rule())
            self._write(
                tmp,
                "preferences.json",
                decision_validator.serialize_preferences(data),
            )
            self._write(tmp, "preferences.txt", "a different rule.\n")
            self.assertNotEqual(render_preferences.main(["check", "--root", tmp]), 0)
            self.assertEqual(render_preferences.main(["render", "--root", tmp]), 0)
            self.assertEqual(render_preferences.main(["check", "--root", tmp]), 0)

    def test_an_invalid_source_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "preferences.json", "{nope")
            self.assertNotEqual(render_preferences.main(["render", "--root", tmp]), 0)
            self.assertNotEqual(render_preferences.main(["check", "--root", tmp]), 0)

    @staticmethod
    def _write(root, name, text):
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(text)


class RuleLinesTests(unittest.TestCase):
    def test_the_rendered_format_parses(self):
        text = "Prefers machine checks.\nTakes the simplest shape.\n"
        self.assertEqual(
            similarity.rule_lines(text),
            ["Prefers machine checks.", "Takes the simplest shape."],
        )

    def test_an_empty_set_yields_no_rules(self):
        self.assertEqual(similarity.rule_lines(""), [])


if __name__ == "__main__":
    unittest.main()
