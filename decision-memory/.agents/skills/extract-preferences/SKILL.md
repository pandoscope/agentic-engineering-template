---
name: extract-preferences
description: Turn decision records into preference rules — confirm, flag drift, or propose. Use when ingesting a decision session, on request to extract preferences, or before compacting a set never extracted into.
---

# Extracting preference rules from decision records

> Copier-vendored from the agentic-engineering-template decision-memory
> subtemplate — do NOT edit it in the store repo;
> change it in the template and pull via `copier update`.

`decisions/` records what happened; the active preference set
(`preferences.json`, rendered to the injected `preferences.txt`) tells
the next session what to expect. Extraction connects the two. Until it runs
every record lands `prediction_stream: cold`, because no rule was there
to drive a prediction.

Run this before `compact-preferences` on a store never extracted into —
the compaction replay gate cannot measure a rule set no record was
scored against.

**The watermark is a commit.** Every pass ends with `pref-extract:`, so
the scope is everything recorded since the last one. Two consequences:

- **A missed pass is not lost.** The watermark is simply older and this
  pass picks those records up too — read the scope before assuming it
  is only your session's.
- **The first pass needs no special mode.** No `pref-extract:` anywhere
  means the watermark is the start of the corpus.

**An empty pass still commits.** A pass that finds nothing produces no
proposal and no counter bump, so a watermark keyed on those would stall
whenever extraction legitimately had nothing to say.

**Scope is not evidence.** Scope — what you must act on — is the
records since the watermark. Evidence — what you may reason from — is
the whole corpus, shipped as `history`.

## Invariants

CI rejects the PR if any of these break.

- **`decisions/` is read-only here.** Extraction never modifies,
  deletes or renames a record; there is no carve-out.
- **Merging is not promotion.** Agents write candidates to
  `proposals/`; only a human `pref-promote` commit moves text into
  the active set. This skill touches the active set only for
  `pref-confirm` counter bumps.
- **One outcome per pattern.** Confirm, flag drift, or propose — never
  two, never a silent overwrite of a rule the records contradict.
- **The pass commit comes last.** A PR adding records must contain a
  `pref-extract:` commit with no record added after it; a record
  landing later is one the pass never saw. CI checks positionally.

## Procedure

Run from the repo root on the session branch whose records you are
ingesting — this is a step in that PR, not a separate branch. Fetch
`main` first so the watermark walk sees every pass that has landed.

### 1. Size and build the batch

```bash
python .github/store/extraction.py status
python .github/store/extraction.py batch --out /tmp/batch.json
```

`scope` is everything since the watermark; `history` is what earlier
passes covered; `watermark` names the commit walked back to, or `null`
on an untouched corpus.

A scope larger than your own session means an earlier one merged
without a pass. Cover them all.

Work the four queues in order — the order is the evidence ranking:

1. **`corrections`** — a `"N, but actually because…"` ruling. The
   decider stated their own reason where the model guessed wrong, so
   this is the one place the corpus carries a reason nobody inferred.
   Process first and let it reshape how you read the rest.
2. **`misses`** — the prediction was wrong. Every miss must refine a
   rule, split one covering two conditions, or spawn a candidate. A
   miss that does none is written down as unexplained — a state, not a
   silence, and the seed of the next pass.
3. **`refinements`** — `refined` and `near-tie` outcomes. The rule was
   directionally right and incomplete. Usually wording or condition.
4. **`confirmations`** — clean hits. Counter bumps, and the evidence a
   rule is earning its tokens.

### 2. Find the patterns

Read `history` before writing anything. A single record cannot
distinguish a principle from a one-off, and two records from one
session agreeing is one data point — **cross-session repetition is the
evidence**. A pattern appearing once here and twice in `history` is a
three-record pattern.

Compare each candidate against the current `preferences.json` and
everything in `proposals/`: a rule proposed on an earlier branch and
not yet promoted is not a new discovery.

### 3. Classify — exactly one outcome each

**a) Matches an existing rule → confirm.** Bump counter and date in
`preferences.json`, re-render (`python
.github/store/render_preferences.py render`), commit both files — one
commit per rule:

```text
pref-confirm: rejects new infrastructure dependencies (n=5)
```

CI validates the math: increment by exactly 1, rule text unchanged,
render in sync.

Do **not** bump on a record in `rule_driven_acceptances` — there the
rule cited itself into the prediction slot and that slot was chosen, so
the recommendation caused the choice it would be credited with
predicting. Zero independent evidence.

**b) Contradicts an existing rule → flag drift.** Never rewrite the
rule. Write `proposals/<YYYY-MM-DD>-drift-<slug>.md` naming the rule,
the contradicting records, and a choice between conditionalizing it
(holds, but only under a condition the records now show) and retiring
it (the principle changed).

```text
pref-drift: infrastructure rule mispredicts solution shape (3 records)
```

The choice belongs to the human merging the PR. Present it as a
decision, not a recommendation dressed as one.

**c) Genuinely new → propose.** `proposals/<YYYY-MM-DD>-<slug>.md`, one
rule per file, conditional and falsifiable: a condition, an outcome,
and a way to be wrong.

State the candidate as a **fundamental**: one preference, in the
plainest words that survive a cold read. A rule that needs a
corollary is two rules — write two files. Decomposing at proposal
time is cheaper than decomposing at promotion, when a counter has to
be divided along with the words.

```text
pref-proposal: prefers the simplest solution that solves the actual problem
```

If you cannot state what would falsify it, it is an observation, not a
rule.

**d) A later rule keeps beating an earlier one → propose a reorder.**
Rule order is priority — earlier wins. The batch's `conflict_tally`
counts decided contests between current rules (the chosen option's
cited rules beat the cited rules of options the decider declined). An
entry with `order_violation: true` says the evidence disagrees with
the order. Write `proposals/<YYYY-MM-DD>-reorder-<slug>.md` naming the
pair, the counts, and the records behind them:

```text
pref-proposal: move machine-checks above simplest-shape (3 contests, 3 wins)
```

A human reorders the set under the carve-out label; this pass never
moves a rule.

### 4. Close the pass

The last commit of the PR, after every proposal, drift flag and counter
bump has landed:

```bash
git commit --allow-empty -m "pref-extract: 4 records — 1 proposal, 1 unexplained"
```

`--allow-empty` because a pass that found nothing must still move the
watermark. Put the detail in the body — records covered, what was
found, what was left unexplained. Nothing parses it; it is for whoever
reads the log.

This commit must come **after** every record in the PR; CI fails a
record added after it.

### 5. Write the rest of the PR description

- what was found, per queue, with counts;
- every proposal and drift flag, and the records behind each;
- every unexplained miss, named — a pass that explains nothing is a
  legitimate result and must be visible as one;
- the counter bumps, and separately the rule-driven acceptances
  deliberately NOT counted.

Merging is promotion only for the `pref-confirm` bumps. Proposals and
drift flags land as files; a human turns them into rules with a
`pref-promote` commit, or does not.

## Afterwards

A set that has just grown is a candidate for compaction, not an
obligation. Check the budget:

```bash
python .github/store/budget.py
```

Compact when the budget workflow says compression is due, through
`compact-preferences`.
