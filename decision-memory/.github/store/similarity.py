"""Ingestion gate: dedup, re-decision links, false-cold, ref completeness.

Copier-vendored from the agentic-engineering-template decision-memory
subtemplate — change it there, pull via `copier update`.

Run this over a drafts file plus the store, BEFORE ingestion. Every
check here shares one deadline: **drafts are mutable and records are
not.** `decisions/` is append-only with no carve-out, so whatever is
not fixed at ingestion is frozen permanently.

That is what separates this gate from the analysis passes. Those read
immutable history and can run any time; these three cannot.

1. **Dedup.** The same ruling extracted twice — by two runs over one
   session, say — would mint two immutable records for one decision.
2. **Re-decision links.** A `related`/`supersedes` edge cannot be added
   after ingestion without violating append-only, so ingestion is the
   only moment an edge can be written. An unlinked re-decision is a
   permanently disconnected node.
3. **False cold + ref completeness.** A record claiming
   `prediction_stream: cold` while a matching rule was active
   permanently understates that rule's evidence, and the replay gate's
   stream split is built on that field. A null `artifact_ref` that
   could have been filled stays null forever.

**The gate never writes.** It reports and a human acts: discarding a
duplicate to `discarded-drafts.json` (never deleting it), adding a link
to a draft, restreaming a false cold, enriching a ref. Every one of
those is a judgement call, and the tool's job is to make sure the call
gets made while it still can be.

Stdlib only. Usage:

    python .github/store/similarity.py --drafts drafts.json
    python .github/store/similarity.py --drafts drafts.json --json
    python .github/store/similarity.py            # store against itself
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as store_config  # noqa: E402  (path bootstrap above)
from similarity_measure import (  # noqa: E402
    DECISIONS_DIR,
    PREFERENCES_FILENAME,
    REF_COMPLETE,
    REF_NULL,
    REF_PARTIAL,
    VERDICT_ORDER,
    _provenance,
    find_pairs,
    identity,
    record_tokens,
    ref_tier,
    tokenize,
    tuning,
)


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def preferences_at(commit: object, root: str = ".") -> str | None:
    """The preference set as of a pinned commit, or None if unavailable.

    A draft with no pinned commit, a commit this checkout does not
    have, or one predating the file yields None — the caller then
    compares against the current set and RECORDS that it did
    (`compared_against`), rather than silently using the wrong rules.
    """
    if not isinstance(commit, str) or not commit:
        return None
    text = _git("-C", root, "show", f"{commit}:{PREFERENCES_FILENAME}")
    return text or None


def rule_lines(preferences_text: str) -> list[str]:
    """The rules of a rendered preference set — one per line.

    The render carries rules and nothing else, so there is nothing to
    strip: no counters, no headings, no prose to mistake for a rule.
    """
    return [line.strip() for line in preferences_text.splitlines() if line.strip()]


# Share of a RULE's terms that appear in a record. Not jaccard: rules
# run ~8 tokens and records ~20-40, and jaccard divides by the union,
# so the size gap caps the score below any useful threshold. A 7-token
# rule against a 42-token record maxes out at 0.167 — the check could
# not fire even on a rule quoted verbatim.
#
# Containment is size-invariant and says something a human can act on:
# "this fraction of the rule's terms is present". Measured on a real
# corpus the distribution is sparse — one pair at 0.43, then a cliff to
# 0.12 — so 0.4 sits in the gap rather than on a slope.
FALSE_COLD_THRESHOLD = store_config.DEFAULTS["false_cold_threshold"]


def false_cold_candidates(
    record: dict, preferences_text: str, threshold: float = FALSE_COLD_THRESHOLD
) -> list[dict]:
    """Rules that plausibly applied to a record claiming `cold`.

    Advisory by construction. Conventions call a false cold claim a
    detectable provenance defect, and this is the detection — but term
    overlap cannot know whether a rule actually drove anything, so a
    human confirms before restreaming the draft.
    """
    if record.get("prediction_stream") != "cold":
        return []
    tokens = record_tokens(record)
    matches = []
    for line in rule_lines(preferences_text):
        rule_tokens = tokenize(line)
        if not rule_tokens:
            continue
        overlap = tokens & rule_tokens
        score = len(overlap) / len(rule_tokens)
        if score >= threshold:
            matches.append(
                {
                    "rule": line,
                    "rule_coverage": round(score, 4),
                    "shared_terms": sorted(overlap),
                }
            )
    matches.sort(key=lambda match: -match["rule_coverage"])
    return matches


def build_report(
    candidates: list[dict],
    corpus: list[dict],
    root: str = ".",
    threshold: float | None = None,
    config: dict | None = None,
) -> dict:
    """The whole gate: pairs, false-cold flags, ref completeness."""
    values = tuning(config)
    pairs = find_pairs(candidates, corpus, threshold, values)

    false_cold = []
    current = read_preferences(root)
    for record in candidates:
        commit, _ = _provenance(record)
        pinned = preferences_at(commit, root)
        text = pinned if pinned is not None else current
        if not text:
            continue
        matches = false_cold_candidates(record, text, values["false_cold_threshold"])
        if matches:
            false_cold.append(
                {
                    "id": identity(record),
                    "preference_set_commit": commit,
                    "compared_against": "pinned" if pinned is not None else "current",
                    "matches": matches,
                }
            )

    tiers = [
        {"id": identity(record), "tier": ref_tier(record)} for record in candidates
    ]

    # Staleness is measured against the WHOLE corpus, not this batch:
    # a threshold's evidence expires as the store grows, whether or not
    # today's run happens to be large.
    stale = store_config.stale_calibrations(
        config or store_config.DEFAULTS, len(corpus) + len(candidates)
    )

    return {
        "candidates": len(candidates),
        "corpus": len(corpus),
        "threshold": threshold
        if threshold is not None
        else values["similarity_threshold"],
        "tuning": values,
        "stale_calibrations": stale,
        "pairs": pairs,
        "verdict_counts": {
            verdict: sum(1 for pair in pairs if pair["verdict"] == verdict)
            for verdict in VERDICT_ORDER
        },
        "false_cold": false_cold,
        "artifact_ref_tiers": tiers,
        "tier_counts": {
            tier: sum(1 for entry in tiers if entry["tier"] == tier)
            for tier in (REF_COMPLETE, REF_PARTIAL, REF_NULL)
        },
    }


def read_preferences(root: str = ".") -> str:
    path = os.path.join(root, PREFERENCES_FILENAME)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_records(root: str = ".") -> list[dict]:
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


def load_drafts(path: str) -> list[dict]:
    """Drafts file: a bare array, or an object with a `drafts` list."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("drafts") or payload.get("records") or []
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a list of drafts")
    return [entry for entry in payload if isinstance(entry, dict)]


