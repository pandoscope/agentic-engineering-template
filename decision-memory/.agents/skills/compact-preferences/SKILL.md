---
name: compact-preferences
description: Shrink the active preference set below its token budget, gated on a replay of recent decisions. Use when the budget reports "compression due", when the rendered set is at or over budget, or on request to compact the preference set.
---

# Compacting the active preference set

> Copier-vendored from the agentic-engineering-template guard
> subtemplate — do NOT edit it in the store repo;
> change it in the template and pull via `copier update`.

`preferences.txt` — the render of `preferences.json` — is injected
into every grilled session, so every rule costs context forever. Compaction brings that cost down without losing
what the rules encode.

Manual trigger only — never on a schedule, never as a side effect. A
human merges the result.

**Extraction comes first.** If the store has never been extracted into
(`extraction.py status` shows the whole corpus pending, or every record
reads `prediction_stream: cold`), there is nothing to gate against —
run `extract-preferences` first.

## Invariants

CI rejects the PR if any of these break.

- `decisions/` stays append-only. Compaction rewrites the ACTIVE SET,
  never history. Do not modify, delete, or rename a single record —
  there is no carve-out for `decisions/`, ever.
- Every surviving rule keeps its conditional, falsifiable form: one
  entry, a condition, an outcome you could be wrong about, and its
  counters.
- `preferences.txt` is never edited directly — edit `preferences.json`
  and re-render. CI fails on any drift between the pair.
- The replay gate is a gate, not a report. A failing gate means the
  compaction is wrong; revise or abandon it.
- The compacted set is smaller than what it replaces. If it is not,
  there was nothing to compact.

## Procedure

Run from the repo root, on a clean tree, with `main` up to date.

### 1. Measure

```bash
python .github/store/budget.py
```

Note the starting token count — the PR description reports the
before/after.

### 2. Branch

```bash
git switch -c "compaction/$(date -u +%Y%m%dT%H%M%SZ)" origin/main
```

Compaction branches are not `session/` branches: no records are being
written.

### 3. Build the replay cases and the baseline rule set

```bash
git show origin/main:preferences.txt > /tmp/baseline-preferences.txt
python .github/store/replay.py cases --out /tmp/cases.json
```

`cases.json` holds the most recent decisions (`replay_window` in
`store.config.json`) **masked to their input side** — question,
context, and options with the recorded prediction role, cited rules and
in-session reasoning stripped, in a per-record shuffled order so slot
position carries no signal.

### 4. Predict under the baseline set

This is the grilling skill in eval mode: given a rule set and a case's
input side, predict which slot the decider chose.

Delegate this to a **subagent**, one per run, and give it only
`/tmp/cases.json` and the rule-set file. Tell it explicitly: do not
open `decisions/`, do not open the store's own preference files, do
not search the repo. The answers are sitting in this repository, and a scoring run
that has read them measures nothing.

The subagent returns, for every case:

```json
{"predictions": [
  {"id": "<record id>", "predicted_slot": 2, "rules_cited": ["<verbatim rule>"]}
]}
```

`rules_cited` MUST be empty when no rule applies. An honest cold claim
is not a penalty — cold is the control stream. A false cold claim, or
a rule cited that did not drive the prediction, corrupts the gate in
both directions.

Save it, then score:

```bash
python .github/store/replay.py score \
  --predictions /tmp/baseline-predictions.json \
  --preferences /tmp/baseline-preferences.txt \
  --out /tmp/baseline-report.json
```

### 5. Compact

Now edit `preferences.json`, then re-render (`python
.github/store/render_preferences.py render`). The moves, in order of
preference:

1. **Drop what is dead.** A rule superseded by a later rule, or whose
   condition can no longer occur, goes. Check the records for a
   `supersedes` chain before assuming.
2. **Merge overlapping rules.** Two rules firing on the same condition
   become one. The merged rule takes the **lowest** counter of its
   constituents and the **most recent** `last:` date — a merged claim
   is only as well-evidenced as its weakest part. A merge is for rules
   that say the same thing twice — never for two preferences that
   merely fit on one line. One line states one preference (the
   fundamentals rule in `docs/conventions.md`); re-fusing split lines
   to save tokens is a format violation, not a compression.
