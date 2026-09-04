"""Replay-regression harness for preference-set compaction.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

Records are replay-ready by design: input-side fields are written
before the ruling, output-side after. That is what makes compaction
falsifiable — mask the outcomes of the last N decisions, re-predict
them under a candidate (compacted) rule set, and score against what the
decider actually chose.

Three steps, each a plain CLI over JSON so the predicting agent (the
grilling skill in eval mode) sits in the middle without this repo
depending on it:

    cases   emit masked input-side cases for the last N decisions
    score   join an agent's predictions with the recorded outcomes,
            under one preference set, into a per-stream report
    gate    compare a baseline report against a candidate report

Scoring runs in two streams, exactly as recording does. A prediction
that cites rules scores **preference-driven**; one that cites none
scores **cold**. Compaction is gated on the preference-driven stream
only: its hit rate must not degrade. The cold stream is the control
group — it measures plain judgment, which a rule-set edit should not
move, so it is reported and never gated. Records shift streams under a
candidate set (a merged rule may now match a previously-cold record, or
a dropped one may leave a record cold); each case scores under the
stream the CANDIDATE assigns, and the shifts are reported so a hit-rate
change driven by re-labelling rather than by better rules is visible.

Masking removes `role` and `rules_cited` from the options — they encode
the original prediction and its stream — the `if_clause`, which only
alternatives carry and so singles out the prediction slot by its
absence, and, by default, the in-session `reasoning`, which was written
under the OLD rule set and would otherwise leak that set's answer into
the candidate run.

Slot ORDER is masked too, because slot 1 is the prediction slot by
convention and deciders pick it far more often than chance: on a real
corpus a blind "always slot 1" scored 82%, so an unpermuted number
measures ordering as much as it measures rules. `cases` presents each
record's options in an order derived from its id, and `score` derives
the same order to map a prediction back. The mapping is never written
into the cases file — shipping it would hand the ordering signal
straight back.

Two channels survive masking and are REPORTED rather than repaired,
because records are immutable: `cases` lists `leaks` — a key carried
by every option but one, or a context that narrates the ruling — and
`score` carries blind baselines (always slot 1, the odd option) next to
the streams, so a hit rate is always read against what the case alone
gives away.

Stdlib only. Usage:

    python .github/store/replay.py cases --out /tmp/cases.json
    python .github/store/replay.py score --predictions /tmp/base-preds.json \\
        --preferences /tmp/baseline-preferences.txt --out /tmp/base.json
    python .github/store/replay.py gate --baseline /tmp/base.json \\
        --candidate /tmp/cand.json --out /tmp/report.json

`gate` exits 0 on `pass`, 1 on `fail`, and 3 on
`insufficient-evidence` — a distinct code, because a gated stream too
small to mean anything is not the same event as a measured regression.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)

import budget as store_budget  # noqa: E402  (path bootstrap above)
import config as store_config  # noqa: E402  (path bootstrap above)
import decision_validator  # noqa: E402  (path bootstrap above)

DECISIONS_DIR = "decisions"

PREFERENCE_DRIVEN = "preference-driven"
COLD = "cold"
STREAMS = (PREFERENCE_DRIVEN, COLD)

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_INSUFFICIENT = "insufficient-evidence"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INSUFFICIENT = 3


def preferences_sha256(text: str) -> str:
    """Content address of a preference set — what ties a replay report
    to the exact rules it was scored against.

    Lives here rather than in the guard that checks it: the guard reads
    this module's verdicts, so the dependency runs guard -> replay and
    only that way.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


from replay_cases import (  # noqa: E402,F401  (path bootstrap above; re-exported)
    _option_dicts,
    build_cases,
    case_leaks,
    load_records,
    mask_record,
    record_slots,
    select_window,
    slot_order,
    unmap_slot,
)


def normalise_predictions(payload: object) -> tuple[dict[str, dict], list[str]]:
    """Accept `{"predictions": [...]}` or a bare list; validate entries."""
    if isinstance(payload, dict):
        raw = payload.get("predictions")
    else:
        raw = payload
    if not isinstance(raw, list):
        return {}, [
            "predictions: must be a list (or an object with a 'predictions' list)"
        ]
    errors: list[str] = []
    predictions: dict[str, dict] = {}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"predictions[{i}]: must be an object")
            continue
        record_id = entry.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append(f"predictions[{i}].id: must be a non-empty string")
            continue
        slot = entry.get("predicted_slot")
        if not isinstance(slot, int) or isinstance(slot, bool):
            errors.append(f"predictions[{i}].predicted_slot: must be an integer")
            continue
        cited = entry.get("rules_cited", [])
        if not isinstance(cited, list):
            errors.append(f"predictions[{i}].rules_cited: must be a list")
            continue
        if record_id in predictions:
            errors.append(f"predictions: duplicate entry for {record_id!r}")
            continue
        predictions[record_id] = {"predicted_slot": slot, "rules_cited": cited}
    return predictions, errors


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def odd_option_slot(record: dict) -> int | None:
    """The recorded slot a reader of the raw record would pick: the one
    option without an if-clause, when exactly one lacks it."""
    without = [
        slot
        for slot, option in zip(record_slots(record), _option_dicts(record))
        if "if_clause" not in option
    ]
    return without[0] if len(without) == 1 else None


