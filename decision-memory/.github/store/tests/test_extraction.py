"""Extraction: the rule-vs-rule contests counted from the corpus, the
watermark derived from a commit's position, and the gate on a missed pass.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`. Run the whole
layer from the repo root:

    python -m unittest discover --start-directory .github/store/tests
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from store_support import (
    contest_record,
    extraction,
    guard,
    make_record,
    make_rule,
    store_config,
)


class ConflictTallyTests(unittest.TestCase):
    """Rule-vs-rule contests, counted from the corpus, never stored."""

    RULES = [
        make_rule(text="Earlier rule wins by default."),
        make_rule(text="Later rule, lowest priority."),
    ]

    def test_a_decided_contest_is_counted(self):
        record = contest_record(
            "20260714T000000Z-a",
            1,
            {1: ["Earlier rule wins by default."], 2: ["Later rule, lowest priority."]},
        )
        self.assertEqual(
            extraction.record_contests(record),
            [("Earlier rule wins by default.", "Later rule, lowest priority.")],
        )

    def test_no_chosen_slot_means_no_contest(self):
        record = contest_record("20260714T000000Z-a", 1, {1: ["A."], 2: ["B."]})
        record["chosen_slot"] = None
        self.assertEqual(extraction.record_contests(record), [])

    def test_the_same_rule_on_both_sides_is_no_contest(self):
        record = contest_record("20260714T000000Z-a", 1, {1: ["A."], 2: ["a."]})
        self.assertEqual(extraction.record_contests(record), [])

    def test_tally_flags_later_beats_earlier(self):
        records = [
            contest_record(
                f"2026071{i}T00000{i}Z-x",
                2,
                {
                    1: ["Earlier rule wins by default."],
                    2: ["Later rule, lowest priority."],
                },
            )
            for i in range(2)
        ]
        tally = extraction.conflict_tally(records, self.RULES)
        self.assertEqual(len(tally), 1)
        entry = tally[0]
        self.assertEqual(entry["earlier"], "Earlier rule wins by default.")
        self.assertEqual(entry["later_wins"], 2)
        self.assertEqual(entry["earlier_wins"], 0)
        self.assertTrue(entry["order_violation"])

    def test_a_winning_earlier_rule_is_not_a_violation(self):
        records = [
            contest_record(
                "20260714T000000Z-x",
                1,
                {
                    1: ["Earlier rule wins by default."],
                    2: ["Later rule, lowest priority."],
                },
            )
        ]
        tally = extraction.conflict_tally(records, self.RULES)
        self.assertFalse(tally[0]["order_violation"])

    def test_a_conflict_free_corpus_reports_nothing(self):
        records = [contest_record("20260714T000000Z-x", 1, {1: ["A."], 2: []})]
        self.assertEqual(extraction.conflict_tally(records, self.RULES), [])

    def test_an_unresolved_citation_is_skipped(self):
        records = [
            contest_record(
                "20260714T000000Z-x",
                1,
                {1: ["A rule nobody promoted."], 2: ["Later rule, lowest priority."]},
            )
        ]
        self.assertEqual(extraction.conflict_tally(records, self.RULES), [])

    def test_the_batch_carries_conflicts_and_tally(self):
        record = contest_record(
            "20260714T000000Z-x",
            2,
            {1: ["Earlier rule wins by default."], 2: ["Later rule, lowest priority."]},
        )
        batch = extraction.build_batch(
            [record], {record["id"]}, active_rules=self.RULES
        )
        self.assertEqual(
            batch["conflicts"],
            [
                {
                    "record": record["id"],
                    "winner": "Later rule, lowest priority.",
                    "loser": "Earlier rule wins by default.",
                }
            ],
        )
        self.assertTrue(batch["conflict_tally"][0]["order_violation"])

    def test_the_batch_without_a_rule_set_still_reports_raw_conflicts(self):
        record = contest_record("20260714T000000Z-x", 1, {1: ["A."], 2: ["B."]})
        batch = extraction.build_batch([record], {record["id"]})
        self.assertEqual(len(batch["conflicts"]), 1)
        self.assertEqual(batch["conflict_tally"], [])


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


if __name__ == "__main__":
    unittest.main()
