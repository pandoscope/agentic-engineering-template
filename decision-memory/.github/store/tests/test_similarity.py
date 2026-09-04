"""The similarity gate, the properties every scoring measure holds at any
input size, and the thresholds that expire with the corpus.

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
    make_record,
    similarity,
    store_config,
)


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
            "Rejects new infrastructure dependencies unless they remove an "
            "entire class of maintenance.\n"
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
            "Rejects new infrastructure dependencies unless they remove an "
            "entire class of maintenance.\n"
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
        preferences = f"{self.RULE}.\n"
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
        preferences = f"{self.RULE}.\n"
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


if __name__ == "__main__":
    unittest.main()