def render(report: dict) -> str:
    lines: list[str] = []
    counts = report["verdict_counts"]
    lines.append(
        f"ingestion gate: {report['candidates']} candidate(s) vs "
        f"{report['corpus']} record(s) — "
        + ", ".join(f"{counts[v]} {v}" for v in VERDICT_ORDER)
    )

    if not report["pairs"]:
        lines.append("  no pairs above the threshold")
    for pair in report["pairs"]:
        lines.append("")
        corroborated = " +artifact" if pair["artifact_corroborated"] else ""
        via = (
            f" containment {pair['containment']}"
            if pair["surfaced_by"] == "containment"
            else ""
        )
        lines.append(
            f"  [{pair['verdict']}] {pair['score']}{corroborated}{via}  "
            f"{pair['left']}  vs  {pair['right']} ({pair['right_side']})"
        )
        for field, (left, right) in sorted(pair["diffs"].items()):
            lines.append(f"      {field}: {left!r}  ->  {right!r}")

    lines.append("")
    if report["false_cold"]:
        lines.append(f"false-cold? {len(report['false_cold'])} draft(s) to confirm")
        for entry in report["false_cold"]:
            lines.append(
                f"  {entry['id']} (vs {entry['compared_against']} preference set)"
            )
            for match in entry["matches"]:
                lines.append(
                    f"      {match['rule_coverage']}  {match['rule']}\n"
                    f"          shared: {', '.join(match['shared_terms'])}"
                )
    else:
        lines.append("false-cold? none flagged")

    lines.append("")
    tier_counts = report["tier_counts"]
    lines.append(
        "artifact_ref: "
        + ", ".join(f"{count} {tier}" for tier, count in tier_counts.items())
    )
    for entry in report["artifact_ref_tiers"]:
        if entry["tier"] != REF_COMPLETE:
            lines.append(f"  {entry['tier']:<8} {entry['id']}")

    # Surfaced to whoever runs the gate, because they are the one
    # person who can see whether today's verdicts look right — and a
    # threshold nobody has measured is most visible next to the
    # clusters it just produced.
    stale = report.get("stale_calibrations") or []
    if stale:
        lines.append("")
        lines.append(
            f"calibration: {len(stale)} threshold(s) due a re-measurement "
            "— run the recalibrate-thresholds skill"
        )
        for entry in stale:
            lines.append(
                f"  {entry['constant']:<22} {entry['value']}  ({entry['reason']})"
            )

    lines.append("")
    lines.append(
        "Nothing was written. Duplicates go to discarded-drafts.json (never "
        "deleted), links and restreams are edits to the DRAFTS — all of it "
        "impossible once these records are ingested."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="store root (default: cwd)")
    parser.add_argument(
        "--drafts",
        help="drafts file to gate; omit to check the store against itself",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the configured similarity_threshold for this run",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--out", help="write the JSON report here instead of stdout (implies --json)"
    )
    args = parser.parse_args(argv)

    config = store_config.load_config(args.root)
    corpus = load_records(args.root)
    if args.drafts:
        candidates = load_drafts(args.drafts)
    else:
        # No drafts: fold the corpus onto itself so an already-ingested
        # duplicate still surfaces. Nothing can be fixed at this point,
        # but knowing is better than not.
        candidates, corpus = corpus, []

    report = build_report(candidates, corpus, args.root, args.threshold, config)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {args.out}")
    elif args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
