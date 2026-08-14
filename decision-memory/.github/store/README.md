# Preference-set lifecycle

Copier-vendored from the agentic-engineering-template guard
subtemplate — do NOT edit these files in the store repo;
change them in the template and pull via `copier update`.

Everything under `.github/store/`, plus
`.github/workflows/preferences-budget.yml`,
`.github/workflows/preferences-guard.yml` and the
`extract-preferences` and `compact-preferences` skills, is vendored
alongside the record guard in `.github/guards/`.

The two directories stay separate because they answer to different
things: `guards/` gates the record corpus, `store/` runs the
preference-set lifecycle on top of it.
They are not separate trust domains — the record guard reads this
layer's config for the token budget, so there is one budget number and
one place to change it.
Both are vendored, so neither can drift from the schema —
which is the point.
N stores share one budget rule, one carve-out rule, one replay gate.

`store.config.json` is the exception: it is seeded once and owned by
the store, so a human adjusts the knobs below without fighting
`copier update`.

## Why

`preferences.txt` — the render of `preferences.json` — is injected
into every grilled session. Everything in it costs context on every
session, forever — so it is a hard budget,
not a wishlist, and shrinking it needs to be safe rather than brave.
That gives four jobs: grow the set from what the records actually
show, measure the budget, protect the file from casual edits, and make
a compaction provably non-degrading before it merges.

Growing comes first. Until rules are extracted, every record is
recorded `prediction_stream: cold`, the preference-driven stream is
empty, and the compaction gate has nothing to measure.

## Configuration

`store.config.json`, at the repo root — the one place a human adjusts
these knobs:

Defaults live in `config.DEFAULTS` and are not repeated here — one
authority, so this table cannot drift out of step with the code.

| key | meaning |
| --- | --- |
| `budget_tokens` | hard budget for the rendered `preferences.txt` |
| `warn_at_percent` | "compression due" threshold |
| `carve_out_label` | label permitting edits to existing lines |
| `budget_issue_label` | label on the automated budget issue |
| `replay_waiver_label` | label accepting an `insufficient-evidence` gate |
| `replay_window` | how many recent decisions the replay scores |
| `min_gated_cases` | below this many preference-driven cases the gate reports `insufficient-evidence` |
| `similarity_threshold` | below this a pair is not worth a human's attention |
| `containment_threshold` | the split channel: one draft re-extracted as two |
| `artifact_boost` | how much repo+path agreement lifts a borderline pair |
| `answer_agreement` | how close two rewordings must be to count as one answer |
| `false_cold_threshold` | rule coverage that flags a suspect `cold` record |
| `calibration_growth_factor` | corpus growth past a stamp before it reads as stale |
| `calibration` | evidence behind each calibrated constant |

A missing file is fine — the defaults are the contract. Unknown keys
are tolerated (`_comment` is one), invalid values fail loudly.

### Policy knobs and calibrated knobs are different things

`budget_tokens`, `warn_at_percent` and `replay_window` are **policy**:
choices about how much context to spend. No corpus can contradict
them — a budget is not wrong, it is a decision.

The five in `config.CALIBRATED` are **empirical**: each is a claim
about where a real distribution separates, and a claim can be false,
silently, for as long as nobody re-measures. `false_cold_threshold`
once held a value the measure could not reach at any input, and every
run duly reported no false colds.

They live in config rather than in `similarity.py` because the right
value is a property of *a store's corpus*. Two stores with different
corpora should hold different numbers, and a store must not have to
edit a vendored module — and take a merge conflict on every
`copier update` — to act on its own measurement.

Each carries its evidence under `calibration`:

```json
"calibration": {
  "false_cold_threshold": {
    "corpus_size": 94, "separation": 0.18, "measured": "2026-07-27"
  }
}
```

Empty by default, deliberately: a store that has never measured should
say so rather than inherit a stamp earned on somebody else's corpus.
The gate reports a constant as **never measured** or **outgrown**, and
`/recalibrate-thresholds` is the pass that resolves either. It is a
prompt, never a failing gate — failing CI over a stale threshold would
only teach people to silence it.

`budget_tokens` is the ONLY budget. The vendored record guard loads
this config and enforces whatever the store chose;
`decision_validator.PREFERENCES_TOKEN_BUDGET` is the default it falls
back to when a store ships no config file, not a ceiling over it. One
number, one place to change it, checked once.

`render_preferences.py` keeps the pair honest: `render` writes
`preferences.txt` from `preferences.json`, `check` fails on drift. The
guards re-render on every PR, so the mirror cannot drift silently.

Token counting is not reimplemented here either — `estimate_tokens`
from the vendored validator is the single authority, so this layer and
the vendored guard can never disagree about how big the file is.