def blind_baselines(records: list[dict]) -> dict:
    """What scoring without any rule set would get on these records.

    `always_slot_1` answers the prediction slot every time; `odd_option`
    answers the one option without an if-clause and abstains where there
    is none. Both are reported next to the streams so a hit rate is
    read against what the case alone gives away.
    """
    slot_1 = {"n": 0, "hits": 0}
    odd = {"n": 0, "hits": 0}
    for record in records:
        chosen = record.get("chosen_slot")
        slot_1["n"] += 1
        slot_1["hits"] += int(chosen == 1)
        odd_slot = odd_option_slot(record)
        if odd_slot is not None:
            odd["n"] += 1
            odd["hits"] += int(chosen == odd_slot)
    return {
        "always_slot_1": {**slot_1, "hit_rate": _rate(slot_1["hits"], slot_1["n"])},
        "odd_option": {**odd, "hit_rate": _rate(odd["hits"], odd["n"])},
    }


def score(
    records: list[dict],
    predictions: dict[str, dict],
    window: int,
    preferences_text: str,
    preferences_path: str | None = None,
) -> tuple[dict, list[str]]:
    """Score one run of predictions against the recorded outcomes."""
    selected = select_window(records, window)
    errors: list[str] = []
    cases: list[dict] = []
    shifts: list[dict] = []
    tallies = {stream: {"n": 0, "hits": 0} for stream in STREAMS}

    for record in selected:
        record_id = record.get("id")
        prediction = predictions.get(record_id)
        if prediction is None:
            errors.append(f"predictions: missing an entry for {record_id!r}")
            continue
        stream = PREFERENCE_DRIVEN if prediction["rules_cited"] else COLD
        # Predictions are made against the permuted case, outcomes are
        # recorded against the record — un-map before comparing.
        predicted_slot = unmap_slot(record, prediction["predicted_slot"])
        hit = predicted_slot == record.get("chosen_slot")
        tallies[stream]["n"] += 1
        tallies[stream]["hits"] += int(hit)
        recorded_stream = record.get("prediction_stream")
        if recorded_stream in STREAMS and recorded_stream != stream:
            shifts.append(
                {"id": record_id, "recorded": recorded_stream, "candidate": stream}
            )
        cases.append(
            {
                "id": record_id,
                "predicted_slot": predicted_slot,
                "presented_slot": prediction["predicted_slot"],
                "chosen_slot": record.get("chosen_slot"),
                "hit": hit,
                "stream": stream,
                "recorded_stream": recorded_stream,
                "rules_cited": prediction["rules_cited"],
            }
        )

    extra = sorted(set(predictions) - {record.get("id") for record in selected})
    for record_id in extra:
        errors.append(
            f"predictions: {record_id!r} is not in the replay window of {len(selected)}"
        )

    report = {
        "window": window,
        "scored": len(cases),
        "preferences_path": preferences_path,
        "preferences_sha256": preferences_sha256(preferences_text),
        "preferences_tokens": decision_validator.estimate_tokens(preferences_text),
        "slot_order": "permuted",
        "streams": {
            stream: {
                "n": tallies[stream]["n"],
                "hits": tallies[stream]["hits"],
                "hit_rate": _rate(tallies[stream]["hits"], tallies[stream]["n"]),
            }
            for stream in STREAMS
        },
        "blind_baselines": blind_baselines(selected),
        "stream_shifts": shifts,
        "cases": cases,
    }
    return report, errors


