# Decision-Memory Store

> Copier-vendored from the agentic-engineering-template store
> subtemplate — do NOT edit in the store repo; change it in the
> template and pull via `copier update`.

Private decision store: memory of decisions and preference signals
written by agentic grilling sessions across projects, scoped to one
principal — a person or a team (one store per principal, so personal
and team preferences never mix). **Knowledge base, not code:** the only executable content is the CI guard
protecting the data's integrity.

## Layout

```text
<store-repo>/
├── README.md               # this overview, human-facing
├── CLAUDE.md               # thin Claude Code shim → AGENTS.md
├── AGENTS.md               # agent entry point: golden rules, git rules
├── docs/
│   ├── conventions.md      # authoritative writing conventions — the
│   │                       # contract any writer must satisfy
│   └── extraction-prompt.md  # copy-paste prompt: extract draft
│                           # records from past conversations
├── preferences.json        # active preference set — machine-owned
│                           # source of truth
├── preferences.txt         # its render — the ONLY file injected
│                           # into agent context
├── store.config.json       # store-owned knobs: token budget, labels,
│                           # replay window, small-n threshold
├── proposals/              # agent-proposed preference rules awaiting
│                           # human promotion (merge != promotion)
├── decisions/              # full history, append-only, flat —
│                           # one immutable JSON file per decision
├── .agents/skills/         # the two manual preference-set skills
│   ├── extract-preferences/  # records -> candidate rules
│   └── compact-preferences/  # shrink the set, gated on a replay
└── .github/
    ├── workflows/          # record guards, budget report, PR gate
    ├── guards/             # copier-vendored record guard + validator
    └── store/              # copier-vendored: ingestion gate +
                            # preference-set lifecycle
```

(`proposals/` and `decisions/` materialize with their first files.)

## How it works

(Summary — the authoritative contract is
[docs/conventions.md](docs/conventions.md).)

- **Records:** one immutable JSON file per decision in `decisions/`,
  append-only. Integrity is CI-enforced rather than permission-based:
  guards reject any PR that modifies, deletes, or renames existing
  records.
- **Preferences:** the active set is a pair — `preferences.json` is
  the machine-owned source of truth, `preferences.txt` its render and
  the only file injected into agent sessions, kept under a hard token
  budget (`budget_tokens` in `store.config.json`). CI re-renders and
  fails on any drift between the two. Confirmation counters are the
  one sanctioned routine edit; everything else goes through the
  lifecycle below.
- **Proposals:** agents write candidate rules to `proposals/` (one
  rule per file); only a human `pref-promote` commit moves content
  into the active set.
- **Write flow:** one PR per session, one commit per record. Merging a
  PR accepts the records. Closing a PR without merging is itself
  signal: the next session records why (`closure_of`).
- **Consumers** reference this repo only through the
  `DECISION_MEMORY_URL` environment variable (full git URL, never
  committed anywhere public) and inject `preferences.txt` only — never
  `decisions/` wholesale.

## The preference-set lifecycle

Every rule in the rendered set costs context on every grilled session,
forever. Two manual skills manage that cost, in this order — there is
nothing to compact until rules exist, and the compaction gate cannot
measure a rule set no record was ever scored against.

| | What it does | Driven by |
| --- | --- | --- |
| **Extract** (`/extract-preferences`) | Runs on the PR ingesting a session: per pattern, bumps a counter, flags drift, or proposes a rule. Acts on the records since the last `pref-extract:` commit, reasons from the whole corpus. Never writes to `decisions/`. | `.github/store/extraction.py` |
| **Budget** (automatic) | Token-counts `preferences.txt` on every push to `main` and keeps one pinned "compression due" issue in sync. Reports, never blocks. | `.github/store/budget.py` |
| **Compact** (`/compact-preferences`) | Merges overlapping rules, drops dead ones, tightens wording — then replays the last decisions under the old and new sets and gates on the preference-driven hit rate. | `.github/store/replay.py` |

Before any of that, drafts pass the ingestion gate
(`.github/store/similarity.py`) and `/adjudicate-drafts`: duplicates,
missing re-decision links, false cold claims and unenriched
`artifact_ref`s are fixable only while the drafts are still drafts.

The gate's five thresholds live in `store.config.json`, not in the
vendored module, because the right value is a property of a store's
own corpus. Each carries its evidence under `calibration`; the gate
reports any that are unmeasured or that the corpus has outgrown, and
`/recalibrate-thresholds` re-measures and proposes — never applies.

A PR that ADDS decision records must contain a `pref-extract:` commit,
with no record added after it. That commit's position in history is the
extraction watermark — there is no watermark file — so a session that
merged without a pass is picked up by the next pass rather than lost. A
pass that found nothing commits anyway, empty.

A PR rewriting existing rules in `preferences.json` needs the carve-out
label and a passing replay report in its description; the PR gate
(`.github/store/preferences_guard.py`) checks the report was produced
against the exact file in the PR head. Below `min_gated_cases`
preference-driven cases the gate returns `insufficient-evidence`
rather than `pass`, and merging then needs an explicit waiver label —
so a store with no extracted rules yet gets an honest amber instead of
a green check that means nothing.

Check the current state at any time:

```bash
python .github/store/budget.py              # tokens, percent, level
python .github/store/extraction.py status   # records since the last pass
```

The knobs are `store.config.json` — store-owned, seeded once, never
overwritten by `copier update`. Details in
[.github/store/README.md](.github/store/README.md).

## Writing to this repo

The contract lives in [docs/conventions.md](docs/conventions.md).
The writer tool ships here, in `tools/record.py`, vendored from the
agentic-engineering-template decision-memory subtemplate;
its `--help` (the module docstring) is the authoritative behavior doc,
and design history lives in that repo's issue #37.

The recorder operates on the checkout it lives in,
so a session clones this repo and runs the copy that arrives with it:

```bash
git clone "$DECISION_MEMORY_URL" <dir>
python <dir>/tools/record.py open
```

Clone fresh per session.
A fresh clone is clean and on the default branch,
which is what keeps a session's PR to that session's own records;
a reused checkout parked on an earlier session's branch does not.
Each record is pushed as it lands, so the clone is disposable.

Hand-written records are allowed — they get no help and face the same
guards.

To extract decisions from a past conversation (no repo access needed
there), use
[docs/extraction-prompt.md](docs/extraction-prompt.md).

## Guards

`.github/guards/` — like this file and everything under `docs/` — is
copier-vendored from the template repo's decision-memory subtemplate; the guard
uses the same validator the writer tool imports, so writer and CI
validation cannot drift. Update via `copier update` (the diff is
reviewed here like any PR — the human gate on guard changes).
Every guard update re-validates the entire existing corpus. The
vendored copy keeps this repo self-contained: if the template repo
disappears, the guard keeps working; only updates stop.