## Enforcement

**On push to `main`** (`preferences-budget.yml`) the file is counted
and one pinned issue is kept in sync: opened or updated at or above
`warn_at_percent`, closed once the file is back under it. It reports;
it never blocks.

**On every PR** (`preferences-guard.yml`, alongside the vendored
`guards.yml`):

- Over 100% of budget, a PR that touches the preference set fails. PRs
  that do not touch it are not blocked by someone else's overspend.
- Rewriting an EXISTING rule in `preferences.json` requires the
  carve-out label. Pure additions never need it; mechanical
  `pref-confirm` counter bumps are exempt, since the vendored guard
  already validates their counter math.
- A carve-out PR must carry a replay report in its description, gated
  `pass`, whose `candidate_preferences_sha256` matches the
  `preferences.txt` in the PR head — a stale report from an earlier
  round fails.
- A report gated `insufficient-evidence` merges only with
  `replay_waiver_label` on the PR. A report gated `fail` never merges;
  the waiver does not apply to a measured regression.

- A PR that ADDS decision records must contain a `pref-extract:`
  commit, with no record added after it. Positional, so there is
  nothing to enumerate and nothing copyable from another branch. A
  missed pass is recoverable — the watermark walk reaches past it — but
  recoverable is not the same as caught, since nothing would prompt the
  recovery. The gate is what turns "can be picked up later" into "was
  picked up here".

`decisions/` gets **no carve-out**. Append-only there is absolute and
neither this layer nor extraction touches that rule.

## Replay regression

`replay.py` is the harness the compaction skill drives; the predicting
agent sits in the middle, so this repo depends on no skill.

```bash
python .github/store/replay.py cases   --out cases.json
python .github/store/replay.py score   --predictions preds.json \
    --preferences preferences.txt --out report.json
python .github/store/replay.py gate    --baseline base.json \
    --candidate cand.json --out replay-report.json
```

`cases` masks each record to its input side and strips the fields that
leak the old rule set's answer (`role`, `rules_cited`, and the
in-session `reasoning`). `score` joins an agent's predictions with the
recorded `chosen_slot`. `gate` compares two scored runs: exit 0 on
`pass`, 1 on `fail` when the **preference-driven** hit rate degrades,
and 3 on `insufficient-evidence`.

Two streams, exactly as recording uses them: a prediction citing rules
scores preference-driven, one citing none scores cold. The gate is the
preference-driven stream. The cold stream is the control group — plain
judgment, which a rule-set edit should not move — so it is reported
and never gated. Cases that change stream under the candidate set are
counted separately, so a hit-rate change caused by re-labelling rather
than by better rules is visible.

### What the calibration fixed

A null test — two blind runs over the *same* rule set — passed the
gate by luck rather than by design, and both problems are addressed
here.

**The gated denominator was unstable.** Both runs picked the same slot
on all 17 cases but disagreed on whether a rule drove the pick for 4
of them, so `n` moved 3 -> 5 under a change that was not a change. At
that size one case flipping swings the hit rate 20-33 points, so a
`pass` meant nothing. `min_gated_cases` is the answer: below it the
gate reports `insufficient-evidence`, and merging takes a waiver label
that puts a human's name on an unvalidated compaction. On a corpus
with no extracted rules, every compaction needs the waiver — that is
the honest state, and it is meant to be visible rather than papered
over with a green check.

**Slot ordering leaked.** `chosen_slot` was 1 in 14 of 17 records, so
a blind "always slot 1" scored 82% and both runs scored 94%. Masking
stripped `role` and `rules_cited` but left slot order, and slot 1 is
the prediction slot by convention. `cases` now presents each record's
options in an order derived from its ID and `score` derives the same
order to map predictions back, so the number measures rules rather
than ordering. The mapping is never written into the cases file:
shipping it would hand the signal straight back.

The remaining calibration is data, not code — the gate is trustworthy
once records carry `prediction_stream: preference-driven`, which is
what extraction produces. Re-run the null test then.

## Extraction

`extraction.py` is the read side of the growth half, driven by the
`extract-preferences` skill:

```bash
python .github/store/extraction.py status
python .github/store/extraction.py batch --out batch.json
```

`batch` emits the records since the watermark, sorted into four queues in
descending evidence order — `corrections`, `misses`, `refinements`,
`confirmations` — plus the rule-driven acceptances, where a rule cited
itself into the prediction slot and that slot was chosen. Those confirm
nothing: the recommendation caused the choice it would be credited with
predicting. They are flagged precisely because counting them is
tempting.

### The watermark is a commit

