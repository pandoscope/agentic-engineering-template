"""The store layer under test, and the record factories every suite uses.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

The store's modules are plain files, not a package: this puts their two
directories on `sys.path` once, so every suite beside it imports them by
bare name the way the scripts themselves do.
"""

from __future__ import annotations

import json
import os
import sys

STORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARDS_DIR = os.path.join(os.path.dirname(STORE_DIR), "guards")
sys.path.insert(0, STORE_DIR)
sys.path.insert(0, GUARDS_DIR)

import budget as store_budget  # noqa: E402,F401
import config as store_config  # noqa: E402,F401
import decision_validator  # noqa: E402,F401
import extraction  # noqa: E402,F401
import guards  # noqa: E402,F401
import preferences_guard as guard  # noqa: E402,F401
import render_preferences  # noqa: E402,F401
import replay  # noqa: E402,F401
import similarity  # noqa: E402,F401


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
    confirmed=1,
    independent=0,
    last="2026-07-15",
    doc=None,
):
    return {
        "rule": text,
        "confirmed": confirmed,
        "independent": independent,
        "last": last,
        "doc": doc,
    }


def make_preferences(*rules):
    return {"rules": list(rules)}


def source_text(*rules):
    return json.dumps(make_preferences(*rules))


def contest_record(record_id, chosen_slot, slot_rules):
    """A record whose options cite rules — a decided contest fixture.

    ``slot_rules`` maps slot -> list of cited rule texts.
    """
    options = [
        {"slot": slot, "label": f"option {slot}", "rules_cited": list(rules)}
        for slot, rules in sorted(slot_rules.items())
    ]
    options[0]["role"] = "prediction"
    return dict(make_record(record_id, chosen_slot), options=options)
