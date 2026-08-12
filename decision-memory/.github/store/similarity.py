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
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as store_config  # noqa: E402  (path bootstrap above)

DECISIONS_DIR = "decisions"
PREFERENCES_FILENAME = "preferences.txt"
# Pre-migration commits pin the markdown set; preferences_at still
# serves those verbatim, and rule_lines reads both shapes.
LEGACY_PREFERENCES_FILENAME = "preferences.md"
RENDERED_HEADER = "confirmed\tindependent\trule"

# Pair verdicts, worst first — the order the report ranks by.
DUPLICATE = "duplicate"
RE_DECISION = "re-decision"
UNCERTAIN = "uncertain"
LINKED = "linked"
VERDICT_ORDER = (DUPLICATE, RE_DECISION, UNCERTAIN, LINKED)

# The calibrated constants below are DEFAULTS sourced from
# `config.DEFAULTS`, so there is one place a number lives and one place
# a store overrides it (`store.config.json`). They stay module-level
# names because every function here takes them as an ordinary
# argument — the gate must remain callable on a bare pair of dicts,
# with no config file anywhere near it.
#
# Why they are configurable at all: the right value is a property of a
# store's corpus, not of this algorithm. See `config.CALIBRATED` and
# the `recalibrate-thresholds` skill.
DEFAULT_THRESHOLD = store_config.DEFAULTS["similarity_threshold"]

# Asymmetric surfacing channel, for the case symmetric similarity is
# structurally blind to: one draft re-extracted as TWO. Jaccard divides
# by the union, so a bundle split in half scores low against each half
# even when the half is entirely inside the bundle. Containment divides
# by the smaller side instead and sees it.
#
# Measured on a real split: jaccard 0.34 against the half that shares
# vocabulary, containment 0.68. It cost 1 flagged pair in 136 on a real
# 17-record corpus, and that pair was a true relation — cheap enough to
# run alongside rather than instead.
#
# Still only a surfacing aid. Pairwise comparison cannot ASSERT a
# split, and a half sharing little vocabulary with its bundle (0.17 in
# the same real case) stays invisible. Surfacing one half is enough to
# bring a human to the cluster.
CONTAINMENT_THRESHOLD = store_config.DEFAULTS["containment_threshold"]

# An artifact_ref agreeing on repo+path is strong corroboration that
# two records are about the same thing, so it lifts an otherwise
# borderline text score over the line rather than deciding alone.
ARTIFACT_BOOST = store_config.DEFAULTS["artifact_boost"]

REF_COMPLETE = "complete"
REF_PARTIAL = "partial"
REF_NULL = "null"


def tuning(config: dict | None = None) -> dict:
    """The calibrated values in effect for one run.

    Threaded explicitly rather than read from a global, so a caller
    that hands the gate two dicts and no config still gets the
    documented defaults, and a test can vary one number without
    mutating module state under every other test.
    """
    resolved = dict(
        (key, store_config.DEFAULTS[key]) for key in store_config.CALIBRATED
    )
    if config:
        for key in store_config.CALIBRATED:
            if key in config:
                resolved[key] = config[key]
    return resolved


_WORD_RE = re.compile(r"[a-z0-9]+")

# Dropped before comparison: these carry no signal about WHICH decision
# a record is, and leaving them in inflates every pair's score toward a
# uniform middle where nothing stands out.
_STOPWORDS = frozenset(
    """
    a an and are as at be by do does for from has have how i in is it its of on
    or that the this to was what when where which who why will with we you
    should would could not no yes if then than there their them these those
    """.split()
)


def tokenize(text: object) -> set[str]:
    """Content words of a string, lowercased and de-duplicated."""
    if not isinstance(text, str):
        return set()
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _STOPWORDS}