Every pass ends with a `pref-extract:` commit, so the scope is
everything recorded since the last one. Derived from history rather
than tracked beside it: nothing to advance, nothing to keep in sync,
nothing to conflict when two branches run a pass at once — and
`record.py submit` already works this way for the `pref-confirm` half
of the same pass.

Two properties fall out of deriving rather than storing:

- **It self-heals.** A session merged without a pass is not lost; the
  next pass simply reaches further back. A stored watermark that was
  never advanced looks identical to one with nothing to do.
- **Bootstrap is not a special case.** No `pref-extract:` commit
  anywhere means the watermark is the beginning of the corpus, so the
  first pass covers everything by construction. There is no `--all`
  mode because there is nothing for it to do.

**An empty pass commits too**, and that is what makes detection
reliable rather than merely convenient. A pass that finds nothing
produces no proposal and no counter bump, so a watermark keyed on
outcome commits would stall every time extraction legitimately had
nothing to say. `pref-confirm:` is worse than useless for the purpose:
`submit` emits those, so it would move the watermark for a pass that
never ran — and a false watermark skips records rather than re-reading
them. Hence a dedicated type that nothing else produces.

One thing this leans on: **the repo must not squash-merge.** Squashing
rewrites subjects, and the watermark is a subject in history. Merge
commits or rebase both preserve it.

### Scope is not evidence

Running the pass per session does NOT make it per-session in the sense
that matters. The pass ACTS on the records since the watermark and
REASONS from the whole corpus, which the batch ships as `history`.
Cross-session repetition is the evidence a single session cannot see,
and it stays available: a pattern appearing once in the scope and twice
in history is a three-record pattern.

## Ingestion gate

`similarity.py` runs over a drafts file plus the store, BEFORE
ingestion:

```bash
python .github/store/similarity.py --drafts drafts.json
python .github/store/similarity.py --drafts drafts.json --json
python .github/store/similarity.py            # store against itself
```

Everything it checks shares one deadline: **drafts are mutable and
records are not.** `decisions/` is append-only with no carve-out, so
whatever is not fixed at ingestion is frozen permanently. That is what
separates this gate from the analysis passes, which read immutable
history and can run whenever.

Three checks, one run:

- **Dedup.** Two extraction runs over one session produce the same
  ruling twice, worded differently. Ingesting both mints two immutable
  records for one decision.
- **Re-decision links.** A `related`/`supersedes` edge cannot be added
  after ingestion without violating append-only, so ingestion is the
  only moment an edge can be written. An unlinked re-decision is a
  permanently disconnected node.
- **False cold + ref completeness.** A record claiming `cold` while a
  matching rule was active permanently understates that rule's
  evidence — and the replay gate's stream split reads exactly that
  field. A null `artifact_ref` that could have been filled stays null.

Pairs are classified by **provenance**, which is what separates "one
ruling recorded twice" from "the same question decided again later":

| Verdict | When | What a human does |
| --- | --- | --- |
| `duplicate` | same `preference_set.commit`, same `chosen` | discard one to `discarded-drafts.json` — never delete |
| `re-decision` | distinct provenance, no link | add the `related`/`supersedes` edge to the draft |
| `uncertain` | provenance missing on either side | adjudicate |
| `linked` | an edge already exists | informational |

Chat-extracted drafts carry null provenance by design, so absence is
never read as "distinct" — it falls back to the session key, then to
the answer, and lands in `uncertain` rather than guessing.

Pairs also surface by **containment** (overlap coefficient), not only
by similarity. That is the one case symmetric scoring is structurally
blind to: a draft re-extracted as TWO. Jaccard divides by the union, so
a bundle scores low against each half even when the half sits entirely
inside it — measured on a real split, jaccard 0.34 against the half
that shares vocabulary versus containment 0.68. It cost one flagged
pair in 136 on a real corpus, and that pair was a true relation.

Still only a surfacing aid: pairwise comparison cannot ASSERT a split,
and a half sharing little vocabulary with its bundle (0.17 in the same
case) stays invisible. Surfacing one half is enough to bring a human to
the cluster.

**The gate never writes.** Every remedy above is a judgement call; the
tool's job is to make sure the call gets made while it still can be.
`/adjudicate-drafts` is the skill that turns each cluster into a
decision with its costs laid out.

## Tests

```bash
python .github/store/tests/test_store.py
```

The git-facing adapters are thin; the decisions live in pure functions,
which is what the tests cover. They also build replay cases from the
real corpus, so a record the harness cannot handle fails CI.

## Known seams

- Nothing here is store-local any more,
  so a store that wants a different budget policy cannot have one —
  it gets the vendored policy and tunes it through `store.config.json`.
  A store needing more than the knobs allow changes the template.
