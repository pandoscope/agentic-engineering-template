"""Case building for the replay gate: records masked to their input side.

Split from replay.py (the scoring and gating side) so each stays under
the code-line limit. Everything here runs before a predictor sees a
case; nothing here reads predictions or verdicts, so the dependency
runs replay -> replay_cases and only that way.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)


DECISIONS_DIR = "decisions"

# Option fields that leak the recorded prediction or the old rule set.
# `if_clause` is structural: the prediction slot never carries one and
# every alternative must, so the one option without it IS the recorded
# prediction. The clause is the recommender's argument for an
# alternative, not the decider's input, so nothing a predictor
# legitimately needs is lost.
_LEAKY_OPTION_FIELDS = ("role", "rules_cited", "if_clause")
_OLD_RULE_SET_FIELDS = ("reasoning",)

_CASE_FIELDS = ("id", "date", "project", "question", "context", "artifact_ref")


def load_records(root: str = ".") -> list[dict]:
    """Every record in `decisions/`, oldest first (IDs sort chronologically)."""
    directory = os.path.join(root, DECISIONS_DIR)
    records: list[dict] = []
    if not os.path.isdir(directory):
        return records
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.startswith("."):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            records.append(json.load(handle))
    return records


def select_window(records: list[dict], window: int) -> list[dict]:
    """The most recent `window` records, oldest first."""
    return records[-window:] if window > 0 else []


def _option_dicts(record: dict) -> list[dict]:
    return [
        option for option in (record.get("options") or []) if isinstance(option, dict)
    ]


def record_slots(record: dict) -> list[int]:
    """The recorded slot numbers of a record's options, in file order.

    Falls back to 1-based position for an option with no usable `slot`,
    so a hand-written record still replays instead of crashing the run.
    """
    slots: list[int] = []
    for position, option in enumerate(_option_dicts(record), start=1):
        slot = option.get("slot")
        usable = isinstance(slot, int) and not isinstance(slot, bool)
        slots.append(slot if usable else position)
    return slots


def slot_order(record_id: str, slots: list[int]) -> list[int]:
    """Presentation order of one record's slots — index i holds the
    recorded slot shown in presented position i+1.

    Derived from the record id (sha256 per slot) rather than stored, so
    `score` recomputes it and the cases file never carries the mapping.
    A shipped mapping would tell the predicting agent which presented
    option is recorded slot 1 — the prediction slot — which is the
    signal permuting exists to remove.
    """
    return sorted(
        slots,
        key=lambda slot: hashlib.sha256(
            f"{record_id}:{slot}".encode("utf-8")
        ).hexdigest(),
    )


def unmap_slot(record: dict, presented_slot: int) -> int:
    """Map a presented slot number back to the recorded one.

    A slot beyond the listed options maps to itself: that is the
    free-text slot the decider may answer in (`chosen_slot` 4 against
    three options), which was never permuted because it was never
    presented.
    """
    order = slot_order(record.get("id") or "", record_slots(record))
    if 1 <= presented_slot <= len(order):
        return order[presented_slot - 1]
    return presented_slot


def mask_record(record: dict, include_reasoning: bool = False) -> dict:
    """Strip a record down to its input side, minus the leaky fields.

    Options come back in the record's presentation order (see
    `slot_order`) and renumbered 1..n, so neither the recorded ordering
    nor the recorded slot numbers survive into the case.
    """
    case = {field: record.get(field) for field in _CASE_FIELDS}
    slots = record_slots(record)
    masked_by_slot = {}
    for slot, option in zip(slots, _option_dicts(record)):
        masked_by_slot[slot] = {
            key: value
            for key, value in option.items()
            if key not in _LEAKY_OPTION_FIELDS
            and key != "slot"
            and (include_reasoning or key not in _OLD_RULE_SET_FIELDS)
        }
    order = slot_order(record.get("id") or "", slots)
    case["options"] = [
        {"slot": position, **masked_by_slot[slot]}
        for position, slot in enumerate(order, start=1)
    ]
    return case


LEAK_ODD_OPTION = "odd-option"
LEAK_CONTEXT = "context"

# Ruling narration in the input-side context. A denylist, deliberately
# small: it flags the phrasings measured on a real corpus (a verdict in
# the past tense, a reference to what was ruled) and nothing subtler.
# The context is written before the ruling by contract, so a match is a
# recording-discipline finding as much as a masking one.
_CONTEXT_NARRATION = re.compile(
    r"\b(?:ruled|ruling)\b"
    r"|\bthe principal (?:caught|chose|decided|reviewed|accepted|rejected"
    r"|merged|reverted)\b"
    r"|\bwas (?:chosen|decided|accepted|rejected|merged|reverted|rewritten)\b",
    re.IGNORECASE,
)


def case_leaks(case: dict) -> list[dict]:
    """What still identifies the recorded answer in one masked case.

    Structural: a key carried by every option but one singles that
    option out, exactly as the if-clause did before it was masked. The
    check runs on the masked case, so a field the recorder adds later
    is caught the day it ships, not the day someone notices 20/20.

    Textual: a context that narrates the ruling. Reported once per
    case, on the first match, so a long context does not flood the
    report.
    """
    options = case.get("options") or []
    leaks: list[dict] = []
    if len(options) >= 2:
        keys = set().union(*(option.keys() for option in options))
        for key in sorted(keys):
            carriers = sum(1 for option in options if key in option)
            if carriers == len(options) - 1:
                leaks.append(
                    {"id": case.get("id"), "channel": LEAK_ODD_OPTION, "key": key}
                )
    context = case.get("context")
    if isinstance(context, str):
        match = _CONTEXT_NARRATION.search(context)
        if match:
            leaks.append(
                {"id": case.get("id"), "channel": LEAK_CONTEXT, "match": match.group(0)}
            )
    return leaks


def build_cases(
    records: list[dict], window: int, include_reasoning: bool = False
) -> dict:
    selected = select_window(records, window)
    cases = [mask_record(record, include_reasoning) for record in selected]
    return {
        "window": window,
        "count": len(selected),
        "slot_order": "permuted",
        "leaks": [leak for case in cases for leak in case_leaks(case)],
        "instructions": (
            "For each case, predict which slot the decider will choose. "
            "Answer only from the injected preference set plus the case "
            'itself. Emit {"predictions": [{"id", "predicted_slot", '
            '"rules_cited"}]}; rules_cited lists the verbatim preference '
            "rules that drove the prediction and MUST be empty when none "
            "applies (an honest cold claim — cold is the control stream, "
            "not a penalty). Slot numbers are shuffled per case and carry "
            "no meaning across cases — judge each option on its content. "
            "If you expect the decider to answer with none of the listed "
            "options, predict the slot one past the last one."
        ),
        "cases": cases,
    }
