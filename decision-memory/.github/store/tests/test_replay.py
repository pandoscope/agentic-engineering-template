"""The replay harness: masked cases, the blind baseline, slot permutation
and the verdict small-n must not read as validation.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`. Run the whole
layer from the repo root:

    python -m unittest discover --start-directory .github/store/tests
"""

from __future__ import annotations

import json
import os
import unittest

from store_support import (
    STORE_DIR,
    make_prediction,
    make_record,
    replay,
)


class ReplayTests(unittest.TestCase):
    def test_mask_strips_leaky_fields(self):
        """The if-clause singles out the prediction slot: alternatives
        carry one by contract, the prediction does not (AET#228)."""
        case = replay.mask_record(make_record("20260715T143205Z-a", 1))
        self.assertNotIn("chosen_slot", case)
        self.assertNotIn("outcome", case)
        for option in case["options"]:
            self.assertNotIn("role", option)
            self.assertNotIn("rules_cited", option)
            self.assertNotIn("reasoning", option)
            self.assertNotIn("if_clause", option)

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

    def test_gate_carries_the_candidate_blind_baselines(self):
        """The gate report is what lands in the PR body; the baselines
        must travel with it or the reader never sees them."""
        baseline = self._report(pd=(4, 5), cold=(1, 5), sha="base")
        candidate = self._report(pd=(4, 5), cold=(1, 5), sha="cand")
        candidate["blind_baselines"] = {
            "always_slot_1": {"n": 5, "hits": 4, "hit_rate": 0.8},
            "odd_option": {"n": 3, "hits": 3, "hit_rate": 1.0},
        }
        result = replay.gate(baseline, candidate)
        self.assertEqual(result["blind_baselines"], candidate["blind_baselines"])

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


class LeakCheckTests(unittest.TestCase):
    """`cases` reports what still identifies the recorded answer (AET#228).

    Records are immutable, so a leak is reported, never repaired: the
    reader of the report decides what a pass is worth.
    """

    def test_a_key_carried_by_all_but_one_option_is_an_odd_option_leak(self):
        options = [
            {"slot": 1, "label": "a", "role": "prediction"},
            {"slot": 2, "label": "b", "if_clause": "if x", "note": "n"},
            {"slot": 3, "label": "c", "if_clause": "if y", "note": "n"},
        ]
        record = make_record("20260715T143205Z-a", 1, options=options)
        payload = replay.build_cases([record], 20)
        self.assertEqual(
            payload["leaks"],
            [{"id": "20260715T143205Z-a", "channel": "odd-option", "key": "note"}],
        )

    def test_a_symmetric_key_set_is_no_leak(self):
        payload = replay.build_cases([make_record("20260715T143205Z-a", 1)], 20)
        self.assertEqual(payload["leaks"], [])

    def test_a_context_that_narrates_the_ruling_is_a_context_leak(self):
        """The context is input side, written before the ruling; a
        past-tense verdict in it hands the reader the answer."""
        record = make_record("20260715T143205Z-a", 1)
        record["context"] = "Two layouts were open. The principal reviewed: 'B.'"
        payload = replay.build_cases([record], 20)
        self.assertEqual(
            payload["leaks"],
            [
                {
                    "id": "20260715T143205Z-a",
                    "channel": "context",
                    "match": "The principal reviewed",
                }
            ],
        )

    def test_a_context_stating_the_situation_is_no_leak(self):
        record = make_record("20260715T143205Z-a", 1)
        record["context"] = "Two layouts are open; the reviewer prefers neither yet."
        self.assertEqual(replay.build_cases([record], 20)["leaks"], [])

    def test_a_missing_context_is_no_leak(self):
        record = make_record("20260715T143205Z-a", 1)
        record["context"] = None
        self.assertEqual(replay.build_cases([record], 20)["leaks"], [])


class BlindBaselineTests(unittest.TestCase):
    """Every score report carries what a reader of the case alone would
    score, so a rule-set hit rate is read against it (AET#228)."""

    @staticmethod
    def _odd(record_id, chosen_slot):
        options = [
            {"slot": 1, "label": "a", "role": "prediction"},
            {"slot": 2, "label": "b", "if_clause": "if x"},
            {"slot": 3, "label": "c", "if_clause": "if y"},
        ]
        return make_record(record_id, chosen_slot, options=options)

    @staticmethod
    def _even(record_id, chosen_slot):
        options = [
            {"slot": 1, "label": "a", "role": "prediction", "if_clause": "if w"},
            {"slot": 2, "label": "b", "if_clause": "if x"},
        ]
        return make_record(record_id, chosen_slot, options=options)

    def test_score_reports_always_slot_1_and_odd_option_baselines(self):
        records = [
            self._odd("20260715T143205Z-a", 1),
            self._odd("20260716T143205Z-b", 2),
            self._even("20260717T143205Z-c", 2),
        ]
        predictions, _ = replay.normalise_predictions(
            [make_prediction(record["id"], 1) for record in records]
        )
        report, _ = replay.score(records, predictions, 20, "prefs")
        self.assertEqual(
            report["blind_baselines"],
            {
                "always_slot_1": {"n": 3, "hits": 1, "hit_rate": 0.3333},
                "odd_option": {"n": 2, "hits": 1, "hit_rate": 0.5},
            },
        )


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


if __name__ == "__main__":
    unittest.main()
