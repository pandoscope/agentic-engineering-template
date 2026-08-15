# Decision-Memory Store — Agent Guidelines

> Copier-vendored from the agentic-engineering-template store
> subtemplate — do NOT edit in the store repo; change it in the
> template and pull via `copier update`.

Private decision store for one principal (a person or a team):
records, preferences, the CI guards protecting them,
and the recorder that writes them.
The data is this repo's; every line of code around it is vendored from
the agentic-engineering-template decision-memory subtemplate,
so N stores cannot drift from one schema.
`tools/record.py --help` is the recorder's authoritative behavior doc
(design history in that repo's issue #37).

## Golden rules

Compressed summary — [docs/conventions.md](docs/conventions.md) is the
authoritative contract.

- `decisions/` is append-only: NEVER modify, delete, or rename an
  existing record. CI rejects it; do not try.
- Inject `preferences.txt` ONLY into agent context — never
  `preferences.json` (its source of truth) or `decisions/` wholesale.
- Write records through the recorder (`tools/record.py`, here in this
  repo). It operates on the checkout it lives in, so run this copy —
  clone the store fresh per session rather than reusing a checkout
  parked on someone else's branch. Hand-written records are allowed;
  they get no help and face the same guards.
- The active set may only change via counter bumps (`pref-confirm`),
  human promotion (`pref-promote`), or a gated compaction
  (`pref-compact`, carve-out label + replay report) — always by
  editing `preferences.json` and re-rendering `preferences.txt`
  (`python .github/store/render_preferences.py render`). Promotion is
  human-only, always.
- Growing and shrinking the preference set are two manual skills, run
  in that order — see [Preference-set lifecycle](#preference-set-lifecycle)
  below. Neither runs on a schedule or as a side effect of another
  task.
- Never write this repo's URL into any public artifact. Consumers
  reference it via the `DECISION_MEMORY_URL` env var only.

## Git

- Never push to `main`; PRs only.
- Session branches: `session/<YYYYMMDDTHHMMSSZ>` (created by the
  recorder's `open`).
- One PR per session; one commit per record.
- Commit types are this repo's own and are CI-linted — see
  [docs/conventions.md](docs/conventions.md) (`decision(...)`,
  `pref-proposal`, `pref-promote`, `pref-confirm`, `pref-compact`,
  `pref-drift`, `pref-extract`, `chore`).
- In managed environments (e.g. Claude Code on the Web), ALWAYS use
  the tooling the environment itself declares for forge operations
  (PR creation etc.) — `gh`/`curl` are typically sabotaged there. The
  recorder's `submit` hands the PR off to you in that case.

## Before ingesting drafts

Run the ingestion gate over the drafts and the store:

```bash
python .github/store/similarity.py --drafts <drafts.json>
```

`decisions/` is append-only, so duplicates, missing re-decision links,
false cold claims and unenriched `artifact_ref`s are all fixable ONLY
while the drafts are still drafts.

The gate finds clusters and deliberately does not resolve them — every
resolution trades one loss against another. **`/adjudicate-drafts`**
turns each cluster into a decision with its implications laid out, so
the call is one answer rather than an investigation.

Then ingest. Each surviving draft becomes **one record file and one
commit** — `record.py record` does the split, so a batch is never one
bulk commit. That is what makes partial acceptance possible: a reviewer
drops or reverts individual commits before merge.

The gate's thresholds are calibration, not settings: each is a claim
about where this store's corpus separates, and it expires as the
corpus grows. When the gate reports thresholds due a re-measurement,
**`/recalibrate-thresholds`** measures each one and proposes a value
with its evidence. It never applies the change — a threshold tuned
until the gate stops complaining is a gate switched off without anyone
deciding to switch it off.

See [docs/conventions.md](docs/conventions.md) § Ingestion gate.

## Preference-set lifecycle

`preferences.txt` — the render of `preferences.json` — is injected
into every grilled session, so every rule in it is a permanent
per-session context tax. Two manual, human-merged
skills manage it. **Run them in this order** — there is nothing to
compact until rules have been extracted, and the compaction gate
cannot measure a rule set no record was ever scored against.

1. **`/extract-preferences`** — runs on the PR that ingests a session.
   Per pattern it does exactly one of: bump a counter, flag drift
   against a rule the records contradict, or propose a new rule. Reads
   `decisions/`, never writes there. The pass ends with a
   `pref-extract:` commit whose position is the watermark, so a PR
   adding records must carry one with no record added after it. An
   empty pass commits too.
2. **`/compact-preferences`** — merges, drops and tightens, then
   replays the last decisions under the old and new sets and gates on
   the preference-driven hit rate. Needs the carve-out label and a
   fresh replay report in the PR description.

Status, any time:

```bash
python .github/store/budget.py              # tokens, percent, level
python .github/store/extraction.py status   # records since the last pass
```

The budget workflow opens a pinned "compression due" issue on its own
when the file approaches its budget — that issue, not a schedule, is
the trigger for compaction. Knobs live in `store.config.json`
(store-owned, never clobbered by `copier update`); the flow is
documented in [.github/store/README.md](.github/store/README.md).

## Pointers

- [docs/conventions.md](docs/conventions.md) — the authoritative
  writing contract: record schema, field conventions, commit types,
  PR flow, preference-set lifecycle.
- [docs/extraction-prompt.md](docs/extraction-prompt.md) — paste into
  any chat to extract draft records from a past conversation.
- `.github/guards/`, the docs, and this file are vendored from the
  template repo's decision-memory subtemplate; update via `copier update`,
  reviewed here as a normal PR diff. Only the preference-set pair (and
  the records) are owned by this store.
