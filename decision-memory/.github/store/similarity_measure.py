"""How alike two decision records are, and what that likeness means.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

The measure and the verdict it feeds: tokens, Jaccard and containment,
artifact corroboration, answer agreement, and the classification a
pair lands in. Pure over records — `similarity.py` beside it is the
report and the CLI. Stdlib only.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as store_config  # noqa: E402  (path bootstrap above)

DECISIONS_DIR = "decisions"
PREFERENCES_FILENAME = "preferences.txt"

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