3. **Tighten wording.** Same condition, same falsifiable outcome,
   fewer tokens. This is the safest move and usually the smallest win.

What not to do:

- Do not generalise two narrow rules into one vague one. A rule that
  cannot be wrong is worth less than the tokens it costs.
- Do not invent rules. Compaction has no promotion power; new rules go
  through `proposals/` and a human `pref-promote`.
- Do not touch a counter except as part of a documented merge. Counter
  bumps are `pref-confirm`'s job.
- Do not reorder surviving rules: order is priority (earlier wins),
  and the order is the human's ruling, not a compaction lever.

### 6. Predict under the compacted set and gate

Fresh subagent, same cases, same rules of engagement, the compacted
`preferences.txt` as the rule set:

```bash
python .github/store/replay.py score \
  --predictions /tmp/candidate-predictions.json \
  --out /tmp/candidate-report.json
python .github/store/replay.py gate \
  --baseline /tmp/baseline-report.json \
  --candidate /tmp/candidate-report.json \
  --out /tmp/replay-report.json
```

`gate` reports one of three verdicts, with a distinct exit code each:

- **`pass`** (exit 0) — the **preference-driven** hit rate held, over
  enough cases to mean it. That stream is the gate. The **cold**
  stream is the control group: it measures plain judgment, which a
  rule-set edit should barely move — a large swing there says the two
  runs were not comparable, so re-run rather than explain it away.
- **`fail`** (exit 1) — a measured regression, or two runs that are
  not comparable. Revise the compaction and re-run this step. Never
  merge a failing gate, and never edit the report to make it pass.
- **`insufficient-evidence`** (exit 3) — nothing degraded, but fewer
  than `min_gated_cases` cases were preference-driven, so the pass
  carries no evidence. This is the expected verdict on a store whose
  records are all `cold` because no rules have been extracted yet.

Cases shift streams under the compacted set (a merged rule may now
match a case that was cold, or a dropped rule may leave one cold).
Each case scores under the stream the candidate set assigns; the
report counts the shifts so a hit-rate change caused by re-labelling
rather than by better rules is visible. Read them before trusting a
pass — a candidate whose gated `n` moved a lot under a small edit is
measuring its own labelling, not its rules.

Slot numbers in the cases are shuffled per record and mapped back
during scoring, so a run cannot score by learning that slot 1 is the
prediction slot. `predicted_slot` in the report is the recorded slot;
`presented_slot` is what the run answered.

On `insufficient-evidence`, extract first: run `extract-preferences`,
let sessions score against the rules, and compact once the gated stream
can carry the claim. If the compaction cannot wait, add the
`replay_waiver_label` from `store.config.json` alongside the carve-out
label and state in the PR description why a human accepts an
unvalidated compaction. The waiver does not apply to a `fail`, by
design.

### 7. Commit

One commit, `pref-compact:` type:

```text
pref-compact: compact active set — 7 rules -> 4 (~1.8k -> ~1.1k tokens)
```

`pref-compact` exists so the log can tell compaction from promotion —
both rewrite the active set, but promotion adopts a rule a human
decided on.

### 8. Open the PR

Draft PR, carrying:

- the `carve_out_label` from `store.config.json` — without it CI
  rejects the edit to existing lines. Create the label once if it does
  not exist, using the forge tooling the environment declares.
- the `replay_waiver_label` ONLY when the gate returned
  `insufficient-evidence` and step 6's reasoning applies.
- the replay report, verbatim, in the description:

````markdown
<!-- replay-report -->
```json
{ ...contents of /tmp/replay-report.json... }
```
````

CI checks that the report is gated `pass` AND that its
`candidate_preferences_sha256` matches the `preferences.txt` in the PR
head, so a report from before the last edit fails. Re-run step 6 after
any further change to the file, and update the report in the
description.

- a summary of every merge and drop: which rules went, which survived,
  which counter the merged rule inherited, and the before/after token
  count.

## Afterwards

Merging a compaction PR is a decision. If the session that produced it
was a grilling session, it gets a record like any other, through the
recorder in its own PR. Compaction never writes to `decisions/`.
