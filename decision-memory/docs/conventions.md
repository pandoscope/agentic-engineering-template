# Decision-Memory Store — Writing Conventions

> Copier-vendored from the agentic-engineering-template store
> subtemplate — do NOT edit in the store repo; change it in the
> template and pull via `copier update`.

The authoritative contract any writer — tool or hand — must satisfy.
The CI guards (`.github/guards/`) enforce it mechanically; this file
is the human-readable authority, `decision_validator.py` the
machine-readable one. Both live in the template repo's store
subtemplate and change together there, in the same PR.

## Storage layout

- `decisions/<id>.json` — one immutable JSON **file** per decision,
  flat directory. Append-only: existing files are NEVER modified,
  deleted, or renamed.
- `predictions/<id>.json` — the same schema and the same append-only
  guarantee, for what an AUTONOMOUS run chose under the active
  preference set with no decider present. Written by
  `record.py record --predict`, committed as
  `prediction(<project>): …`.

  A prediction is not a ruling, so it stays outside the preference
  pipeline entirely: extraction never walks the directory, no
  prediction can bump a counter, and a PR adding only predictions needs
  no extraction pass. It is replay material — the records carry
  `preference_set.commit`, so a candidate rule set can be run against
  them to see what an agent WOULD have chosen. Keeping the corpora
  apart is what lets `decisions/` keep meaning a-human-ruled.
- ID = filename stem = `<timestamp>-<slug>`, e.g.
  `20260715T143205Z-agent-access`. The slug is a writer-chosen
  kebab-case title, ≤40 chars; the timestamp is minted (UTC) by the
  writer tool. No project prefix — `project` is a record field only;
  artifact URLs never appear in IDs or filenames.

## Record schema

Records are **replay-ready**: input-side fields are written BEFORE
the ruling, output-side after, so a replay harness can mask outcomes
and score predictions. Field order groups the two sides.

Example (a real record):

```json
{
  "v": 1,
  "type": "decision",
  "id": "20260715T143205Z-agent-access",
  "date": "2026-07-15",
  "project": "factory",

  "question": "How do agent environments access the preference repo?",
  "context": "session-local facts informing the options, written before the ruling",
  "options": [
    {"slot": 1, "label": "read-only deploy key, human-approved writes",
     "role": "prediction+recommendation",
     "rules_cited": [],
     "reasoning": "integrity via least privilege"},
    {"slot": 2, "label": "full-access PAT everywhere",
     "if_clause": "if write friction matters more than blast radius"},
    {"slot": 3, "label": "local clone, manual sync",
     "if_clause": "if offline work dominates"}
  ],
  "prediction_stream": "cold",
  "preference_set": {"commit": "<sha>"},
  "artifact_ref": {
    "repo": "skills",
    "path": "skills/grilling/SKILL.md",
    "commit": "<sha>",
    "anchor": "#recording"
  },
  "session": "session_01ABC…",

  "chosen_slot": 4,
  "chosen": "all agents as collaborators, PRs against main",
  "operative_reason": "write friction defeats seamless recording — reintroduces manual journaling step",
  "correction": false,
  "rejections": [
    {"option": "read-only deploy key",
     "reason": "write friction defeats seamless recording — reintroduces manual journaling step",
     "status": "operative", "reason_class": "TBD"},
    {"option": "full-access direct push",
     "reason": "agent could silently rewrite preference history",
     "status": "presumed-false", "reason_source": "inferred",
     "reason_class": "TBD"}
  ],
  "outcome": "miss",
  "drill_down_of": null,

  "related": ["20260715T141020Z-repo-hosting"],
  "supersedes": null,
  "notes": "Compromise adopted: CI-enforced append-only replaces access control"
}
```

Required fields: `v`, `type`, `id`, `date`, `project`, `question`,
`options`, `prediction_stream`, `artifact_ref`, `chosen_slot`,
`chosen`, `rejections`, `outcome`. Unknown fields are TOLERATED
everywhere — new optional fields need no migration.

### Envelope

