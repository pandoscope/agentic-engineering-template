"""The store's config defaults and the token budget computed from them.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`. Run the whole
layer from the repo root:

    python -m unittest discover --start-directory .github/store/tests
"""

from __future__ import annotations

import os
import tempfile
import unittest

from store_support import (
    STORE_DIR,
    store_budget,
    store_config,
)


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


if __name__ == "__main__":
    unittest.main()
