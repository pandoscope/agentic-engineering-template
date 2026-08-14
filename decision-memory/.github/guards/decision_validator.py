"""Single-source validator for decision-memory records.

This file is the one validation authority for the decision-memory
contract. It lives in the agentic-engineering-template repo (guard
subtemplate) and is copier-vendored into the decision-memory repo,
where BOTH consumers import it:

- the CI guard (guards.py, next to this file), and
- the writer tool (tools/record.py in template-instantiated repos),
  which imports it from the data-repo clone at runtime.

Stdlib only, no dependencies — the vendored copy must keep working
even if the template repo disappears (fails soft: guard keeps
working, only updates stop).

All validators return a list of human-readable error strings (empty =
valid) and TOLERATE unknown fields: new optional fields need no
migration.

The store-independent half — ID grammar, envelope, required-field
presence, corpus link integrity — lives in ``validator_core.py`` next
to this file and is shared with every other store's validator. What
stays here is the decision contract itself.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import validator_core  # noqa: E402  (path bootstrap above)

SCHEMA_VERSION = validator_core.SCHEMA_VERSION
RECORD_TYPE = "decision"

# The store's own link vocabulary, walked by the corpus check.
LINK_FIELDS = ("related", "supersedes", "drill_down_of")
STORE_DIR = "decisions"

# Autonomous agent records: same schema, same append-only guarantee,
# outside the preference pipeline entirely. A run with no decider
# present is a prediction under the active set, not a ruling — so
# extraction never walks this directory and no record in it can bump a
# counter. Naming it for what it holds keeps `decisions/` meaning
# a-human-ruled.
PREDICTIONS_DIR = "predictions"

REQUIRED_FIELDS = (
    "v",
    "type",
    "id",
    "date",
    "project",
    "question",
    "options",
    "prediction_stream",
    "artifact_ref",
    "chosen_slot",
    "chosen",
    "rejections",
    "outcome",
)

PREDICTION_ROLES = frozenset({"prediction", "prediction+recommendation"})
OPTION_ROLES = PREDICTION_ROLES | frozenset({"recommendation", "wildcard"})
PREDICTION_STREAMS = frozenset({"preference-driven", "cold"})
OUTCOMES = frozenset({"hit", "miss", "near-tie", "refined"})
REJECTION_STATUSES = frozenset({"operative", "presumed-false"})
# Reason provenance for presumed-false rejections: the model records
# the most-likely reason and DECLARES where it came from; a null
# reason is only valid when explicitly declared "none" — never a lazy
# default.
PRESUMED_REASON_SOURCES = frozenset({"if_clause", "inferred", "none"})
# Operative reasons are decider-confirmed only — deliberately NO
# 'inferred' tier (an inferred why-chosen belongs in the chosen
# option's own reasoning and in the rejections). 'none' declares a
# silent pick: the decider chose without stating a reason.
OPERATIVE_REASON_SOURCES = frozenset({"stated", "none"})

MAX_SLUG_LENGTH = validator_core.MAX_SLUG_LENGTH
ID_RE = validator_core.ID_RE
DATE_RE = validator_core.DATE_RE

# Single source for the preference-set format (see
# decision-memory/docs/conventions.md): `preferences.json` is the
# machine-owned source of truth, `preferences.tsv` its render and the
# ONLY file injected into sessions. The two are a declared mirror —
# the guard re-renders and fails on any drift, and the writer's
# pref-confirm bumps edit the JSON then re-render. Both sides consume
# exactly these definitions, so they cannot disagree about the format.

PREFERENCES_SOURCE = "preferences.json"
PREFERENCES_RENDERED = "preferences.tsv"

RENDERED_HEADER = "confirmed\tindependent\trule"

COUNTER_KEY = "confirmed"
INDEPENDENT_KEY = "independent"
DATE_KEY = "last"

# Exactly these, on every rule. A closed set is what keeps a rule from
# gaining keys nothing reads: adding one is a deliberate change here,
# with its consumer, rather than something a writer can introduce in
# passing.
#
# List ORDER is the set's priority order, human-owned: when two rules
# match contradicting solutions, the earlier rule wins. New rules
# append at the end; moving one is a rewrite of the active set and
# gated like any other. Nothing here enforces a particular order — the
# order IS the ruling.
RULE_KEYS = ("rule", COUNTER_KEY, INDEPENDENT_KEY, DATE_KEY)


def parse_preferences(text: str) -> tuple[dict | None, list[str]]:
    """Parse and validate preferences.json content.

    Returns ``(data, errors)``. ``data`` is None only when the text is
    not JSON at all; schema errors come back alongside the parsed data
    so callers can decide how far to trust it.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"not valid JSON: {exc}"]
    return data, validate_preferences(data)