- `v`: schema version, minted by the writer tool.
- `type`: always `"decision"` in this repo — routes records in future
  mixed dojo ledgers. The envelope `{v, id, type}` is the universal
  write format shared with all future dojo record kinds; this repo is
  a terminal store (no compaction — per-record PR review is the
  point). `ts` is deliberately absent: the ID embeds it.
- `id`: stable, unique, always equal to the filename stem.

### Input side (pre-ruling)

- `question`, `context`, `options` (the MC block verbatim: slot,
  label, role, if-clause, reasoning, cited preference rules),
  `prediction_stream`, `preference_set`, `artifact_ref`, `session`.
- `options[].role`: `prediction` (slot 1 — what the preference set
  predicts; `rules_cited` names the rules, empty = cold),
  `recommendation` (slot 2 — the agent's independent best),
  `prediction+recommendation` when merged, `wildcard` (slot 3).
  Exactly ONE option carries a prediction role. Recommendations are
  recorded as made in-session, NEVER back-filled after the choice is
  known.
- `prediction_stream`: `preference-driven` | `cold` — the two scoring
  streams. `rules_cited` non-empty iff preference-driven. Cold misses
  never count against the preference model (pure judgment
  calibration, prime seeds for new rules).
- `preference_set.commit`: SHA of this repo as injected at session
  start — content-addresses the active preference set, so replay can
  flag matching-but-uncited rules (false cold claims are detectable
  provenance defects).
- `artifact_ref`: REQUIRED when an artifact exists — repo-relative
  path + commit SHA (content-addressed, survives rewrites) + anchor
  when possible. Null only if genuinely no artifact. Chat-extracted
  drafts carry null refs by design (never guess SHAs); PARTIAL refs
  are welcome — repo/path/anchor with `commit: null` beats all-null.
  Enrich them in the drafts file at ingestion time, once the commits
  exist — drafts are plain JSON, no tooling needed.
- `session`: opaque grouping key, NOT a locator — minted best-effort
  by the writer tool, `null` when unavailable. Never load-bearing.

### Output side (post-ruling)

- `chosen_slot`, `chosen` (free text when slot 4),
  `operative_reason` (the confirmed if-clause verbatim, or the stated
  free-text reason — required when a listed non-prediction option is
  chosen), `correction` ("N, but actually because…" — highest-signal
  event, first-class flag), `rejections`, `outcome`.
- `operative_reason_source`: `stated` (default — non-empty
  `operative_reason` required) | `none` (silent pick: the decider
  chose a listed non-prediction option without stating a reason;
  `operative_reason` must be null — declared, never lazy).
  Deliberately NO inferred tier: operative means decider-confirmed;
  an inferred why-chosen lives in the chosen option's own `reasoning`
  and in the rejections.
- `rejections[].status`: `operative` (confirmed by the choice,
  recorded verbatim, no inference) | `presumed-false` (the likely
  reason the option lost, recorded as inference). Never conflate —
  only operative reasons feed rule extraction. Deciders can upgrade a
  presumed-false reason to operative by stating their own (e.g.
  "Option N, because XYZ" in the free-text slot).
- `rejections[].reason_source`: REQUIRED on presumed-false rejections
  — `if_clause` (the option's own if-clause did not hold), `inferred`
  (most-likely reason from context, marked as the model's inference),
  or `none` (nothing stated or inferable — ONLY then is
  `reason: null` valid; declared, never a lazy default or a filler
  string). Prefer `if_clause`/`inferred` over `none`. Operative
  rejections are stated by definition (`reason_source` omitted or
  `stated`).
- `reason_class`: free text / `"TBD"` for now; a taxonomy emerges
  after ~20 real entries, not before.
- `outcome`: `hit` | `miss` | `near-tie` | `refined` — scored
  against the prediction slot per stream. Near-ties are never scored
  as misses and never carry fabricated rejection reasons. `refined`
  = the chosen answer CONTAINS the prediction plus an extension
  (right but incomplete — the most common free-text answer style);
  bucketed separately in hit rates, never counted as a miss, and
  never auto-bumping preference counters (only clean hits confirm
  rules).
- `drill_down_of`: ID of the parent record when this record is a
  drill-down follow-up question (drill-downs are themselves
  prediction-scored MC events with their own records); else null.

### Links

- `related` (informs/refines) and `supersedes` (replaces —
  decision-level drift signal). Stable IDs + link fields ARE the
  graph; no edge-type taxonomy beyond these two. All referenced IDs
  must exist (CI-checked).
- `closure_of`: optional — the number of a closed-unmerged PR in THIS
  repo that the record explains ("why was PR #N rejected"). Doubles
  as the stateless sweep watermark: the writer's `open` lists all
  closed-unmerged PRs and prompts records only for those not yet
  covered by a matching `closure_of` — the records themselves are the
  state.

## Active preference set (`preferences.json` + `preferences.txt`)

Storage and priming are two concerns, so the set is a pair:

- **`preferences.json` is the source of truth**, machine-owned. Every
  write goes here, through tooling — `submit`'s counter bumps, a
  human's promote/compact edits followed by a re-render. One object
  per rule:

  ```json
  {"rules": [
    {"rule": "Prefers machine checks over model checks wherever feasible.",
     "confirmed": 3, "independent": 1, "last": "2026-08-11"}
  ]}
  ```

- **`preferences.txt` is its render and the ONLY file injected into
  sessions.** A plain ordered list — one rule per line, nothing else.
  ALL bookkeeping stays out: counters and dates matter at update time,
  never in-session, and numbers in the render would invite the reader
  to discount young rules — a promoted rule is equally binding at zero
  confirmations. The order carries the only in-session ranking.

  ```text
  Prefers machine checks over model checks wherever feasible.
  ```

- **The pair is a declared mirror with a machine check.** The guards
  re-render the JSON and fail on any diff (per commit and at head), so
  the copy cannot drift silently and no model maintains the sync by
  hand. `python .github/store/render_preferences.py render` is the
  update mechanism; `check` verifies. A store arriving from the legacy
  markdown format converts its rules by hand, once — the schema,
  mirror, and budget guards verify the result.
- Hard token budget on the RENDERED file, CI-enforced — the render is
  the thing that actually costs. The number is `budget_tokens` in the
  store-owned `store.config.json`; the vendored guard reads that
  value, so the budget is checked in exactly one place against exactly
  one authority. Promoting a rule at budget means merging or demoting
  another ("promote requires demote").
- **Rule order is priority, and it is a ruling.** When two rules match
  contradicting solutions, the earlier rule wins. The order is
  human-owned: a newly promoted rule appends at the END (lowest
  priority) unless the promoting human places it; moving an existing
  rule is a rewrite of the active set and takes the carve-out label
  like any other. Nothing recomputes the order — observed conflicts
  where a later rule beat an earlier one are extraction findings that
  PROPOSE a reorder, never apply one.
- Rules are conditional and falsifiable, one object each. The schema
  is a closed set — `rule`, `confirmed`, `independent`, `last`,
  nothing else — so a rule cannot gain keys nothing reads, and a
  hand-added rule cannot enter without counters.
- `rule` is one plain single-spaced line; the render never wraps. It
  may hold a joined qualifier sentence under the one counter — "one
  line, one preference" counts preferences, not sentences: a qualifier
  the decider reads as part of the rule belongs to it, not to a second
  entry.
- Rule text is unique within the set (bumps match by text).

Counter semantics:

| Key | Meaning |
| --- | --- |
| `confirmed` | Times the rule predicted the chosen slot |
| `independent` | Of those, how many were NOT the rule citing itself into the slot that was then chosen. Never greater than `confirmed` |
| `last` | Date of the most recent confirmation |

### Counter updates

- Counter-field updates are the ONE sanctioned edit in this repo,
  executed mechanically: `submit` bumps the JSON, re-renders, and
  auto-generates `pref-confirm` commits; CI validates the math
  structurally (increment by exactly 1, rule text unchanged, other
  fields untouched).
- `submit` counts two kinds of confirmation, and keeps them apart. A
  `hit` credits the rules cited on the slot that won — the rule
  agreeing with the option it authored, which raises `confirmed`
  alone. The other kind is a rule cited on some option the rule did
  NOT propose, which the decider chose anyway; that raises
  `independent` as well. Only the second is evidence the rule tracks
  the decider rather than steering them.
- `independent` may rise by at most 1 per bump and never falls. A
  mechanical bump that lowered it would be erasing evidence under a
  subject that reads as routine.
- Editing counters for any reason OTHER than a mechanical bump —
  correcting a count, backfilling a field — rewrites an existing rule,
  so it needs the carve-out label and a replay report like any other
  edit to the active set.
- Promotion is separate and human-only: agents write candidate rules
  to `proposals/<YYYY-MM-DD>-<slug>.md` (one rule per file); only a
  human `pref-promote` commit moves content into the active set.
  Merging a proposal file is NOT promotion.

## Ingestion gate

Drafts are mutable; records are not. Everything below is fixable only
before ingestion, so `.github/store/similarity.py` runs over the drafts
plus the store first:

- **Duplicates** — the same ruling extracted twice (two runs over one
  session) would mint two immutable records for one decision. Classified
  by provenance: same `preference_set.commit` + same `chosen`. The
  remedy is discarding one to `discarded-drafts.json`, never deleting.
- **Re-decisions** — a `related`/`supersedes` edge cannot be added after
  ingestion without violating append-only, so ingestion is the ONLY
  moment an edge can be written.
- **False cold** — `preference_set.commit` content-addresses the active
  set precisely so a false cold claim is a detectable provenance defect;
  the gate runs that check against the pinned set. Advisory, human
  confirms, and a confirmed one is restreamed in the draft.
- **`artifact_ref` completeness** — chat-extracted drafts carry
  null/partial refs by design and are enriched here, once the commits
  exist. SHAs are never guessed.

The gate reports and never writes; `/adjudicate-drafts` proposes a
resolution per cluster with what each way costs, and a human decides.
An undetected duplicate is not merely a wasted file — it double-counts
as evidence, and cross-record repetition is exactly what extraction
reads as the signal that a pattern is a principle. Details in
[.github/store/README.md](../.github/store/README.md).

## Preference-set lifecycle

The active set is grown by extraction and shrunk by compaction, in
that order — there is nothing to compact until rules exist, and the
compaction gate cannot measure a rule set no record was scored
against.

- **Extraction** (`.agents/skills/extract-preferences/`, driven by
  `.github/store/extraction.py`) runs on the PR that ingests a
  session and, per pattern, does exactly one of: bump a counter, flag
  drift, or propose a rule. It never writes to `decisions/`.
  **The watermark is a `pref-extract:` commit**, so the scope is
  everything recorded since the last one — derived from history, never
  tracked beside it. Nothing to advance or keep in sync, nothing to
  conflict when two branches run a pass at once; a session that merged
  without a pass is picked up by the next one rather than lost; and the
  first pass on a corpus needs no special mode, because no
  `pref-extract:` commit means the whole corpus is in scope.
  An empty pass commits too (`--allow-empty`) — see § Commit types.
  Scope and evidence are separate: the pass ACTS on the records since
  the watermark and REASONS from the whole corpus, because
  cross-session repetition is the evidence one session cannot see.
  A PR adding records must contain a `pref-extract:` commit with no
  record added after it — extraction is the last step of the pass. The
  check is positional; there is nothing to enumerate.
- **Budget** (`.github/store/budget.py`) reports usage against
  `budget_tokens` on every push to `main`, keeping one pinned
  "compression due" issue in sync. It reports; it never blocks.
- **Compaction** (`.agents/skills/compact-preferences/`, driven by
  `.github/store/replay.py`) merges overlapping rules, drops dead
  ones, tightens wording, then replays the last `replay_window`
  decisions under the old and new sets and compares the
  preference-driven hit rate. A carve-out PR carries the gate report
  in its description and the carve-out label; CI verifies the report
  is `pass` and was produced against the exact `preferences.txt` in
  the PR head.
- **Small-n honesty.** Below `min_gated_cases` preference-driven
  cases, the gate returns `insufficient-evidence` rather than `pass`,
  and CI accepts that only with the replay-waiver label. A store with
  no extracted rules yet needs the waiver on every compaction. That is
  the honest state and it is meant to be visible.
- **Slot ordering is masked.** Replay cases present each record's
  options in an order derived from its ID and scoring maps predictions
  back, so the number measures rules rather than the convention that
  slot 1 is the prediction slot.

## Commit types

This repo's own conventional-commit types, CI-linted on every PR
commit:

- `decision(<project>): <slug> — <chosen>`
- `pref-proposal: <rule>`
- `pref-promote: <rule>` (human only)
- `pref-confirm: <rule> (n=<count>)` (counter bump)
- `pref-compact: <summary>` (compaction of the active set)
- `pref-drift: <summary>` (a rule the records contradict)
- `pref-extract: <summary>` (an extraction pass — the watermark)
- `chore: ...` (structure, CI, docs)

Three of these may REWRITE existing rules in the active set:
`pref-confirm` (counter bumps, math CI-validated),
`pref-promote` (a human adopting a rule, possibly demoting another to
make room), and `pref-compact` (rewriting the set without adding
anything that was not already promoted).
They are separate types because they are separate acts: compaction is
not promotion, and a log where both read `pref-promote:` cannot tell a
reader which one happened.
The human gate on compaction is the merge plus the carve-out label,
not the commit subject.

`pref-drift` adds a file to `proposals/` and never touches the active
set: a rule the records contradict is conditionalized or retired by a
human, never silently overwritten.

`pref-extract` closes an extraction pass and is usually EMPTY
(`--allow-empty`). Its position in history is the extraction
watermark, so it is committed even when the pass found nothing — a
watermark that only moved on findings would stall every time
extraction legitimately had nothing to say.

Examples:

```text
decision(factory): repo hosting — private GitHub over self-hosted/synced
decision(factory): agent access — collaborators+PRs over read-only key
pref-proposal: prefers CI-enforced integrity over access restrictions
pref-confirm: rejects new infrastructure dependencies (n=4)
pref-compact: compact active set — 7 rules -> 4 (~1.8k -> ~1.1k tokens)
pref-drift: infrastructure rule mispredicts solution shape (3 records)
pref-extract: 4 records — 1 proposal, 1 unexplained
```

## PR flow

- One PR per session (branch `session/<YYYYMMDDTHHMMSSZ>`); ONE
  commit per record — atomic and dissectable. Partial accept =
  hand-edit the branch, drop or revert individual commits before
  merge.
- Merging a decision-record PR = acceptance of the *record*.
- Closing a PR without merge is itself signal: the next session's
  `open` sweep prompts a closure record (`closure_of`), with the
  `correction` flag where applicable.
- `supersedes` claims must be surfaced in the PR description for
  explicit human review.
- The session-end PR description states prediction hit rates as two
  streams (preference-driven vs cold — the control group).

## CI guards

`.github/guards/` — copier-vendored from the
agentic-engineering-template decision-memory subtemplate (single shared source;
the writer tool imports the same validator from its session clone).
Stdlib-only, no dependencies; fails soft on factory loss. Checks:

- Append-only on `decisions/**` (no modify/delete/rename, no
  exceptions); rewrites of existing `preferences.json` rules only from
  `pref-confirm`/`pref-promote`/`pref-compact` commits, counter math
  validated structurally.
- Schema + consistency on the ENTIRE corpus every run — guard updates
  can never retroactively invalidate or silently mis-accept records
  without it showing.
- Dangling-reference check on `related`/`supersedes`/`drill_down_of`.
- The preferences.json schema, the preferences.txt mirror (re-rendered
  and compared), and the token budget on the render, against
  `budget_tokens`.
- Commit lint (the types above).
- A PR adding records contains a `pref-extract:` commit, with no
  record added after it.

`.github/store/` — vendored from the same subtemplate — adds the
preference-set lifecycle on top: the carve-out label, the replay gate,
the budget report, and the extraction batch. The two directories split
by audience, not by trust: `guards/` is the record contract the writer
tool shares, `store/` is everything about the preference set.