def record_tokens(record: dict) -> set[str]:
    """The tokens that identify WHICH decision this is.

    Question plus option labels: the input side, which both a duplicate
    and a re-decision share. Deliberately not `chosen` — two records of
    the same question with different answers are the interesting case,
    and scoring the answer in would push them apart.
    """
    tokens = tokenize(record.get("question"))
    for option in record.get("options") or []:
        if isinstance(option, dict):
            tokens |= tokenize(option.get("label"))
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def containment(left: set[str], right: set[str]) -> float:
    """Overlap coefficient: how much of the SMALLER side is shared.

    Asymmetric where jaccard is symmetric, which is the whole point —
    a part fully inside a whole scores 1.0 here and much lower there.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _ref(record: dict) -> dict:
    ref = record.get("artifact_ref")
    return ref if isinstance(ref, dict) else {}


def artifact_corroborates(left: dict, right: dict) -> bool:
    """Do two records point at the same artifact?

    Repo+path agreement is the signal; commit is not required, because
    two records about one file at different SHAs are exactly the
    re-decision case this gate is looking for.
    """
    left_ref, right_ref = _ref(left), _ref(right)
    if not left_ref or not right_ref:
        return False
    keys = ("repo", "path")
    if any(not left_ref.get(key) for key in keys):
        return False
    return all(left_ref.get(key) == right_ref.get(key) for key in keys)


def similarity(left: dict, right: dict, tune: dict | None = None) -> float:
    """Combined score in 0..1: token overlap plus artifact corroboration."""
    values = tune or tuning()
    score = jaccard(record_tokens(left), record_tokens(right))
    if artifact_corroborates(left, right):
        score = min(1.0, score + values["artifact_boost"])
    return round(score, 4)


def identity(record: dict) -> str | None:
    """What to call this thing in a report.

    Drafts are keyed by `slug` and only gain an `id` when the recorder
    mints one at ingestion — which is after this gate runs, by
    definition. A gate that only understood `id` would print `None` for
    every draft it was built to check.
    """
    for key in ("id", "slug"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


# Resolved link fields, and the batch-local slug forms drafts carry
# before ingestion resolves them into IDs. Both are checked, because a
# link written in a draft is exactly the thing this gate exists to
# preserve — reporting it as missing would send a human to re-add an
# edge that is already there.
_LINK_FIELDS = (
    ("related", "related_slugs"),
    ("supersedes", "supersedes_slug"),
    ("drill_down_of", "drill_down_of_slug"),
)


def _linked(left: dict, right: dict) -> bool:
    """Is either record already pointing at the other?"""
    for source, target in ((left, right), (right, left)):
        names = {
            value
            for key in ("id", "slug")
            for value in [target.get(key)]
            if isinstance(value, str) and value
        }
        if not names:
            continue
        for resolved_field, slug_field in _LINK_FIELDS:
            for field in (resolved_field, slug_field):
                value = source.get(field)
                if isinstance(value, str) and value in names:
                    return True
                if isinstance(value, list) and names & set(value):
                    return True
    return False


def _provenance(record: dict) -> tuple[object, object]:
    preference_set = record.get("preference_set")
    commit = preference_set.get("commit") if isinstance(preference_set, dict) else None
    return commit, record.get("session")


# Two extractions of one ruling reword the answer freely, so exact
# equality would call every reworded duplicate "a different answer".
# Measured on a real pair of batches: identical rulings scored 1.00 on
# the option label and 0.30 on the free-text prose.
ANSWER_AGREEMENT = store_config.DEFAULTS["answer_agreement"]


def chosen_texts(record: dict) -> dict[str, str]:
    """The two phrasings of what was chosen, keyed by kind.

    `chosen_slot` is NOT comparable across extractions: two runs that
    listed the same options in a different order give one ruling
    different slot numbers (seen in real data — slot 1 vs slot 3 for
    the same answer). The label the slot points at survives that; the
    free-text `chosen` is the fallback for a slot beyond the listed
    options, and the two are kept apart so they are only ever compared
    like with like.
    """
    texts: dict[str, str] = {}
    slot = record.get("chosen_slot")
    for option in record.get("options") or []:
        if isinstance(option, dict) and option.get("slot") == slot:
            label = option.get("label")
            if isinstance(label, str) and label:
                texts["label"] = label
    chosen = record.get("chosen")
    if isinstance(chosen, str) and chosen:
        texts["prose"] = chosen
    return texts


def answers_agree(left: dict, right: dict, tune: dict | None = None) -> bool:
    """Did two records land on the same answer, however worded?

    Label against label and prose against prose, best of the two.
    Never label against prose: one record's option label routinely
    echoes the other's question, which would read as agreement between
    answers that have nothing to do with each other.
    """
    left_texts, right_texts = chosen_texts(left), chosen_texts(right)
    shared = set(left_texts) & set(right_texts)
    if not shared:
        return not left_texts and not right_texts
    best = max(
        jaccard(tokenize(left_texts[kind]), tokenize(right_texts[kind]))
        for kind in shared
    )
    return best >= (tune or tuning())["answer_agreement"]


def classify(left: dict, right: dict, tune: dict | None = None) -> str:
    """Verdict for one similar pair.

    Provenance is what separates "the same ruling recorded twice" from
    "the same question decided again later". `preference_set.commit`
    content-addresses the rule set the decision was made under, so two
    records sharing it were made against the same state of the world.
    """
    if _linked(left, right):
        return LINKED

    left_commit, left_session = _provenance(left)
    right_commit, right_session = _provenance(right)

    same_answer = answers_agree(left, right, tune)

    if left_commit and right_commit:
        if left_commit == right_commit:
            # Same rule set, same answer, no link: one ruling recorded
            # twice. Different answer under one rule set is a genuine
            # re-decision within a session, which still wants an edge.
            return DUPLICATE if same_answer else RE_DECISION
        return RE_DECISION

    # Chat-extracted drafts carry null provenance by design, so absence
    # is normal and must not be read as "distinct". Fall back to the
    # session key, then to the answer.
    if left_session and right_session and left_session == right_session:
        return DUPLICATE if same_answer else UNCERTAIN
    if left_session and right_session and left_session != right_session:
        return RE_DECISION
    return DUPLICATE if same_answer else UNCERTAIN


_DIFF_FIELDS = (
    "question",
    "project",
    "chosen",
    "chosen_slot",
    "operative_reason",
    "prediction_stream",
    "outcome",
    "date",
)


def field_diffs(left: dict, right: dict) -> dict[str, list]:
    """Per-field differences, so a human can adjudicate from the report."""
    diffs = {}
    for field in _DIFF_FIELDS:
        if left.get(field) != right.get(field):
            diffs[field] = [left.get(field), right.get(field)]
    return diffs


def find_pairs(
    candidates: list[dict],
    corpus: list[dict],
    threshold: float | None = None,
    tune: dict | None = None,
) -> list[dict]:
    """Every candidate pair at or above `threshold`, worst first.

    Candidates are compared against each other AND against the corpus:
    a draft can duplicate another draft in the same batch, or a record
    that was ingested months ago.
    """
    values = tune or tuning()
    if threshold is None:
        threshold = values["similarity_threshold"]
    contain_at = values["containment_threshold"]
    pairs: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def consider(left: dict, right: dict, right_side: str) -> None:
        score = similarity(left, right, values)
        overlap = round(containment(record_tokens(left), record_tokens(right)), 4)
        if score < threshold and overlap < contain_at:
            return
        pairs.append(
            {
                "score": score,
                "containment": overlap,
                "surfaced_by": "similarity" if score >= threshold else "containment",
                "verdict": classify(left, right, values),
                "left": identity(left),
                "right": identity(right),
                "right_side": right_side,
                "artifact_corroborated": artifact_corroborates(left, right),
                "diffs": field_diffs(left, right),
            }
        )

    for i, left in enumerate(candidates):
        for j, right in enumerate(candidates):
            if i >= j or (i, j) in seen:
                continue
            seen.add((i, j))
            consider(left, right, "draft")
        for record in corpus:
            consider(left, record, "store")

    pairs.sort(key=lambda pair: (VERDICT_ORDER.index(pair["verdict"]), -pair["score"]))
    return pairs


def ref_tier(record: dict) -> str:
    """How complete is this draft's `artifact_ref`?

    Enrichment is only possible while the draft is mutable, so a tier
    of `partial` or `null` here is a task with a deadline. Conventions
    forbid guessing SHAs, so this reports and never fills anything in.
    """
    ref = _ref(record)
    if not ref:
        return REF_NULL
    present = [key for key in ("repo", "path", "commit") if ref.get(key)]
    if len(present) == 3:
        return REF_COMPLETE
    return REF_PARTIAL if present else REF_NULL


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def preferences_at(commit: object, root: str = ".") -> str | None:
    """The preference set as of a pinned commit, or None if unavailable.

    Tries the rendered file first, then the legacy markdown set — a
    record may pin a commit from before the format split, and the
    pinned content is served in whichever shape it has. A draft with no
    pinned commit, or a commit this checkout does not have, yields None
    — the check is skipped and said to be skipped, rather than silently
    compared against the wrong rule set.
    """
    if not isinstance(commit, str) or not commit:
        return None
    for name in (PREFERENCES_FILENAME, LEGACY_PREFERENCES_FILENAME):
        text = _git("-C", root, "show", f"{commit}:{name}")
        if text:
            return text
    return None


# The `[confirmed: N, last: DATE]` suffix every rule carries. It is
# bookkeeping, identical in shape across the whole set, and counting it
# as rule vocabulary dilutes every coverage score by a fixed amount that
# grows with how well-confirmed the rule is.
_RULE_METADATA_RE = re.compile(r"\[[^\]]*\]\s*$")


def rule_lines(preferences_text: str) -> list[str]:
    """The rule texts of a preference set, without the bookkeeping.

    Reads both shapes: the rendered TSV, and the legacy markdown
    bullets that pre-migration `preference_set.commit`s still pin.
    """
    lines = preferences_text.splitlines()
    if lines and lines[0] == RENDERED_HEADER:
        return [
            line.split("\t", 2)[2].strip()
            for line in lines[1:]
            if line and not line.startswith("#") and line.count("\t") >= 2
        ]
    return [
        _RULE_METADATA_RE.sub("", line.strip()[2:]).strip()
        for line in lines
        if line.strip().startswith("- ")
    ]


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