def _validate_rule(index: int, rule: object) -> list[str]:
    """Schema errors for one entry of the ``rules`` list."""
    where = f"rules[{index}]"
    if not isinstance(rule, dict):
        return [f"{where}: must be an object"]
    if tuple(rule) != RULE_KEYS:
        return [f"{where}: keys must be exactly {list(RULE_KEYS)}, got {list(rule)}"]
    errors: list[str] = []
    text = rule["rule"]
    # One physical line in the render, single-spaced. A rule may hold a
    # joined qualifier sentence under the one counter — "one line, one
    # preference" counts preferences, not sentences — but never
    # structure the render would have to escape.
    if not isinstance(text, str) or not text or text != " ".join(text.split()):
        errors.append(
            f"{where}: rule text must be one non-empty single-spaced line: {text!r}"
        )
    for key in (COUNTER_KEY, INDEPENDENT_KEY):
        value = rule[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"{where}: {key}={value!r} is not a non-negative integer")
    if not errors and rule[INDEPENDENT_KEY] > rule[COUNTER_KEY]:
        errors.append(
            f"{where}: {INDEPENDENT_KEY} > {COUNTER_KEY} "
            f"({rule[INDEPENDENT_KEY]} > {rule[COUNTER_KEY]}): independent "
            "confirmations are a subset of all of them"
        )
    if not isinstance(rule[DATE_KEY], str) or not DATE_RE.fullmatch(rule[DATE_KEY]):
        errors.append(f"{where}: {DATE_KEY}={rule[DATE_KEY]!r} is not YYYY-MM-DD")
    return errors


def validate_preferences(data: object) -> list[str]:
    """Schema errors for a parsed preferences.json, empty when valid."""
    if not isinstance(data, dict):
        return ["top level must be a JSON object"]
    errors: list[str] = []
    unknown = [key for key in data if key not in ("rules", "_comment")]
    if unknown:
        errors.append(
            f"unknown top-level key(s) {unknown} — only 'rules' (and a '_comment' banner) are read"
        )
    rules = data.get("rules")
    if not isinstance(rules, list):
        return errors + ["'rules' must be a list"]
    for index, rule in enumerate(rules):
        errors.extend(_validate_rule(index, rule))
    if errors:
        return errors
    seen_text: dict[str, int] = {}
    for index, rule in enumerate(rules):
        normalized = rule["rule"].lower()
        if normalized in seen_text:
            errors.append(
                f"rules[{index}]: duplicates the text of "
                f"rules[{seen_text[normalized]}] — counter bumps match by rule "
                "text, so it must be unique"
            )
        seen_text[normalized] = index
    return errors


def render_preferences(data: dict) -> str:
    """Render the injected surface from the source of truth.

    TSV with one header line, one rule per row, in source order — the
    order is the priority order. Deterministic — equal data renders
    byte-equal, which is what lets the guard check the mirror by
    re-rendering.
    """
    lines = [RENDERED_HEADER]
    for rule in data.get("rules", []):
        lines.append(f"{rule[COUNTER_KEY]}\t{rule[INDEPENDENT_KEY]}\t{rule['rule']}")
    return "\n".join(lines) + "\n"


def serialize_preferences(data: dict) -> str:
    """Canonical serialization of preferences.json.

    One shape, so mechanical edits (counter bumps) produce one-field
    diffs instead of reformatting the file.
    """
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ~1-2k-token hard budget on the rendered set (ticket §5); estimated at
# the common ~4 chars/token heuristic — deliberately coarse, the budget
# is a forcing function, not an accounting system.
PREFERENCES_TOKEN_BUDGET = 2000
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Coarse token estimate for the preferences budget check."""
    return len(text) // CHARS_PER_TOKEN


validate_id = validator_core.validate_id


def validate_envelope(record: dict) -> list[str]:
    """Check the universal envelope against this store's record type."""
    return validator_core.validate_envelope(record, RECORD_TYPE)


