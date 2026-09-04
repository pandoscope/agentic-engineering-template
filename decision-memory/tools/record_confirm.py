"""Preference-counter math and the session PR body it explains.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

Pure over the records a session wrote: which rules a hit confirms, the
two hit-rate streams, and the body that shows the reader both.
"""

from __future__ import annotations


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def bump_preference_counter(
    data: dict,
    rule: str,
    today: str,
    validator,
    *,
    independent: bool = False,
) -> int | None:
    """Bump the confirmation counter of the rule matching ``rule``,
    in the parsed `preferences.json` data, in place.

    ``validator`` is the data repo's vendored decision_validator — the
    single source of the preference-set schema, shared with the CI
    guard, so writer and guard cannot disagree about the format.

    ``independent`` also raises the `independent` count. It is true only
    when the confirmation was NOT the rule crediting itself: the rule
    was cited on an option, and the decider chose that option, but it
    was not the slot the rule was written into. The two are counted
    apart because `confirmed` alone also rises when a rule predicts the
    slot it authored, which reads as evidence without being any.

    Returns the new count, or None when no rule matches. Matching is by
    normalized containment, so a cited fragment finds its rule.
    """
    wanted = _normalize(rule)
    for entry in data.get("rules", []):
        if wanted not in _normalize(entry["rule"]):
            continue
        entry[validator.COUNTER_KEY] += 1
        entry[validator.DATE_KEY] = today
        if independent:
            entry[validator.INDEPENDENT_KEY] += 1
        return entry[validator.COUNTER_KEY]
    return None


def session_hit_rates(records: list[dict]) -> dict[str, dict[str, int]]:
    streams: dict[str, dict[str, int]] = {
        "preference-driven": {"hit": 0, "miss": 0, "near-tie": 0, "refined": 0},
        "cold": {"hit": 0, "miss": 0, "near-tie": 0, "refined": 0},
    }
    for record in records:
        stream = record.get("prediction_stream")
        outcome = record.get("outcome")
        if stream in streams and outcome in streams[stream]:
            streams[stream][outcome] += 1
    return streams


def prediction_rules(record: dict) -> list[str]:
    for option in record.get("options", []):
        if isinstance(option, dict) and option.get("role") in (
            "prediction",
            "prediction+recommendation",
        ):
            rules = option.get("rules_cited")
            return [r for r in rules if isinstance(r, str)] if rules else []
    return []


def independent_rules(record: dict) -> list[str]:
    """Rules confirmed by the decider WITHOUT the rule picking the slot.

    `prediction_rules` returns the rules cited on the prediction slot,
    and a `hit` means that slot was chosen — so every confirmation it
    yields is the rule agreeing with the option it authored. That is
    worth counting, but it is not evidence the rule tracks the decider.

    This is the other case: a rule cited on some non-prediction option
    that the decider chose anyway. The rule did not put the option in
    front of them, and they took it regardless.
    """
    chosen_slot = record.get("chosen_slot")
    if chosen_slot is None:
        return []
    for option in record.get("options", []):
        if not isinstance(option, dict) or option.get("slot") != chosen_slot:
            continue
        if option.get("role") in ("prediction", "prediction+recommendation"):
            return []
        rules = option.get("rules_cited") or []
        return [rule for rule in rules if isinstance(rule, str)]
    return []


def confirmations_for(
    record: dict,
) -> tuple[list[tuple[str, bool]], list[tuple[str, str]]]:
    """The counter bumps a record earns, and the citations it withholds.

    Returns `(confirmations, skipped)`: `confirmations` is a list of
    `(rule, independent)` pairs to bump, `skipped` a list of
    `(rule, reason)` pairs that were cited but earn nothing.

    Two ways a rule earns a confirmation, counted apart. A `hit`
    credits the rules cited on the slot that won — the rule agreeing
    with itself. Anything else can still confirm a rule, if the decider
    chose an option that cited it without that rule having proposed it;
    that one is independent.

    Two ways a citation earns nothing (AET#227). A rule listed in
    `rules_disconfirmed` was set aside by the decider: neither win nor
    loss. A record with `correction: true` had its reason replaced, so
    the rules it cites did not drive the ruling and none auto-bumps;
    the decider promotes by hand if one did.
    """
    if record.get("outcome") == "hit":
        candidates = [(rule, False) for rule in prediction_rules(record)]
    else:
        candidates = [(rule, True) for rule in independent_rules(record)]
    disconfirmed = {
        rule for rule in record.get("rules_disconfirmed") or [] if isinstance(rule, str)
    }
    confirmations: list[tuple[str, bool]] = []
    skipped: list[tuple[str, str]] = []
    for rule, independent in candidates:
        if rule in disconfirmed:
            skipped.append((rule, "disconfirmed"))
        elif record.get("correction") is True:
            skipped.append((rule, "correction"))
        else:
            confirmations.append((rule, independent))
    return confirmations, skipped


def build_pr_body(records: list[dict], streams: dict[str, dict[str, int]]) -> str:
    def rate(stream: str) -> str:
        counts = streams[stream]
        scored = counts["hit"] + counts["miss"]
        shown = f"{counts['hit']}/{scored} hits" if scored else "no scored"
        extras = [
            f"{counts[bucket]} {bucket}"
            for bucket in ("refined", "near-tie")
            if counts[bucket]
        ]
        if extras:
            shown += f" ({', '.join(extras)})"
        return shown

    lines = [
        f"Decision session PR: {len(records)} record(s).",
        "",
        "Prediction hit rates (two streams):",
        f"- preference-driven: {rate('preference-driven')}",
        f"- cold (control): {rate('cold')}",
    ]
    supersedes = [
        (record["id"], record["supersedes"])
        for record in records
        if record.get("supersedes")
    ]
    if supersedes:
        lines += ["", "Supersedes claims — review explicitly:"]
        lines += [
            f"- {record_id} supersedes {target}" for record_id, target in supersedes
        ]
    closures = [
        (record["id"], record["closure_of"])
        for record in records
        if record.get("closure_of")
    ]
    if closures:
        lines += ["", "Closure records (closed-unmerged PR sweep):"]
        lines += [
            f"- {record_id} explains the closure of PR #{number}"
            for record_id, number in closures
        ]
    return "\n".join(lines) + "\n"
