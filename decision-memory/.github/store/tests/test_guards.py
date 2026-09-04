"""The guards that stand between a session and the corpus: the carve-out
label, the budget gate, the commit grammar, and a throwaway store the
real-corpus checks can run against.

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
    decision_validator,
    extraction,
    guard,
    guards,
    make_preferences,
    make_record,
    make_rule,
    replay,
    similarity,
    source_text,
    store_config,
)


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

    def test_doc_backfill_migration_needs_no_label(self):
        # The one-time doc-field backfill is meaning-preserving: the
        # commit guard accepts it as a migration, so the carve-out must
        # not disagree by demanding a label or a replay report.
        pre_doc = make_rule()
        del pre_doc["doc"]
        required, notes = guard.classify_pref_commits(
            [
                self.commit(
                    "chore: update agentic template",
                    old=json.dumps({"rules": [pre_doc]}),
                    new=source_text(make_rule(doc=None)),
                )
            ]
        )
        self.assertFalse(required)
        self.assertTrue(any("migration" in note for note in notes))

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
        self._write("preferences.txt", "a different rule.\n")
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("not the render" in e for e in errors))

    def test_a_missing_render_fails_the_corpus_check(self):
        os.remove(os.path.join(self.root, "preferences.txt"))
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("preferences.txt: missing" in e for e in errors))

    def test_a_missing_preference_set_fails(self):
        """Absence is observable: a store whose active set vanished says
        so, rather than passing because there was nothing to check."""
        os.remove(os.path.join(self.root, "preferences.json"))
        os.remove(os.path.join(self.root, "preferences.txt"))
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("preferences.json: missing" in e for e in errors), errors)

    def test_a_stale_render_without_its_source_fails(self):
        """The render is not the authority — one left behind after the
        source is gone would prime sessions from a file nothing checks."""
        os.remove(os.path.join(self.root, "preferences.json"))
        self._write("preferences.txt", "A rule nothing can verify any more.\n")
        errors = guards.check_corpus(self.root)
        self.assertTrue(any("preferences.json: missing" in e for e in errors), errors)

    def test_preferences_at_serves_the_pinned_set(self):
        """A record pins the set it was primed from; a commit that has
        no set yields None, so the caller falls back to the current one
        and says that it did."""
        for args in (
            ("init", "-q"),
            ("config", "user.email", "t@e.st"),
            ("config", "user.name", "test"),
        ):
            subprocess.run(["git", "-C", self.root, *args], check=True)
        os.remove(os.path.join(self.root, "preferences.json"))
        os.remove(os.path.join(self.root, "preferences.txt"))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "chore: no set yet"], check=True
        )
        unset_sha = self._head_sha()
        self._write_preferences(make_preferences(make_rule()))
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "chore: set"], check=True
        )
        set_sha = self._head_sha()
        self.assertIsNone(similarity.preferences_at(unset_sha, self.root))
        self.assertIn("a short rule.", similarity.preferences_at(set_sha, self.root))

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