def _validate_options(record: dict, errors: list[str]) -> dict | None:
    """Validate the options block; return the prediction option if unique."""
    options = record.get("options")
    if not isinstance(options, list) or not options:
        errors.append("options: must be a non-empty list")
        return None
    prediction_options = []
    seen_slots: set[int] = set()
    for i, option in enumerate(options):
        if not isinstance(option, dict):
            errors.append(f"options[{i}]: must be an object")
            continue
        slot = option.get("slot")
        if not isinstance(slot, int) or isinstance(slot, bool):
            errors.append(f"options[{i}].slot: must be an integer")
        elif slot in seen_slots:
            errors.append(f"options[{i}].slot: duplicate slot {slot}")
        else:
            seen_slots.add(slot)
        label = option.get("label")
        if not isinstance(label, str) or not label:
            errors.append(f"options[{i}].label: must be a non-empty string")
        role = option.get("role")
        if role is not None and role not in OPTION_ROLES:
            errors.append(f"options[{i}].role: {role!r} not in {sorted(OPTION_ROLES)}")
        if role in PREDICTION_ROLES:
            prediction_options.append(option)
    if len(prediction_options) != 1:
        errors.append(
            "options: exactly one option must carry a prediction role "
            f"({len(prediction_options)} found)"
        )
        return None
    return prediction_options[0]


def _validate_streams(
    record: dict, prediction_option: dict | None, errors: list[str]
) -> None:
    stream = record.get("prediction_stream")
    if stream not in PREDICTION_STREAMS:
        errors.append(
            f"prediction_stream: {stream!r} not in {sorted(PREDICTION_STREAMS)}"
        )
        return
    if prediction_option is None:
        return
    rules_cited = prediction_option.get("rules_cited", [])
    if not isinstance(rules_cited, list):
        errors.append("options[].rules_cited: must be a list")
        return
    if stream == "preference-driven" and not rules_cited:
        errors.append(
            "rules_cited: must be non-empty when prediction_stream is preference-driven"
        )
    if stream == "cold" and rules_cited:
        errors.append(
            "rules_cited: must be empty when prediction_stream is cold "
            "(cold means no preference rule predicted this)"
        )


def _validate_ruling(
    record: dict, prediction_option: dict | None, errors: list[str]
) -> None:
    chosen_slot = record.get("chosen_slot")
    if not isinstance(chosen_slot, int) or isinstance(chosen_slot, bool):
        errors.append("chosen_slot: must be an integer")
        chosen_slot = None
    chosen = record.get("chosen")
    if not isinstance(chosen, str) or not chosen:
        errors.append("chosen: must be a non-empty string")

    outcome = record.get("outcome")
    if outcome not in OUTCOMES:
        errors.append(f"outcome: {outcome!r} not in {sorted(OUTCOMES)}")
    elif (
        outcome != "near-tie"
        and prediction_option is not None
        and chosen_slot is not None
    ):
        # Scored outcomes must match the slots. Near-ties are exempt by
        # design (never scored as misses); 'refined' requires a slot
        # MISMATCH like miss — the chosen answer CONTAINS the
        # prediction plus an extension, distinguished from miss only by
        # that containment judgment (same slot would be a plain hit).
        hit = chosen_slot == prediction_option.get("slot")
        if outcome == "hit" and not hit:
            errors.append(
                "outcome: 'hit' but chosen_slot differs from the prediction slot"
            )
        if outcome in ("miss", "refined") and hit:
            errors.append(
                f"outcome: {outcome!r} but chosen_slot equals the "
                "prediction slot (that is a hit)"
            )

    # operative_reason is required when a listed non-prediction option
    # won — unless the pick was declared silent.
    operative_source = record.get("operative_reason_source")
    if operative_source is not None:
        if operative_source not in OPERATIVE_REASON_SOURCES:
            errors.append(
                f"operative_reason_source: {operative_source!r} not in "
                f"{sorted(OPERATIVE_REASON_SOURCES)} (operative reasons are "
                "decider-confirmed only — no inferred tier)"
            )
        elif operative_source == "none":
            if record.get("operative_reason") is not None:
                errors.append(
                    "operative_reason: must be null when "
                    "operative_reason_source is 'none' (silent pick)"
                )
        elif not record.get("operative_reason"):
            errors.append(
                "operative_reason: must be a non-empty string when "
                "operative_reason_source is 'stated'"
            )
    options = record.get("options")
    if isinstance(options, list) and chosen_slot is not None:
        chosen_option = next(
            (
                o
                for o in options
                if isinstance(o, dict) and o.get("slot") == chosen_slot
            ),
            None,
        )
        if (
            chosen_option is not None
            and chosen_option.get("role") not in PREDICTION_ROLES
            and not record.get("operative_reason")
            and operative_source != "none"
        ):
            errors.append(
                "operative_reason: required when a listed non-prediction "
                "option is chosen (declare operative_reason_source 'none' "
                "for a silent pick)"
            )

    rejections = record.get("rejections")
    if not isinstance(rejections, list):
        errors.append("rejections: must be a list")
    else:
        for i, rejection in enumerate(rejections):
            if not isinstance(rejection, dict):
                errors.append(f"rejections[{i}]: must be an object")
                continue
            if not isinstance(rejection.get("option"), str) or not rejection["option"]:
                errors.append(f"rejections[{i}].option: must be a non-empty string")
            status = rejection.get("status")
            if status not in REJECTION_STATUSES:
                errors.append(
                    f"rejections[{i}].status: {status!r} not in "
                    f"{sorted(REJECTION_STATUSES)}"
                )
                continue
            reason = rejection.get("reason")
            source = rejection.get("reason_source")
            if status == "operative":
                # Operative reasons are decider-stated by definition.
                if source not in (None, "stated"):
                    errors.append(
                        f"rejections[{i}].reason_source: {source!r} — "
                        "operative rejections are stated by definition"
                    )
                if not isinstance(reason, str) or not reason:
                    errors.append(
                        f"rejections[{i}].reason: operative rejections "
                        "require the stated reason, verbatim"
                    )
            else:  # presumed-false
                if source not in PRESUMED_REASON_SOURCES:
                    errors.append(
                        f"rejections[{i}].reason_source: {source!r} not in "
                        f"{sorted(PRESUMED_REASON_SOURCES)} (required for "
                        "presumed-false rejections)"
                    )
                elif source == "none":
                    if reason is not None:
                        errors.append(
                            f"rejections[{i}].reason: must be null when "
                            "reason_source is 'none'"
                        )
                elif not isinstance(reason, str) or not reason:
                    errors.append(
                        f"rejections[{i}].reason: must be a non-empty "
                        f"string when reason_source is {source!r} (declare "
                        "reason_source 'none' if nothing is inferable)"
                    )


