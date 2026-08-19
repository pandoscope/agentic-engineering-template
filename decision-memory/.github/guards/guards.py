"""CI guard for the decision-memory repo.

Copier-vendored from the agentic-engineering-template decision-memory subtemplate
(single shared source with the writer tool's validation — both import
decision_validator.py, which lives next to this file). Stdlib only:
the guard must keep working even if the template repo disappears.

Checks, per PR (run with --base <base-sha> from a full checkout):

1. Append-only: no modify/delete/rename under decisions/** or
   predictions/**; rewrites of existing preferences.json rules only
   from pref-confirm/pref-promote/pref-compact commits, with
   pref-confirm counter math validated structurally.
2. Full-corpus schema check: EVERY decisions/*.json and
   predictions/*.json validates (not just added files), so guard
   updates re-validate the entire corpus.
3. Dangling-reference check across the corpus.
4. The preference-set pair: preferences.json validates against its
   schema, preferences.txt equals its render (per commit and at head),
   and the render fits the repo-local token budget.
5. Commit lint: every PR commit subject uses one of the repo's own
   types (decision/prediction/pref-proposal/pref-promote/
   pref-confirm/pref-compact/pref-drift/pref-extract/chore).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store"),
)

import config as store_config  # noqa: E402  (path bootstrap above)
import decision_validator  # noqa: E402  (path bootstrap above)

# Match-side of the repo's own commit types. Grammar authority:
# docs/conventions.md (§ Commit types, vendored with this file); the
# writer composes these subjects in record.py.
COMMIT_SUBJECT_RES = (
    re.compile(r"^decision\([a-z0-9][a-z0-9-]*\): .+ — .+$"),
    re.compile(r"^prediction\([a-z0-9][a-z0-9-]*\): .+ — .+$"),
    re.compile(r"^pref-proposal: .+$"),
    re.compile(r"^pref-promote: .+$"),
    re.compile(r"^pref-confirm: .+ \(n=\d+\)$"),
    re.compile(r"^pref-compact: .+$"),
    re.compile(r"^pref-drift: .+$"),
    re.compile(r"^pref-extract: .+$"),
    re.compile(r"^chore(\([\w-]+\))?: .+$"),
)

COUNTER_KEY = decision_validator.COUNTER_KEY
INDEPENDENT_KEY = decision_validator.INDEPENDENT_KEY
PREFERENCES_SOURCE = decision_validator.PREFERENCES_SOURCE
PREFERENCES_RENDERED = decision_validator.PREFERENCES_RENDERED

# The types permitted to REWRITE existing rules in the active set.
# Promotion and compaction are different acts on the same file:
# promotion adds a rule a human decided to adopt (and may demote another
# to make room); compaction rewrites the set without adding anything
# that was not already promoted. Typing them apart is what lets a reader
# tell one from the other in the log — the human gate on both is the
# merge, not the commit subject.
PREF_EDIT_TYPES = ("pref-confirm:", "pref-promote:", "pref-compact:")


def check_commit_subject(subject: str) -> str | None:
    """Return an error string if the subject matches none of the repo's
    commit types, else None."""
    if any(pattern.match(subject) for pattern in COMMIT_SUBJECT_RES):
        return None
    return (
        f"commit subject {subject!r} matches none of the repo's types: "
        "decision(<project>): <slug> — <chosen> | "
        "prediction(<project>): <slug> — <chosen> | pref-proposal: | "
        "pref-promote: | pref-confirm: ... (n=N) | pref-compact: | "
        "pref-drift: | pref-extract: | chore:"
    )


def validate_pref_confirm_change(old_data: dict, new_data: dict) -> list[str]:
    """Validate the counter math of a pref-confirm commit, structurally:
    same rules in the same order, and every changed rule is exactly a
    bump — counter +1, `independent` held or +1, text untouched.

    `independent` is a second count, earned differently: it rises only
    when a confirmation was NOT the rule crediting itself. It never
    falls here, since lowering it under a mechanical subject would erase
    evidence as routine bookkeeping.
    """
    old_rules = old_data.get("rules", [])
    new_rules = new_data.get("rules", [])
    if len(old_rules) != len(new_rules):
        return [
            "pref-confirm: must only update counters "
            f"(rule count {len(old_rules)} -> {len(new_rules)})"
        ]
    errors: list[str] = []
    changed = 0
    for old, new in zip(old_rules, new_rules):
        if old == new:
            continue
        changed += 1
        if old["rule"] != new["rule"]:
            errors.append(
                f"pref-confirm: rule text changed: {old['rule']!r} -> {new['rule']!r}"
            )
            continue
        if new[COUNTER_KEY] != old[COUNTER_KEY] + 1:
            errors.append(
                "pref-confirm: counter must increment by exactly 1: "
                f"{old[COUNTER_KEY]} -> {new[COUNTER_KEY]} ({old['rule']!r})"
            )
        if new[INDEPENDENT_KEY] not in (old[INDEPENDENT_KEY], old[INDEPENDENT_KEY] + 1):
            errors.append(
                f"pref-confirm: {INDEPENDENT_KEY} moved "
                f"{old[INDEPENDENT_KEY]} -> {new[INDEPENDENT_KEY]}; a bump may "
                "hold it or raise it by exactly 1"
            )
    if not changed:
        errors.append("pref-confirm: changes no counter")
    return errors


def rules_purely_added(old_rules: list[dict], new_rules: list[dict]) -> bool:
    """True when every old rule survives unchanged, in order — the
    change only inserts or appends new rules."""
    candidates = iter(new_rules)
    return all(
        any(candidate == wanted for candidate in candidates) for wanted in old_rules
    )


def _parse_side(text: str | None, side: str) -> tuple[dict, list[str]]:
    """One side of a preferences.json change; an absent file is an
    empty set, so file creation classifies as pure addition."""
    if text is None:
        return {"rules": []}, []
    data, errors = decision_validator.parse_preferences(text)
    if data is None or errors:
        return {"rules": []}, [
            f"{side} {PREFERENCES_SOURCE}: {error}" for error in errors
        ]
    return data, []


# The keys a rule carried before `doc` became required, in order. A file
# from a store that predates the field parses under no current schema, so
# the classifier below recognizes exactly its backfill rather than reading
# every unparsable old side as tampering.
_PRE_DOC_RULE_KEYS = tuple(k for k in decision_validator.RULE_KEYS if k != "doc")


def is_doc_backfill(old_text: str | None, new_text: str | None) -> bool:
    """True when the change is exactly the one-time `doc`-field backfill.

    A store crossing the release that made `doc` required converts its
    legacy set by hand: every rule gains ``doc`` and nothing else moves.
    The old side cannot parse under the current schema (that is the whole
    reason it needs converting), so this reads raw JSON and accepts the
    change only when it is precisely that migration — each pre-`doc` rule
    reappears with ``doc`` appended as a null, its other keys, values and
    order untouched, no rule added, removed or reordered, and the file's
    non-rule content (the `_comment`) unchanged. Any rule already holding
    ``doc`` must be identical on both sides. Anything else is not a
    backfill, so a real rewrite hiding behind a schema bump stays caught.
    """
    try:
        old = json.loads(old_text or "")
        new = json.loads(new_text or "")
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    old_rules, new_rules = old.get("rules"), new.get("rules")
    if not isinstance(old_rules, list) or not isinstance(new_rules, list):
        return False
    if len(old_rules) != len(new_rules) or old_rules == new_rules:
        return False
    if {k: v for k, v in old.items() if k != "rules"} != {
        k: v for k, v in new.items() if k != "rules"
    }:
        return False
    for old_rule, new_rule in zip(old_rules, new_rules):
        if not isinstance(old_rule, dict) or not isinstance(new_rule, dict):
            return False
        if "doc" in old_rule:
            if old_rule != new_rule:
                return False
            continue
        if tuple(old_rule) != _PRE_DOC_RULE_KEYS:
            return False
        if tuple(new_rule) != decision_validator.RULE_KEYS:
            return False
        if new_rule["doc"] is not None:
            return False
        if {k: new_rule[k] for k in old_rule} != old_rule:
            return False
    return True


def classify_preferences_change(
    old_text: str | None, new_text: str | None, subject: str
) -> tuple[str, list[str]]:
    """Classify one commit's preferences.json change, structurally.

    Returns ``(kind, errors)`` with kind one of ``none`` (rules
    identical), ``addition`` (existing rules untouched), ``bump-exempt``
    (a pref-confirm commit whose math validates), ``migration`` (the
    one-time `doc`-field backfill — see ``is_doc_backfill``), ``rewrite``
    (anything else touching an existing rule), or ``invalid`` (either
    side does not parse). One classifier feeds both the commit guard and
    the carve-out, so the two can never disagree about what a commit did.
    """
    old_data, old_errors = _parse_side(old_text, "old")
    new_data, new_errors = _parse_side(new_text, "new")
    if old_errors or new_errors:
        # The old side of the `doc` backfill predates the required key,
        # so it cannot parse under this schema. Accept it only when the
        # new side is valid and the change is exactly that migration;
        # every other unparsable side stays a hard failure.
        if not new_errors and is_doc_backfill(old_text, new_text):
            return "migration", []
        return "invalid", old_errors + new_errors
    old_rules = old_data.get("rules", [])
    new_rules = new_data.get("rules", [])
    if old_rules == new_rules:
        return "none", []
    if rules_purely_added(old_rules, new_rules):
        return "addition", []
    if subject.startswith("pref-confirm:"):
        errors = validate_pref_confirm_change(old_data, new_data)
        if not errors:
            return "bump-exempt", []
        return "rewrite", errors
    return "rewrite", []


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


APPEND_ONLY_DIRS = (
    f"{decision_validator.STORE_DIR}/",
    f"{decision_validator.PREDICTIONS_DIR}/",
)


def check_append_only(base: str) -> list[str]:
    """No modify/delete/rename ever touches a recorded ruling.

    Predictions are covered too: an agent's recorded choice is the
    input a counterfactual replay reads, and a corpus that can be
    quietly rewritten cannot support one.
    """
    errors: list[str] = []
    diff = _git("diff", "--name-status", "--find-renames", f"{base}...HEAD")
    for line in diff.splitlines():
        parts = line.split("\t")
        status = parts[0]
        paths = parts[1:]
        touched = [p for p in paths if p.startswith(APPEND_ONLY_DIRS)]
        if touched and status != "A":
            directory = touched[0].split("/", 1)[0]
            errors.append(
                f"append-only: {directory}/ change {status} {' '.join(paths)} "
                "— existing records are never modified, deleted, or renamed"
            )
    return errors


def show_file(ref: str) -> str | None:
    """`git show <ref>` content, or None when the path is absent there."""
    result = subprocess.run(
        ["git", "show", ref], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def check_commits(base: str) -> list[str]:
    """Commit lint + preference-set edit discipline, per commit."""
    errors: list[str] = []
    shas = _git("rev-list", "--no-merges", "--reverse", f"{base}..HEAD").split()
    for sha in shas:
        subject = _git("log", "-1", "--format=%s", sha).strip()
        subject_error = check_commit_subject(subject)
        if subject_error:
            errors.append(f"{sha[:9]}: {subject_error}")

        old_source = show_file(f"{sha}^:{PREFERENCES_SOURCE}")
        new_source = show_file(f"{sha}:{PREFERENCES_SOURCE}")
        old_rendered = show_file(f"{sha}^:{PREFERENCES_RENDERED}")
        new_rendered = show_file(f"{sha}:{PREFERENCES_RENDERED}")
        if old_source == new_source and old_rendered == new_rendered:
            continue
        kind, change_errors = classify_preferences_change(
            old_source, new_source, subject
        )
        errors.extend(f"{sha[:9]}: {e}" for e in change_errors)
        if kind == "rewrite" and not subject.startswith(PREF_EDIT_TYPES):
            errors.append(
                f"{sha[:9]}: rewrites existing rules in {PREFERENCES_SOURCE} "
                "but is not a pref-confirm/pref-promote/pref-compact commit"
            )
        # Per-commit mirror: any commit touching either file leaves the
        # pair in sync, so no commit in history shows a drifted render.
        if kind != "invalid" and new_source is not None:
            data, _ = decision_validator.parse_preferences(new_source)
            if data is not None and decision_validator.render_preferences(data) != (
                new_rendered or ""
            ):
                errors.append(
                    f"{sha[:9]}: {PREFERENCES_RENDERED} is not the render of "
                    f"{PREFERENCES_SOURCE} — run "
                    "`python .github/store/render_preferences.py render`"
                )
    return errors


def _check_records_in(root: str, directory: str, records: dict[str, dict]) -> list[str]:
    """Validate every record in one corpus directory.

    Records from both directories share the ID namespace and the link
    graph, so they accumulate into one ``records`` map: a prediction
    may reference a decision, and a dangling link is dangling either
    way.
    """
    errors: list[str] = []
    path_dir = os.path.join(root, directory)
    if not os.path.isdir(path_dir):
        return errors
    for name in sorted(os.listdir(path_dir)):
        if name.startswith("."):
            continue
        path = os.path.join(path_dir, name)
        if not name.endswith(".json"):
            errors.append(f"{path}: non-JSON file in {directory}/")
            continue
        stem = name[: -len(".json")]
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: unreadable or invalid JSON: {exc}")
            continue
        errors.extend(
            f"{path}: {e}"
            for e in decision_validator.validate_record(record, filename_stem=stem)
        )
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records[record["id"]] = record
    return errors


def check_corpus(root: str = ".") -> list[str]:
    """Validate BOTH record corpora + refs + token budget.

    The token budget comes from the repo-local `store.config.json`
    (`budget_tokens`), not from a constant in this file: the budget is
    per-principal, and a second authority for it would only ever
    disagree with the first. `decision_validator`'s constant is the
    DEFAULT that config falls back to when a store ships no file.
    """
    errors: list[str] = []
    records: dict[str, dict] = {}
    try:
        config = store_config.load_config(root)
    except store_config.ConfigError as exc:
        return [str(exc)]
    for directory in (decision_validator.STORE_DIR, decision_validator.PREDICTIONS_DIR):
        errors.extend(_check_records_in(root, directory, records))
    errors.extend(decision_validator.validate_corpus(records))
    source_path = os.path.join(root, PREFERENCES_SOURCE)
    rendered_path = os.path.join(root, PREFERENCES_RENDERED)
    if not os.path.isfile(source_path):
        # Absence is a finding, never a skipped check: a store without
        # its active set would otherwise pass every guard by having
        # nothing left to verify, while sessions keep being primed from
        # whatever file survives.
        errors.append(
            f"{PREFERENCES_SOURCE}: missing — every store carries an active "
            f"set, empty or not, rendered to {PREFERENCES_RENDERED}. Write "
            "it, or restore it from history."
        )
        return errors
    with open(source_path, encoding="utf-8") as handle:
        data, source_errors = decision_validator.parse_preferences(handle.read())
    errors.extend(f"{PREFERENCES_SOURCE}: {e}" for e in source_errors)
    if source_errors:
        return errors
    if not os.path.isfile(rendered_path):
        errors.append(
            f"{PREFERENCES_RENDERED}: missing — run "
            "`python .github/store/render_preferences.py render`"
        )
        return errors
    with open(rendered_path, encoding="utf-8") as handle:
        rendered_given = handle.read()
    if decision_validator.render_preferences(data) != rendered_given:
        errors.append(
            f"{PREFERENCES_RENDERED}: not the render of {PREFERENCES_SOURCE} "
            "— run `python .github/store/render_preferences.py render`"
        )
    errors.extend(
        decision_validator.check_preferences_budget(
            rendered_given, int(config["budget_tokens"])
        )
    )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        required=True,
        help="base SHA of the PR (github.event.pull_request.base.sha)",
    )
    args = parser.parse_args(argv)

    errors = check_append_only(args.base) + check_commits(args.base) + check_corpus()
    for error in errors:
        print(f"GUARD FAIL: {error}")
    if not errors:
        print("All guards passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