def gate(baseline: dict, candidate: dict, min_gated_cases: int = 0) -> dict:
    """Compare two scored runs. The preference-driven stream is the gate.

    Returns a report whose `gate` is one of:

    - `pass` — the gated hit rate held, over enough cases to mean it.
    - `fail` — a measured regression, or two runs that are not
      comparable. Always wins over `insufficient-evidence`: a
      degradation visible at small n is still a degradation.
    - `insufficient-evidence` — nothing degraded, but the gated stream
      is smaller than `min_gated_cases`, so the pass carries no
      evidence. Reported rather than upgraded to `pass`, because a
      green check that means nothing is worse than an amber one that
      says so.
    """
    reasons: list[str] = []
    notes: list[str] = []

    base_ids = [case["id"] for case in baseline.get("cases", [])]
    cand_ids = [case["id"] for case in candidate.get("cases", [])]
    if sorted(base_ids) != sorted(cand_ids):
        reasons.append(
            "baseline and candidate cover different cases — both runs must "
            "score the same replay window"
        )
    if not cand_ids:
        reasons.append("no cases scored — nothing to gate on")

    base_pd = baseline.get("streams", {}).get(PREFERENCE_DRIVEN, {})
    cand_pd = candidate.get("streams", {}).get(PREFERENCE_DRIVEN, {})
    base_rate = base_pd.get("hit_rate")
    cand_rate = cand_pd.get("hit_rate")
    # An empty stream scores 0.0 for comparison: a candidate set that
    # predicts nothing at all is a degradation, not a free pass.
    if (cand_rate or 0.0) < (base_rate or 0.0):
        reasons.append(
            f"preference-driven hit rate degraded: {base_rate} -> {cand_rate}"
        )
    if base_pd.get("n") and not cand_pd.get("n"):
        reasons.append(
            "the candidate set drove no predictions at all — every case went cold"
        )

    gated_cases = cand_pd.get("n") or 0
    if gated_cases < min_gated_cases:
        notes.append(
            f"only {gated_cases} preference-driven case(s), below the "
            f"{min_gated_cases} this store requires — the gated hit rate is "
            "noise at this size. A human owns this compaction: apply the "
            "replay-waiver label to merge it, or grow the preference-driven "
            "stream by extracting rules first"
        )

    if reasons:
        verdict = GATE_FAIL
    elif notes:
        verdict = GATE_INSUFFICIENT
    else:
        verdict = GATE_PASS

    shifts = candidate.get("stream_shifts", [])
    return {
        "gate": verdict,
        "reasons": reasons,
        "notes": notes,
        "gated_cases": gated_cases,
        "min_gated_cases": min_gated_cases,
        "window": candidate.get("window"),
        "scored": candidate.get("scored"),
        "baseline_preferences_sha256": baseline.get("preferences_sha256"),
        "candidate_preferences_sha256": candidate.get("preferences_sha256"),
        "tokens": {
            "baseline": baseline.get("preferences_tokens"),
            "candidate": candidate.get("preferences_tokens"),
        },
        "preference_driven": {
            "baseline": base_pd,
            "candidate": cand_pd,
        },
        "cold_control": {
            "baseline": baseline.get("streams", {}).get(COLD, {}),
            "candidate": candidate.get("streams", {}).get(COLD, {}),
        },
        "blind_baselines": candidate.get("blind_baselines", {}),
        "stream_shifts": {
            "cold_to_preference_driven": sum(
                1 for shift in shifts if shift["candidate"] == PREFERENCE_DRIVEN
            ),
            "preference_driven_to_cold": sum(
                1 for shift in shifts if shift["candidate"] == COLD
            ),
            "detail": shifts,
        },
    }


def _read_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _emit(payload: dict, out: str | None) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
        print(f"wrote {out}")
    else:
        print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    cases = sub.add_parser("cases", help="emit masked input-side cases")
    cases.add_argument("--window", type=int, help="override the configured window")
    cases.add_argument(
        "--include-reasoning",
        action="store_true",
        help="keep the in-session reasoning (leaks the old rule set)",
    )
    cases.add_argument("--out")

    scoring = sub.add_parser("score", help="score one run of predictions")
    scoring.add_argument("--predictions", required=True)
    scoring.add_argument(
        "--preferences",
        default=None,
        help="preference set the run used (default: ./preferences.txt)",
    )
    scoring.add_argument("--window", type=int)
    scoring.add_argument("--out")

    gating = sub.add_parser("gate", help="compare baseline against candidate")
    gating.add_argument("--baseline", required=True)
    gating.add_argument("--candidate", required=True)
    gating.add_argument("--out")

    args = parser.parse_args(argv)

    try:
        config = store_config.load_config(args.root)
    except store_config.ConfigError as exc:
        print(f"CONFIG FAIL: {exc}", file=sys.stderr)
        return 2

    if args.command == "gate":
        report = gate(
            _read_json(args.baseline),
            _read_json(args.candidate),
            int(config["min_gated_cases"]),
        )
        _emit(report, args.out)
        for reason in report["reasons"]:
            print(f"GATE FAIL: {reason}", file=sys.stderr)
        for note in report["notes"]:
            print(f"GATE INSUFFICIENT EVIDENCE: {note}", file=sys.stderr)
        if report["gate"] == GATE_FAIL:
            return EXIT_FAIL
        if report["gate"] == GATE_INSUFFICIENT:
            return EXIT_INSUFFICIENT
        return EXIT_OK

    window = args.window or int(config["replay_window"])
    records = load_records(args.root)

    if args.command == "cases":
        payload = build_cases(records, window, args.include_reasoning)
        _emit(payload, args.out)
        for leak in payload["leaks"]:
            detail = leak.get("key") or leak.get("match")
            print(
                f"LEAK {leak['channel']}: {leak['id']} — {detail!r} identifies the "
                "recorded answer; read the gate's pass with that in mind",
                file=sys.stderr,
            )
        return 0

    predictions, errors = normalise_predictions(_read_json(args.predictions))
    path = args.preferences or os.path.join(
        args.root, store_budget.PREFERENCES_FILENAME
    )
    with open(path, encoding="utf-8") as handle:
        preferences_text = handle.read()
    report, score_errors = score(records, predictions, window, preferences_text, path)
    errors += score_errors
    _emit(report, args.out)
    for error in errors:
        print(f"SCORE FAIL: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