def _validate_optional_fields(record: dict, errors: list[str]) -> None:
    date = record.get("date")
    if date is not None and (not isinstance(date, str) or not DATE_RE.match(date)):
        errors.append(f"date: {date!r} is not YYYY-MM-DD")

    project = record.get("project")
    if project is not None and (not isinstance(project, str) or not project):
        errors.append("project: must be a non-empty string")

    artifact_ref = record.get("artifact_ref")
    if artifact_ref is not None and not isinstance(artifact_ref, dict):
        errors.append("artifact_ref: must be an object or null")

    correction = record.get("correction")
    if correction is not None and not isinstance(correction, bool):
        errors.append("correction: must be a boolean")

    closure_of = record.get("closure_of")
    if closure_of is not None and (
        not isinstance(closure_of, int)
        or isinstance(closure_of, bool)
        or closure_of < 1
    ):
        errors.append(f"closure_of: {closure_of!r} must be a positive PR number")

    related = record.get("related")
    if related is not None:
        if not isinstance(related, list):
            errors.append("related: must be a list of record IDs")
        else:
            for ref in related:
                for err in validate_id(ref):
                    errors.append(f"related: {err}")

    for link_field in ("supersedes", "drill_down_of"):
        ref = record.get(link_field)
        if ref is not None:
            for err in validate_id(ref):
                errors.append(f"{link_field}: {err}")


def validate_record(record: object, filename_stem: str | None = None) -> list[str]:
    """Validate a single decision record against the full contract.

    Returns a list of error strings; empty means valid. Unknown fields
    are tolerated. When ``filename_stem`` is given, the record's ``id``
    must equal it (ID = filename stem, always).
    """
    if not isinstance(record, dict):
        return ["record: must be a JSON object"]
    errors = validate_envelope(record)
    errors.extend(validator_core.validate_required(record, REQUIRED_FIELDS))
    if filename_stem is not None and record.get("id") != filename_stem:
        errors.append(
            f"id: {record.get('id')!r} does not equal the filename stem "
            f"{filename_stem!r}"
        )
    prediction_option = _validate_options(record, errors)
    _validate_streams(record, prediction_option, errors)
    _validate_ruling(record, prediction_option, errors)
    _validate_optional_fields(record, errors)
    return errors


def validate_corpus(records: dict) -> list[str]:
    """Cross-record checks: no dangling link into a record that is not
    in the corpus.

    ``records`` maps record ID -> record dict (normally the whole
    ``decisions/`` directory).
    """
    return validator_core.validate_corpus(records, LINK_FIELDS, STORE_DIR)


def check_preferences_budget(
    text: str, budget_tokens: int = PREFERENCES_TOKEN_BUDGET
) -> list[str]:
    """Enforce the hard token budget on the rendered preference set."""
    tokens = estimate_tokens(text)
    if tokens > budget_tokens:
        return [
            f"{PREFERENCES_RENDERED}: ~{tokens} tokens exceeds the "
            f"{budget_tokens} budget — promote requires demote (merge or "
            "demote another rule to make room)"
        ]
    return []
